// The white speech-bubble cards that rise above the dock. A card points its
// tail at the mode it's about and carries at most a couple of actions.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import St from 'gi://St';

import {MOTION} from './motion.js';

const TEXT_WIDTH = 330;
const TAIL_WIDTH = 18;
const TAIL_HEIGHT = 9;
const TAIL_INSET = 34; // the tail sits this far from the card's left edge when it can
const GAP = 6;

const GRAY = '#8e8e93';
const SPARKLE = '#7c5cff';

function esc(text) {
    return GLib.markup_escape_text(String(text ?? ''), -1);
}

function markupLabel(markup, styleClass) {
    const label = new St.Label({style_class: styleClass});
    label.clutter_text.set_markup(markup);
    label.clutter_text.line_wrap = true;
    label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
    return label;
}

export class Card {
    constructor(onAction) {
        this._onAction = onAction;
        this.spec = null;

        this.actor = new St.Widget({name: 'focusdockCard', reactive: true, visible: false, opacity: 0});
        this._body = new St.BoxLayout({style_class: 'focusdock-card'});
        this._tail = new St.DrawingArea({width: TAIL_WIDTH, height: TAIL_HEIGHT});
        this._tail.connect('repaint', area => {
            const cr = area.get_context();
            const [w, h] = area.get_surface_size();
            cr.moveTo(0, 0);
            cr.lineTo(w, 0);
            cr.lineTo(w / 2, h);
            cr.closePath();
            cr.setSourceRGBA(1, 1, 1, 0.98);
            cr.fill();
            cr.$dispose();
        });
        this.actor.add_child(this._body);
        this.actor.add_child(this._tail);

        // A click on the card itself: its `click` action (open what it's about), else dismiss
        // when it has no buttons. Buttons inside take their own clicks.
        this.actor.connect('button-release-event', (_actor, event) => {
            for (let target = global.stage.get_event_actor(event); target && target !== this.actor;
                target = target.get_parent()) {
                if (target instanceof St.Button)
                    return Clutter.EVENT_PROPAGATE;
            }
            if (this.spec?.click)
                this._onAction(this.spec, this.spec.click);
            else if (!this.spec?.actions?.length)
                this._onAction(this.spec, 'dismiss');
            return Clutter.EVENT_STOP;
        });
    }

    get shown() {
        return this.actor.visible && this.spec !== null;
    }

    destroy() {
        this.actor.destroy();
    }

    // spec: {title, subtitle, text, verdict: {label, reason}, chip: {label, detail},
    //        actions: [{id, label, primary, icon}], click}
    // place: {gicon, accent, anchorX, bottomY, monitor}
    open(spec, place) {
        const fresh = !this.shown;
        this.spec = spec;
        this._populate(spec, place);

        // St applies fonts on map, so show (transparent) before measuring.
        this.actor.remove_all_transitions();
        this.actor.show();
        for (const label of this._wrapLabels) {
            label.width = -1;
            const [, natural] = label.get_preferred_width(-1);
            label.width = Math.min(natural, TEXT_WIDTH);
        }
        this._body.set_size(-1, -1);
        const [, width] = this._body.get_preferred_width(-1);
        const [, height] = this._body.get_preferred_height(width);
        this._body.set_size(width, height);

        const {monitor, anchorX, bottomY} = place;
        const x = Math.round(Math.max(monitor.x + 8,
            Math.min(anchorX - TAIL_INSET, monitor.x + monitor.width - width - 8)));
        const tailX = Math.round(Math.max(14, Math.min(anchorX - x - TAIL_WIDTH / 2, width - 14 - TAIL_WIDTH)));
        this._tail.set_position(tailX, height - 1);
        this._tail.queue_repaint();
        this.actor.set_size(width, height + TAIL_HEIGHT);
        this.actor.set_position(x, Math.round(bottomY - height - TAIL_HEIGHT - GAP));
        this.actor.set_pivot_point((tailX + TAIL_WIDTH / 2) / width, 1);

        if (fresh) {
            this.actor.opacity = 0;
            this.actor.scale_x = this.actor.scale_y = 0.94;
            this.actor.translation_y = 8;
        }
        this.actor.ease({opacity: 255, ...MOTION.fadeIn});
        this.actor.ease({scale_x: 1, scale_y: 1, translation_y: 0, ...MOTION.pop});
    }

