// The command bar: a place above the dock to type what you want. Enter hands it to the brain,
// Escape or a click anywhere else closes it, and Up brings back what you typed before.

import Clutter from 'gi://Clutter';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const WIDTH = 640;
const GAP = 14;
const HISTORY = 30;

export class CommandBar {
    // onSubmit(text); onClose()
    constructor({gicon, onSubmit, onClose}) {
        this._gicon = gicon;
        this._onSubmit = onSubmit;
        this._onClose = onClose;
        this._history = [];
    }

    get isOpen() {
        return Boolean(this._backdrop);
    }

    // place: {hint, monitor, bottomY, buttons: [{label, command}]}: a click on one submits its command
    open({hint, monitor, bottomY, buttons = []}) {
        if (this.isOpen)
            return;
        this._backdrop = new St.Widget({
            reactive: true,
            x: monitor.x,
            y: monitor.y,
            width: monitor.width,
            height: monitor.height,
        });
        this._bar = new St.BoxLayout({style_class: 'deskai-command', reactive: true});
        this._bar.add_child(new St.Icon({gicon: this._gicon, style_class: 'deskai-command-icon', y_align: Clutter.ActorAlign.CENTER}));
        this._entry = new St.Entry({
            style_class: 'deskai-command-entry',
            hint_text: hint,
            can_focus: true,
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._bar.add_child(this._entry);
        for (const {label, command} of buttons) {
            const button = new St.Button({
                style_class: 'deskai-command-chip',
                label,
                can_focus: false,
                y_align: Clutter.ActorAlign.CENTER,
            });
            button.connect('clicked', () => {
                this.close();
                this._onSubmit(command);
            });
            this._bar.add_child(button);
        }
        this._backdrop.add_child(this._bar);
        Main.uiGroup.add_child(this._backdrop);

        const width = Math.min(WIDTH, monitor.width - 32);
        this._bar.width = width;
        const [, height] = this._bar.get_preferred_height(width);
        this._bar.set_position(Math.round((monitor.width - width) / 2), Math.round(bottomY - monitor.y - height - GAP));

        this._grab = Main.pushModal(this._backdrop, {actionMode: Shell.ActionMode.SYSTEM_MODAL});
        this._backdrop.connect('button-press-event', (_actor, event) => {
            if (global.stage.get_event_actor(event) === this._backdrop)
                this.close();
            return Clutter.EVENT_PROPAGATE;
        });
        const text = this._entry.clutter_text;
        text.connect('activate', () => this._submit());
        text.connect('key-press-event', (_actor, event) => this._onKey(event.get_key_symbol()));
        global.stage.set_key_focus(text);
        this._recalled = this._history.length;

        this._bar.opacity = 0;
        this._bar.translation_y = 8;
        this._bar.ease({opacity: 255, translation_y: 0, duration: 140, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    close() {
        if (!this.isOpen)
            return;
        if (this._grab)
            Main.popModal(this._grab);
        this._grab = null;
        this._backdrop.destroy();
        this._backdrop = null;
        this._bar = null;
        this._entry = null;
        this._onClose();
    }

    destroy() {
        this.close();
    }

    _onKey(key) {
        if (key === Clutter.KEY_Escape) {
            this.close();
            return Clutter.EVENT_STOP;
        }
        if (key === Clutter.KEY_Up || key === Clutter.KEY_Down) {
            this._recall(key === Clutter.KEY_Up ? -1 : 1);
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }

    _recall(delta) {
        const index = this._recalled + delta;
        if (index < 0 || index > this._history.length)
            return;
        this._recalled = index;
        this._entry.text = this._history[index] ?? '';
        this._entry.clutter_text.set_cursor_position(-1);
    }

    _submit() {
        const text = this._entry.text.trim();
        if (!text)
            return;
        this._history = [...this._history.filter(old => old !== text), text].slice(-HISTORY);
        this.close();
        this._onSubmit(text);
    }
}
