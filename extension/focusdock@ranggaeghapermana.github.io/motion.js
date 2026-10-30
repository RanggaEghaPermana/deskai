// Motion: one animation language for the whole shell, shared with the dock.
//
// Things arrive with a soft spring, leave quickly, and changes of size or
// place glide. Windows opening, closing, minimizing and coming to the front
// are animated here from scratch; menus, dialogs, banners, the switcher, OSDs,
// resizes and workspace switches keep GNOME's logic and callbacks but get
// retimed onto these curves.

import Clutter from 'gi://Clutter';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as BoxPointer from 'resource:///org/gnome/shell/ui/boxpointer.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';
import * as OsdWindow from 'resource:///org/gnome/shell/ui/osdWindow.js';
import * as SwitcherPopup from 'resource:///org/gnome/shell/ui/switcherPopup.js';
import {adjustAnimationTime} from 'resource:///org/gnome/shell/misc/animationUtils.js';

const {AnimationMode} = Clutter;

// The dock's curves, named by what they're for.
export const MOTION = {
    arrive: {duration: 420, mode: AnimationMode.EASE_OUT_BACK}, // dock rising, windows opening
    fadeIn: {duration: 200, mode: AnimationMode.EASE_OUT_QUAD},
    leave: {duration: 260, mode: AnimationMode.EASE_IN_CUBIC}, // dock hiding, windows closing
    fadeOut: {duration: 200, mode: AnimationMode.EASE_IN_QUAD},
    pop: {duration: 300, mode: AnimationMode.EASE_OUT_BACK}, // cards, menus, dialogs, OSDs
    popOut: {duration: 160, mode: AnimationMode.EASE_IN_QUAD},
    glide: {duration: 380, mode: AnimationMode.EASE_OUT_EXPO}, // resizes, tiling, workspaces
    absorb: {duration: 480}, // minimizing into the dock (per-axis curves below)
};

// How windows open, close, (un)minimize and come to the front.
// "smooth" (default): windows rise in with the dock's spring and close in place;
//   minimizing runs straight into the dock icon, shrinking to the icon's size
//   and staying visible until it lands.
// "calm": everything in place, only a fade and a hint of scale; minimizing
//   contracts around the icon.
// "lively": windows rise and sink, and minimizing travels to the icon on a curve.
// raise: a window brought to the front starts at `lift` scale; the windows that
//   covered it fade away on top of it, shrinking to `recede`.
const WINDOW_STYLES = {
    smooth: {
        open: {scale: 0.92, rise: 26, pivotY: 0.7, fade: MOTION.fadeIn, move: MOTION.arrive},
        openDialog: {scale: 0.88, rise: 0, pivotY: 0.5, fade: MOTION.fadeIn, move: MOTION.pop},
        close: {scale: 0.97, drop: 0, fade: {duration: 160, mode: AnimationMode.EASE_IN_QUAD}, move: {duration: 180, mode: AnimationMode.EASE_IN_QUAD}},
        closeDialog: {scale: 0.97, drop: 0, fade: {duration: 140, mode: AnimationMode.EASE_IN_QUAD}, move: {duration: 140, mode: AnimationMode.EASE_IN_QUAD}},
        minimize: 'straight',
        minimizeDuration: MOTION.absorb.duration,
        raise: {lift: 0.985, recede: 0.98, fade: {duration: 220, mode: AnimationMode.EASE_OUT_QUAD}, move: {duration: 280, mode: AnimationMode.EASE_OUT_CUBIC}},
    },
    calm: {
        open: {scale: 0.97, rise: 0, pivotY: 0.5, fade: {duration: 180, mode: AnimationMode.EASE_OUT_QUAD}, move: {duration: 240, mode: AnimationMode.EASE_OUT_CUBIC}},
        openDialog: {scale: 0.95, rise: 0, pivotY: 0.5, fade: {duration: 160, mode: AnimationMode.EASE_OUT_QUAD}, move: {duration: 200, mode: AnimationMode.EASE_OUT_CUBIC}},
        close: {scale: 0.97, drop: 0, fade: {duration: 160, mode: AnimationMode.EASE_IN_QUAD}, move: {duration: 180, mode: AnimationMode.EASE_IN_QUAD}},
        closeDialog: {scale: 0.97, drop: 0, fade: {duration: 140, mode: AnimationMode.EASE_IN_QUAD}, move: {duration: 140, mode: AnimationMode.EASE_IN_QUAD}},
        minimize: 'scale',
        minimizeDuration: 300,
        raise: {lift: 1, recede: 1, fade: {duration: 180, mode: AnimationMode.EASE_OUT_QUAD}, move: {duration: 200, mode: AnimationMode.EASE_OUT_CUBIC}},
    },
    lively: {
        open: {scale: 0.92, rise: 26, pivotY: 0.7, fade: MOTION.fadeIn, move: MOTION.arrive},
        openDialog: {scale: 0.88, rise: 0, pivotY: 0.5, fade: MOTION.fadeIn, move: MOTION.pop},
        close: {scale: 0.94, drop: 14, fade: MOTION.fadeOut, move: MOTION.leave},
        closeDialog: {scale: 0.96, drop: 0, fade: MOTION.popOut, move: MOTION.popOut},
        minimize: 'curve',
        minimizeDuration: MOTION.absorb.duration,
        raise: {lift: 0.96, recede: 0.95, fade: {duration: 240, mode: AnimationMode.EASE_OUT_QUAD}, move: MOTION.pop},
    },
};

