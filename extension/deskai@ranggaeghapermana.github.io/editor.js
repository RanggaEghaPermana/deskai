// Point-and-click editing: the app picker behind the dock's "+" button and
// the dialog for creating or editing a mode.

import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as CheckBox from 'resource:///org/gnome/shell/ui/checkBox.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {VERTICAL_BOX} from './compat.js';
import {breakMinutes} from './config.js';

export const MODE_COLORS = [
    '#e8833a', '#3b82f6', '#10b981', '#e5484d', '#8b5cf6',
    '#ec4899', '#f59e0b', '#14b8a6', '#64748b',
];

export const MODE_ICONS = [
    'document-edit-symbolic', 'applications-engineering-symbolic', 'accessories-dictionary-symbolic',
    'chat-message-new-symbolic', 'applications-graphics-symbolic', 'input-gaming-symbolic',
    'media-playback-start-symbolic', 'audio-headphones-symbolic', 'camera-video-symbolic',
    'mail-unread-symbolic', 'x-office-presentation-symbolic', 'utilities-terminal-symbolic',
    'emoji-nature-symbolic', 'face-smile-symbolic', 'weather-clear-night-symbolic', 'starred-symbolic',
];

// Fills a popup menu with a searchable list of installed apps; clicking one
// toggles it without closing the menu. Returns the search entry to focus.
export function fillAppPicker(menu, {title, searchHint, isPinned, onToggle}) {
    const header = new PopupMenu.PopupMenuItem(title, {reactive: false, can_focus: false});
    header.label.add_style_class_name('deskai-picker-title');
    menu.addMenuItem(header);

    const entry = new St.Entry({
        style_class: 'deskai-picker-search',
        hint_text: searchHint,
        can_focus: true,
        x_expand: true,
    });
    const searchItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
    searchItem.add_child(entry);
    menu.addMenuItem(searchItem);

    const list = new St.BoxLayout({...VERTICAL_BOX, style_class: 'deskai-picker-list'});
    const scroll = new St.ScrollView({
        style_class: 'deskai-picker-scroll',
        hscrollbar_policy: St.PolicyType.NEVER,
        vscrollbar_policy: St.PolicyType.AUTOMATIC,
        overlay_scrollbars: true,
        child: list,
    });
    menu.box.add_child(scroll);

    const appSystem = Shell.AppSystem.get_default();
    const apps = appSystem.get_installed()
        .filter(info => info.should_show())
        .map(info => appSystem.lookup_app(info.get_id()))
        .filter(Boolean)
        .sort((a, b) => Number(isPinned(b.get_id())) - Number(isPinned(a.get_id())) ||
            a.get_name().localeCompare(b.get_name()));

    const rows = apps.map(app => {
        // Plain buttons rather than menu items: menu items close the menu on click.
        const row = new St.Button({style_class: 'deskai-picker-row', can_focus: true, x_expand: true});
        const box = new St.BoxLayout({x_expand: true});
        box.add_child(app.create_icon_texture(24));
        box.add_child(new St.Label({
            text: app.get_name(),
            style_class: 'deskai-picker-name',
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        }));
        const check = new St.Icon({
            icon_name: 'object-select-symbolic',
            style_class: 'deskai-picker-check',
            y_align: Clutter.ActorAlign.CENTER,
        });
        box.add_child(check);
        row.set_child(box);
        const sync = () => (check.opacity = isPinned(app.get_id()) ? 255 : 0);
        sync();
        row.connect('clicked', () => {
            onToggle(app.get_id());
            sync();
        });
        list.add_child(row);
        return {row, text: `${app.get_name()} ${app.get_id()}`.toLowerCase()};
    });

    entry.clutter_text.connect('text-changed', () => {
        const query = entry.get_text().trim().toLowerCase();
        for (const {row, text} of rows)
            row.visible = !query || text.includes(query);
    });
    // Enter toggles the first match, so "spot⏎" pins Spotify.
    entry.clutter_text.connect('activate', () => rows.find(({row}) => row.visible)?.row.emit('clicked', 1));
    return entry;
}

