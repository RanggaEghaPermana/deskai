// Plays the login animation once the desktop is ready.
//
// GNOME starts its 500 ms grow-in at the moment it starts loading extensions, both on the
// compositor's thread, so most of the animation's frames never get drawn. The session-modes
// in metadata.json make GNOME load this extension before every other one. It lets the shell
// finish starting with the desktop hidden, waits for the other extensions, then plays the
// animation with nothing else in the way.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {STARTUP_ANIMATION_TIME} from 'resource:///org/gnome/shell/ui/layout.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

// The scale GNOME's own animation starts from.
const START_SCALE = 0.75;
// A dark screen is worse than a stutter: past this, the animation plays anyway.
const WAIT_LIMIT_MS = 2000;
const FIRST_PAINT_LIMIT_MS = 100;
// Later than this, the animation has been on screen and restarting it would flash.
const TOO_LATE_MS = 50;

export default class DeskAILoginExtension extends Extension {
    enable() {
        const layout = Main.layoutManager;
        const running = layout.uiGroup.get_transition('opacity');
        if (!layout._startingUp || layout._startInOverview || Main.sessionMode.isGreeter ||
            running?.get_elapsed_time() > TOO_LATE_MS)
            return null;

        this._timers = new Set();
        this._active = true;
        const {promise, resolve} = Promise.withResolvers();

        layout.connectObject('startup-complete', () => {
            layout.disconnectObject(this);
            delete layout._startupAnimationSession;
            // At full size, so extensions measuring the shell as they load get real positions.
            layout.uiGroup.set_scale(1, 1);
            layout.uiGroup.opacity = 0;
            Main.extensionManager._initializationPromise?.finally(() => this._reveal());
            this._later(WAIT_LIMIT_MS, () => this._reveal());
            resolve();
        }, this);

        // With no animation to wait for, the shell goes straight to `startup-complete`.
        // Returning the promise holds the other extensions back until it has, so they
        // set themselves up now instead of waiting for it and popping in afterwards.
        layout._startupAnimationSession = async () => {};
        layout.uiGroup.remove_all_transitions();
        this._later(WAIT_LIMIT_MS, resolve);
        return promise;
    }

    disable() {
        if (!this._active)
            return;
        const layout = Main.layoutManager;
        layout.disconnectObject(this);
        delete layout._startupAnimationSession;
        this._cancelPaintWait?.();
        this._settle();
    }

    _reveal() {
        if (this._revealing || !this._active)
            return;
        this._revealing = true;
        const ui = Main.layoutManager.uiGroup;
        ui.set_scale(START_SCALE, START_SCALE);
        // Too faint to see, but it draws everything once: the slow first paint doesn't
        // land on the animation's first frame.
        ui.opacity = 1;

        const paintId = global.stage.connect('after-paint', () => this._play());
        this._cancelPaintWait = () => {
            global.stage.disconnect(paintId);
            this._cancelPaintWait = null;
        };
        this._later(FIRST_PAINT_LIMIT_MS, () => this._play());
    }

    _play() {
        if (!this._cancelPaintWait)
            return;
        this._cancelPaintWait();
        Main.layoutManager.uiGroup.ease({
            scale_x: 1,
            scale_y: 1,
            opacity: 255,
            duration: STARTUP_ANIMATION_TIME,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onStopped: () => this._settle(),
        });
    }

    _settle() {
        if (!this._active)
            return;
        this._active = false;
        this._revealing = false;
        for (const id of this._timers)
            GLib.source_remove(id);
        this._timers.clear();
        const ui = Main.layoutManager.uiGroup;
        ui.remove_all_transitions();
        ui.set_scale(1, 1);
        ui.opacity = 255;
    }

    _later(ms, fn) {
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._timers.delete(id);
            fn();
            return GLib.SOURCE_REMOVE;
        });
        this._timers.add(id);
    }
}