// Windows that take part in coming to the front: no desktop icons, menus or tooltips.
const STACKED_TYPES = [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG, Meta.WindowType.UTILITY];

function shownOn(win, workspace) {
    return STACKED_TYPES.includes(win.get_window_type()) && !win.customJS_ding && !win.minimized &&
        (win.is_on_all_workspaces() || win.get_workspace() === workspace) &&
        Boolean(win.get_compositor_private()?.visible);
}

function overlaps(a, b) {
    const [r, q] = [a.get_frame_rect(), b.get_frame_rect()];
    return r.x < q.x + q.width && q.x < r.x + r.width && r.y < q.y + q.height && q.y < r.y + r.height;
}

// A dialog and its parent move together; neither is "covering" the other.
function related(a, b) {
    return a.find_root_ancestor() === b.find_root_ancestor();
}

// GNOME's window handlers we take over entirely, and the call that ends each.
const TAKEOVER = /\b_(mapWindow|destroyWindow|minimizeWindow|unminimizeWindow)@/;
const COMPLETIONS = {
    mapWindow: 'completed_map',
    destroyWindow: 'completed_destroy',
    minimizeWindow: 'completed_minimize',
    unminimizeWindow: 'completed_unminimize',
};

// Changes the curve of transitions GNOME just started, leaving their callbacks alone.
function retime(actor, properties, {duration, mode}) {
    for (const name of properties) {
        const transition = actor?.get_transition(name);
        if (!transition)
            continue;
        transition.set_duration(adjustAnimationTime(duration));
        transition.set_progress_mode(mode);
    }
}

class Patches {
    constructor() {
        this._undo = [];
    }

    method(object, name, wrap) {
        const original = object[name];
        const own = Object.prototype.hasOwnProperty.call(object, name);
        object[name] = wrap(original);
        this._undo.push(() => {
            if (own)
                object[name] = original;
            else
                delete object[name];
        });
    }

    restore() {
        for (const undo of this._undo.reverse())
            undo();
        this._undo = [];
    }
}

export class Motion {
    // Style changes apply to the next animation; no need to re-patch.
    setStyle(name) {
        this._style = WINDOW_STYLES[name] ?? WINDOW_STYLES.smooth;
    }

    enable() {
        this._style ??= WINDOW_STYLES.smooth;
        if (this._patches)
            return;
        this._patches = new Patches();
        this._claimed = new Map(); // actor → which GNOME handler handed it to us
        this._running = new Map(); // actor → finish(), run once when our animation ends
        this._takeOverWindows();
        this._watchStacking();
        this._retimeShell();
    }

    disable() {
        if (!this._patches)
            return;
        global.window_manager.disconnectObject(this);
        global.display.disconnectObject(this);
        global.workspace_manager.disconnectObject(this);
        Main.overview.disconnectObject(this);
        this._settleRaises();
        this._stack = null;
        // Never leave a window half-animated or waiting on a completion.
        for (const actor of [...this._running.keys()])
            actor.remove_all_transitions();
        for (const finish of [...this._running.values()])
            finish();
        this._running.clear();
        this._claimed.clear();
        this._patches.restore();
        this._patches = null;
    }

    // ---- windows --------------------------------------------------------