function fieldLabel(text) {
    return new St.Label({text, style_class: 'deskai-dialog-label'});
}

export const ModeDialog = GObject.registerClass(
class ModeDialog extends ModalDialog.ModalDialog {
    // mode: the existing mode to edit, or null for a new one; t: the dock's translator.
    _init(mode, t, onSave) {
        super._init({styleClass: 'deskai-mode-dialog'});
        this._onSave = onSave;
        this._color = mode?.color ?? MODE_COLORS[4];
        this._icon = mode?.icon ?? MODE_ICONS[4];

        const content = new St.BoxLayout({
            ...VERTICAL_BOX,
            style_class: 'deskai-dialog-content',
        });
        content.add_child(new St.Label({
            text: mode ? t('dialogEdit', {mode: mode.label}) : t('dialogNew'),
            style_class: 'deskai-dialog-title',
        }));

        content.add_child(fieldLabel(t('name')));
        this._name = new St.Entry({
            style_class: 'deskai-dialog-entry',
            hint_text: t('nameHint'),
            text: mode?.label ?? '',
            can_focus: true,
        });
        content.add_child(this._name);

        content.add_child(fieldLabel(t('purpose')));
        this._description = new St.Entry({
            style_class: 'deskai-dialog-entry',
            hint_text: t('purposeHint'),
            text: mode?.description ?? '',
            can_focus: true,
        });
        content.add_child(this._description);

        content.add_child(fieldLabel(t('color')));
        const swatches = new St.BoxLayout({style_class: 'deskai-swatches'});
        this._swatches = MODE_COLORS.map(color => {
            const button = new St.Button({style_class: 'deskai-swatch', can_focus: true});
            button.set_style(`background-color: ${color};`);
            button.connect('clicked', () => {
                this._color = color;
                this._sync();
            });
            swatches.add_child(button);
            return {button, value: color};
        });
        content.add_child(swatches);

        content.add_child(fieldLabel(t('icon')));
        const grid = new St.BoxLayout({...VERTICAL_BOX, style_class: 'deskai-icon-grid'});
        let row = null;
        this._icons = MODE_ICONS.map((name, i) => {
            if (i % 8 === 0) {
                row = new St.BoxLayout({style_class: 'deskai-icon-row'});
                grid.add_child(row);
            }
            const button = new St.Button({style_class: 'deskai-icon-choice', can_focus: true});
            button.set_child(new St.Icon({icon_name: name, fallback_icon_name: 'emblem-default-symbolic', icon_size: 16}));
            button.connect('clicked', () => {
                this._icon = name;
                this._sync();
            });
            row.add_child(button);
            return {button, value: name};
        });
        content.add_child(grid);

        this._focus = new CheckBox.CheckBox(t('focusCheck'));
        this._focus.checked = mode?.focus ?? true;
        this._focus.add_style_class_name('deskai-dialog-check');
        content.add_child(this._focus);

        this.contentLayout.add_child(content);
        this.setButtons([
            {label: t('cancel'), action: () => this.close(), key: Clutter.KEY_Escape},
            {label: t(mode ? 'save' : 'create'), action: () => this._save(), default: true},
        ]);
        this.setInitialKeyFocus(this._name);
        this._sync();
    }

    _sync() {
        for (const {button, value} of this._swatches) {
            if (value === this._color)
                button.add_style_class_name('selected');
            else
                button.remove_style_class_name('selected');
        }
        for (const {button, value} of this._icons) {
            if (value === this._icon)
                button.add_style_class_name('selected');
            else
                button.remove_style_class_name('selected');
        }
    }

    _save() {
        const label = this._name.get_text().trim();
        if (!label) {
            this._name.grab_key_focus();
            return;
        }
        this.close();
        this._onSave({
            label,
            description: this._description.get_text().trim(),
            color: this._color,
            icon: this._icon,
            focus: this._focus.checked,
        });
    }
});

