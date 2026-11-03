// GNOME notifications as the daemon's cards see them: the "notify" message
// a notification becomes, the messages around it, and which button, if
// any, an answer may press.
//
// Plain JavaScript with no GNOME imports, so the tests run it under node
// and feed what it builds to the daemon's own parser. The daemon decides
// what shows (mirror_refusal in screenpad_touchbar/notify.py); this only
// describes the notification and filters the answers.

// The app name this says in hello, and the message tray's urgencies (LOW,
// NORMAL, HIGH, CRITICAL) as the daemon names them.
export const APP = 'gnome-notifications';
export const URGENCY = ['low', 'normal', 'high', 'critical'];
// The option ids the daemon gives a card's buttons, by index.
export const ACTION = /^action-([0-9])$/;
// Messages waiting to be written; more means the daemon stopped reading.
export const MAX_PENDING = 64;

export function hello() {
    return {op: 'hello', app: APP};
}

export function cancel(id) {
    return {op: 'cancel', id};
}

// One message as a line on the socket.
export function encodeLine(msg) {
    return new TextEncoder().encode(`${JSON.stringify(msg)}\n`);
}

// Markup tags removed, for when Pango cannot parse a body.
export function stripMarkup(text) {
    return text.replace(/<[^>]*>/g, '');
}

// The notify message for a notification, from what GNOME says of it:
// appId (its app's id when the app's own policy applies, else null),
// source (the source's title), title, text (the body as plain text),
// urgency (MessageTray.Urgency), actions (the buttons' labels), banners
// (whether GNOME shows banners for the app), dnd (Do Not Disturb) and
// acknowledged (it was already seen when added).
export function notifyMessage(id, n) {
    return {
        op: 'notify',
        id,
        app: n.appId ? `${n.appId}.desktop` : null,
        source: n.source ?? null,
        title: n.title ?? null,
        text: n.text ?? null,
        urgency: URGENCY[n.urgency] ?? 'normal',
        actions: [...(n.actions ?? [])],
        banners: n.banners ?? true,
        dnd: Boolean(n.dnd),
        acknowledged: Boolean(n.acknowledged),
    };
}

// A line from the daemon as an answer to one of our cards, or null.
export function parseAnswer(line) {
    let msg;
    try {
        msg = JSON.parse(line);
    } catch {
        return null;
    }
    return msg?.op === 'answer' && typeof msg.id === 'string' ? msg : null;
}

// The button an answer presses: only "chosen" presses one, and only the
// very button the card showed (shown, by index), still on the notification
// (current). One an update replaced since is never pressed in its place.
export function actionToPress(answer, shown, current) {
    if (answer.reason !== 'chosen' || typeof answer.option !== 'string') return null;
    const match = ACTION.exec(answer.option);
    const action = match ? shown[Number(match[1])] : undefined;
    return action !== undefined && current.includes(action) ? action : null;
}