    _takeOverWindows() {
        const motion = this;
        const shellwm = Main.wm._shellwm;

        // When GNOME is about to animate a map, destroy or (un)minimize, say no and
        // remember the actor: GNOME then calls the completion right away, and
        // the completion below runs our animation instead.
        this._patches.method(Main.wm, '_shouldAnimateActor', original => function (actor, types) {
            if (!original.call(this, actor, types))
                return false;
            const match = new Error().stack.match(TAKEOVER);
            if (!match)
                return true; // e.g. size changes: GNOME animates, _retimeShell adjusts
            motion._claimed.set(actor, match[1]);
            return false;
        });

        for (const [kind, completion] of Object.entries(COMPLETIONS)) {
            this._patches.method(shellwm, completion, original => actor => {
                const done = () => original.call(shellwm, actor);
                if (this._claimed.get(actor) !== kind) {
                    done();
                    return;
                }
                this._claimed.delete(actor);
                try {
                    this._animateWindow(kind, actor, done);
                } catch (e) {
                    logError(e, 'FocusDock motion');
                    this._running.delete(actor);
                    this._reset(actor);
                    done();
                }
            });
        }

        global.window_manager.connectObject(
            // Mutter wants this window settled now (e.g. it moved workspace).
            'kill-window-effects', (_wm, actor) => {
                if (this._running.has(actor))
                    actor.remove_all_transitions();
            },
            this);
    }

    _animateWindow(kind, actor, done) {
        switch (kind) {
        case 'mapWindow':
            done(); // the window is mapped now; the motion is only visual
            this._open(actor);
            break;
        case 'destroyWindow':
            this._close(actor, done);
            break;
        case 'minimizeWindow': {
            const minimize = {straight: this._minimizeStraight, scale: this._minimizeScale}[this._style.minimize] ??
                this._minimize;
            minimize.call(this, actor, done);
            break;
        }
        case 'unminimizeWindow': {
            done();
            const unminimize = {straight: this._unminimizeStraight, scale: this._unminimizeScale}[this._style.minimize] ??
                this._unminimize;
            unminimize.call(this, actor);
            break;
        }
        }
    }

    _isDialog(actor) {
        const type = actor.meta_window?.get_window_type();
        return type === Meta.WindowType.DIALOG || type === Meta.WindowType.MODAL_DIALOG;
    }

    // Runs `finish` once, whichever way the animation ends.
    _track(actor, finish) {
        this._running.set(actor, () => {
            this._running.delete(actor);
            finish();
        });
    }

    _finish(actor) {
        this._running.get(actor)?.();
    }

    _reset(actor) {
        actor.set_pivot_point(0, 0);
        actor.set_scale(1, 1);
        actor.set_translation(0, 0, 0);
        actor.opacity = 255;
    }

    // Opening: the window comes up in place (calm) or rises with the dock's spring (lively).
    _open(actor) {
        const style = this._isDialog(actor) ? this._style.openDialog : this._style.open;
        actor.remove_all_transitions();
        actor.set_pivot_point(0.5, style.pivotY);
        actor.set_scale(style.scale, style.scale);
        actor.translation_y = style.rise;
        actor.opacity = 0;
        this._track(actor, () => this._reset(actor));
        actor.ease({opacity: 255, ...style.fade});
        actor.ease({
            scale_x: 1,
            scale_y: 1,
            translation_y: 0,
            ...style.move,
            onStopped: () => this._finish(actor),
        });
    }

    // Closing: quick and light; lively also lets it sink a little, like the dock hiding.
    _close(actor, done) {
        const style = this._isDialog(actor) ? this._style.closeDialog : this._style.close;
        actor.remove_all_transitions();
        actor.set_pivot_point(0.5, 0.5);
        this._track(actor, done);
        actor.ease({opacity: 0, ...style.fade});
        actor.ease({
            scale_x: style.scale,
            scale_y: style.scale,
            translation_y: style.drop,
            ...style.move,
            onStopped: () => this._finish(actor),
        });
    }