export const BreakDialog = GObject.registerClass(
class BreakDialog extends ModalDialog.ModalDialog {
    // breaks: modes.json's {enabled, after_minutes}; t: the dock's translator.
    _init(breaks, t, onSave) {
        super._init({styleClass: 'deskai-mode-dialog'});
        this._onSave = onSave;
        const after = Math.max(1, Math.round(breaks.after_minutes));

        const content = new St.BoxLayout({
            ...VERTICAL_BOX,
            style_class: 'deskai-dialog-content',
        });
        content.add_child(new St.Label({text: t('breakTitle'), style_class: 'deskai-dialog-title'}));

        this._on = new CheckBox.CheckBox(t('breakCheck'));
        this._on.checked = breaks.enabled;
        this._on.connect('notify::checked', () => this._sync());
        content.add_child(this._on);

        this._when = new St.BoxLayout({...VERTICAL_BOX, style_class: 'deskai-dialog-content'});
        this._when.add_child(fieldLabel(t('breakAfter')));
        const time = new St.BoxLayout({style_class: 'deskai-break-time'});
        const box = (value, unit) => {
            const entry = new St.Entry({style_class: 'deskai-break-entry', text: String(value), can_focus: true});
            entry.clutter_text.set_max_length(3);
            entry.clutter_text.connect('text-changed', () => this._problem.hide());
            // A box keeps Enter to itself, so the dialog's default button never hears it.
            entry.clutter_text.connect('activate', () => this._save());
            time.add_child(entry);
            time.add_child(new St.Label({text: unit, y_align: Clutter.ActorAlign.CENTER}));
            return entry;
        };
        this._hours = box(Math.floor(after / 60), t('breakHours'));
        this._minutes = box(after % 60, t('breakMinutes'));
        this._when.add_child(time);
        this._problem = new St.Label({text: t('breakBadTime'), style_class: 'deskai-dialog-problem', visible: false});
        this._when.add_child(this._problem);
        content.add_child(this._when);

        this.contentLayout.add_child(content);
        this.setButtons([
            {label: t('cancel'), action: () => this.close(), key: Clutter.KEY_Escape},
            {label: t('save'), action: () => this._save(), default: true},
        ]);
        this.setInitialKeyFocus(breaks.enabled ? this._hours : this._on);
        this._sync();
    }

    _sync() {
        this._when.visible = this._on.checked;
    }

    _save() {
        const on = this._on.checked;
        const after = breakMinutes(this._hours.get_text(), this._minutes.get_text());
        if (on && after === null) {
            this._problem.show();
            this._hours.grab_key_focus();
            return;
        }
        this.close();
        // Turned off with nonsense in the boxes, the time it had stays for when it's turned back on.
        this._onSave({enabled: on, ...(after === null ? {} : {after_minutes: after})});
    }
});

const APPEARANCE_DEFAULTS = {
    theme: 'glass', scheme: 'auto', glass: 'clear', blur_windows: true, rim: true, reduce_transparency: false,
};

