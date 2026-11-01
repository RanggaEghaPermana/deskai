// One dock at a time. The daemon turns off any other dock it finds on (daemon/docks.py), and this stops the one
// case it couldn't put right afterwards. Turning on an extension GNOME hasn't loaded yet from the Extensions app changes
// two settings at once, and on Ubuntu both of them turn its dock on, so GNOME can start that dock twice over. Turning
// it off then restarts it, for good: it stays on the screen on top of this dock until the next login.

import {InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

// The same as OTHERS in daemon/docks.py, and a test keeps them the same.
const OTHER_DOCKS = new Set([
    'ubuntu-dock@ubuntu.com',
    'dash-to-dock@micxgx.gmail.com',
    'dash-to-panel@jderose9.github.com',
    'dash2dock-lite@icedman.github.com',
    'cosmic-dock@system76.com',
    'aztaskbar@aztaskbar.gitlab.com',
    'floatingDock@sun.wxg@gmail.com',
    'dock-from-dash@fthx',
    'docka@devster3000',
    'simple-taskbar@sultech',
    'rocketbar@chepkun.github.com',
    'window-list@gnome-shell-extensions.gcampax.github.com',
]);

export class OneDock {
    // refused(name) says so, since the switch someone just flipped goes back by itself.
    constructor(refused) {
        const manager = Main.extensionManager;
        this._injections = new InjectionManager();
        this._injections.overrideMethod(manager, 'enableExtension', original => function (uuid) {
            if (!OTHER_DOCKS.has(uuid))
                return original.call(this, uuid);
            const extension = manager.lookup(uuid);
            if (extension) {
                manager.emit('extension-state-changed', extension); // tells the Extensions app it's still off
                refused(extension.metadata?.name ?? uuid);
            }
            return false;
        });
    }

    destroy() {
        this._injections.clear();
    }
}