    // The point a window shrinks around to land on its dock icon, as a pivot
    // (fractions of the window, possibly outside 0..1) plus the final scale.
    _iconPivot(actor) {
        const window = actor.meta_window;
        const [width, height] = [actor.width, actor.height];
        if (width <= 0 || height <= 0)
            return null;
        const [ok, icon] = window.get_icon_geometry();
        let cx, cy, scale;
        if (ok && icon.width > 0 && icon.height > 0) {
            cx = icon.x + icon.width / 2;
            cy = icon.y + icon.height / 2;
            scale = Math.min(icon.width / width, icon.height / height);
        } else {
            const monitor = Main.layoutManager.monitors[window.get_monitor()];
            if (!monitor)
                return null;
            cx = monitor.x + monitor.width / 2;
            cy = monitor.y + monitor.height;
            scale = 0.08;
        }
        return {x: (cx - actor.x) / width, y: (cy - actor.y) / height, scale};
    }

    // Smooth minimize: position and size share one easing, so the window runs in a
    // straight line into the icon, ends at the icon's size, and only fades once it's there.
    _minimizeStraight(actor, done) {
        const target = this._iconTarget(actor);
        if (!target) {
            done();
            return;
        }
        const duration = this._style.minimizeDuration;
        actor.remove_all_transitions();
        actor.set_pivot_point(0, 0);
        this._track(actor, () => {
            this._reset(actor);
            done();
        });
        actor.ease({
            translation_x: target.x,
            translation_y: target.y,
            scale_x: target.scale,
            scale_y: target.scale,
            duration,
            mode: AnimationMode.EASE_IN_OUT_CUBIC,
        });
        actor.ease({
            opacity: 0,
            duration,
            mode: AnimationMode.EASE_IN_EXPO,
            onStopped: () => this._finish(actor),
        });
    }

    // Smooth unminimize: appears at the icon's size and grows straight back into place.
    _unminimizeStraight(actor) {
        const target = this._iconTarget(actor);
        if (!target)
            return;
        const duration = this._style.minimizeDuration;
        actor.remove_all_transitions();
        actor.set_pivot_point(0, 0);
        actor.set_translation(target.x, target.y, 0);
        actor.set_scale(target.scale, target.scale);
        actor.opacity = 0;
        this._track(actor, () => this._reset(actor));
        actor.ease({opacity: 255, duration: 160, mode: AnimationMode.EASE_OUT_QUAD});
        actor.ease({
            translation_x: 0,
            translation_y: 0,
            scale_x: 1,
            scale_y: 1,
            duration,
            mode: AnimationMode.EASE_OUT_QUINT,
            onStopped: () => this._finish(actor),
        });
    }

    // Calm minimize: the window contracts toward its icon. Because the icon is
    // the pivot, it shrinks into place instead of travelling across the screen.
    _minimizeScale(actor, done) {
        const target = this._iconPivot(actor);
        if (!target) {
            done();
            return;
        }
        const duration = this._style.minimizeDuration;
        actor.remove_all_transitions();
        actor.set_pivot_point(target.x, target.y);
        this._track(actor, () => {
            this._reset(actor);
            done();
        });
        actor.ease({opacity: 0, duration, mode: AnimationMode.EASE_IN_CUBIC});
        actor.ease({
            scale_x: target.scale,
            scale_y: target.scale,
            duration,
            mode: AnimationMode.EASE_IN_OUT_CUBIC,
            onStopped: () => this._finish(actor),
        });
    }

    _unminimizeScale(actor) {
        const target = this._iconPivot(actor);
        if (!target)
            return;
        const duration = this._style.minimizeDuration + 20;
        actor.remove_all_transitions();
        actor.set_pivot_point(target.x, target.y);
        actor.set_scale(target.scale, target.scale);
        actor.opacity = 0;
        this._track(actor, () => this._reset(actor));
        actor.ease({opacity: 255, duration: 180, mode: AnimationMode.EASE_OUT_QUAD});
        actor.ease({
            scale_x: 1,
            scale_y: 1,
            duration,
            mode: AnimationMode.EASE_OUT_CUBIC,
            onStopped: () => this._finish(actor),
        });
    }

    // Where a window shrinks to: its dock icon, or the bottom middle of its monitor.
    _iconTarget(actor) {
        const window = actor.meta_window;
        const [width, height] = [actor.width, actor.height];
        if (width <= 0 || height <= 0)
            return null;
        const [ok, icon] = window.get_icon_geometry();
        if (ok && icon.width > 0 && icon.height > 0) {
            const scale = Math.min(icon.width / width, icon.height / height);
            return {
                scale,
                x: icon.x + (icon.width - width * scale) / 2 - actor.x,
                y: icon.y + (icon.height - height * scale) / 2 - actor.y,
            };
        }
        const monitor = Main.layoutManager.monitors[window.get_monitor()];
        if (!monitor)
            return null;
        const scale = 0.08;
        return {
            scale,
            x: monitor.x + (monitor.width - width * scale) / 2 - actor.x,
            y: monitor.y + monitor.height - height * scale - actor.y,
        };
    }