export const AppearanceDialog = GObject.registerClass(
class AppearanceDialog extends ModalDialog.ModalDialog {
    // appearance: modes.json's appearance (any field may be missing); t: the dock's translator.
    // Every change goes to onPreview(draft) at once; Save hands the draft to onSave, anything else that closes it to onCancel.
    _init(appearance, t, {onPreview, onSave, onCancel}) {
        super._init({styleClass: 'deskai-mode-dialog'});
        this._draft = {...APPEARANCE_DEFAULTS, ...appearance};
        this._onPreview = onPreview;
        this._onSave = onSave;
        this._saved = false;
        this._groups = [];
        this._switches = [];

        const content = new St.BoxLayout({
            ...VERTICAL_BOX,
            style_class: 'deskai-dialog-content deskai-appearance-content',
        });
        content.add_child(new St.Label({text: t('appearanceTitle'), style_class: 'deskai-dialog-title'}));

        content.add_child(this._section(t('appearanceTheme'), 'theme', [
            ['glass', t('appearanceGlassTheme'), 'glass'],
            ['classic', t('appearanceClassicTheme'), 'classic'],
        ]));

        // Classic has no color scheme or glass to choose, so these only show for the glass theme.
        this._glassOptions = new St.BoxLayout({...VERTICAL_BOX, style_class: 'deskai-appearance-options'});
        this._glassOptions.add_child(this._section(t('appearanceScheme'), 'scheme', [
            ['auto', t('appearanceAuto'), 'auto'],
            ['light', t('appearanceLight'), 'light'],
            ['dark', t('appearanceDark'), 'dark'],
        ]));
        this._glassOptions.add_child(this._section(t('appearanceGlass'), 'glass', [
            ['clear', t('appearanceClear'), 'clear'],
            ['tinted', t('appearanceTinted'), 'tinted'],
        ]));
        const switches = new St.BoxLayout({...VERTICAL_BOX, style_class: 'deskai-appearance-switches'});
        switches.add_child(this._switchRow(t('appearanceBlurWindows'), 'blur_windows'));
        switches.add_child(this._switchRow(t('appearanceRim'), 'rim'));
        switches.add_child(this._switchRow(t('appearanceReduce'), 'reduce_transparency'));
        this._glassOptions.add_child(switches);
        content.add_child(this._glassOptions);

        this.contentLayout.add_child(content);
        this.setButtons([
            {label: t('cancel'), action: () => this.close(), key: Clutter.KEY_Escape},
            {label: t('save'), action: () => this._save(), default: true},
        ]);
        // Escape, Cancel and anything else that closes it count as cancelling, so the preview never outlives the dialog.
        this.connect('closed', () => {
            if (!this._saved)
                onCancel();
        });
        this.setInitialKeyFocus(this._groups[0].tiles.find(({value}) => value === this._draft.theme).button);
        this._sync();
    }

    _section(title, field, options) {
        const section = new St.BoxLayout({...VERTICAL_BOX, style_class: 'deskai-appearance-section'});
        section.add_child(fieldLabel(title));
        const row = new St.BoxLayout({style_class: 'deskai-appearance-tiles'});
        // The thumbnail is its own style class (deskai-appearance-thumb-<look>), drawn by the stylesheet.
        const tiles = options.map(([value, label, look]) => {
            const button = new St.Button({
                style_class: 'deskai-appearance-tile',
                can_focus: true,
                x_expand: true,
                accessible_name: label,
            });
            const box = new St.BoxLayout({...VERTICAL_BOX});
            const thumb = new St.Widget({style_class: `deskai-appearance-thumb deskai-appearance-thumb-${look}`});
            thumb.add_child(new St.Widget({style_class: 'deskai-appearance-thumb-bar'}));
            box.add_child(thumb);
            box.add_child(new St.Label({text: label, style_class: 'deskai-appearance-tile-label', x_align: Clutter.ActorAlign.CENTER}));
            button.set_child(box);
            button.connect('clicked', () => this._set(field, value));
            row.add_child(button);
            return {button, value};
        });
        section.add_child(row);
        this._groups.push({field, tiles});
        return section;
    }

    _switchRow(label, field) {
        const row = new St.Button({
            style_class: 'deskai-appearance-switch-row',
            can_focus: true,
            x_expand: true,
            accessible_name: label,
        });
        const box = new St.BoxLayout({x_expand: true});
        box.add_child(new St.Label({text: label, x_expand: true, y_align: Clutter.ActorAlign.CENTER}));
        const toggle = new PopupMenu.Switch(this._draft[field]);
        // The row takes the click; a live switch would swallow it for its own drag.
        toggle.reactive = false;
        box.add_child(toggle);
        row.set_child(box);
        row.connect('clicked', () => this._set(field, !this._draft[field]));
        this._switches.push({field, toggle});
        return row;
    }

    _set(field, value) {
        if (this._draft[field] === value)
            return;
        this._draft[field] = value;
        this._sync();
        this._onPreview({...this._draft});
    }

    _sync() {
        for (const {field, tiles} of this._groups) {
            for (const {button, value} of tiles) {
                if (value === this._draft[field])
                    button.add_style_class_name('selected');
                else
                    button.remove_style_class_name('selected');
            }
        }
        for (const {field, toggle} of this._switches)
            toggle.state = this._draft[field];
        this._glassOptions.visible = this._draft.theme === 'glass';
    }

    _save() {
        this._saved = true;
        this.close();
        this._onSave({...this._draft});
    }
});
