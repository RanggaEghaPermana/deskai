// The desk as windows: where each one sits, for the brain's snapshots, and putting
// them back after a logout (size, place, maximized, workspace, stacking).

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const {HORIZONTAL, VERTICAL} = Meta.MaximizeFlags;
const BOTH = HORIZONTAL | VERTICAL;
// Apps size a new window themselves right after it maps; placing it again a moment later wins.
const SETTLE_MS = [250, 900];
// A window that hasn't shown up on screen by then isn't coming.
const SHOWN_TIMEOUT_MS = 15000;

function maximizeFlags(win) {
    return (win.maximized_horizontally ? HORIZONTAL : 0) | (win.maximized_vertically ? VERTICAL : 0);
}

function onScreen([x, y, w, h]) {
    const display = global.display;
    for (let i = 0; i < display.get_n_monitors(); i++) {
        const m = display.get_monitor_geometry(i);
        if (x < m.x + m.width && x + w > m.x && y < m.y + m.height && y + h > m.y)
            return true;
    }
    return false;
}

export class Desk {
    // pressKeys(keyvals): types a shortcut into the focused window.
    constructor(isAppWindow, pressKeys) {
        this._isAppWindow = isAppWindow;
        this._pressKeys = pressKeys;
        this._timers = new Set();
        this._waiting = new Map(); // window → its 'shown' handler, for windows not on screen yet
    }

    destroy() {
        for (const timer of this._timers)
            GLib.source_remove(timer);
        this._timers.clear();
        for (const [win, handler] of this._waiting)
            win.disconnect(handler);
        this._waiting.clear();
    }

    _later(ms, fn) {
        const timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._timers.delete(timer);
            fn();
            return GLib.SOURCE_REMOVE;
        });
        this._timers.add(timer);
    }

    _windows() {
        return global.display.list_all_windows().filter(this._isAppWindow);
    }

    _find(id) {
        return this._windows().find(win => win.get_stable_sequence() === id) ?? null;
    }

    // Every app window, with an id that stays the same while it's open.
    list() {
        const windows = this._windows();
        const stacked = global.display.sort_windows_by_stacking(windows);
        const tracker = Shell.WindowTracker.get_default();
        return windows.map(win => {
            const rect = win.get_frame_rect();
            return {
                id: win.get_stable_sequence(),
                app: tracker.get_window_app(win)?.get_id() ?? '',
                title: win.get_title() ?? '',
                pid: win.get_pid(),
                workspace: win.get_workspace()?.index() ?? -1,
                sticky: win.on_all_workspaces,
                monitor: win.get_monitor(),
                rect: [rect.x, rect.y, rect.width, rect.height],
                max_h: win.maximized_horizontally,
                max_v: win.maximized_vertically,
                maximized: win.maximized_horizontally && win.maximized_vertically,
                fullscreen: win.is_fullscreen(),
                minimized: win.minimized,
                above: win.is_above(),
                stack: stacked.indexOf(win), // 0 is the bottom
                focused: win === global.display.focus_window,
            };
        });
    }

    // Puts a window back the way a snapshot saw it. A window that was just opened is
    // listed before the app has drawn it (0×0): it's placed once it shows up.
    place(id, spec) {
        const win = this._find(id);
        if (!win)
            return false;
        const rect = win.get_frame_rect();
        if (rect.width > 0 && rect.height > 0) {
            this._settle(win, spec);
            return true;
        }
        if (this._waiting.has(win))
            win.disconnect(this._waiting.get(win));
        const stopWaiting = () => {
            win.disconnect(this._waiting.get(win));
            this._waiting.delete(win);
        };
        this._waiting.set(win, win.connect('shown', () => {
            stopWaiting();
            this._settle(win, spec);
        }));
        this._later(SHOWN_TIMEOUT_MS, () => {
            if (this._waiting.has(win))
                stopWaiting();
        });
        return true;
    }

    _settle(win, spec) {
        this._apply(win, spec);
        for (const ms of SETTLE_MS) {
            this._later(ms, () => {
                if (this._windows().includes(win))
                    this._apply(win, spec);
            });
        }
    }

    _apply(win, spec) {
        const workspaces = global.workspace_manager;
        if (spec.sticky) {
            win.stick();
        } else if (spec.workspace >= 0) {
            while (workspaces.get_n_workspaces() <= spec.workspace)
                workspaces.append_new_workspace(false, global.get_current_time());
            if (win.get_workspace()?.index() !== spec.workspace)
                win.change_workspace_by_index(spec.workspace, false);
        }
        if (spec.fullscreen) {
            if (!win.is_fullscreen())
                win.make_fullscreen();
            return;
        }
        if (win.is_fullscreen())
            win.unmake_fullscreen();
        const wanted = (spec.max_h ? HORIZONTAL : 0) | (spec.max_v ? VERTICAL : 0);
        const current = maximizeFlags(win);
        if (wanted === BOTH) {
            if (current !== BOTH) {
                if (spec.monitor >= 0 && spec.monitor < global.display.get_n_monitors())
                    win.move_to_monitor(spec.monitor);
                win.maximize();
            }
        } else {
            // Half-maximized is GNOME's tiling to one side: the rect, then the one direction.
            if (current)
                win.set_unmaximize_flags(current);
            if (spec.rect?.length === 4 && onScreen(spec.rect))
                win.move_resize_frame(true, ...spec.rect);
            if (wanted)
                win.set_maximize_flags(wanted);
        }
        if (spec.above && !win.is_above())
            win.make_above();
        if (spec.minimized && !win.minimized)
            win.minimize();
    }

    // Brings a terminal window's tab to the front with its Alt+<n> shortcut (index from 0).
    selectTab(id, index) {
        const win = this._find(id);
        if (!win || index < 0 || index > 8)
            return false;
        Main.activateWindow(win);
        this._later(200, () => {
            if (global.display.focus_window === win) // never type into some other window
                this._pressKeys([Clutter.KEY_Alt_L, Clutter.KEY_1 + index]);
        });
        return true;
    }

    // Raises the windows in this order (the last ends on top) and focuses the last one.
    raise(ids) {
        const windows = ids.map(id => this._find(id)).filter(Boolean);
        for (const win of windows)
            win.raise();
        const top = windows.at(-1);
        if (top)
            Main.activateWindow(top);
        return windows.length;
    }
}
