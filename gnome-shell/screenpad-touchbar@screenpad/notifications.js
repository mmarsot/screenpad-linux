// Mirrors GNOME's notifications into the touch bar (screenpad-touchbar).
//
// For each notification the message tray asks a banner for, this sends the
// daemon a normalized "notify" message over the bar's socket. The daemon
// decides what shows (touchbar.py mirror_refusal: buttons or urgent, Do Not
// Disturb, the config) and grows the bar into a card for it. When a button
// on the card is tapped, the daemon answers with its index, and this presses
// that notification's own button, only if the notification and that very
// button still exist. Dismiss, a timeout, the notification going away or the
// bar going away only close the card: nothing here activates a notification
// by itself. With no daemon to talk to, nothing is mirrored or kept.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';

// The app name this says in hello, and the message tray's urgencies (LOW,
// NORMAL, HIGH, CRITICAL) as the daemon names them.
const APP = 'gnome-notifications';
const URGENCY = ['low', 'normal', 'high', 'critical'];
const ACTION = /^action-([0-9])$/;
// Messages waiting to be written; more means the daemon stopped reading.
const MAX_PENDING = 64;

// A notification's body as plain text (fdo bodies may carry markup).
function plain(text, markup) {
    if (!text || !markup)
        return text ?? null;
    try {
        return Pango.parse_markup(text, -1, '')[2];
    } catch {
        return text.replace(/<[^>]*>/g, '');
    }
}

export class NotificationMirror {
    constructor(socketFile) {
        this._path = socketFile.get_path();
        this._cancellable = new Gio.Cancellable();
        this._conn = null;
        this._in = null;
        this._out = null;
        this._connecting = false;
        this._pending = [];
        this._writing = false;
        // id -> {notification, actions}: cards the daemon may show now,
        // with the buttons as they were when sent.
        this._cards = new Map();
        this._ids = new Map();          // notification -> id
        // notification -> whether it was acknowledged when added. GNOME's
        // own banner handler runs before ours and may acknowledge it (it
        // is on screen), so this keeps what GNOME decided on.
        this._added = new Map();
        this._next = 0;
        this._settings = new Gio.Settings({schema_id: 'org.gnome.desktop.notifications'});
        Main.messageTray.connectObject(
            'source-added', (_tray, source) => this._watch(source),
            'source-removed', (_tray, source) => source.disconnectObject(this), this);
        Main.messageTray.getSources().forEach(source => this._watch(source));
    }

    destroy() {
        Main.messageTray.disconnectObject(this);
        Main.messageTray.getSources().forEach(source => source.disconnectObject(this));
        this._ids.forEach((_id, notification) => notification.disconnectObject(this));
        this._ids.clear();
        this._added.clear();
        this._cancellable.cancel();
        this._close();
        this._settings.run_dispose();
        this._settings = null;
    }

    // Connect to the daemon if not connected (the extension calls this
    // whenever touchbar.json changes, so a restarted daemon is found).
    wake() {
        if (this._conn || this._connecting || this._cancellable.is_cancelled())
            return;
        this._connecting = true;
        const client = new Gio.SocketClient();
        client.connect_async(Gio.UnixSocketAddress.new(this._path), this._cancellable,
            (c, result) => {
                this._connecting = false;
                let conn;
                try {
                    conn = c.connect_finish(result);
                } catch {
                    return;             // no daemon, or disabled meanwhile
                }
                this._conn = conn;
                this._in = new Gio.DataInputStream({base_stream: conn.get_input_stream()});
                this._out = conn.get_output_stream();
                this._send({op: 'hello', app: APP});
                this._read(this._in);
            });
    }

    _watch(source) {
        source.connectObject(
            'notification-added', (_source, notification) =>
                this._added.set(notification, notification.acknowledged),
            'notification-request-banner', (_source, notification) => {
                // A new one as it was added; asked again later, it was
                // just made unacknowledged.
                this._forward(notification, this._added.get(notification) ?? false);
                this._added.delete(notification);
            }, this);
    }

