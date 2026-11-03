// The command bar: a place above the dock to type what you want. Enter hands it to the brain,
// Escape or a click anywhere else closes it. What you asked for before shows above it: typing narrows
// it down, Up and Down go through it, and Delete forgets the one you're on. A click puts one in the
// entry and leaves running it to Enter, since some of them stop things.

import Clutter from 'gi://Clutter';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {VERTICAL_BOX} from './compat.js';
import {SPRINGS, animationsOn, springEase} from './spring.js';

const WIDTH = 640;
const GAP = 14;
const LIST_GAP = 6;
const HISTORY = 30;
const SHOWN = 5;

export class CommandBar {
    // history: what was asked before, newest first; onSubmit(text); onHistory(history); onClose();
    // decorate(bar, list): dresses the two for the current theme each time they're made.
    constructor({gicon, history = [], onSubmit, onHistory, onClose, decorate}) {
        this._gicon = gicon;
        this._history = history;
        this._onSubmit = onSubmit;
        this._onHistory = onHistory;
        this._onClose = onClose;
        this._decorate = decorate;
        this._closing = new Set(); // bars still folding away
    }

    get isOpen() {
        return Boolean(this._backdrop);
    }

    // place: {hint, monitor, bottomY, buttons: [{label, command}], from}: a click on a button submits its
    // command; from: the actor the bar grows out of and folds back into (the dock's button, on glass)
    open({hint, monitor, bottomY, buttons = [], from = null}) {
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
                if (!this.isOpen) // folding away: it's already been answered
                    return;
                this.close();
                this._onSubmit(command);
            });
            this._bar.add_child(button);
        }
        // The list goes in first, under the bar: their glass overlaps, and the list's shadow mustn't
        // darken the bar.
        this._list = new St.BoxLayout({...VERTICAL_BOX, style_class: 'deskai-command-list', reactive: true});
        this._backdrop.add_child(this._list);
        this._backdrop.add_child(this._bar);
        Main.uiGroup.add_child(this._backdrop);
        this._decorate?.(this._bar, this._list);

        const width = Math.min(WIDTH, monitor.width - 32);
        this._bar.width = width;
        this._list.width = width;
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
        text.connect('text-changed', () => this._onTextChanged());
        global.stage.set_key_focus(text);
        this._typed = '';
        this._selected = -1;
        this._showList();

        this._from = from?.get_stage() && animationsOn() ? from : null;
        if (this._from) {
            this._grow(monitor, width, height);
            return;
        }
        for (const actor of [this._bar, this._list]) {
            actor.opacity = 0;
            actor.translation_y = 8;
            actor.ease({opacity: 255, translation_y: 0, duration: 140, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        }
    }

    // The bar starts as the button it was opened from and stretches into place on a spring.
    _grow(monitor, width, height) {
        const [fx, fy] = this._from.get_transformed_position();
        const [fw, fh] = this._from.get_transformed_size();
        const x = monitor.x + this._bar.x;
        const y = monitor.y + this._bar.y;
        this._bar.set_pivot_point((fx + fw / 2 - x) / width, (fy + fh / 2 - y) / height);
        this._folded = [fw / width, fh / height];
        this._bar.set_scale(...this._folded);
        this._bar.opacity = 0;
        this._bar.ease({opacity: 255, duration: 100, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        springEase(this._bar, {scale_x: 1, scale_y: 1}, SPRINGS.bouncy);
        this._list.opacity = 0;
        this._list.translation_y = 8;
        this._list.ease({opacity: 255, translation_y: 0, delay: 120, duration: 180, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    close() {
        if (!this.isOpen)
            return;
        if (this._grab)
            Main.popModal(this._grab);
        this._grab = null;
        const backdrop = this._backdrop;
        if (this._from && animationsOn() && this._folded) {
            // Folds back into its button, then goes.
            backdrop.reactive = false;
            this._closing.add(backdrop);
            springEase(this._bar, {scale_x: this._folded[0], scale_y: this._folded[1]}, SPRINGS.smooth);
            this._list.ease({opacity: 0, duration: 120, mode: Clutter.AnimationMode.EASE_IN_QUAD});
            this._bar.ease({
                opacity: 0,
                duration: 180,
                delay: 120,
                mode: Clutter.AnimationMode.EASE_IN_QUAD,
                onComplete: () => {
                    this._closing.delete(backdrop);
                    backdrop.destroy();
                },
            });
        } else {
            backdrop.destroy();
        }
        this._from = null;
        this._folded = null;
        this._backdrop = null;
        this._bar = null;
        this._list = null;
        this._entry = null;
        this._onClose();
    }

    destroy() {
        this._from = null; // going away with the extension: no folding back
        this.close();
        this._closing.forEach(backdrop => backdrop.destroy());
        this._closing.clear();
    }

    _onKey(key) {
        if (key === Clutter.KEY_Escape) {
            this.close();
            return Clutter.EVENT_STOP;
        }
        if (key === Clutter.KEY_Up || key === Clutter.KEY_Down) {
            this._move(key === Clutter.KEY_Up ? 1 : -1);
            return Clutter.EVENT_STOP;
        }
        if (key === Clutter.KEY_Delete && this._selected >= 0) {
            this._forgetSelected();
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }

    _onTextChanged() {
        if (this._settingText)
            return;
        this._typed = this._entry.text;
        this._selected = -1;
        this._showList();
    }

    _setText(text) {
        this._settingText = true;
        this._entry.text = text;
        this._settingText = false;
        this._entry.clutter_text.set_cursor_position(-1);
    }

    _matches() {
        const typed = this._typed.trim().toLowerCase();
        return typed ? this._history.filter(old => old.toLowerCase().includes(typed)) : this._history;
    }

    // Newest next to the entry, so Up goes further back as it goes up the screen.
    _showList() {
        const matches = this._matches();
        this._list.destroy_all_children();
        this._list.visible = matches.length > 0;
        if (!matches.length)
            return;
        const first = Math.max(0, this._selected - SHOWN + 1);
        for (let index = Math.min(first + SHOWN, matches.length) - 1; index >= first; index--)
            this._list.add_child(this._row(matches[index], index === this._selected));
        const [, height] = this._list.get_preferred_height(this._list.width);
        this._list.set_position(this._bar.x, this._bar.y - height - LIST_GAP);
    }

    _row(text, selected) {
        const row = new St.Button({
            style_class: selected ? 'deskai-command-recent selected' : 'deskai-command-recent',
            can_focus: false,
            x_expand: true,
        });
        row.set_child(new St.Label({text, x_align: Clutter.ActorAlign.START, x_expand: true}));
        row.connect('clicked', () => {
            if (!this.isOpen) // folding away
                return;
            this._selected = this._matches().indexOf(text);
            this._setText(text);
            this._showList();
        });
        return row;
    }

    _move(delta) {
        const matches = this._matches();
        const index = this._selected + delta;
        if (index < -1 || index >= matches.length)
            return;
        this._selected = index;
        this._setText(index < 0 ? this._typed : matches[index]);
        this._showList();
    }

    _forgetSelected() {
        const forgotten = this._matches()[this._selected];
        this._history = this._history.filter(old => old !== forgotten);
        this._onHistory(this._history);
        const matches = this._matches();
        this._selected = Math.min(this._selected, matches.length - 1);
        this._setText(this._selected < 0 ? this._typed : matches[this._selected]);
        this._showList();
    }

    _submit() {
        const text = this._entry.text.trim();
        if (!text)
            return;
        this._history = [text, ...this._history.filter(old => old !== text)].slice(0, HISTORY);
        this._onHistory(this._history);
        this.close();
        this._onSubmit(text);
    }
}