    close() {
        if (!this.spec)
            return;
        this.spec = null;
        this.actor.ease({
            opacity: 0,
            scale_x: 0.97,
            scale_y: 0.97,
            ...MOTION.popOut,
            onComplete: () => {
                if (!this.spec)
                    this.actor.hide();
            },
        });
    }

    _populate(spec, {gicon, accent}) {
        this._body.destroy_all_children();
        this._wrapLabels = [];

        const icon = new St.Icon({gicon, icon_size: 28, style_class: 'focusdock-card-icon', y_align: Clutter.ActorAlign.START});
        this._body.add_child(icon);

        const column = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: 'focusdock-card-col'});
        this._body.add_child(column);

        if (spec.title || spec.subtitle) {
            const parts = [];
            if (spec.title)
                parts.push(`<b>${esc(spec.title)}</b>`);
            if (spec.subtitle)
                parts.push(`<span foreground="${GRAY}">${spec.title ? ' · ' : ''}${esc(spec.subtitle)}</span>`);
            column.add_child(this._wrap(markupLabel(parts.join(''), 'focusdock-card-head')));
        }
        if (spec.text)
            column.add_child(this._wrap(markupLabel(esc(spec.text), 'focusdock-card-text')));

        if (spec.verdict) {
            const row = new St.BoxLayout({style_class: 'focusdock-card-verdict'});
            row.add_child(new St.Icon({
                icon_name: 'object-select-symbolic',
                style_class: 'focusdock-card-check',
                y_align: Clutter.ActorAlign.CENTER,
            }));
            let markup = `<b>${esc(spec.verdict.label)}</b>`;
            if (spec.verdict.reason) {
                markup += `<span foreground="${GRAY}"> · </span><span foreground="${SPARKLE}">✦</span>` +
                    `<span foreground="#6e6e73"> ${esc(spec.verdict.reason)}</span>`;
            }
            row.add_child(this._wrap(markupLabel(markup, 'focusdock-card-text')));
            column.add_child(row);
        }

        if (spec.chip) {
            // Colors come from CSS here: a span covering a whole label lost its color in testing.
            const chip = new St.BoxLayout({style_class: 'focusdock-card-chip', x_align: Clutter.ActorAlign.START});
            chip.add_child(new St.Label({
                text: `✦ ${spec.chip.label}`,
                style_class: 'focusdock-card-chip-tag',
                y_align: Clutter.ActorAlign.CENTER,
            }));
            if (spec.chip.detail) {
                chip.add_child(this._wrap(markupLabel(`· ${esc(spec.chip.detail)}`, 'focusdock-card-chip-detail')));
            }
            column.add_child(chip);
        }

        if (spec.actions?.length) {
            const row = new St.BoxLayout({style_class: 'focusdock-card-actions'});
            for (const action of spec.actions)
                row.add_child(this._button(action, accent));
            column.add_child(row);
        }
    }

    _wrap(label) {
        this._wrapLabels.push(label);
        return label;
    }

    _button(action, accent) {
        const button = new St.Button({
            style_class: action.primary ? 'focusdock-card-button primary' : 'focusdock-card-button',
            can_focus: false,
        });
        const box = new St.BoxLayout({style_class: 'focusdock-card-button-box'});
        if (action.icon) {
            box.add_child(new St.Icon({
                icon_name: action.icon,
                style_class: 'focusdock-card-button-icon',
                y_align: Clutter.ActorAlign.CENTER,
            }));
        }
        box.add_child(new St.Label({text: action.label, y_align: Clutter.ActorAlign.CENTER}));
        button.set_child(box);
        if (action.primary && accent)
            button.set_style(`background-color: ${accent};`);
        button.connect('clicked', () => this._onAction(this.spec, action.id));
        return button;
    }
}