    // GNOME wants a banner for this notification: tell the daemon.
    _forward(notification, acknowledged = false) {
        if (!this._out)
            return;
        let id = this._ids.get(notification);
        if (!id) {
            id = `n${++this._next}`;
            this._ids.set(notification, id);
            const changed = () => this._changed(notification);
            notification.connectObject(
                'destroy', () => this._gone(notification),
                'action-added', changed, 'action-removed', changed,
                'notify::title', changed, 'notify::body', changed, this);
        }
        const source = notification.source;
        // Only an app's own policy names the app (the generic one is "generic").
        const policy = source?.policy;
        const appId = policy instanceof MessageTray.NotificationApplicationPolicy
            ? policy.id : null;
        this._cards.set(id, {notification, actions: [...notification.actions]});
        this._send({
            op: 'notify', id,
            app: appId ? `${appId}.desktop` : null,
            source: source?.title ?? null,
            title: notification.title ?? null,
            text: plain(notification.body, notification.useBodyMarkup),
            urgency: URGENCY[notification.urgency] ?? 'normal',
            actions: notification.actions.map(action => action.label),
            banners: policy?.showBanners ?? true,
            dnd: !this._settings.get_boolean('show-banners'),
            acknowledged,
        });
    }

    // An app updated a notification whose card may show: send it again,
    // once, after the update's other changes.
    _changed(notification) {
        const id = this._ids.get(notification);
        if (!this._cards.has(id) || this._cards.get(id).idle)
            return;
        this._cards.get(id).idle = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            if (this._cards.get(id)?.notification === notification)
                this._forward(notification);
            return GLib.SOURCE_REMOVE;
        });
    }

    _gone(notification) {
        const id = this._ids.get(notification);
        this._ids.delete(notification);
        this._added.delete(notification);
        notification.disconnectObject(this);
        if (this._forget(id))
            this._send({op: 'cancel', id});
    }

    _forget(id) {
        const card = this._cards.get(id);
        if (!card)
            return false;
        if (card.idle)
            GLib.source_remove(card.idle);
        this._cards.delete(id);
        return true;
    }

    // The daemon closed a card. Only "chosen" presses a button, and only
    // the very button the card showed, still on the notification: one an
    // update replaced since is never pressed in its place.
    _answer(msg) {
        const card = this._cards.get(msg.id);
        if (!card)
            return;
        this._forget(msg.id);
        if (msg.reason !== 'chosen' || typeof msg.option !== 'string')
            return;
        const match = ACTION.exec(msg.option);
        const action = match ? card.actions[Number(match[1])] : null;
        if (action && card.notification.actions.includes(action))
            action.activate();
    }

    _read(input) {
        input.read_line_async(GLib.PRIORITY_DEFAULT, this._cancellable, (stream, result) => {
            let line = null;
            try {
                [line] = stream.read_line_finish_utf8(result);
            } catch {
                // Cancelled, or the daemon went away.
            }
            if (input !== this._in)
                return;
            if (line === null) {
                this._close();
                return;
            }
            let msg = null;
            try {
                msg = JSON.parse(line);
            } catch {
                // Not for us.
            }
            if (msg?.op === 'answer' && typeof msg.id === 'string')
                this._answer(msg);
            this._read(input);
        });
    }

    _send(msg) {
        if (!this._out)
            return;
        if (this._pending.length >= MAX_PENDING) {
            this._close();
            return;
        }
        this._pending.push(new TextEncoder().encode(`${JSON.stringify(msg)}\n`));
        this._flush();
    }

    _flush() {
        if (this._writing || !this._out || this._pending.length === 0)
            return;
        const out = this._out;
        const data = this._pending.shift();
        this._writing = true;
        out.write_bytes_async(new GLib.Bytes(data), GLib.PRIORITY_DEFAULT, this._cancellable,
            (stream, result) => {
                if (out !== this._out)
                    return;
                this._writing = false;
                let written;
                try {
                    written = stream.write_bytes_finish(result);
                } catch {
                    this._close();
                    return;
                }
                if (written < data.length)
                    this._pending.unshift(data.slice(written));
                this._flush();
            });
    }

    // The connection is gone: the daemon dropped its cards with it.
    _close() {
        const conn = this._conn;
        this._conn = this._in = this._out = null;
        this._pending = [];
        this._writing = false;
        [...this._cards.keys()].forEach(id => this._forget(id));
        conn?.close_async(GLib.PRIORITY_DEFAULT, null, null);
    }
}