    // Lively minimize: drawn into the dock icon along a curve. Sideways and downward
    // motion use different easings, so the path bends instead of running straight.
    _minimize(actor, done) {
        const target = this._iconTarget(actor);
        if (!target) {
            done();
            return;
        }
        const duration = this._style.minimizeDuration;
        actor.remove_all_transitions();
        actor.set_pivot_point(0, 0);
        this._track(actor, () => {
            this._reset(actor);
            done();
        });
        actor.ease({translation_x: target.x, duration, mode: AnimationMode.EASE_IN_OUT_SINE});
        actor.ease({translation_y: target.y, duration, mode: AnimationMode.EASE_IN_CUBIC});
        actor.ease({scale_x: target.scale, scale_y: target.scale, duration, mode: AnimationMode.EASE_IN_OUT_CUBIC});
        actor.ease({
            opacity: 0,
            duration,
            mode: AnimationMode.EASE_IN_EXPO,
            onStopped: () => this._finish(actor),
        });
    }

    // Lively unminimize: the same curve played outward, rising out of the icon.
    _unminimize(actor) {
        const target = this._iconTarget(actor);
        if (!target)
            return;
        const duration = this._style.minimizeDuration;
        actor.remove_all_transitions();
        actor.set_pivot_point(0, 0);
        actor.set_translation(target.x, target.y, 0);
        actor.set_scale(target.scale, target.scale);
        actor.opacity = 0;
        this._track(actor, () => this._reset(actor));
        actor.ease({opacity: 255, duration: 220, mode: AnimationMode.EASE_OUT_QUAD});
        actor.ease({translation_x: 0, duration, mode: AnimationMode.EASE_OUT_SINE});
        actor.ease({translation_y: 0, duration, mode: AnimationMode.EASE_OUT_QUART});
        actor.ease({
            scale_x: 1,
            scale_y: 1,
            duration,
            mode: AnimationMode.EASE_OUT_QUINT,
            onStopped: () => this._finish(actor),
        });
    }

    // ---- coming to the front ----------------------------------------------

    // Raising a window (a click, Alt+Tab, the dock, the inbox) used to swap the
    // picture in one frame. Now the windows that covered it stay for a moment as
    // live copies, clipped to it and stacked right above it, and fade back while
    // it lifts into place. Only the covered part changes, so edges that were
    // already visible never flicker, and windows kept above everything (a call's
    // picture-in-picture, a sticky note) stay above the whole time.
    _watchStacking() {
        this._raises = new Set();
        this._lifting = new Set();
        this._stack = this._stackSnapshot();
        global.display.connectObject('restacked', () => this._onRestacked(), this);
        // Leaving the picture behind: settle instead of fading over the new one.
        global.workspace_manager.connectObject('active-workspace-changed', () => this._settleRaises(), this);
        Main.overview.connectObject('showing', () => this._settleRaises(), this);
    }

    _stackSnapshot() {
        const workspace = global.workspace_manager.get_active_workspace();
        const order = new Map();
        global.display.sort_windows_by_stacking(global.display.list_all_windows())
            .forEach((win, index) => order.set(win, {index, shown: shownOn(win, workspace)}));
        return {workspace, order};
    }

    // Mutter has already put the actors in their new order when it says "restacked".
    _onRestacked() {
        const before = this._stack;
        const after = this._stackSnapshot();
        this._stack = after;
        if (!before || before.workspace !== after.workspace || Main.overview.visible ||
            Main.overview.animationInProgress || !St.Settings.get().enable_animations) {
            this._settleRaises();
            return;
        }

        // A window rose when a window that was above it and overlaps it is now below it.
        const risen = [];
        for (const [win, now] of after.order) {
            const was = before.order.get(win);
            // New, unminimized or moved-here windows have their own animations; a fullscreen
            // window may be scanned out directly, past anything drawn over it.
            if (!was?.shown || !now.shown || win.is_fullscreen())
                continue;
            const covers = [];
            for (const [other, otherNow] of after.order) {
                const otherWas = before.order.get(other);
                if (other !== win && otherWas?.shown && otherNow.shown &&
                    otherWas.index > was.index && otherNow.index < now.index &&
                    overlaps(win, other) && !related(win, other))
                    covers.push(other);
            }
            if (covers.length) {
                covers.sort((a, b) => before.order.get(a).index - before.order.get(b).index);
                risen.push([win, covers]);
            }
        }

        if (!risen.length) {
            this._restackRaises(); // something else moved: keep the copies on their windows
            return;
        }
        this._settleRaises(); // flicking on: finish the last switch first
        for (const [win, covers] of risen)
            this._raise(win, covers);
    }

