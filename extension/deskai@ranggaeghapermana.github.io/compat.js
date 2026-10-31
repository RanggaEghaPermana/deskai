// What differs between the GNOME versions DeskAI runs on.

import Clutter from 'gi://Clutter';

import * as Config from 'resource:///org/gnome/shell/misc/config.js';

export const SHELL_MAJOR = parseInt(Config.PACKAGE_VERSION);

// GNOME 48 gave boxes an orientation. Before that they only know `vertical`.
export const VERTICAL_BOX = SHELL_MAJOR >= 48 ? {orientation: Clutter.Orientation.VERTICAL} : {vertical: true};

// GNOME 47 moved the backend to the stage's context, and 51 dropped the old way.
export function defaultSeat() {
    const backend = SHELL_MAJOR >= 47 ? global.stage.get_context().get_backend() : Clutter.get_default_backend();
    return backend.get_default_seat();
}

// GNOME 49 split maximizing a window from maximizing it in one direction.
export function maximize(win, flags) {
    if (SHELL_MAJOR < 49)
        win.maximize(flags);
    else
        win.set_maximize_flags(flags);
}

export function unmaximize(win, flags) {
    if (SHELL_MAJOR < 49)
        win.unmaximize(flags);
    else
        win.set_unmaximize_flags(flags);
}
