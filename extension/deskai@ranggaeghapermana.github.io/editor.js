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