    _raise(win, covers) {
        const actor = win.get_compositor_private();
        const sources = covers.map(cover => cover.get_compositor_private());
        if (!actor || sources.some(s => !s) || this._running.has(actor) || sources.some(s => this._running.has(s)))
            return; // opening, closing or minimizing: that animation wins
        const style = this._style.raise;

        const group = {actor, clones: new Set(), opacity: actor.opacity};
        const fit = () => {
            // Follow the window if you drag it straight away.
            const rect = win.get_frame_rect();
            for (const clone of group.clones)
                clone.set_clip(rect.x - clone.x, rect.y - clone.y, rect.width, rect.height);
        };
        group.drop = clone => {
            if (!group.clones.delete(clone))
                return;
            clone.destroy();
            if (!group.clones.size)
                this._endRaise(group);
        };

        let above = actor;
        for (const source of sources) {
            const clone = new Clutter.Clone({source, reactive: false});
            clone.set_position(source.x, source.y);
            clone.set_pivot_point(0.5, 0.5);
            global.window_group.insert_child_above(clone, above);
            above = clone;
            group.clones.add(clone);
            source.connectObject('destroy', () => group.drop(clone), clone);
        }
        fit();
        const [first] = group.clones;
        win.connectObject('position-changed', fit, 'size-changed', fit, first);
        actor.connectObject('destroy', () => this._settleRaise(group), first);
        // Mutter doesn't paint the parts of a window hidden under an opaque one, which
        // is everything the copies show. A hair under opaque, it paints them.
        if (actor.opacity === 255)
            actor.opacity = 254;
        this._raises.add(group);

        for (const clone of group.clones) {
            clone.ease({opacity: 0, ...style.fade, onStopped: () => group.drop(clone)});
            if (style.recede !== 1)
                clone.ease({scale_x: style.recede, scale_y: style.recede, ...style.move});
        }

        // A maximized or tiled window shrinking would bare the screen edges behind it.
        if (style.lift !== 1 && !win.maximized_horizontally && !win.maximized_vertically) {
            actor.set_pivot_point(0.5, 0.5);
            actor.set_scale(style.lift, style.lift);
            this._lifting.add(actor);
            this._track(actor, () => {
                this._lifting.delete(actor);
                this._reset(actor);
                if (this._raises.has(group))
                    actor.opacity = 254; // still fading
            });
            actor.ease({scale_x: 1, scale_y: 1, ...style.move, onStopped: () => this._finish(actor)});
        }
    }

    _endRaise(group) {
        if (!this._raises.delete(group))
            return;
        if (group.actor.opacity === 254)
            group.actor.opacity = group.opacity;
    }

    _settleRaise(group) {
        for (const clone of [...group.clones])
            group.drop(clone);
        this._endRaise(group);
    }

    // Ends every raise now: copies gone, windows at full size.
    _settleRaises() {
        for (const group of [...this._raises])
            this._settleRaise(group);
        for (const actor of [...this._lifting])
            actor.remove_all_transitions();
    }

    // Mutter reorders window actors on every restack; put the copies back right above theirs.
    _restackRaises() {
        for (const group of this._raises) {
            let above = group.actor;
            for (const clone of group.clones) {
                global.window_group.set_child_above_sibling(clone, above);
                above = clone;
            }
        }
    }

    // ---- everything else: GNOME's animations on the dock's curves --------

