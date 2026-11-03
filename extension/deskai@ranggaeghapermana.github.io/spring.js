// Springs, the way Liquid Glass moves: things overshoot a little and settle instead of stopping dead.
//
// ease() still runs the clock (so GNOME's "animations off" and slow-down setting still apply); a spring
// only shapes the curve. response: seconds for one swing; damping: 1 settles without overshooting.
// Clutter can't take a curve from JavaScript (what the function returns never reaches it), so each
// spring rides the cubic Bézier closest to it, which overshoots the same way.

import Clutter from 'gi://Clutter';
import Graphene from 'gi://Graphene';
import St from 'gi://St';

function curve(response, damping) {
    const w = 2 * Math.PI / response;
    if (damping >= 1)
        return t => 1 - Math.exp(-w * t) * (1 + w * t);
    const wd = w * Math.sqrt(1 - damping * damping);
    return t => 1 - Math.exp(-damping * w * t) * (Math.cos(wd * t) + (damping * w / wd) * Math.sin(wd * t));
}

// bezier: the control points that follow the spring most closely over its settling time (within 4%).
function spring(response, damping, bezier) {
    const at = curve(response, damping);
    let settled = 0;
    for (let t = 0; t < 4; t += 0.001) {
        if (Math.abs(at(t) - 1) > 0.002)
            settled = t;
    }
    const [x1, y1, x2, y2] = bezier;
    return {
        at,
        duration: Math.round((settled + 0.02) * 1000),
        response,
        damping,
        points: [new Graphene.Point({x: x1, y: y1}), new Graphene.Point({x: x2, y: y2})],
    };
}

export const SPRINGS = {
    bouncy: spring(0.5, 0.68, [0.333, 0.798, 0, 1.188]), // arriving: cards, menus, the command bar
    snappy: spring(0.34, 0.82, [0.287, 0.426, 0.045, 1.154]), // small and quick: a button let go
    // Never past the target: what changes the dock's width uses it, and the centered dock would shake.
    smooth: spring(0.5, 1, [0.283, 0.422, 0.05, 1]),
};

// actor.ease(), on a spring. props: what to animate (scale_x, width, …); extra: ease()'s other
// params (onComplete, delay, …).
export function springEase(actor, props, kind = SPRINGS.bouncy, extra = {}) {
    actor.ease({...props, ...extra, duration: kind.duration, mode: Clutter.AnimationMode.LINEAR});
    for (const name of Object.keys(props))
        actor.get_transition(name.replace(/_/g, '-'))?.set_cubic_bezier_progress(...kind.points);
}

export function animationsOn() {
    return St.Settings.get().enable_animations;
}

// Moves a value toward a target one frame at a time, like a weight on a spring; for things that chase
// a target that keeps moving (the selection pill follows a mode button while it grows).
export class Follower {
    // epsilon: how close counts as there, in the value's own units (pixels, or 0.01 of a 0–1 amount).
    constructor(kind, epsilon = 0.3) {
        this.setKind(kind);
        this.value = null;
        this._speed = 0;
        this._epsilon = epsilon;
    }

    setKind(kind) {
        const w = 2 * Math.PI / kind.response;
        this._stiffness = w * w;
        this._friction = 2 * kind.damping * w;
    }

    // Returns true while still moving.
    step(target, seconds) {
        if (this.value === null || !animationsOn()) {
            this.value = target;
            this._speed = 0;
            return false;
        }
        for (let left = Math.min(seconds, 0.05); left > 0; left -= 0.008) {
            const dt = Math.min(left, 0.008);
            this._speed += (this._stiffness * (target - this.value) - this._friction * this._speed) * dt;
            this.value += this._speed * dt;
        }
        if (Math.abs(target - this.value) < this._epsilon && Math.abs(this._speed) < this._epsilon * 10) {
            this.value = target;
            this._speed = 0;
            return false;
        }
        return true;
    }

    get speed() {
        return this._speed;
    }

    snap(target) {
        this.value = target;
        this._speed = 0;
    }
}
