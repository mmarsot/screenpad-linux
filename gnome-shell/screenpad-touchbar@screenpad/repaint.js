// When to repaint the whole ScreenPad monitor (the repaint safeguard).
//
// GNOME composites every monitor on the Intel GPU; for the ScreenPad,
// which hangs off the NVIDIA GPU, mutter then has the NVIDIA GPU copy each
// frame into two NVIDIA scanout buffers, used in turn. That copy covers
// only the area that changed in the last two frames, so a bad copy in an
// area that then stops changing stays on the panel until something draws
// there again. A frame in which the whole monitor is damaged is copied
// whole, into one buffer, and the frame after it, which still counts the
// damage of the frame before, into the other. So the safeguard damages the
// whole monitor for FULL_FRAMES frames in a row:
// - once the monitor's content has stopped changing for SETTLE_MS (the
//   main case: a bad copy while things move stays while they are still),
//   or every MAX_WAIT_MS while it keeps changing;
// - on a display event (resume, monitors changed, the bar drawn again, as
//   after unlock): at once, and again once things settle.
// Otherwise nothing runs: no timer is armed while the pad is idle.
// docs/touchbar.md ("The repaint safeguard") explains it, with the mutter
// source it relies on.
//
// Plain JavaScript with no GNOME imports, so the tests run it under node
// with a stand-in clock. Every input takes the time in ms and returns a
// step: {redraw, timer}. redraw: queue a redraw of the full-monitor actor
// now. timer: null to keep the one timer as it is, CANCEL to remove it, or
// the delay in ms to (re)arm it at, replacing the armed one; when it
// fires, call wake(). The timer is moved only when it must fire earlier:
// a paint that pushes the settle time back leaves it alone, and wake()
// re-arms it for the later time. So a burst of activity costs one timer,
// not one per frame.

// How long the monitor's content must stay still before the settle repaint.
export const SETTLE_MS = 1500;
// The longest the settle repaint waits while the content keeps changing.
export const MAX_WAIT_MS = 10000;
// Frames in a row with the whole monitor damaged: one per scanout buffer.
export const FULL_FRAMES = 2;
// How long to wait for a forced frame before giving up (no frame comes
// while the monitor is blanked, for example).
export const FRAME_WAIT_MS = 1000;

export const CANCEL = -1;

export class RepaintPlan {
    constructor() {
        // Whether the bar is drawn: nothing happens otherwise.
        this.on = false;
        // Forced frames still to paint, and when to give up on them.
        this._frames = 0;
        this._frameBy = null;
        // After this repaint, another once things settle.
        this._again = false;
        // The settle repaint: when the content has been still long
        // enough, and the latest it may wait for that.
        this._settleAt = null;
        this._settleBy = null;
        // When the one timer fires; null if none is armed.
        this._armedAt = null;
    }

    // The bar is drawn on the ScreenPad's monitor, or not. Drawing it
    // repaints: the bar is drawn again after unlock (GNOME disables the
    // extension on the lock screen) and after the ScreenPad comes back.
    setOn(on, now) {
        if (on === this.on) return this._step(false, now);
        if (!on) {
            const armed = this._armedAt !== null;
            Object.assign(this, new RepaintPlan());
            return {redraw: false, timer: armed ? CANCEL : null};
        }
        this.on = true;
        return this.request(now);
    }

    // A display event (resume, monitors changed): repaint now, and again
    // once things settle.
    request(now) {
        if (!this.on) return this._step(false, now);
        this._start(now);
        this._again = true;
        return this._step(true, now);
    }

    // The ScreenPad monitor's view was painted.
    painted(now) {
        if (!this.on) return this._step(false, now);
        if (this._frames > 0) {
            // A forced frame (whatever else changed in it).
            this._frames -= 1;
            if (this._frames > 0) {
                this._frameBy = now + FRAME_WAIT_MS;
                return this._step(true, now);
            }
            this._frameBy = null;
            if (this._again) {
                this._again = false;
                this._settle(now);
            }
            return this._step(false, now);
        }
        // The content changed.
        this._settle(now);
        return this._step(false, now);
    }

    // The timer fired.
    wake(now) {
        this._armedAt = null;
        if (!this.on) return this._step(false, now);
        if (this._frames > 0) {
            if (now < this._frameBy) return this._step(false, now);
            // No frame came: give up on this repaint.
            this._frames = 0;
            this._frameBy = null;
            this._again = false;
        }
        const settled = this._settleAt !== null && (now >= this._settleAt || now >= this._settleBy);
        if (settled) {
            this._start(now);
            return this._step(true, now);
        }
        return this._step(false, now);
    }

    _start(now) {
        this._frames = FULL_FRAMES;
        this._frameBy = now + FRAME_WAIT_MS;
        this._again = false;
        this._settleAt = null;
        this._settleBy = null;
    }

    _settle(now) {
        this._settleAt = now + SETTLE_MS;
        if (this._settleBy === null) this._settleBy = now + MAX_WAIT_MS;
    }

    // When the timer must fire next; null for never.
    _next() {
        if (!this.on) return null;
        if (this._frames > 0) return this._frameBy;
        if (this._settleAt === null) return null;
        return Math.min(this._settleAt, this._settleBy);
    }

    _step(redraw, now) {
        const next = this._next();
        if (next === null) {
            const armed = this._armedAt !== null;
            this._armedAt = null;
            return {redraw, timer: armed ? CANCEL : null};
        }
        if (this._armedAt !== null && this._armedAt <= next) return {redraw, timer: null};
        this._armedAt = next;
        return {redraw, timer: Math.max(0, Math.ceil(next - now))};
    }
}