    _retimeShell() {
        const patches = this._patches;

        // Maximize, unmaximize, tiling: GNOME cross-fades a snapshot; make it glide.
        global.window_manager.connectObject('size-changed', (_wm, actor) => {
            const info = actor.__animationInfo;
            if (!info)
                return;
            retime(info.clone, ['x', 'y', 'scale-x', 'scale-y'], MOTION.glide);
            retime(info.clone, ['opacity'], {duration: 260, mode: AnimationMode.EASE_OUT_QUAD});
            retime(actor, ['scale-x', 'scale-y', 'translation-x', 'translation-y'], MOTION.glide);
        }, this);

        // Workspace switches.
        patches.method(Main.wm._workspaceAnimation, 'animateSwitch', original => function (...args) {
            original.apply(this, args);
            for (const group of this._switchData?.monitors ?? [])
                retime(group, ['progress'], MOTION.glide);
        });

        // Every popup menu (top bar, quick settings, right-click menus) grows from its button.
        patches.method(BoxPointer.BoxPointer.prototype, 'open', original => function (...args) {
            original.apply(this, args);
            retime(this, ['opacity'], {duration: 180, mode: AnimationMode.EASE_OUT_QUAD});
            retime(this, ['scale-x', 'scale-y', 'translation-x', 'translation-y'], MOTION.pop);
        });
        patches.method(BoxPointer.BoxPointer.prototype, 'close', original => function (...args) {
            original.apply(this, args);
            retime(this, ['opacity', 'scale-x', 'scale-y', 'translation-x', 'translation-y'], MOTION.popOut);
        });

        // Shell dialogs (power off, our mode dialog, …) pop like the dock's cards.
        patches.method(ModalDialog.ModalDialog.prototype, '_fadeOpen', original => function (...args) {
            original.apply(this, args);
            retime(this, ['opacity'], MOTION.fadeIn);
            const layout = this.dialogLayout;
            if (layout) {
                layout.set_pivot_point(0.5, 0.5);
                layout.set_scale(0.9, 0.9);
                layout.ease({scale_x: 1, scale_y: 1, ...MOTION.pop});
            }
        });
        patches.method(ModalDialog.ModalDialog.prototype, 'close', original => function (...args) {
            const result = original.apply(this, args);
            retime(this, ['opacity'], MOTION.popOut);
            this.dialogLayout?.ease({scale_x: 0.96, scale_y: 0.96, ...MOTION.popOut});
            return result;
        });

        // Notification banners drop in on the dock's spring and leave quickly.
        patches.method(Main.messageTray, '_showNotification', original => function (...args) {
            original.apply(this, args);
            retime(this._bannerBin, ['opacity'], MOTION.fadeIn);
            retime(this._bannerBin, ['y', 'scale-x', 'scale-y'], MOTION.arrive);
        });
        patches.method(Main.messageTray, '_hideNotification', original => function (...args) {
            original.apply(this, args);
            retime(this._bannerBin, ['opacity', 'y'], MOTION.leave);
        });

        // Alt+Tab pops in and out.
        patches.method(SwitcherPopup.SwitcherPopup.prototype, '_showImmediately', original => function (...args) {
            const appearing = this.opacity === 0;
            original.apply(this, args);
            if (!appearing || this.opacity !== 255 || !this._switcherList)
                return;
            this.opacity = 0;
            this.ease({opacity: 255, ...MOTION.fadeIn});
            this._switcherList.set_pivot_point(0.5, 0.5);
            this._switcherList.set_scale(0.92, 0.92);
            this._switcherList.ease({scale_x: 1, scale_y: 1, ...MOTION.pop});
        });
        patches.method(SwitcherPopup.SwitcherPopup.prototype, '_fadeAndDestroy', original => function (...args) {
            original.apply(this, args);
            retime(this, ['opacity'], MOTION.popOut);
            this._switcherList?.ease({scale_x: 0.96, scale_y: 0.96, ...MOTION.popOut});
        });

        // Volume and brightness OSDs.
        patches.method(OsdWindow.OsdWindow.prototype, 'show', original => function (...args) {
            const appearing = !this.visible;
            original.apply(this, args);
            const box = this._hbox;
            if (!box)
                return;
            box.set_pivot_point(0.5, 0.5);
            if (appearing && this.visible) {
                retime(this, ['opacity'], MOTION.fadeIn);
                box.set_scale(0.86, 0.86);
            }
            box.ease({scale_x: 1, scale_y: 1, ...MOTION.pop});
        });
        patches.method(OsdWindow.OsdWindow.prototype, '_hide', original => function (...args) {
            original.apply(this, args);
            retime(this, ['opacity'], MOTION.popOut);
            this._hbox?.ease({scale_x: 0.94, scale_y: 0.94, ...MOTION.popOut});
        });
    }
}
