// DeskAI: a dock with focus modes for GNOME Shell.
//
// The extension is the body: it draws the dock and its cards, senses the
// focused window and incoming notifications, and holds notifications the
// active mode doesn't allow. The brain (daemon/deskaid.py) listens over D-Bus,
// classifies what you're doing, and calls back with cards to show.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Mtk from 'gi://Mtk';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as AppFavorites from 'resource:///org/gnome/shell/ui/appFavorites.js';
import * as BoxPointer from 'resource:///org/gnome/shell/ui/boxpointer.js';
import * as DND from 'resource:///org/gnome/shell/ui/dnd.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {Card} from './card.js';
import {CommandBar} from './commandbar.js';
import {SHELL_MAJOR, VERTICAL_BOX, defaultSeat} from './compat.js';
import {ModeDialog, fillAppPicker} from './editor.js';
import {Desk} from './desk.js';
import {LANGUAGES, languageOf, translator} from './i18n.js';
import {INBOX_DEFAULTS, Inbox, RANK, sourceAppId} from './inbox.js';
import {MOTION, Motion} from './motion.js';
import {Music, describe} from './music.js';
import {FILES_APP, Places, baseName, displayPath} from './places.js';

const BUS_NAME = 'io.github.RanggaEghaPermana.DeskAI';
const BRAIN_NAME = `${BUS_NAME}.Brain`;
const OBJECT_PATH = '/io/github/RanggaEghaPermana/DeskAI';

const IFACE_XML = `
<node>
  <interface name="${BUS_NAME}">
    <method name="SetMode">
      <arg type="s" direction="in" name="mode"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="RequestMode">
      <arg type="s" direction="in" name="mode"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="GetState">
      <arg type="s" direction="out" name="json"/>
    </method>
    <method name="GetWindows">
      <arg type="s" direction="out" name="json"/>
    </method>
    <method name="PlaceWindow">
      <arg type="u" direction="in" name="id"/>
      <arg type="s" direction="in" name="json"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="SelectTab">
      <arg type="u" direction="in" name="id"/>
      <arg type="u" direction="in" name="index"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="RaiseWindows">
      <arg type="s" direction="in" name="json"/>
      <arg type="u" direction="out" name="count"/>
    </method>
    <method name="Toast">
      <arg type="s" direction="in" name="text"/>
      <arg type="s" direction="in" name="kind"/>
      <arg type="s" direction="in" name="action"/>
      <arg type="s" direction="in" name="action_label"/>
    </method>
    <method name="ShowCard">
      <arg type="s" direction="in" name="json"/>
    </method>
    <method name="SurfaceNotification">
      <arg type="u" direction="in" name="id"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="Inbox">
      <arg type="s" direction="in" name="json"/>
    </method>
    <method name="Servers">
      <arg type="s" direction="in" name="json"/>
    </method>
    <method name="MinimizeFocused"/>
    <method name="Reload"/>
    <signal name="ModeChanged">
      <arg type="s" name="mode"/>
      <arg type="s" name="previous"/>
    </signal>
    <signal name="FocusChanged">
      <arg type="s" name="app_id"/>
      <arg type="s" name="app_name"/>
      <arg type="s" name="wm_class"/>
      <arg type="s" name="title"/>
    </signal>
    <signal name="NotificationHeld">
      <arg type="u" name="id"/>
      <arg type="s" name="app"/>
      <arg type="s" name="title"/>
      <arg type="s" name="body"/>
    </signal>
    <signal name="CardAction">
      <arg type="s" name="card"/>
      <arg type="s" name="action"/>
    </signal>
    <signal name="Command">
      <arg type="s" name="text"/>
    </signal>
  </interface>
</node>`;

// A mode without an `apps` list shows your favorites; `[]` means no apps.
const FALLBACK_CONFIG = {
    default_mode: 'social',
    modes: {social: {label: 'Social', icon: 'chat-message-new-symbolic', color: '#e5484d'}},
};

// What a mode made from the dock starts with; the dialog fills in the rest.
const NEW_FOCUS_MODE = {
    focus: true,
    allow: [],
    block: ['Instagram', 'TikTok', 'Twitter', ' / X', ' on X:', 'Reddit', 'Facebook', 'Netflix', 'Twitch', 'Shopee', 'Tokopedia'],
    notifications: {policy: 'filter', allow_apps: ['Calendar', 'Clocks'], keywords: ['urgent', 'darurat', 'OTP']},
    exit: {delay: 10, per_press: 3},
    nudge_after: 60,
    on_distraction: 'nudge',
};
const NEW_FREE_MODE = {focus: false, notifications: {policy: 'all'}};

const CARD_ICONS = {
    info: 'dialog-information-symbolic',
    nudge: 'alarm-symbolic',
    summary: 'mail-unread-symbolic',
    suggest: 'starred-symbolic',
};
const CARD_SECONDS = 8;
// Inbox colors: badges, the inbox pill, and the card's main button.
const INBOX_COLORS = {attention: '#f59e0b', done: '#10b981', message: '#e5484d'};
const SERVER_COLORS = {up: '#10b981', starting: '#f59e0b', down: '#e5484d'};
const MESSAGE_COLORS = {whatsapp: '#25d366', telegram: '#2aabee', signal: '#3a76f0'};
const MUSIC_COVER_SIZE = 24;
// A preview that plays for a moment doesn't bring the music pill up, and the gap between two songs doesn't fold it.
const MUSIC_SHOW_MS = 1000;
const MUSIC_HIDE_MS = 3000;
// A pointer resting on the dock this long, while you work in a window, stops holding it up.
const POINTER_REST_MS = 3000;
const DOUBLE_CLICK_US = 400 * 1000;
const PREVIEW_DELAY_MS = 250;   // a pointer just passing over the dock doesn't bring previews up
const PREVIEW_LINGER_MS = 200;  // long enough to cross from an icon to its previews
const PREVIEW_WIDTH = 200;
const PREVIEW_HEIGHT = 130;
const AUTOHIDE_MODES = ['windows', 'smart', 'always', 'never'];
const BROWSER_IDS = new Set([
    'google-chrome.desktop', 'com.google.Chrome.desktop', 'chromium.desktop', 'chromium_chromium.desktop',
    'firefox.desktop', 'firefox_firefox.desktop', 'org.mozilla.firefox.desktop', 'brave-browser.desktop',
    'com.brave.Browser.desktop', 'microsoft-edge.desktop', 'zen.desktop', 'app.zen_browser.zen.desktop',
]);
const COLOR_RE = /^(#[0-9a-f]{3,8}|rgba?\([\d\s.,%]+\))$/i;
const FOCUSABLE_TYPES = [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG];
const {AnimationMode} = Clutter;

function readText(file) {
    try {
        const [, bytes] = file.load_contents(null);
        return new TextDecoder().decode(bytes);
    } catch (e) {
        if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND))
            console.warn(`DeskAI: cannot read ${file.get_path()}: ${e.message}`);
        return null;
    }
}

function readJson(file) {
    const text = readText(file);
    try {
        return text === null ? null : JSON.parse(text);
    } catch (e) {
        console.warn(`DeskAI: bad JSON in ${file.get_path()}: ${e.message}`);
        return null;
    }
}

function slugify(label) {
    return label.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'mode';
}

function writeJson(file, data) {
    try {
        file.get_parent().make_directory_with_parents(null);
    } catch (e) {
        if (!e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.EXISTS))
            throw e;
    }
    const bytes = new TextEncoder().encode(`${JSON.stringify(data, null, 2)}\n`);
    file.replace_contents(bytes, null, false, Gio.FileCreateFlags.REPLACE_DESTINATION | Gio.FileCreateFlags.PRIVATE, null);
}

function isValidConfig(cfg) {
    return cfg && typeof cfg.modes === 'object' && Object.keys(cfg.modes).length > 0;
}

function separator() {
    return new St.Widget({style_class: 'deskai-sep', y_expand: true});
}

function withAlpha(color, alpha) {
    const hex = color.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)?.[1];
    if (!hex)
        return color;
    const full = hex.length === 3 ? [...hex].map(c => c + c).join('') : hex;
    const [r, g, b] = [0, 2, 4].map(i => parseInt(full.slice(i, i + 2), 16));
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// Windows that count as "an app is open": no desktop-icon or helper windows.
function isAppWindow(win) {
    return win && !win.skip_taskbar && !win.customJS_ding &&
        FOCUSABLE_TYPES.includes(win.get_window_type());
}

// The first enable in a shell is the login; later ones are unlocking or switching DeskAI back on.
let firstEnable = true;
// Locking the screen turns the extension off, but what's in the inbox is still waiting for you: {inbox, since}.
let keptWhileLocked = null;

export default class DeskAIExtension extends Extension {
    enable() {
        // Without Ubuntu Dock, GNOME 46 starts a session in the overview. Later versions keep their dash.
        if (SHELL_MAJOR < 50)
            this._beTheOnlyDock();
        const configDir = GLib.build_filenamev([GLib.get_user_config_dir(), 'deskai']);
        this._modesFile = Gio.File.new_for_path(GLib.build_filenamev([configDir, 'modes.json']));
        this._stateFile = Gio.File.new_for_path(
            GLib.build_filenamev([GLib.get_user_state_dir(), 'deskai', 'state.json']));
        this._commandsFile = Gio.File.new_for_path(
            GLib.build_filenamev([GLib.get_user_state_dir(), 'deskai', 'commands.json']));

        this._held = new Map();
        this._heldSeq = 0;
        this._watchedSources = new Set();
        this._focus = {appId: '', appName: '', wmClass: '', title: ''};
        this._focusWindow = null;
        this._brainOnline = false;
        this._timeouts = new Set();
        this._pending = null;
        this._appButtons = [];
        this._quiet = new WeakSet(); // notifications kept out of banners (held, or in the inbox)
        this._inbox = new Inbox({
            changed: () => this._syncInbox(),
            announce: item => this._announceInbox(item),
            hush: notification => this._quiet.add(notification),
        });
        this._keyboard = defaultSeat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);

        this._loadConfig();
        const saved = readJson(this._stateFile);
        this._mode = saved?.mode in this._modes ? saved.mode : this._defaultMode();

        this._buildDock();
        this._menuManager = new PopupMenu.PopupMenuManager(this._dock);
        this._buildIndicator();
        this._card = new Card((spec, action) => this._onCardAction(spec, action));
        Main.layoutManager.addTopChrome(this._card.actor);
        this._grabCommandKey();
        this._buildSwitcher();
        this._rebuildApps(false);
        this._updateIndicator();

        this._watchConfig();
        this._watchApps();
        this._watchWindows();
        this._watchFocus();
        this._watchNotifications();
        this._desk = new Desk(isAppWindow, keyvals => this._pressKeys(keyvals));
        this._exportDBus();
        this._places = new Places({
            changed: () => this._syncNext(),
            announce: s => this._announcePlace(s),
            busy: () => this._nextButton?.hover || (this._menu?.isOpen && this._menu.sourceActor === this._nextButton),
        });
        this._places.enable();
        this._music = new Music(() => this._onMusicChanged());
        this._onMusicChanged();
        this._queueVisibility();

        this._motion = new Motion();
        this._syncMotion();
        this._pickUpAfterLock();
    }

    // Back from a locked screen: what the inbox held, what agents said meanwhile (`deskai hook` leaves
    // that in a file while there's no dock to tell), and what came in as notifications. At any other
    // start, whatever is in that file is old.
    _pickUpAfterLock() {
        const kept = keptWhileLocked;
        keptWhileLocked = null;
        const waiting = Gio.File.new_for_path(GLib.build_filenamev([GLib.get_user_state_dir(), 'deskai', 'inbox-waiting.jsonl']));
        const lines = readText(waiting)?.split('\n').filter(Boolean);
        try {
            if (lines)
                waiting.delete(null);
        } catch (e) {
            console.warn(`DeskAI: cannot remove ${waiting.get_path()}: ${e.message}`);
        }
        if (!kept)
            return;
        this._inbox.pickUp(kept.inbox);
        const settings = this._inboxSettings();
        if (!settings.enabled)
            return;
        for (const line of lines ?? []) {
            try {
                const event = JSON.parse(line);
                // A new turn looks for the window in front, which says nothing about a turn from before you came back.
                if (event.at >= kept.since && event.event !== 'start' && event.event !== 'prompt')
                    this._inbox.takeHook(event);
            } catch (e) {
                console.warn(`DeskAI: bad inbox event: ${e.message}`);
            }
        }
        for (const source of Main.messageTray.getSources()) {
            for (const notification of [...source.notifications]) {
                if (notification.datetime?.to_unix?.() >= kept.since && !notification.forFeedback &&
                    notification.urgency !== MessageTray.Urgency.CRITICAL)
                    this._inbox.takeNotification(source, notification, settings);
            }
        }
    }

    // The shell-wide animations, on unless modes.json says {"motion": {"enabled": false}};
    // "style" picks how windows move: "smooth" (default), "calm" or "lively".
    _syncMotion() {
        try {
            this._motion?.setStyle(this._config.motion?.style ?? 'smooth');
            if (this._config.motion?.enabled === false)
                this._motion?.disable();
            else
                this._motion?.enable();
        } catch (e) {
            logError(e, 'DeskAI: motion failed, keeping GNOME animations');
            this._motion?.disable();
        }
    }

    disable() {
        this._giveBackDash();
        this._motion?.disable();
        this._motion = null;
        if (this._busNameId) {
            Gio.bus_unown_name(this._busNameId);
            this._busNameId = 0;
        }
        this._dbusImpl?.unexport();
        this._dbusImpl = null;
        if (this._brainWatchId) {
            Gio.bus_unwatch_name(this._brainWatchId);
            this._brainWatchId = 0;
        }

        this._stopPendingTimeline();
        this._pending = null;
        for (const id of this._timeouts)
            GLib.source_remove(id);
        this._timeouts.clear();
        this._debounceIds?.clear();

        this._configMonitor?.cancel();
        this._configMonitor = null;
        if (this._repositionLater) {
            global.compositor.get_laters().remove(this._repositionLater);
            this._repositionLater = 0;
        }

        global.display.disconnectObject(this);
        global.workspace_manager.disconnectObject(this);
        global.window_manager.disconnectObject(this);
        this._focusWindow?.disconnectObject(this);
        this._focusWindow = null;
        Shell.AppSystem.get_default().disconnectObject(this);
        AppFavorites.getAppFavorites().disconnectObject(this);
        Main.messageTray.disconnectObject(this);
        if (this._updateStateOriginal) {
            Main.messageTray._updateState = this._updateStateOriginal;
            this._updateStateOriginal = null;
        }
        for (const source of this._watchedSources)
            source.disconnectObject(this);
        this._watchedSources.clear();
        this._releaseHeld();
        // In whole seconds, which is all a notification's time says.
        keptWhileLocked = Main.sessionMode.isLocked && this._inbox ? {inbox: this._inbox.keep(), since: Math.floor(Date.now() / 1000)} : null;
        this._inbox?.destroy();
        this._inbox = null;
        this._releaseCommandKey();
        this._commandBar?.destroy();
        this._commandBar = null;
        this._places?.disable();
        this._places = null;
        this._music?.destroy();
        this._music = null;
        this._desk?.destroy();
        this._desk = null;
        Main.layoutManager.disconnectObject(this);
        Main.overview.disconnectObject(this);
        this._clearIconGeometry();

        this._keyboard = null;
        this._menu?.destroy();
        this._menu = null;
        this._menuManager = null;
        this._dialog?.close();
        this._dialog = null;
        this._card?.destroy();
        this._card = null;
        this._indicator?.destroy();
        this._indicator = null;
        this._menuSignature = null;
        this._tooltip?.destroy();
        this._tooltip = null;
        this._previewApp?.disconnectObject(this._previews);
        this._previewApp = null;
        this._previews?.destroy();
        this._previews = null;
        this._edge?.destroy();
        this._edge = null;
        this._frame?.destroy();
        this._frame = null;
        this._dock = null;
        this._inboxClip = null;
        this._inboxButton = null;
        this._nextClip = null;
        this._nextButton = null;
        this._musicClip = null;
        this._musicCover = null;
        this._modeEntries = null;
        this._appButtons = [];
        this._trackedStruts = undefined;
    }

    // ---- config ---------------------------------------------------------

    _loadConfig() {
        let cfg = null;
        if (this._modesFile.query_exists(null)) {
            cfg = readJson(this._modesFile);
            if (!isValidConfig(cfg)) {
                // Mid-edit or broken file: keep what we had rather than reset.
                if (this._config)
                    return false;
                cfg = null;
            }
        }
        if (!cfg) {
            const defaults = Gio.File.new_for_path(GLib.build_filenamev([this.path, 'modes.default.json']));
            cfg = readJson(defaults);
            if (!isValidConfig(cfg))
                cfg = FALLBACK_CONFIG;
            if (!this._modesFile.query_exists(null))
                writeJson(this._modesFile, cfg);
        }
        this._config = cfg;
        this._t = translator(languageOf(cfg));
        this._modes = cfg.modes;
        this._modeOrder = Object.keys(cfg.modes);
        return true;
    }

    _defaultMode() {
        const wanted = this._config.default_mode;
        return wanted in this._modes ? wanted : this._modeOrder[0];
    }

    _reloadConfig() {
        // Our own saves come back through the file monitor; they're applied already.
        if (this._lastWritten !== undefined && readText(this._modesFile) === this._lastWritten)
            return;
        if (!this._loadConfig())
            return;
        if (this._pending && !(this._pending.target in this._modes))
            this._cancelPending();
        if (!(this._mode in this._modes))
            this._setMode(this._defaultMode());
        this._trackFrame();
        this._buildSwitcher();
        this._rebuildApps(false);
        this._updateIndicator();
        if (!this._inboxSettings().enabled)
            this._inbox.clear();
        this._syncInbox();
        this._queueVisibility();
        this._syncMotion();
    }

    _watchConfig() {
        this._configMonitor = this._modesFile.monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, null);
        this._configMonitor.connect('changed', () => this._debounce('config', 300, () => this._reloadConfig()));
    }

    _debounce(key, ms, fn) {
        this._debounceIds ??= new Map();
        this._cancelDebounce(key);
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, Math.max(0, ms), () => {
            this._timeouts.delete(id);
            this._debounceIds.delete(key);
            fn();
            return GLib.SOURCE_REMOVE;
        });
        this._timeouts.add(id);
        this._debounceIds.set(key, id);
    }

    _cancelDebounce(key) {
        const id = this._debounceIds?.get(key);
        if (!id)
            return;
        GLib.source_remove(id);
        this._timeouts.delete(id);
        this._debounceIds.delete(key);
    }

    // ---- modes ----------------------------------------------------------

    _modeColor(mode) {
        return COLOR_RE.test(mode?.color ?? '') ? mode.color : '#e8833a';
    }

    _modeLabel(key) {
        return this._modes[key]?.label ?? key;
    }

    // Leaving a focus mode for one that isn't costs a wait; everything else is instant.
    _exitWait(target) {
        const current = this._modes[this._mode];
        if (!current?.focus || this._modes[target]?.focus)
            return 0;
        return Math.max(0, Number(current.exit?.delay ?? 10));
    }

    // What clicks ask for: a mode change that may have to wait out a countdown.
    _requestMode(key) {
        if (!(key in this._modes))
            return false;
        if (this._pending) {
            if (this._pending.target === key) {
                this._shortenPending();
                return true;
            }
            this._cancelPending();
        }
        if (key === this._mode)
            return true;
        const wait = this._exitWait(key);
        if (wait <= 0)
            return this._setMode(key);
        this._startPending(key, wait);
        return true;
    }

    _setMode(name) {
        if (!(name in this._modes))
            return false;
        if (this._pending) {
            this._stopPendingTimeline();
            this._pending = null;
            this._showStay(false);
        }
        const previous = this._mode;
        if (previous === name) {
            this._syncModeButtons(true);
            return true;
        }

        const held = this._releaseHeld();
        this._mode = name;
        try {
            writeJson(this._stateFile, {mode: name, since: Date.now()});
        } catch (e) {
            console.warn(`DeskAI: cannot save state: ${e.message}`);
        }
        this._peek(2000);
        this._syncModeButtons(true);
        this._rebuildApps(true);
        this._updateIndicator();
        this._emitSignal('ModeChanged', '(ss)', [name, previous ?? '']);
        this._places?.onMode(name);

        // If a held notification is why we came here, show it now.
        const wanted = this._surfaceOnSwitch;
        this._surfaceOnSwitch = null;
        const surfaced = wanted?.mode === name ? held.find(h => h.id === wanted.id) : null;
        surfaced?.notification.source?.emit('notification-request-banner', surfaced.notification);

        const rest = held.filter(h => h !== surfaced);
        if (rest.length) {
            const byApp = new Map();
            for (const h of rest)
                byApp.set(h.app || this._t('otherApp'), (byApp.get(h.app || this._t('otherApp')) ?? 0) + 1);
            this._showCard({
                kind: 'summary',
                icon: {name: CARD_ICONS.summary},
                title: this._t('summaryTitle', {n: rest.length, mode: this._modeLabel(previous)}),
                text: this._t('summaryText', {list: [...byApp].map(([app, n]) => `${app} ${n}`).join(', ')}),
                anchor_mode: name,
            });
        }
        return true;
    }

    // ---- exit countdown -------------------------------------------------

    _startPending(target, seconds) {
        const now = Date.now();
        this._pending = {target, start: now, deadline: now + seconds * 1000};
        this._peek(seconds * 1000 + 1500);
        const entry = this._modeEntries.get(target);
        const height = entry.button.height || 34;
        entry.progress.set_size(height, height);
        this._tickPending(); // fill in the time first, so the label opens to its full width
        this._syncModeButtons(true);
        this._showStay(true);

        this._pendingTimeline = new Clutter.Timeline({actor: this._dock, duration: 3600 * 1000});
        this._pendingTimeline.connect('new-frame', () => this._tickPending());
        this._pendingTimeline.start();
        this._tickPending();
    }

    _tickPending() {
        const pending = this._pending;
        if (!pending)
            return;
        const now = Date.now();
        const remaining = pending.deadline - now;
        if (remaining <= 0) {
            this._completePending();
            return;
        }
        const entry = this._modeEntries.get(pending.target);
        const fraction = Math.min(1, (now - pending.start) / (pending.deadline - pending.start));
        const height = entry.button.height || 34;
        entry.progress.set_size(Math.max(height, Math.round(fraction * entry.button.width)), height);
        const seconds = Math.ceil(remaining / 1000);
        const text = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
        if (entry.time.text !== text)
            entry.time.text = text;
    }

    // Each extra press on the mode you're waiting for cuts the wait.
    _shortenPending() {
        const perPress = Number(this._modes[this._mode]?.exit?.per_press ?? 3);
        this._pending.deadline -= perPress * 1000;
        this._tickPending();
    }

    _cancelPending() {
        if (!this._pending)
            return;
        this._stopPendingTimeline();
        this._pending = null;
        this._surfaceOnSwitch = null;
        this._syncModeButtons(true);
        this._showStay(false);
        this._endPeek();
    }

    _completePending() {
        const {target} = this._pending;
        this._stopPendingTimeline();
        this._pending = null;
        this._showStay(false);
        this._setMode(target);
    }

    _stopPendingTimeline() {
        this._pendingTimeline?.stop();
        this._pendingTimeline = null;
    }

    // ---- dock -----------------------------------------------------------

    _buildDock() {
        // The frame touches the bottom edge (so a strut can reserve space) and
        // tracks hover; its bottom padding is the visual gap under the dock.
        this._frame = new St.Widget({
            name: 'deskaiFrame',
            style_class: 'deskai-frame',
            layout_manager: new Clutter.BinLayout(),
            reactive: true,
            track_hover: true,
        });
        this._dock = new St.BoxLayout({style_class: 'deskai', reactive: true});
        this._frame.add_child(this._dock);

        this._switcher = new St.BoxLayout({style_class: 'deskai-modes', y_align: Clutter.ActorAlign.CENTER});
        this._appsClip = new St.Widget({clip_to_allocation: true, y_align: Clutter.ActorAlign.CENTER});
        this._appsBox = new St.BoxLayout({style_class: 'deskai-apps'});
        this._appsClip.add_child(this._appsBox);
        // "+" opens the app picker for the current mode. It lives outside the
        // rebuilt app list so an open picker survives the dock updating under it.
        this._addButton = new St.Button({style_class: 'deskai-add', can_focus: true, y_align: Clutter.ActorAlign.CENTER});
        this._addButton.set_child(new St.Icon({icon_name: 'list-add-symbolic', style_class: 'deskai-add-icon'}));
        this._addButton.connect('clicked', () => this._openAppPicker());
        this._dock.add_child(this._switcher);
        this._dock.add_child(separator());
        this._dock.add_child(this._appsClip);
        this._dock.add_child(this._addButton);
        this._buildNext();
        this._buildMusic();
        this._buildServers();
        this._buildInbox();
        this._buildCommand();
        // Icons move when the dock resizes or a mode pill grows; re-aim minimizes after.
        this._dock.connect('notify::allocation', () => this._queueIconGeometry());
        this._appsClip.connect('notify::allocation', () => this._queueIconGeometry());

        this._dockShown = true;
        this._peekUntil = 0;
        this._edgeArmed = false;
        // Where the overview is going: at login it may be on its way out, and 'hiding' already past.
        this._inOverview = Main.overview.visibleTarget;

        // A one-pixel strip on the bottom edge; touching it brings a hidden dock back.
        this._edge = new St.Widget({name: 'deskaiEdge', reactive: true, track_hover: true, visible: false});
        Main.layoutManager.addTopChrome(this._edge);
        this._trackFrame();

        this._lastDockMotion = 0;
        this._restHidden = false;
        this._frame.connectObject(
            // Size changes arrive mid-layout; moving the frame then re-queues
            // layout ("needs an allocation"), so move it just before the next redraw.
            'notify::width', () => this._queueReposition(),
            'notify::height', () => this._queueReposition(),
            'notify::hover', () => this._onFrameHover(),
            'motion-event', () => {
                this._noteDockMotion();
                return Clutter.EVENT_PROPAGATE;
            },
            this);
        // Drop target for rearranging apps: DND walks up from the actor under
        // the pointer, so anything inside the dock lands here.
        this._frame._delegate = {
            handleDragOver: source => this._handleDragOver(source),
            acceptDrop: source => this._acceptDrop(source),
        };
        this._edge.connectObject(
            'notify::hover', () => this._onEdgeHover(),
            // After the dock hid under a resting pointer, only fresh movement brings it back.
            'motion-event', () => {
                if (this._restHidden) {
                    this._restHidden = false;
                    this._onEdgeHover();
                }
                return Clutter.EVENT_PROPAGATE;
            },
            this);
        // A cheap safety net: re-evaluate now and then in case a trigger was missed.
        const recheck = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, 2, () => {
            this._updateVisibility();
            return GLib.SOURCE_CONTINUE;
        });
        this._timeouts.add(recheck);
        Main.layoutManager.connectObject('monitors-changed', () => this._reposition(), this);
        Main.overview.connectObject(
            'showing', () => {
                this._inOverview = true;
                this._updateVisibility();
            },
            'hiding', () => {
                this._inOverview = false;
                this._updateVisibility();
            },
            this);

        this._tooltip = new St.Label({style_class: 'deskai-tooltip', visible: false});
        Main.uiGroup.add_child(this._tooltip);
        this._previews = new St.BoxLayout({style_class: 'deskai-previews', reactive: true, track_hover: true, visible: false});
        Main.uiGroup.add_child(this._previews);
        this._previews.connect('notify::hover', () => {
            if (this._previews.hover)
                this._cancelDebounce('previews-leave');
            else
                this._leavePreviews();
        });
        this._previews.connect('motion-event', () => {
            this._noteDockMotion();
            return Clutter.EVENT_PROPAGATE;
        });
        this._addTooltip(this._addButton, () => this._t('addAppsTooltip', {mode: this._modeLabel(this._mode)}));
    }

    _dockSettings() {
        const dock = this._config.dock ?? {};
        const autohide = AUTOHIDE_MODES.includes(dock.autohide) ? dock.autohide : 'windows';
        return {autohide, struts: autohide === 'never' && (dock.reserve_space ?? true)};
    }

    // Registers the frame as chrome. Reserving space only makes sense for a
    // dock that never hides, so this re-registers when autohide changes.
    _trackFrame() {
        const {struts} = this._dockSettings();
        if (this._trackedStruts === struts)
            return;
        if (this._trackedStruts !== undefined)
            Main.layoutManager.removeChrome(this._frame);
        Main.layoutManager.addChrome(this._frame, {affectsStruts: struts});
        this._trackedStruts = struts;
    }

    _queueReposition() {
        if (this._repositionLater)
            return;
        const laters = global.compositor.get_laters();
        this._repositionLater = laters.add(Meta.LaterType.BEFORE_REDRAW, () => {
            this._repositionLater = 0;
            this._reposition();
            return GLib.SOURCE_REMOVE;
        });
    }

    _reposition() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._frame)
            return;
        const [width, height] = this._frame.get_size();
        const x = Math.floor(monitor.x + (monitor.width - width) / 2);
        this._frame.set_position(x, monitor.y + monitor.height - height);
        this._edge.set_position(x, monitor.y + monitor.height - 1);
        this._edge.set_size(width, 1);
        this._queueVisibility();
        this._queueIconGeometry();
    }

    // ---- mode switcher --------------------------------------------------

    _buildSwitcher() {
        this._tooltip.hide();
        this._switcher.destroy_all_children();
        this._modeEntries = new Map();
        for (const key of this._modeOrder) {
            const entry = this._modeButton(key);
            this._modeEntries.set(key, entry);
            this._switcher.add_child(entry.button);
        }

        this._stayClip = new St.Widget({clip_to_allocation: true, visible: false, width: 0, y_align: Clutter.ActorAlign.CENTER});
        this._stay = new St.Button({style_class: 'deskai-stay', label: this._t('stay'), can_focus: true});
        this._stay.set_pivot_point(0.5, 0.5);
        this._stay.connect('clicked', () => this._cancelPending());
        this._stayClip.add_child(this._stay);
        this._switcher.add_child(this._stayClip);
        if (this._pending)
            this._showStay(true);

        this._syncModeButtons(false);
    }

    _modeButton(key) {
        const mode = this._modes[key];
        const button = new St.Button({
            style_class: 'deskai-mode',
            can_focus: true,
            track_hover: true,
            accessible_name: mode.label ?? key,
            button_mask: St.ButtonMask.ONE | St.ButtonMask.THREE,
        });
        // The progress fill sits behind the content and grows from the left during a
        // countdown. BinLayout centers its children, so the fill lives in a holder that
        // spans the button and places it at 0,0 (a plain FixedLayout).
        const inner = new St.Widget({layout_manager: new Clutter.BinLayout(), x_expand: true, y_expand: true});
        const holder = new St.Widget({x_expand: true, y_expand: true});
        const progress = new St.Widget({style_class: 'deskai-mode-progress', visible: false});
        holder.add_child(progress);
        const content = new St.BoxLayout({style_class: 'deskai-mode-box', y_align: Clutter.ActorAlign.CENTER});
        content.add_child(new St.Icon({
            icon_name: mode.icon ?? 'emblem-default-symbolic',
            fallback_icon_name: 'emblem-default-symbolic',
            style_class: 'deskai-mode-icon',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        // The label lives in a clipping box whose width animates open and closed.
        const labelClip = new St.Widget({clip_to_allocation: true, width: 0, y_align: Clutter.ActorAlign.CENTER});
        const labelBox = new St.BoxLayout({style_class: 'deskai-mode-labels', opacity: 0});
        const label = new St.Label({text: mode.label ?? key, style_class: 'deskai-mode-label', y_align: Clutter.ActorAlign.CENTER});
        const time = new St.Label({style_class: 'deskai-mode-time', y_align: Clutter.ActorAlign.CENTER, visible: false});
        const badge = new St.Label({style_class: 'deskai-badge', y_align: Clutter.ActorAlign.CENTER, visible: false});
        labelBox.add_child(label);
        labelBox.add_child(time);
        labelBox.add_child(badge);
        labelClip.add_child(labelBox);
        content.add_child(labelClip);
        inner.add_child(holder);
        inner.add_child(content);
        button.set_child(inner);
        button.connect('clicked', (_button, mouseButton) => {
            if (mouseButton === Clutter.BUTTON_SECONDARY)
                this._openModeMenu(key, button);
            else
                this._requestMode(key);
        });

        const entry = {key, button, progress, labelClip, labelBox, time, badge, open: false};
        this._addTooltip(button, () => (entry.open ? null : mode.label ?? key));
        return entry;
    }

    _syncModeButtons(animate) {
        if (!this._modeEntries)
            return;
        for (const entry of this._modeEntries.values()) {
            const mode = this._modes[entry.key];
            const color = this._modeColor(mode);
            const active = entry.key === this._mode;
            const pending = this._pending?.target === entry.key;

            entry.button.style_class = `deskai-mode${active ? ' active' : ''}${pending ? ' pending' : ''}`;
            if (active)
                entry.button.set_style(`background-color: ${color};`);
            else if (pending)
                entry.button.set_style(`background-color: ${withAlpha(color, 0.38)};`);
            else
                entry.button.set_style(null);
            entry.progress.set_style(`background-color: ${color};`);
            entry.progress.visible = pending;
            entry.time.visible = pending;
            entry.badge.visible = active && this._held.size > 0;
            entry.badge.text = `${this._held.size}`;
            this._setLabelOpen(entry, active || pending, animate);
        }
    }

    _setLabelOpen(entry, open, animate) {
        const clip = entry.labelClip;
        const wasOpen = entry.open;
        entry.open = open;
        clip.remove_transition('width');
        const from = clip.width;
        clip.width = -1;
        const [, natural] = clip.get_preferred_width(-1);
        const to = open ? natural : 0;

        if (!animate || (wasOpen === open && from === to)) {
            clip.width = open ? -1 : 0;
            entry.labelBox.opacity = open ? 255 : 0;
            return;
        }
        clip.width = from;
        clip.ease({
            width: to,
            duration: 300,
            mode: AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                // Open labels follow their natural width again (the countdown ticks, badges change).
                if (entry.open)
                    clip.width = -1;
            },
        });
        entry.labelBox.ease({
            opacity: open ? 255 : 0,
            duration: open ? 280 : 150,
            mode: AnimationMode.EASE_OUT_QUAD,
        });
    }

    _showStay(show) {
        const clip = this._stayClip;
        if (!clip)
            return;
        clip.remove_all_transitions();
        this._stay.remove_all_transitions();
        if (show) {
            clip.visible = true;
            const from = clip.width;
            clip.width = -1;
            const [, natural] = clip.get_preferred_width(-1);
            clip.width = from;
            this._stay.scale_x = this._stay.scale_y = 0.6;
            this._stay.opacity = 0;
            clip.ease({width: natural, duration: 300, mode: AnimationMode.EASE_OUT_QUAD});
            this._stay.ease({scale_x: 1, scale_y: 1, duration: 320, mode: AnimationMode.EASE_OUT_BACK});
            this._stay.ease({opacity: 255, duration: 200, mode: AnimationMode.EASE_OUT_QUAD});
        } else {
            if (!clip.visible)
                return;
            clip.ease({
                width: 0,
                duration: 260,
                mode: AnimationMode.EASE_IN_OUT_QUAD,
                onComplete: () => {
                    if (!this._pending)
                        clip.visible = false;
                },
            });
            this._stay.ease({scale_x: 0.4, scale_y: 0.4, opacity: 0, duration: 220, mode: AnimationMode.EASE_IN_QUAD});
        }
    }

    // ---- apps -----------------------------------------------------------

    _queueRebuild() {
        this._debounce('rebuild', 80, () => this._rebuildApps(false));
    }

    _pinnedIds(key) {
        const apps = this._modes[key]?.apps;
        return Array.isArray(apps) ? apps : AppFavorites.getAppFavorites().getFavorites().map(app => app.get_id());
    }

    _rebuildApps(staggered) {
        if (!this._appsBox)
            return;
        // An open app menu or a drag hangs off one of these buttons; rebuild once it's over.
        if ((this._menu?.isOpen && this._menuOnApp) || this._drag) {
            this._rebuildAfterMenu = true;
            return;
        }
        this._tooltip.hide();
        this._hidePreviews();
        const oldWidth = this._appsClip.has_allocation() ? this._appsClip.width : -1;
        this._appsBox.destroy_all_children();
        this._appButtons = [];

        const appSystem = Shell.AppSystem.get_default();
        const pinned = this._pinnedIds(this._mode).map(id => appSystem.lookup_app(id)).filter(Boolean);
        const pinnedIds = new Set(pinned.map(app => app.get_id()));
        for (const app of pinned)
            this._appsBox.add_child(this._appButton(app, false));

        if (this._config.dock?.show_running ?? true) {
            const others = this._inOpeningOrder(appSystem.get_running().filter(app => !pinnedIds.has(app.get_id())));
            if (others.length) {
                this._appsBox.add_child(separator());
                for (const app of others)
                    this._appsBox.add_child(this._appButton(app, true));
            }
        }

        // Grow or shrink to the new width instead of jumping to it.
        this._appsClip.remove_transition('width');
        this._appsClip.width = -1;
        const [, newWidth] = this._appsClip.get_preferred_width(-1);
        if (oldWidth >= 0 && oldWidth !== newWidth && this._dockShown) {
            this._appsClip.width = oldWidth;
            this._appsClip.ease({
                width: newWidth,
                duration: 320,
                mode: AnimationMode.EASE_OUT_QUAD,
                onComplete: () => {
                    this._appsClip.width = -1;
                    this._queueIconGeometry();
                },
            });
        }

        if (staggered) {
            this._appButtons.forEach(({button}, i) => {
                button.opacity = 0;
                button.translation_y = 10;
                button.ease({
                    opacity: 255,
                    translation_y: 0,
                    delay: i * 30,
                    duration: 280,
                    mode: AnimationMode.EASE_OUT_QUAD,
                });
            });
        }
        this._syncBadges(null, false);
        this._queueIconGeometry();
    }

    _appButton(app, foreign) {
        const button = new St.Button({
            style_class: foreign ? 'deskai-app foreign' : 'deskai-app',
            can_focus: true,
            track_hover: true,
            accessible_name: app.get_name(),
            button_mask: St.ButtonMask.ONE | St.ButtonMask.TWO | St.ButtonMask.THREE,
        });
        const box = new St.BoxLayout({...VERTICAL_BOX});
        const icon = app.create_icon_texture(this._config.dock?.icon_size ?? 38);
        // The inbox badge: a colored dot on the icon's corner (needs you, done, or a message).
        const iconBox = new St.Widget({layout_manager: new Clutter.BinLayout()});
        iconBox.add_child(icon);
        const badge = new St.Widget({
            style_class: 'deskai-app-badge',
            visible: false,
            x_expand: true,
            y_expand: true,
            x_align: Clutter.ActorAlign.END,
            y_align: Clutter.ActorAlign.START,
        });
        iconBox.add_child(badge);
        box.add_child(iconBox);
        const dot = new St.Widget({style_class: 'deskai-dot', x_align: Clutter.ActorAlign.CENTER});
        dot.opacity = app.state === Shell.AppState.STOPPED ? 0 : 255;
        box.add_child(dot);
        button.set_child(box);
        button.connect('clicked', (_button, mouseButton) => {
            this._tooltip.hide();
            this._hidePreviews();
            if (mouseButton === Clutter.BUTTON_SECONDARY)
                this._openAppMenu(app, button);
            else if (mouseButton === Clutter.BUTTON_MIDDLE && app.can_open_new_window())
                app.open_new_window(-1);
            else if (this._inbox.list(app.get_id()).length)
                this._openInboxItem(this._inbox.list(app.get_id())[0]); // a badge means "take me there"
            else
                this._clickApp(app);
        });
        // New windows need to know where they minimize to.
        app.connectObject('windows-changed', () => this._queueIconGeometry(), button);
        // Its name if it has no windows to show. Where Files would go says more than its windows do.
        this._addTooltip(button, () => {
            const place = this._lastPlaceOf(app);
            if (place)
                return this._t('goTo', {app: app.get_name(), name: place.name});
            return this._windowsToPick(app).length ? null : app.get_name();
        });
        button.connect('notify::hover', () => this._onAppHover(app, button));

        // Hold and drag to rearrange (or drag a running app in among the pinned ones).
        button._delegate = {
            app,
            getDragActor: () => app.create_icon_texture(this._config.dock?.icon_size ?? 38),
            getDragActorSource: () => icon,
        };
        const draggable = DND.makeDraggable(button, {timeoutThreshold: 200});
        draggable.connect('drag-begin', () => this._onDragBegin(app, button));
        draggable.connect('drag-cancelled', () => this._onDragCancelled());
        draggable.connect('drag-end', () => this._onDragEnd());

        this._appButtons.push({app, button, badge, pinned: !foreign});
        return button;
    }

    // Clicking the app you're already in puts all its windows here away, and clicking it again brings them
    // all back. The second click of a double-click doesn't count, or they'd come up and go right back.
    _clickApp(app) {
        const now = GLib.get_monotonic_time();
        const doubled = this._lastClick?.app === app && now - this._lastClick.time < DOUBLE_CLICK_US;
        this._lastClick = {app, time: now};
        if (doubled)
            return;

        const focused = global.display.focus_window;
        if (!this._inOverview && focused && Shell.WindowTracker.get_default().get_window_app(focused) === app) {
            this._appWindowsOn(app, focused.get_workspace()).filter(win => win.can_minimize()).forEach(win => win.minimize());
        } else if (!(app.get_id() === FILES_APP && this._places?.goToLastPlace())) {
            // Before activating: a workspace switch only knows the windows showing when it starts.
            const [next] = app.get_windows(); // the one activating goes to
            if (next)
                this._appWindowsOn(app, next.get_workspace()).forEach(win => win.unminimize());
            app.activate();
        }
    }

    _appWindowsOn(app, workspace) {
        return app.get_windows().filter(win => isAppWindow(win) && win.located_on_workspace(workspace));
    }

    // GNOME lists running apps by last use, which would swap icons under the pointer when the dock rebuilds.
    _inOpeningOrder(apps) {
        const known = (this._openingOrder ?? []).filter(id => apps.some(app => app.get_id() === id));
        const added = apps.map(app => app.get_id()).filter(id => !known.includes(id));
        this._openingOrder = [...known, ...added];
        return this._openingOrder.map(id => apps.find(app => app.get_id() === id));
    }

    // ---- rearranging by drag ------------------------------------------------

    _onDragBegin(app, button) {
        const id = app.get_id();
        const others = this._appButtons.filter(e => e.pinned && e.app.get_id() !== id);
        const pinnedIndex = this._appButtons.filter(e => e.pinned).findIndex(e => e.app.get_id() === id);
        this._drag = {app, button, slot: button.width, home: pinnedIndex, others};
        this._tooltip.hide();
        this._hidePreviews();
        this._updateVisibility();
        // Once DND has taken the icon's picture, hand the button's slot to a
        // placeholder so the dock doesn't change width.
        this._debounce('drag-begin', 0, () => {
            if (!this._drag)
                return;
            button.hide();
            if (pinnedIndex >= 0)
                this._movePlaceholder(pinnedIndex, false);
        });
    }

    _dropIndex() {
        const [pointerX] = global.get_pointer();
        let index = 0;
        for (const {button} of this._drag.others) {
            const [x] = button.get_transformed_position();
            if (pointerX > x + button.width / 2)
                index++;
        }
        return index;
    }

    _movePlaceholder(index, animate = true) {
        if (this._placeholderIndex === index)
            return;
        this._removePlaceholder(animate);
        const placeholder = new St.Widget({style_class: 'deskai-placeholder', width: animate ? 0 : this._drag.slot});
        const others = this._drag.others.map(e => e.button);
        if (others[index])
            this._appsBox.insert_child_below(placeholder, others[index]);
        else if (others.length)
            this._appsBox.insert_child_above(placeholder, others.at(-1));
        else
            this._appsBox.insert_child_at_index(placeholder, 0);
        if (animate)
            placeholder.ease({width: this._drag.slot, duration: 200, mode: AnimationMode.EASE_OUT_QUAD});
        this._placeholder = placeholder;
        this._placeholderIndex = index;
    }

    _removePlaceholder(animate) {
        const placeholder = this._placeholder;
        this._placeholder = null;
        this._placeholderIndex = -1;
        if (!placeholder)
            return;
        if (!animate) {
            placeholder.destroy();
            return;
        }
        placeholder.ease({
            width: 0,
            duration: 200,
            mode: AnimationMode.EASE_OUT_QUAD,
            onComplete: () => placeholder.destroy(),
        });
    }

    _handleDragOver(source) {
        if (!this._drag || source?.app !== this._drag.app)
            return DND.DragMotionResult.CONTINUE;
        this._movePlaceholder(this._dropIndex());
        return DND.DragMotionResult.MOVE_DROP;
    }

    _acceptDrop(source) {
        if (!this._drag || source?.app !== this._drag.app)
            return false;
        const id = this._drag.app.get_id();
        const ids = this._pinnedIds(this._mode).filter(other => other !== id);
        const index = this._placeholderIndex >= 0 ? this._placeholderIndex : this._dropIndex();
        // The index counts visible icons; `apps` may also hold apps that aren't
        // installed, so place the app next to its visible neighbour's id.
        const neighbours = this._drag.others.map(e => e.app.get_id());
        let at = ids.length;
        if (neighbours[index])
            at = ids.indexOf(neighbours[index]);
        else if (neighbours.length)
            at = ids.indexOf(neighbours.at(-1)) + 1;
        ids.splice(at < 0 ? ids.length : at, 0, id);
        this._modes[this._mode].apps = ids;
        this._saveConfig();
        return true;
    }

    // Dropped outside the dock: the icon snaps back, so reopen its old slot.
    _onDragCancelled() {
        if (!this._drag)
            return;
        if (this._drag.home >= 0)
            this._movePlaceholder(this._drag.home);
        else
            this._removePlaceholder(true);
    }

    _onDragEnd() {
        this._drag = null;
        this._removePlaceholder(false);
        this._rebuildAfterMenu = false;
        this._rebuildApps(false);
        this._queueVisibility();
    }

    _addTooltip(actor, textFn) {
        actor.connect('notify::hover', () => {
            const text = actor.hover && !this._card?.shown ? textFn() : null;
            if (!text) {
                this._tooltip.hide();
                return;
            }
            this._tooltip.text = text;
            this._tooltip.show();
            this._tooltip.get_parent().set_child_above_sibling(this._tooltip, null); // above an open menu
            const [x, y] = actor.get_transformed_position();
            const [w] = actor.get_transformed_size();
            const [, tipWidth] = this._tooltip.get_preferred_width(-1);
            const [, tipHeight] = this._tooltip.get_preferred_height(-1);
            this._tooltip.set_position(Math.floor(x + (w - tipWidth) / 2), Math.floor(y - tipHeight - 8));
        });
    }

    _watchApps() {
        Shell.AppSystem.get_default().connectObject(
            'app-state-changed', () => this._queueRebuild(),
            'installed-changed', () => this._queueRebuild(),
            this);
        AppFavorites.getAppFavorites().connectObject('changed', () => this._queueRebuild(), this);
    }

    // Minimize animations (e.g. Magic Lamp) fly a window to its icon geometry.
    // Point every window at its icon, as placed when the dock is shown.
    _queueIconGeometry() {
        this._debounce('icon-geometry', 60, () => this._updateIconGeometry());
    }

    _updateIconGeometry() {
        if (!this._dock)
            return;
        for (const {app, button} of this._appButtons) {
            if (!button.has_allocation())
                continue;
            const [x, y] = button.get_transformed_position();
            const [width, height] = button.get_transformed_size();
            const rect = new Mtk.Rectangle({
                x: Math.round(x - this._dock.translation_x),
                y: Math.round(y - this._dock.translation_y - button.translation_y),
                width: Math.round(width),
                height: Math.round(height),
            });
            for (const win of app.get_windows())
                win.set_icon_geometry(rect);
        }
    }

    _clearIconGeometry() {
        for (const {app} of this._appButtons) {
            for (const win of app.get_windows())
                win.set_icon_geometry(null);
        }
    }

    // ---- menus ----------------------------------------------------------

    // One popup at a time, hung off a dock button. The dock stays up while it's open.
    _openMenu(sourceActor, fill, {onApp = false} = {}) {
        const old = this._menu;
        this._menu = null;
        old?.destroy();
        this._tooltip.hide();
        this._hidePreviews();

        const menu = new PopupMenu.PopupMenu(sourceActor, 0.5, St.Side.BOTTOM);
        menu.actor.add_style_class_name('deskai-menu');
        Main.uiGroup.add_child(menu.actor);
        menu.actor.hide();
        this._menuManager.addMenu(menu);
        this._menu = menu;
        this._menuOnApp = onApp;
        const focus = fill(menu);

        menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                return;
            // Let the close animation finish before tearing the menu down.
            const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
                this._timeouts.delete(id);
                if (this._menu === menu) {
                    this._menu = null;
                    menu.destroy();
                    this._afterMenuClosed();
                }
                return GLib.SOURCE_REMOVE;
            });
            this._timeouts.add(id);
        });
        menu.open(BoxPointer.PopupAnimation.FULL);
        focus?.grab_key_focus();
        this._updateVisibility();
    }

    _afterMenuClosed() {
        if (this._rebuildAfterMenu) {
            this._rebuildAfterMenu = false;
            this._rebuildApps(false);
        }
        this._queueVisibility();
    }

    _menuItem(menu, label, action) {
        const item = new PopupMenu.PopupMenuItem(label);
        // Run once the menu has let go: the action may rebuild the button it hangs off.
        item.connect('activate', () => this._debounce('menu-action', 0, action));
        menu.addMenuItem(item);
        return item;
    }

    _lastPlaceOf(app) {
        return app.get_id() === FILES_APP ? this._places?.lastPlace : null;
    }

    // ---- window previews ---------------------------------------------------

    _onAppHover(app, button) {
        if (!button.hover) {
            this._cancelDebounce('previews');
            this._leavePreviews();
            return;
        }
        this._cancelDebounce('previews-leave');
        if (this._previewApp === app)
            return;
        // Right away when some are up, so moving along the dock doesn't wait at every icon.
        if (this._previewApp)
            this._showPreviews(app, button);
        else
            this._debounce('previews', PREVIEW_DELAY_MS, () => this._showPreviews(app, button));
    }

    _leavePreviews() {
        this._debounce('previews-leave', PREVIEW_LINGER_MS, () => {
            if (!this._previews?.hover)
                this._hidePreviews();
        });
    }

    _showPreviews(app, button) {
        const wasUp = Boolean(this._previewApp);
        this._hidePreviews();
        const windows = this._windowsToPick(app).filter(win => win.get_compositor_private());
        if (!windows.length || this._lastPlaceOf(app) || this._card?.shown || this._menu?.isOpen || this._drag)
            return;

        const monitor = Main.layoutManager.primaryMonitor;
        const width = Math.min(PREVIEW_WIDTH, Math.floor((monitor.width - 64) / windows.length) - 24);
        for (const win of windows)
            this._previews.add_child(this._previewButton(app, win, width));
        app.connectObject('windows-changed', () => this._showPreviews(app, button), this._previews);
        this._previewApp = app;

        this._previews.show();
        this._previews.get_parent().set_child_above_sibling(this._previews, null);
        const [x, y] = button.get_transformed_position();
        const [w] = button.get_transformed_size();
        const [, ownWidth] = this._previews.get_preferred_width(-1);
        const [, ownHeight] = this._previews.get_preferred_height(-1);
        const left = Math.max(monitor.x + 8, Math.min(x + (w - ownWidth) / 2, monitor.x + monitor.width - ownWidth - 8));
        this._previews.set_position(Math.floor(left), Math.floor(y - ownHeight - 14));
        this._previews.remove_all_transitions();
        this._previews.opacity = wasUp ? 255 : 0;
        this._previews.ease({opacity: 255, ...MOTION.fadeIn});
    }

    _previewButton(app, win, width) {
        const title = win.get_title() || app.get_name();
        const button = new St.Button({style_class: 'deskai-preview', accessible_name: title});
        const box = new St.BoxLayout({...VERTICAL_BOX, style_class: 'deskai-preview-box'});
        box.add_child(new St.Label({style_class: 'deskai-preview-title', text: title, width}));

        const height = Math.round(width * PREVIEW_HEIGHT / PREVIEW_WIDTH);
        const source = win.get_compositor_private();
        const [sourceWidth, sourceHeight] = source.get_size();
        const scale = Math.min(1, width / Math.max(sourceWidth, 1), height / Math.max(sourceHeight, 1));
        const picture = new St.Widget({layout_manager: new Clutter.BinLayout(), width, height});
        picture.add_child(new Clutter.Clone({
            source,
            width: sourceWidth * scale,
            height: sourceHeight * scale,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
            y_expand: true,
        }));
        box.add_child(picture);
        button.set_child(box);
        // Once the click is over: hiding destroys this button.
        button.connect('clicked', () => this._debounce('preview-pick', 0, () => {
            this._hidePreviews();
            this._showWindow(app, win);
        }));
        return button;
    }

    _hidePreviews() {
        this._cancelDebounce('previews');
        this._cancelDebounce('previews-leave');
        if (!this._previewApp)
            return;
        this._previewApp.disconnectObject(this._previews);
        this._previewApp = null;
        this._previews.hide();
        this._previews.destroy_all_children();
        this._queueVisibility();
    }

    // In the order they were opened, so a window keeps its row.
    _windowsToPick(app) {
        return app.get_windows()
            .filter(isAppWindow)
            .sort((a, b) => a.get_stable_sequence() - b.get_stable_sequence());
    }

    _showWindow(app, win) {
        if (!app.get_windows().includes(win))
            return; // closed while the menu was open
        win.unminimize(); // before activating, as in _clickApp
        Main.activateWindow(win);
    }

    _openAppMenu(app, button) {
        const id = app.get_id();
        const key = this._mode;
        const label = this._modeLabel(key);
        this._openMenu(button, menu => {
            const windows = this._windowsToPick(app);
            if (windows.length > 1) {
                for (const win of windows) {
                    const item = this._menuItem(menu, win.get_title() || app.get_name(), () => this._showWindow(app, win));
                    item.label.add_style_class_name('deskai-menu-window');
                    item.setOrnament(win.has_focus() ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
                }
                menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            }
            if (app.can_open_new_window()) {
                this._menuItem(menu, this._t('newWindow'), () => app.open_new_window(-1));
                menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            }
            if (id === FILES_APP && this._addRecentPlaces(menu))
                menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            const ids = this._pinnedIds(key);
            const index = ids.indexOf(id);
            this._menuItem(menu, this._t(index >= 0 ? 'unpinFrom' : 'pinTo', {mode: label}), () => this._togglePin(key, id));
            if (index > 0)
                this._menuItem(menu, this._t('moveLeft'), () => this._moveApp(key, id, -1));
            if (index >= 0 && index < ids.length - 1)
                this._menuItem(menu, this._t('moveRight'), () => this._moveApp(key, id, 1));

            const others = this._modeOrder.filter(k => k !== key);
            if (others.length) {
                const sub = new PopupMenu.PopupSubMenuMenuItem(this._t('showInOtherModes'));
                for (const other of others) {
                    const item = this._menuItem(sub.menu, this._modeLabel(other), () => this._togglePin(other, id));
                    item.setOrnament(this._pinnedIds(other).includes(id) ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
                }
                menu.addMenuItem(sub);
            }
            if (app.state !== Shell.AppState.STOPPED) {
                menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
                this._menuItem(menu, this._t('quit'), () => app.request_quit());
            }
        }, {onApp: true});
    }

    _openModeMenu(key, button) {
        const label = this._modeLabel(key);
        const index = this._modeOrder.indexOf(key);
        this._openMenu(button, menu => {
            // Here, not in the app's own menu: it's about this window, wherever you are in it.
            if (key === this._mode && this._modes[key]?.focus && this._focusWindow) {
                for (const button of this._labelButtons())
                    this._menuItem(menu, button.menuLabel, () => this._submitCommand(button.command));
                menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            }
            this._menuItem(menu, this._t('editMode', {mode: label}), () => this._openModeDialog(key));
            this._menuItem(menu, this._t('appsIn', {mode: label}), () => {
                this._requestMode(key);
                if (this._mode === key)
                    this._openAppPicker();
            });
            if (index > 0)
                this._menuItem(menu, this._t('moveLeft'), () => this._moveMode(key, -1));
            if (index < this._modeOrder.length - 1)
                this._menuItem(menu, this._t('moveRight'), () => this._moveMode(key, 1));
            if (this._modeOrder.length > 1)
                this._menuItem(menu, this._t('deleteMode', {mode: label}), () => this._deleteMode(key));
            menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            this._menuItem(menu, this._t('newMode'), () => this._openModeDialog(null));
        });
    }

    _openAppPicker() {
        const key = this._mode;
        const open = () => this._openMenu(this._addButton, menu => fillAppPicker(menu, {
            title: this._t('pickerTitle', {mode: this._modeLabel(key)}),
            searchHint: this._t('searchApps'),
            isPinned: id => this._pinnedIds(key).includes(id),
            onToggle: id => this._togglePin(key, id),
        }));
        // The menu points at the button where it is now, so let a hidden dock arrive first.
        if (this._dockShown && this._dock.translation_y === 0) {
            open();
        } else {
            this._peek(1500);
            this._debounce('picker', 450, open);
        }
    }

    _openModeDialog(key) {
        this._dialog?.close();
        const mode = key ? {...this._modes[key], label: this._modeLabel(key)} : null;
        const dialog = new ModeDialog(mode, this._t, fields => {
            if (key)
                this._updateMode(key, fields);
            else
                this._createMode(fields);
        });
        dialog.connect('destroy', () => {
            if (this._dialog === dialog)
                this._dialog = null;
        });
        this._dialog = dialog;
        dialog.open();
    }

    // ---- editing modes.json ---------------------------------------------

    _saveConfig() {
        this._modes = this._config.modes;
        this._modeOrder = Object.keys(this._modes);
        const text = `${JSON.stringify(this._config, null, 2)}\n`;
        this._lastWritten = text;
        try {
            this._modesFile.replace_contents(new TextEncoder().encode(text), null, false,
                Gio.FileCreateFlags.REPLACE_DESTINATION | Gio.FileCreateFlags.PRIVATE, null);
        } catch (e) {
            Main.notifyError('DeskAI', `Couldn't save modes.json: ${e.message}`);
        }
        this._updateIndicator();
    }

    _togglePin(key, id) {
        if (!(key in this._modes))
            return;
        const ids = [...this._pinnedIds(key)];
        const index = ids.indexOf(id);
        if (index >= 0)
            ids.splice(index, 1);
        else
            ids.push(id);
        this._modes[key].apps = ids;
        this._saveConfig();
        if (key === this._mode)
            this._rebuildApps(false);
    }

    _moveApp(key, id, delta) {
        const ids = [...this._pinnedIds(key)];
        const from = ids.indexOf(id);
        const to = from + delta;
        if (from < 0 || to < 0 || to >= ids.length)
            return;
        [ids[from], ids[to]] = [ids[to], ids[from]];
        this._modes[key].apps = ids;
        this._saveConfig();
        if (key === this._mode)
            this._rebuildApps(false);
    }

    // Rewrites `modes` in the given order: key order in the file is the dock's order.
    _setModes(entries) {
        this._config.modes = Object.fromEntries(entries);
        this._saveConfig();
        this._buildSwitcher();
    }

    _moveMode(key, delta) {
        const entries = Object.entries(this._modes);
        const from = entries.findIndex(([k]) => k === key);
        const to = from + delta;
        if (from < 0 || to < 0 || to >= entries.length)
            return;
        [entries[from], entries[to]] = [entries[to], entries[from]];
        this._setModes(entries);
    }

    _createMode({label, description, color, icon, focus}) {
        let id = slugify(label);
        for (let n = 2; id in this._modes; n++)
            id = `${slugify(label)}-${n}`;
        const template = JSON.parse(JSON.stringify(focus ? NEW_FOCUS_MODE : NEW_FREE_MODE));
        const mode = {label, icon, color, description, apps: [], ...template};

        // Focus modes go before the first free one, the way the defaults are laid out.
        const entries = Object.entries(this._modes);
        const firstFree = entries.findIndex(([, m]) => !m.focus);
        entries.splice(focus && firstFree >= 0 ? firstFree : entries.length, 0, [id, mode]);
        this._setModes(entries);
        this._setMode(id);
        // A new mode has no apps yet: offer the picker right away.
        this._debounce('picker-new', 500, () => this._openAppPicker());
    }

    _updateMode(key, {label, description, color, icon, focus}) {
        const mode = this._modes[key];
        if (!mode)
            return;
        Object.assign(mode, {label, description, color, icon});
        if (focus && !mode.focus) {
            const template = JSON.parse(JSON.stringify(NEW_FOCUS_MODE));
            for (const [field, value] of Object.entries(template)) {
                if (mode[field] === undefined || (field === 'notifications' && mode.notifications?.policy === 'all'))
                    mode[field] = value;
            }
        } else if (!focus && mode.focus) {
            mode.focus = false;
            mode.notifications = {...mode.notifications, policy: 'all'};
        }
        this._saveConfig();
        this._buildSwitcher();
    }

    _deleteMode(key) {
        if (this._modeOrder.length <= 1 || !(key in this._modes))
            return;
        const entries = Object.entries(this._modes);
        const index = entries.findIndex(([k]) => k === key);
        const [[, removed]] = entries.splice(index, 1);
        const fallback = (entries.find(([, m]) => !m.focus) ?? entries[0])[0];
        this._undo = {key, mode: removed, index, wasDefault: this._config.default_mode === key};

        if (this._pending?.target === key)
            this._cancelPending();
        if (this._undo.wasDefault)
            this._config.default_mode = fallback;
        if (this._mode === key)
            this._setMode(fallback); // leave it while it still exists
        this._setModes(entries);
        this._showCard({
            kind: 'info',
            icon: {name: 'user-trash-symbolic'},
            title: this._t('deletedTitle', {mode: removed.label ?? key}),
            text: this._t('deletedText'),
            actions: [{id: 'undo-delete', label: this._t('undo'), primary: true}],
            anchor_mode: this._mode,
            timeout: 10,
        });
    }

    _undoDelete() {
        const undo = this._undo;
        this._undo = null;
        if (!undo || undo.key in this._modes)
            return;
        const entries = Object.entries(this._modes);
        entries.splice(Math.min(undo.index, entries.length), 0, [undo.key, undo.mode]);
        if (undo.wasDefault)
            this._config.default_mode = undo.key;
        this._setModes(entries);
    }

    // ---- autohide -------------------------------------------------------

    _watchWindows() {
        global.display.connectObject(
            'in-fullscreen-changed', () => this._queueVisibility(),
            'restacked', () => this._queueVisibility(),
            this);
        global.workspace_manager.connectObject(
            'active-workspace-changed', () => this._queueVisibility(), this);
        global.window_manager.connectObject(
            'map', () => this._queueVisibility(),
            'destroy', () => this._queueVisibility(),
            // Bring the dock up so the window has an icon to fly into (or out of).
            'minimize', () => this._peek(1400),
            'unminimize', () => this._peek(900),
            this);
    }

    _queueVisibility() {
        this._debounce('visibility', 40, () => this._updateVisibility());
    }

    // One dock, this one. GNOME starts a session in the overview, whose dash would sit on top of this
    // dock as the desktop appears (Ubuntu Dock used to prevent that), and shows it again in the overview.
    _beTheOnlyDock() {
        const layout = Main.layoutManager;
        if (firstEnable && Main.overview.visible) {
            // GNOME 46 enables extensions once its startup animation into the overview has begun:
            // hiding it now is a case GNOME handles, and the session lands on the desktop.
            Main.overview.hide();
        } else if (layout._startingUp && Main.sessionMode.hasOverview) {
            Main.sessionMode.hasOverview = false;
            this._overviewSkipped = true;
            layout.connectObject('startup-complete', () => this._restoreOverview(), this);
        }
        firstEnable = false;
        const dash = Main.overview.dash;
        if (dash) {
            dash.hide();
            dash.height = 0; // a hidden dash still takes its height in the overview's layout
            this._overviewDash = dash;
        }
    }

    _restoreOverview() {
        if (this._overviewSkipped)
            Main.sessionMode.hasOverview = true;
        this._overviewSkipped = false;
    }

    _giveBackDash() {
        this._restoreOverview();
        if (this._overviewDash) {
            this._overviewDash.height = -1;
            this._overviewDash.show();
        }
        this._overviewDash = null;
    }

    _wantVisible() {
        const {autohide, struts} = this._dockSettings();
        if (Main.layoutManager.primaryMonitor?.inFullscreen)
            return false;
        if (this._inOverview)
            return struts; // the overview lays itself out above a strut, not above a floating dock
        if (autohide === 'never' || this._pointerWantsDock() || this._pending ||
            this._card?.shown || this._menu?.isOpen || this._drag || Date.now() < this._peekUntil)
            return true;
        if (autohide === 'always')
            return false;
        if (autohide === 'windows')
            return !this._appWindowOpen();
        return !this._focusOverlapsDock();
    }

    // The pointer holds the dock up while it's on it and moving (or just arrived at
    // the edge). A pointer parked there while you type in a window doesn't count,
    // which also lets go of a hover state left stuck by a missed leave event.
    _pointerWantsDock() {
        if (!this._frame.hover && !this._edgeArmed && !this._previewApp)
            return false;
        return Date.now() - this._lastDockMotion < POINTER_REST_MS;
    }

    _noteDockMotion() {
        this._lastDockMotion = Date.now();
        this._debounce('pointer-rest', POINTER_REST_MS + 50, () => {
            this._hidePreviews(); // or they'd stay over your window while you type
            this._updateVisibility();
        });
    }

    // "windows": any app window open on this workspace and monitor hides the dock.
    _appWindowOpen() {
        const workspace = global.workspace_manager.get_active_workspace();
        return global.display.get_tab_list(Meta.TabList.NORMAL, workspace).some(win =>
            isAppWindow(win) && !win.minimized && win.get_monitor() === Main.layoutManager.primaryIndex);
    }

    // "smart": only the window you're working in, and only if it reaches into the dock.
    _focusOverlapsDock() {
        const win = global.display.focus_window;
        if (!isAppWindow(win) || win.minimized || win.get_monitor() !== Main.layoutManager.primaryIndex)
            return false;
        const rect = win.get_frame_rect();
        const [x, y] = this._frame.get_position();
        const [width, height] = this._frame.get_size();
        return rect.x < x + width && rect.x + rect.width > x &&
            rect.y < y + height && rect.y + rect.height > y;
    }

    _updateVisibility() {
        if (!this._frame)
            return;
        const show = this._wantVisible();
        this._edge.visible = !show && !this._inOverview &&
            !Main.layoutManager.primaryMonitor?.inFullscreen;
        if (show === this._dockShown)
            return;
        this._dockShown = show;
        // Hiding with the pointer still on the dock means it was resting there.
        this._restHidden = !show && (this._frame.hover || this._edgeArmed);
        if (!show)
            this._edgeArmed = false;

        // Position and opacity ease separately: an overshooting curve feels
        // springy on movement but would push opacity past 255.
        // A hidden dock only slides below the screen; it is never hidden as an
        // actor, so its icons keep real positions for minimize animations.
        if (show) {
            this._frame.reactive = true;
            this._dock.ease({translation_y: 0, ...MOTION.arrive});
            this._dock.ease({opacity: 255, ...MOTION.fadeIn});
        } else {
            // Unreactive at once so clicks reach the window while it slides away.
            // St keeps a stale hover when an actor turns unreactive under the
            // pointer (no leave event comes), so clear it by hand.
            this._frame.reactive = false;
            this._frame.hover = false;
            this._tooltip.hide();
            this._hidePreviews();
            this._dock.ease({opacity: 0, ...MOTION.fadeOut});
            this._dock.ease({translation_y: this._frame.height, ...MOTION.leave});
        }
    }

    _onFrameHover() {
        // Only real motion-events count as the pointer moving: St also flips
        // hover by itself when the frame's reactivity changes under a still pointer.
        if (this._frame.hover) {
            this._cancelDebounce('leave');
            this._queueVisibility();
            return;
        }
        // Give the pointer a moment to come back before hiding.
        this._debounce('leave', 500, () => {
            this._edgeArmed = false;
            this._updateVisibility();
        });
    }

    _onEdgeHover() {
        // Hidden under a resting pointer: the edge's motion-event decides instead.
        if (!this._edge.hover || this._restHidden)
            return;
        // A short dwell, so sweeping across the bottom edge doesn't pop the dock.
        this._debounce('edge', 120, () => {
            if (!this._edge?.hover)
                return;
            this._edgeArmed = true;
            this._noteDockMotion();
            this._updateVisibility();
            this._debounce('leave', 1500, () => {
                if (this._frame.hover)
                    return;
                this._edgeArmed = false;
                this._updateVisibility();
            });
        });
    }

    // Shows the dock for a moment (mode switch, card, minimize) even if it would hide.
    _peek(ms) {
        this._peekUntil = Math.max(this._peekUntil, Date.now() + ms);
        this._updateVisibility();
        this._debounce('peek', this._peekUntil - Date.now() + 20, () => this._updateVisibility());
    }

    _endPeek() {
        this._peekUntil = 0;
        this._queueVisibility();
    }

    // ---- cards ----------------------------------------------------------

    _showCard(spec) {
        if (!this._card || Main.layoutManager.primaryMonitor?.inFullscreen)
            return;
        const anchorKey = spec.anchor_mode in this._modes ? spec.anchor_mode : this._mode;
        // Inbox cards point at the app they're about, else at the inbox pill.
        let anchor = spec.anchor_app
            ? this._appButtons.find(e => e.app.get_id() === spec.anchor_app && e.button.visible)?.button ?? null
            : null;
        if (!anchor && spec.kind === 'inbox' && this._inboxClip?.visible)
            anchor = this._inboxButton;
        if (!anchor && spec.kind === 'place' && this._nextClip?.visible)
            anchor = this._nextButton;
        if (!anchor && spec.kind === 'server' && this._serverClip?.visible)
            anchor = this._serverButton;
        if (!anchor && spec.kind === 'command')
            anchor = this._commandButton;
        anchor ??= this._modeEntries.get(anchorKey)?.button ?? this._dock;
        // The pill is still growing in: point at where it ends up.
        const growing = [this._inboxClip, this._nextClip, this._serverClip].some(clip => clip?.get_transition('width'));
        if ((anchor === this._inboxButton || anchor === this._nextButton || anchor === this._serverButton) && growing) {
            this._debounce('card-layout', 340, () => this._showCard(spec));
            return;
        }
        // A switcher rebuilt (or a pill shown) a moment ago has no layout yet;
        // place the card once it does.
        if (!anchor.has_allocation()) {
            const waits = (spec._waits ?? 0) + 1;
            if (waits <= 10)
                this._debounce('card-layout', 50, () => this._showCard({...spec, _waits: waits}));
            return;
        }
        const seconds = Number(spec.timeout ?? CARD_SECONDS);

        this._peek(seconds * 1000);
        // "Stay" acts on the window you were looking at when the card came up, nothing else.
        this._cardWindow = spec.actions?.some(a => a.id === 'stay')
            ? {win: this._focusWindow, title: this._focusWindow?.get_title() ?? ''}
            : null;

        const [anchorX] = anchor.get_transformed_position();
        const [anchorWidth] = anchor.get_transformed_size();
        this._card.open(spec, {
            gicon: this._cardIcon(spec),
            accent: spec.accent ?? this._modeColor(this._modes[anchorKey]),
            anchorX: anchorX + anchorWidth / 2,
            bottomY: this._frame.y,
            monitor: Main.layoutManager.primaryMonitor,
        });
        this._tooltip.hide();
        this._hidePreviews();
        if (spec.kind === 'nudge')
            this._wiggle();
        this._debounce('card', seconds * 1000, () => this._closeCard());
    }

    _cardIcon(spec) {
        if (spec.gicon)
            return spec.gicon; // set by the dock itself, never over D-Bus
        if (spec.kind === 'command')
            return this._sparkle;
        const icon = spec.icon ?? {};
        if (icon.notification_id) {
            const held = this._held.get(icon.notification_id);
            const gicon = held?.notification.gicon ?? held?.notification.source?.icon;
            if (gicon)
                return gicon;
        }
        if (icon.app_id) {
            const app = Shell.AppSystem.get_default().lookup_app(icon.app_id);
            if (app)
                return app.get_icon();
        }
        return new Gio.ThemedIcon({name: icon.name ?? CARD_ICONS[spec.kind] ?? CARD_ICONS.info});
    }

    _closeCard() {
        this._cancelDebounce('card');
        this._card?.close();
        this._cardWindow = null;
        this._endPeek();
    }

    _onCardAction(spec, action) {
        if (!spec)
            return;
        this._emitSignal('CardAction', '(ss)', [String(spec.id ?? ''), action]);
        if (action === 'stay') {
            this._stayFocused();
        } else if (action.startsWith('go:')) {
            const key = action.slice('go:'.length);
            if (spec.notification_id)
                this._surfaceOnSwitch = {mode: key, id: spec.notification_id};
            this._requestMode(key);
        } else if (action.startsWith('mode:')) {
            this._requestMode(action.slice('mode:'.length));
        } else if (action === 'undo-delete') {
            this._undoDelete();
        } else if (action === 'inbox-open') {
            const item = this._inbox.get(spec.inbox_key);
            if (item)
                this._inbox.open(item);
        } else if (action.startsWith('place:')) {
            const what = action.slice('place:'.length);
            if (what === 'undo')
                this._places.undoDelete();
            else if (this._places.suggestion?.id === spec.place_id)
                this._places.act(what);
        }
        this._closeCard();
    }

    // Close the distracting tab (browsers) or get the window out of the way.
    _stayFocused() {
        const target = this._cardWindow;
        const win = global.display.focus_window;
        if (!target?.win || win !== target.win || win.get_title() !== target.title)
            return; // focus moved on since the card appeared; leave it alone
        const app = Shell.WindowTracker.get_default().get_window_app(win);
        if (BROWSER_IDS.has(app?.get_id()))
            this._pressKeys([Clutter.KEY_Control_L, Clutter.KEY_w]);
        else
            win.minimize();
    }

    // The virtual keyboard is made in enable(): events sent right after creating
    // one, or with identical timestamps, were dropped in testing.
    _pressKeys(keyvals) {
        let time = GLib.get_monotonic_time();
        for (const keyval of keyvals)
            this._keyboard.notify_keyval(time += 1000, keyval, Clutter.KeyState.PRESSED);
        for (const keyval of [...keyvals].reverse())
            this._keyboard.notify_keyval(time += 1000, keyval, Clutter.KeyState.RELEASED);
    }

    _wiggle() {
        this._dock.remove_transition('translation-x');
        this._dock.ease({
            translation_x: 6,
            duration: 45,
            mode: AnimationMode.EASE_IN_OUT_QUAD,
            repeatCount: 5,
            autoReverse: true,
            onStopped: () => {
                if (this._dock)
                    this._dock.translation_x = 0;
            },
        });
    }

    // ---- inbox ------------------------------------------------------------

    _inboxSettings() {
        const raw = this._config.inbox ?? {};
        const list = (value, fallback) => Array.isArray(value) ? value.filter(v => typeof v === 'string') : fallback;
        return {
            enabled: raw.enabled ?? INBOX_DEFAULTS.enabled,
            messages: list(raw.messages, INBOX_DEFAULTS.messages),
            agent_apps: list(raw.agent_apps, INBOX_DEFAULTS.agent_apps),
        };
    }

    // A pill at the dock's end: who wants you back, as stacked icons and a count.
    _buildInbox() {
        this._inboxClip = new St.Widget({
            clip_to_allocation: true,
            visible: false,
            y_align: Clutter.ActorAlign.CENTER,
            layout_manager: new Clutter.BinLayout(),
        });
        const row = new St.BoxLayout({style_class: 'deskai-inbox-row'});
        row.add_child(separator());
        this._inboxButton = new St.Button({
            style_class: 'deskai-inbox',
            can_focus: true,
            y_align: Clutter.ActorAlign.CENTER,
            button_mask: St.ButtonMask.ONE | St.ButtonMask.THREE,
        });
        const box = new St.BoxLayout({style_class: 'deskai-inbox-box'});
        this._inboxFaces = new St.Widget({layout_manager: new Clutter.FixedLayout(), y_align: Clutter.ActorAlign.CENTER});
        this._inboxCount = new St.Label({style_class: 'deskai-inbox-count', y_align: Clutter.ActorAlign.CENTER});
        box.add_child(this._inboxFaces);
        box.add_child(this._inboxCount);
        this._inboxButton.set_child(box);
        this._inboxButton.connect('clicked', () => this._openInboxMenu());
        row.add_child(this._inboxButton);
        this._inboxClip.add_child(row);
        this._dock.add_child(this._inboxClip);
        this._addTooltip(this._inboxButton, () => this._t('inboxTooltip', {n: this._inbox.size}));
    }

    _syncInbox(popKey = null) {
        if (!this._inboxClip)
            return;
        const items = this._inbox.list();
        this._syncBadges(popKey, true);
        // Settled elsewhere (read on the phone, dismissed in the tray): its card goes too.
        const shown = this._card?.shown ? this._card.spec : null;
        if (shown?.kind === 'inbox' && !this._inbox.get(shown.inbox_key))
            this._closeCard();

        const FACE = 22, STEP = 13;
        const faces = items.slice(0, 3);
        this._inboxFaces.destroy_all_children();
        // The most pressing one on top, at the left.
        faces.reverse().forEach((item, i) => {
            const face = new St.Bin({
                style_class: 'deskai-inbox-face',
                child: new St.Icon({gicon: item.gicon, fallback_icon_name: 'mail-unread-symbolic', icon_size: 16}),
            });
            face.set_position((faces.length - 1 - i) * STEP, 0);
            this._inboxFaces.add_child(face);
        });
        this._inboxFaces.set_size(faces.length ? FACE + STEP * (faces.length - 1) : 0, FACE);
        this._inboxCount.text = `${items.length}`;
        const top = items[0];
        this._inboxButton.style_class = `deskai-inbox${top && top.kind !== 'message' ? ` ${top.kind}` : ''}`;
        this._showInbox(items.length > 0);
        if (popKey && items.length) {
            this._inboxButton.set_pivot_point(0.5, 0.5);
            this._inboxButton.remove_all_transitions();
            this._inboxButton.scale_x = this._inboxButton.scale_y = 0.85;
            this._inboxButton.ease({scale_x: 1, scale_y: 1, ...MOTION.pop});
        }
    }

    _showInbox(show) {
        this._reveal(this._inboxClip, show, () => this._inbox?.size > 0);
    }

    // Grows a dock pill in from nothing, or folds it away; `wanted` says whether it still should show.
    _reveal(clip, show, wanted) {
        if (show === clip.visible && !clip.get_transition('width'))
            return;
        clip.remove_all_transitions();
        if (show) {
            const from = clip.visible ? clip.width : 0;
            clip.visible = true;
            clip.width = -1;
            const [, natural] = clip.get_preferred_width(-1);
            clip.width = from;
            clip.ease({
                width: natural,
                duration: 300,
                mode: AnimationMode.EASE_OUT_QUAD,
                onComplete: () => {
                    clip.width = -1; // follow the count as it changes
                    this._queueIconGeometry();
                },
            });
        } else {
            clip.ease({
                width: 0,
                duration: 260,
                mode: AnimationMode.EASE_IN_OUT_QUAD,
                onComplete: () => {
                    if (!wanted())
                        clip.visible = false;
                    this._queueIconGeometry();
                },
            });
        }
    }

    // Badges on the dock icons, colored by what's most pressing: an agent that needs you,
    // one that's done, or unread messages (the inbox pill has the counts).
    _syncBadges(popKey, animate) {
        const popApp = popKey ? this._inbox.get(popKey)?.appId : null;
        for (const {app, badge} of this._appButtons) {
            const items = this._inbox.list(app.get_id());
            if (!items.length) {
                badge.visible = false;
                continue;
            }
            const [top] = items;
            const appear = !badge.visible || app.get_id() === popApp;
            // Not a 'message' class: GNOME's theme styles .message (a notification) with padding.
            badge.style_class = `deskai-app-badge${top.kind !== 'message' ? ` ${top.kind}` : ''}`;
            badge.set_style(top.kind === 'message' ? `background-color: ${this._messageColor(top)};` : null);
            badge.visible = true;
            if (appear && animate) {
                badge.set_pivot_point(0.5, 0.5);
                badge.scale_x = badge.scale_y = 0.3;
                badge.ease({scale_x: 1, scale_y: 1, ...MOTION.pop});
            }
        }
    }

    _messageColor(item) {
        return MESSAGE_COLORS[item.label?.toLowerCase()] ?? INBOX_COLORS.message;
    }

    _announceInbox(item) {
        this._syncInbox(item.key);
        const current = this._card?.shown ? this._card.spec : null;
        // Don't bury a question the dock is asking (stay or switch, a held notification, undo),
        // or an agent that needs you under a chat message; the badge and the pill still say it.
        const showing = current?.kind === 'inbox' ? this._inbox.get(current.inbox_key) : null;
        if ((current && current.kind !== 'inbox' && current.actions?.length) ||
            (showing && showing.key !== item.key && RANK[showing.kind] < RANK[item.kind])) {
            this._peek(2500);
            return;
        }
        this._showCard(this._inboxCard(item));
        if (item.kind === 'message')
            item.notification?.playSound?.();
    }

    _inboxSubtitle(item) {
        if (item.kind === 'message')
            return item.count > 1 ? `${item.label} · ${this._t('messages', {n: item.count})}` : item.label;
        const state = this._t(item.kind === 'attention' ? 'agentNeedsYou' : 'agentDone');
        return item.project ? `${item.project} · ${state}` : state;
    }

    _inboxCard(item) {
        return {
            id: `inbox:${item.key}`,
            kind: 'inbox',
            inbox_key: item.key,
            gicon: item.gicon,
            title: item.title || item.label,
            subtitle: this._inboxSubtitle(item),
            text: item.body.length > 160 ? `${item.body.slice(0, 159).trimEnd()}…` : item.body,
            click: 'inbox-open',
            actions: [
                {id: 'inbox-later', label: this._t('later')},
                {id: 'inbox-open', label: this._t('open'), primary: true},
            ],
            anchor_app: item.appId,
            accent: item.kind === 'message' ? this._messageColor(item) : INBOX_COLORS[item.kind],
            timeout: item.kind === 'message' ? 8 : 12,
        };
    }

    _openInboxItem(item) {
        if (this._card?.spec?.inbox_key === item.key)
            this._closeCard();
        this._inbox.open(item);
    }

    _ago(time) {
        const minutes = Math.floor((Date.now() - time) / 60000);
        if (minutes < 1)
            return this._t('justNow');
        if (minutes < 60)
            return this._t('minutesAgo', {n: minutes});
        return this._t('hoursAgo', {n: Math.floor(minutes / 60)});
    }

    _openInboxMenu() {
        this._openMenu(this._inboxButton, menu => {
            const header = new PopupMenu.PopupMenuItem(this._t('inboxTitle'), {reactive: false, can_focus: false});
            header.label.add_style_class_name('deskai-picker-title');
            menu.addMenuItem(header);
            for (const item of this._inbox.list()) {
                const row = new PopupMenu.PopupBaseMenuItem({style_class: 'deskai-inbox-item'});
                row.add_child(new St.Icon({
                    gicon: item.gicon,
                    fallback_icon_name: 'mail-unread-symbolic',
                    icon_size: 28,
                    y_align: Clutter.ActorAlign.CENTER,
                }));
                const text = new St.BoxLayout({
                    ...VERTICAL_BOX,
                    x_expand: true,
                    y_align: Clutter.ActorAlign.CENTER,
                });
                text.add_child(new St.Label({text: item.title || item.label, style_class: 'deskai-inbox-item-title'}));
                text.add_child(new St.Label({
                    text: `${this._inboxSubtitle(item)} · ${this._ago(item.time)}`,
                    style_class: 'deskai-inbox-item-meta',
                }));
                const line = item.body.split('\n').find(l => l.trim()) ?? '';
                if (line)
                    text.add_child(new St.Label({text: line, style_class: 'deskai-inbox-item-body'}));
                row.add_child(text);
                if (item.kind !== 'message') {
                    row.add_child(new St.Widget({
                        style_class: 'deskai-inbox-item-dot',
                        style: `background-color: ${INBOX_COLORS[item.kind]};`,
                        y_align: Clutter.ActorAlign.CENTER,
                    }));
                }
                row.connect('activate', () => this._debounce('menu-action', 0, () => this._openInboxItem(item)));
                menu.addMenuItem(row);
            }
            menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            this._menuItem(menu, this._t('clearAll'), () => this._inbox.clear());
        });
    }

    // ---- places: where you're likely to go next -----------------------------

    // A pill next to the inbox: the folder you closed Files on, the recording or
    // download that just landed, or the folder you usually open in this mode.
    _buildNext() {
        this._nextClip = new St.Widget({
            clip_to_allocation: true,
            visible: false,
            y_align: Clutter.ActorAlign.CENTER,
            layout_manager: new Clutter.BinLayout(),
        });
        const row = new St.BoxLayout({style_class: 'deskai-inbox-row'});
        row.add_child(separator());
        this._nextButton = new St.Button({
            style_class: 'deskai-next',
            can_focus: true,
            y_align: Clutter.ActorAlign.CENTER,
            button_mask: St.ButtonMask.ONE | St.ButtonMask.THREE,
        });
        const box = new St.BoxLayout({style_class: 'deskai-next-box'});
        this._nextIcon = new St.Icon({style_class: 'deskai-next-icon', fallback_icon_name: 'folder-symbolic'});
        this._nextLabel = new St.Label({style_class: 'deskai-next-label', y_align: Clutter.ActorAlign.CENTER});
        box.add_child(this._nextIcon);
        box.add_child(this._nextLabel);
        this._nextButton.set_child(box);
        this._nextButton.connect('clicked', (_button, mouseButton) => {
            this._tooltip.hide();
            const s = this._places?.suggestion;
            if (mouseButton === Clutter.BUTTON_SECONDARY || !s)
                this._openNextMenu();
            else
                this._places.act(s.kind === 'folder' ? 'go' : s.primary);
        });
        row.add_child(this._nextButton);
        this._nextClip.add_child(row);
        this._dock.add_child(this._nextClip);
        this._addTooltip(this._nextButton, () => this._placeTooltip(this._places?.suggestion));
    }

    _syncNext() {
        if (!this._nextClip)
            return;
        const s = this._places?.suggestion ?? null;
        if ((s?.id ?? null) !== this._nextShownId)
            this._tooltip.hide(); // it was about the previous suggestion
        if (s) {
            this._nextIcon.gicon = this._placeIcon(s, true);
            this._nextLabel.text = this._placeLabel(s);
            if (this._nextShownId !== s.id && this._nextClip.visible) {
                this._nextButton.set_pivot_point(0.5, 0.5);
                this._nextButton.scale_x = this._nextButton.scale_y = 0.85;
                this._nextButton.ease({scale_x: 1, scale_y: 1, ...MOTION.pop});
            }
        } else if (this._card?.shown && this._card.spec?.kind === 'place' && this._card.spec.place_id) {
            this._closeCard(); // dealt with elsewhere: its card goes too
        }
        this._nextShownId = s?.id ?? null;
        this._reveal(this._nextClip, Boolean(s), () => Boolean(this._places?.suggestion));
    }

    _placeIcon(s, symbolic) {
        if (s.kind === 'file' && s.gicon)
            return symbolic ? s.symbolic ?? s.gicon : s.gicon;
        try {
            const info = Gio.File.new_for_uri(s.uri).query_info('standard::icon,standard::symbolic-icon',
                Gio.FileQueryInfoFlags.NONE, null);
            return (symbolic ? info.get_symbolic_icon() : info.get_icon()) ?? new Gio.ThemedIcon({name: 'folder-symbolic'});
        } catch {
            return new Gio.ThemedIcon({name: 'folder-symbolic'});
        }
    }

    _placeTime(ms) {
        return GLib.DateTime.new_from_unix_local(Math.floor(ms / 1000)).format(this._t('timeFormat'));
    }

    _placeLabel(s) {
        if (s.trigger === 'recording')
            return this._t('recordingAt', {time: this._placeTime(s.time)});
        if (s.trigger === 'screenshot')
            return this._t('screenshotAt', {time: this._placeTime(s.time)});
        return s.name;
    }

    _placeTooltip(s) {
        if (!s)
            return this._t('recentPlaces');
        if (s.kind === 'folder') {
            return s.reason === 'habit'
                ? this._t('habitFolder', {name: s.name, mode: this._modeLabel(s.mode)})
                : this._t('backTo', {name: s.name});
        }
        return `${this._t(s.trigger)} · ${s.name}`;
    }

    _placeAction(s, action) {
        const media = /^(video|audio)\//.test(Gio.content_type_guess(s.name, null)[0] ?? '');
        return {
            open: this._t(media ? 'play' : 'open'),
            show: this._t('showFolder'),
            delete: this._t('deleteFile'),
            copy: this._t('copyFile'),
        }[action];
    }

    _announcePlace(s) {
        const current = this._card?.shown ? this._card.spec : null;
        const urgent = current?.kind === 'inbox' && this._inbox.get(current.inbox_key)?.kind !== 'message';
        // Don't bury a question the dock is asking, or an agent waiting on you.
        if (urgent || (current && !['inbox', 'place'].includes(current.kind) && current.actions?.length)) {
            this._peek(2500);
            return;
        }
        if (s.kind === 'trashed') {
            this._showCard({
                kind: 'place',
                gicon: new Gio.ThemedIcon({name: 'user-trash-full'}),
                title: this._t('trashed', {name: s.name}),
                actions: [{id: 'place:undo', label: this._t('undo'), primary: true}],
                timeout: 8,
            });
            return;
        }
        // A recording's file name is just its date; a download's name says what it is.
        const named = s.trigger === 'download';
        this._showCard({
            id: `place:${s.id}`,
            kind: 'place',
            place_id: s.id,
            gicon: this._placeIcon(s, false),
            title: named ? s.name : this._t(s.trigger),
            subtitle: `${named ? this._t(s.trigger) : this._placeTime(s.time)} · ${GLib.format_size(s.size)}`,
            text: displayPath(Gio.File.new_for_uri(s.uri).get_parent()?.get_uri() ?? s.uri),
            click: `place:${s.primary}`,
            // Your usual follow-up is the highlighted one.
            actions: ['delete', 'open', 'show'].map(action => ({
                id: `place:${action}`,
                label: this._placeAction(s, action),
                primary: action === s.primary,
            })),
            timeout: 12,
        });
    }

    _openNextMenu() {
        const s = this._places?.suggestion;
        this._openMenu(this._nextButton, menu => {
            if (s?.kind === 'file') {
                const header = new PopupMenu.PopupMenuItem(s.name, {reactive: false, can_focus: false});
                header.label.add_style_class_name('deskai-picker-title');
                menu.addMenuItem(header);
                for (const action of ['open', 'show', 'copy', 'delete'])
                    this._menuItem(menu, this._placeAction(s, action), () => this._places.act(action));
                menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            }
            this._addRecentPlaces(menu);
            if (s) {
                menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
                this._menuItem(menu, this._t('hideSuggestion'), () => this._places.dismiss());
            }
        });
    }

    // "Recent places": the folders you've been in lately, one click away. Returns whether it added any.
    _addRecentPlaces(menu) {
        const folders = this._places?.recentFolders(6) ?? [];
        if (!folders.length)
            return false;
        const header = new PopupMenu.PopupMenuItem(this._t('recentPlaces'), {reactive: false, can_focus: false});
        header.label.add_style_class_name('deskai-picker-title');
        menu.addMenuItem(header);
        for (const uri of folders) {
            const row = new PopupMenu.PopupBaseMenuItem({style_class: 'deskai-inbox-item'});
            row.add_child(new St.Icon({
                gicon: this._placeIcon({kind: 'folder', uri}, true),
                icon_size: 16,
                y_align: Clutter.ActorAlign.CENTER,
            }));
            const text = new St.BoxLayout({...VERTICAL_BOX, x_expand: true});
            text.add_child(new St.Label({text: baseName(uri), style_class: 'deskai-inbox-item-title'}));
            text.add_child(new St.Label({text: displayPath(uri), style_class: 'deskai-inbox-item-meta'}));
            row.add_child(text);
            row.connect('activate', () => this._debounce('menu-action', 0, () => this._places.openFolder(uri)));
            menu.addMenuItem(row);
        }
        return true;
    }

    // ---- panel indicator ----------------------------------------------

    _buildIndicator() {
        this._indicator = new PanelMenu.Button(0.0, 'DeskAI', false);
        const box = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        this._indicatorIcon = new St.Icon({style_class: 'system-status-icon'});
        this._indicatorLabel = new St.Label({y_align: Clutter.ActorAlign.CENTER});
        box.add_child(this._indicatorIcon);
        box.add_child(this._indicatorLabel);
        this._indicator.add_child(box);
        Main.panel.addToStatusArea(this.uuid, this._indicator, 0, 'right');
    }

    _updateIndicator() {
        if (!this._indicator)
            return;
        const current = this._modes[this._mode];
        if (!current)
            return;
        this._indicatorIcon.icon_name = current.icon ?? 'emblem-default-symbolic';
        this._indicatorLabel.text = current.label ?? this._mode;

        // Only rebuild the menu when something it shows changed, so an open menu isn't torn down.
        const signature = JSON.stringify([this._mode, this._held.size, this._brainOnline, languageOf(this._config),
            this._modeOrder.map(k => [this._modes[k].label, this._modes[k].icon])]);
        if (signature === this._menuSignature)
            return;
        this._menuSignature = signature;

        const menu = this._indicator.menu;
        menu.removeAll();
        for (const key of this._modeOrder) {
            const mode = this._modes[key];
            const item = new PopupMenu.PopupImageMenuItem(mode.label ?? key, mode.icon ?? 'emblem-default-symbolic');
            item.setOrnament(key === this._mode ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            item.connect('activate', () => this._requestMode(key));
            menu.addMenuItem(item);
        }
        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        if (this._held.size) {
            const held = new PopupMenu.PopupMenuItem(this._t('heldCount', {n: this._held.size}));
            held.sensitive = false;
            menu.addMenuItem(held);
        }
        const brain = new PopupMenu.PopupMenuItem(this._brainOnline
            ? this._t('brainOnline')
            : this._t('brainOffline'));
        brain.sensitive = false;
        menu.addMenuItem(brain);

        const apps = new PopupMenu.PopupMenuItem(this._t('appsIn', {mode: current.label ?? this._mode}));
        apps.connect('activate', () => this._debounce('menu-action', 250, () => this._openAppPicker()));
        menu.addMenuItem(apps);
        const create = new PopupMenu.PopupMenuItem(this._t('newMode'));
        create.connect('activate', () => this._debounce('menu-action', 0, () => this._openModeDialog(null)));
        menu.addMenuItem(create);

        const edit = new PopupMenu.PopupMenuItem(this._t('editJson'));
        edit.connect('activate', () => {
            try {
                Gio.AppInfo.launch_default_for_uri(this._modesFile.get_uri(), null);
            } catch (e) {
                Main.notifyError('DeskAI', e.message);
            }
        });
        menu.addMenuItem(edit);

        const language = languageOf(this._config);
        const languages = new PopupMenu.PopupSubMenuMenuItem(this._t('language', {name: LANGUAGES[language]}));
        for (const [code, name] of Object.entries(LANGUAGES)) {
            const item = new PopupMenu.PopupMenuItem(name);
            item.setOrnament(code === language ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            item.connect('activate', () => this._debounce('menu-action', 0, () => this._setLanguage(code)));
            languages.menu.addMenuItem(item);
        }
        menu.addMenuItem(languages);
    }

    // Saved to modes.json, so the brain words its cards in the same language.
    _setLanguage(code) {
        if (!(code in LANGUAGES) || languageOf(this._config) === code)
            return;
        this._config.language = code;
        this._t = translator(code);
        this._saveConfig();
        this._buildSwitcher();
        this._updateIndicator();
    }

    // ---- focus sensing --------------------------------------------------

    _watchFocus() {
        global.display.connectObject('notify::focus-window', () => this._onFocusWindowChanged(), this);
        this._onFocusWindowChanged();
    }

    _onFocusWindowChanged() {
        this._focusWindow?.disconnectObject(this);
        this._focusWindow = null;

        const win = global.display.focus_window;
        if (isAppWindow(win)) {
            this._focusWindow = win;
            win.connectObject(
                'notify::title', () => this._reportFocus(),
                'position-changed', () => this._queueVisibility(),
                'size-changed', () => this._queueVisibility(),
                'notify::minimized', () => this._queueVisibility(),
                'unmanaged', () => {
                    this._focusWindow?.disconnectObject(this);
                    this._focusWindow = null;
                    this._queueVisibility();
                },
                this);
        }
        this._inbox.sawWindow(win);
        this._places?.sawWindow(win);
        this._reportFocus();
        this._queueVisibility();
    }

    _reportFocus() {
        const win = this._focusWindow;
        const app = win ? Shell.WindowTracker.get_default().get_window_app(win) : null;
        const focus = {
            appId: app?.get_id() ?? '',
            appName: app?.get_name() ?? '',
            wmClass: win?.get_wm_class() ?? '',
            title: win?.get_title() ?? '',
        };
        if (Object.keys(focus).every(k => focus[k] === this._focus[k]))
            return;
        this._focus = focus;
        this._inbox.sawWindow(win); // e.g. switching to the WhatsApp tab
        this._places?.sawWindow(win);
        this._places?.sawTitle(focus.title);
        this._emitSignal('FocusChanged', '(ssss)', [focus.appId, focus.appName, focus.wmClass, focus.title]);
    }

    // ---- notifications --------------------------------------------------

    _watchNotifications() {
        // Keep "quiet" notifications out of the banner queue. Their urgency is left alone:
        // changing it after GNOME has listed a notification miscounts its urgent ones
        // and scrambles the notification list.
        const tray = Main.messageTray;
        const original = tray._updateState;
        const quiet = this._quiet;
        this._updateStateOriginal = original;
        tray._updateState = function (...args) {
            const queued = this._notificationQueue.length;
            this._notificationQueue = this._notificationQueue.filter(n => !quiet.has(n));
            if (this._notificationQueue.length !== queued)
                this.emit('queue-changed');
            return original.apply(this, args);
        };
        for (const source of Main.messageTray.getSources())
            this._watchSource(source);
        Main.messageTray.connectObject('source-added', (_tray, source) => this._watchSource(source), this);
    }

    _watchSource(source) {
        if (this._watchedSources.has(source))
            return;
        this._watchedSources.add(source);
        // 'notification-added' fires right before 'notification-request-banner',
        // so marking a notification quiet here keeps its banner from ever showing.
        source.connectObject(
            'notification-added', (src, notification) => this._onNotificationAdded(src, notification),
            'destroy', () => this._watchedSources.delete(source),
            this);
    }

    _onNotificationAdded(source, notification) {
        const {Urgency} = MessageTray;
        // GNOME's "Screencast recorded" (no picture, unlike a screenshot's): the dock's card
        // for the recording comes a moment later and takes its place.
        if (source.iconName === 'screenshooter-symbolic' && !notification.gicon && notification.body) {
            this._quiet.add(notification);
            return;
        }
        // Messages and AI agents go to the dock's inbox instead of a banner, in every mode.
        if (!notification.forFeedback && notification.urgency !== Urgency.CRITICAL) {
            const settings = this._inboxSettings();
            if (settings.enabled && this._inbox.takeNotification(source, notification, settings))
                return;
            // You're in that AI app, so the inbox has nothing to add, but the session asking
            // may not be the one on screen. A focus mode doesn't get to hold that back.
            if (settings.enabled && settings.agent_apps.includes(sourceAppId(source)))
                return;
        }
        const policy = this._modes[this._mode]?.notifications?.policy ?? 'all';
        if (policy === 'all' || notification.forFeedback)
            return;
        if (notification.urgency === Urgency.CRITICAL || notification.urgency === Urgency.LOW)
            return;

        const app = source.title ?? '';
        const title = notification.title ?? '';
        const body = notification.body ?? '';
        if (policy === 'filter' && this._passesFilter(app, title, body))
            return;

        const id = ++this._heldSeq;
        this._held.set(id, {id, notification, app});
        this._quiet.add(notification);
        notification.connectObject('destroy', () => {
            this._held.delete(id);
            this._syncModeButtons(false);
            this._updateIndicator();
        }, this);
        this._syncModeButtons(false);
        this._updateIndicator();
        this._emitSignal('NotificationHeld', '(usss)', [id, app, title, body]);
    }

    _passesFilter(app, title, body) {
        const rules = this._modes[this._mode]?.notifications ?? {};
        const appLower = app.toLowerCase();
        const text = `${app}\n${title}\n${body}`.toLowerCase();
        return (rules.allow_apps ?? []).some(a => appLower.includes(a.toLowerCase())) ||
            (rules.keywords ?? []).some(k => text.includes(k.toLowerCase()));
    }

    _surfaceHeld(id) {
        const held = this._held.get(id);
        if (!held)
            return false;
        this._held.delete(id);
        held.notification.disconnectObject(this);
        this._quiet.delete(held.notification);
        held.notification.source?.emit('notification-request-banner', held.notification);
        this._syncModeButtons(false);
        this._updateIndicator();
        return true;
    }

    // Lets held notifications go (they stay in the tray) without showing banners.
    _releaseHeld() {
        const held = [...this._held.values()];
        for (const h of held) {
            h.notification.disconnectObject(this);
            this._quiet.delete(h.notification);
        }
        this._held.clear();
        return held;
    }

    // ---- servers on the desk ----------------------------------------------

    _buildServers() {
        this._servers = [];
        this._serverClip = new St.Widget({
            clip_to_allocation: true,
            visible: false,
            y_align: Clutter.ActorAlign.CENTER,
            layout_manager: new Clutter.BinLayout(),
        });
        const row = new St.BoxLayout({style_class: 'deskai-inbox-row'});
        row.add_child(separator());
        this._serverButton = new St.Button({style_class: 'deskai-add', can_focus: true, y_align: Clutter.ActorAlign.CENTER});
        const box = new St.Widget({layout_manager: new Clutter.BinLayout()});
        box.add_child(new St.Icon({icon_name: 'network-server-symbolic', style_class: 'deskai-add-icon'}));
        this._serverDot = new St.Widget({
            style_class: 'deskai-server-dot',
            x_expand: true,
            y_expand: true,
            x_align: Clutter.ActorAlign.END,
            y_align: Clutter.ActorAlign.START,
        });
        box.add_child(this._serverDot);
        this._serverButton.set_child(box);
        this._serverButton.connect('clicked', () => this._openServerMenu());
        row.add_child(this._serverButton);
        this._serverClip.add_child(row);
        this._dock.add_child(this._serverClip);
        this._addTooltip(this._serverButton, () => this._t('serversTooltip', {
            up: this._servers.filter(s => s.state === 'up').length,
            down: this._servers.filter(s => s.state === 'down').length,
        }));
    }

    _setServers(servers) {
        const worse = servers.some(s => s.state === 'down') && !this._servers.some(s => s.state === 'down');
        this._servers = servers;
        if (this._serverClip)
            this._syncServers(worse);
    }

    _syncServers(pop) {
        const state = ['down', 'starting', 'up'].find(wanted => this._servers.some(s => s.state === wanted));
        if (state)
            this._serverDot.set_style(`background-color: ${SERVER_COLORS[state]};`);
        this._reveal(this._serverClip, this._servers.length > 0, () => this._servers.length > 0);
        if (pop) {
            this._serverButton.set_pivot_point(0.5, 0.5);
            this._serverButton.remove_all_transitions();
            this._serverButton.scale_x = this._serverButton.scale_y = 0.8;
            this._serverButton.ease({scale_x: 1, scale_y: 1, ...MOTION.pop});
        }
    }

    _serverAction(op, unit) {
        this._emitSignal('CardAction', '(ss)', ['servers', `server:${op}:${unit}`]);
    }

    _serverRow(menu, server) {
        const started = server.kind === 'manual';
        const canOpen = (server.kind === 'server' || started) && server.port && server.state === 'up';
        const row = new PopupMenu.PopupBaseMenuItem({style_class: 'deskai-inbox-item', reactive: Boolean(canOpen)});
        row.add_child(new St.Widget({
            style_class: 'deskai-inbox-item-dot',
            style: `background-color: ${SERVER_COLORS[server.state]};`,
            y_align: Clutter.ActorAlign.CENTER,
        }));
        const text = new St.BoxLayout({
            ...VERTICAL_BOX,
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        text.add_child(new St.Label({text: server.label, style_class: 'deskai-inbox-item-title'}));
        const state = started
            ? this._t('serverYours')
            : this._t({up: 'serverUp', starting: 'serverStarting', down: 'serverDown'}[server.state]);
        text.add_child(new St.Label({
            text: server.project ? `${state} · ${server.project}` : state,
            style_class: 'deskai-inbox-item-meta',
        }));
        row.add_child(text);
        const buttons = [
            ['view-refresh-symbolic', 'serverRestart', 'restart'],
            ['utilities-terminal-symbolic', 'serverLog', 'log'],
            server.state === 'down'
                ? ['edit-clear-symbolic', 'serverForget', 'forget']
                : ['media-playback-stop-symbolic', 'serverStop', 'stop'],
        ].filter(([, , op]) => !started || op !== 'log'); // its log is in your terminal
        for (const [icon, label, op] of buttons) {
            const button = new St.Button({
                style_class: 'deskai-server-action',
                accessible_name: this._t(label),
                can_focus: true,
                y_align: Clutter.ActorAlign.CENTER,
                child: new St.Icon({icon_name: icon, icon_size: 16}),
            });
            button.connect('clicked', () => {
                this._tooltip.hide();
                menu.close();
                this._serverAction(op, server.unit);
            });
            this._addTooltip(button, () => this._t(label));
            row.add_child(button);
        }
        if (canOpen)
            row.connect('activate', () => this._serverAction('open', server.unit));
        menu.addMenuItem(row);
    }

    _openServerMenu() {
        this._openMenu(this._serverButton, menu => {
            const header = new PopupMenu.PopupMenuItem(this._t('serversTitle'), {reactive: false, can_focus: false});
            header.label.add_style_class_name('deskai-picker-title');
            menu.addMenuItem(header);
            for (const server of this._servers)
                this._serverRow(menu, server);
            if (this._servers.length > 1) {
                menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
                this._menuItem(menu, this._t('restartAll'), () => {
                    for (const server of this._servers)
                        this._serverAction('restart', server.unit);
                });
            }
        });
    }

    // ---- music --------------------------------------------------------------

    // A pill that's there while something plays: what it is, and the buttons to control it.
    _buildMusic() {
        this._musicWanted = false;
        this._musicCoverUrl = null; // reset so the cover reloads after a disable/enable (e.g. screen lock)
        this._musicClip = new St.Widget({
            clip_to_allocation: true,
            visible: false,
            y_align: Clutter.ActorAlign.CENTER,
            layout_manager: new Clutter.BinLayout(),
        });
        const row = new St.BoxLayout({style_class: 'deskai-inbox-row'});
        row.add_child(separator());
        const pill = new St.BoxLayout({style_class: 'deskai-music', y_align: Clutter.ActorAlign.CENTER});

        this._musicTrack = new St.Button({style_class: 'deskai-music-track', can_focus: true, y_align: Clutter.ActorAlign.CENTER});
        const track = new St.BoxLayout();
        this._musicArt = new St.Widget({
            style_class: 'deskai-music-art',
            layout_manager: new Clutter.BinLayout(),
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._musicAppIcon = new St.Icon({icon_size: MUSIC_COVER_SIZE});
        this._musicArt.add_child(this._musicAppIcon);
        const text = new St.BoxLayout({
            style_class: 'deskai-music-text',
            ...VERTICAL_BOX,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._musicTitle = new St.Label({style_class: 'deskai-music-title'});
        this._musicArtist = new St.Label({style_class: 'deskai-music-artist'});
        text.add_child(this._musicTitle);
        text.add_child(this._musicArtist);
        track.add_child(this._musicArt);
        track.add_child(text);
        this._musicTrack.set_child(track);
        this._musicTrack.connect('clicked', () => {
            this._tooltip.hide();
            this._music?.current?.raise();
        });
        this._addTooltip(this._musicTrack, () => this._musicTrack.accessible_name);
        pill.add_child(this._musicTrack);

        this._musicPrevious = this._musicControl(player => player.previous(), () => this._t('musicPrevious'));
        this._musicPlay = this._musicControl(player => player.playPause(), () => this._musicPlayLabel());
        this._musicNext = this._musicControl(player => player.next(), () => this._t('musicNext'));
        this._musicPrevious.child.icon_name = 'media-skip-backward-symbolic';
        this._musicNext.child.icon_name = 'media-skip-forward-symbolic';
        pill.add_child(this._musicPrevious);
        pill.add_child(this._musicPlay);
        pill.add_child(this._musicNext);

        row.add_child(pill);
        this._musicClip.add_child(row);
        this._dock.add_child(this._musicClip);
    }

    _musicControl(act, label) {
        const button = new St.Button({
            style_class: 'deskai-music-control',
            can_focus: true,
            y_align: Clutter.ActorAlign.CENTER,
            child: new St.Icon({icon_size: 16}),
        });
        // Dimmed with a style, not by making it unreactive: a button that stops reacting under
        // the pointer, on the last song of a playlist, never hears that the pointer has left.
        const usable = () => !button.has_style_class_name('off');
        button.connect('clicked', () => {
            this._tooltip.hide();
            const player = this._music?.current;
            if (player && usable())
                act(player);
        });
        this._addTooltip(button, () => (usable() ? label() : null));
        return button;
    }

    _musicPlayLabel() {
        return this._t(this._music?.current?.status === 'Playing' ? 'musicPause' : 'musicPlay');
    }

    _onMusicChanged() {
        if (!this._musicClip)
            return;
        const wanted = Boolean(this._music?.current);
        if (wanted && this._musicClip.visible) {
            this._cancelDebounce('music');
            this._musicWanted = true;
            this._syncMusic();
        } else if (wanted !== this._musicWanted) {
            this._musicWanted = wanted;
            this._debounce('music', wanted ? MUSIC_SHOW_MS : MUSIC_HIDE_MS, () => this._syncMusic());
        }
    }

    _syncMusic() {
        if (!this._musicClip)
            return;
        const player = this._music?.current;
        if (player) {
            const {title, artists} = describe(player);
            this._musicTitle.text = title;
            this._musicArtist.text = artists;
            this._musicArtist.visible = Boolean(artists);
            this._musicTrack.accessible_name = artists ? `${title} · ${artists}` : title;
            this._musicAppIcon.gicon = player.app?.get_icon() ?? new Gio.ThemedIcon({name: 'audio-x-generic-symbolic'});
            this._setMusicCover(player.trackCoverUrl);
            this._musicPlay.child.icon_name = player.status === 'Playing'
                ? 'media-playback-pause-symbolic' : 'media-playback-start-symbolic';
            this._musicPlay.accessible_name = this._musicPlayLabel();
            this._musicPrevious.accessible_name = this._t('musicPrevious');
            this._musicNext.accessible_name = this._t('musicNext');
            this._musicPrevious[player.canGoPrevious ? 'remove_style_class_name' : 'add_style_class_name']('off');
            this._musicNext[player.canGoNext ? 'remove_style_class_name' : 'add_style_class_name']('off');
        }
        this._reveal(this._musicClip, Boolean(player), () => Boolean(this._music?.current));
    }

    // The app's icon shows until the cover has loaded, and stays if it never does.
    _setMusicCover(url) {
        if (url === this._musicCoverUrl)
            return;
        this._musicCoverUrl = url;
        this._musicCover?.destroy();
        this._musicCover = null;
        this._musicAppIcon.visible = true;
        if (!url)
            return;
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        this._musicCover = St.TextureCache.get_default().load_file_async(
            Gio.File.new_for_uri(url), MUSIC_COVER_SIZE, -1, scale, this._frame.get_resource_scale());
        this._musicCover.content_gravity = Clutter.ContentGravity.RESIZE_ASPECT; // a video's thumbnail is wide
        this._musicCover.connect('notify::content', () => {
            this._musicAppIcon.visible = false;
        });
        this._musicArt.add_child(this._musicCover);
    }

    // ---- the command bar --------------------------------------------------

    _buildCommand() {
        this._sparkle = Gio.icon_new_for_string(GLib.build_filenamev([this.path, 'icons', 'deskai-sparkle-symbolic.svg']));
        this._commandButton = new St.Button({style_class: 'deskai-add', can_focus: true, y_align: Clutter.ActorAlign.CENTER});
        this._commandButton.set_child(new St.Icon({gicon: this._sparkle, style_class: 'deskai-add-icon'}));
        this._commandButton.connect('clicked', () => this._openCommandBar());
        this._dock.add_child(this._commandButton);
        this._addTooltip(this._commandButton, () => this._t('commandTooltip'));
        const history = readJson(this._commandsFile);
        this._commandBar = new CommandBar({
            gicon: this._sparkle,
            history: Array.isArray(history) ? history.filter(text => typeof text === 'string') : [],
            onSubmit: text => this._submitCommand(text),
            onHistory: commands => {
                try {
                    writeJson(this._commandsFile, commands);
                } catch (e) {
                    console.warn(`DeskAI: cannot save the command history: ${e.message}`);
                }
            },
            onClose: () => this._endPeek(),
        });
    }

    _openCommandBar() {
        if (Main.layoutManager.primaryMonitor?.inFullscreen)
            return;
        this._tooltip.hide();
        this._hidePreviews();
        this._closeCard();
        this._peek(60000);
        this._commandBar.open({
            hint: this._t('commandHint'),
            monitor: Main.layoutManager.primaryMonitor,
            bottomY: this._frame.y,
            buttons: this._modes[this._mode]?.focus && this._focusWindow ? this._labelButtons() : [],
        });
    }

    // Saying what the window in front of you is. These words mean the same to the brain in any language.
    _labelButtons() {
        return [
            {label: this._t('labelWork'), menuLabel: this._t('labelWorkMenu'), command: 'this is work'},
            {label: this._t('labelDistraction'), menuLabel: this._t('labelDistractionMenu'), command: 'this is a distraction'},
        ];
    }

    _submitCommand(text) {
        if (!this._brainOnline) {
            this._showCard({id: 'command', kind: 'command', title: this._t('commandOffline'), timeout: 8});
            return;
        }
        this._emitSignal('Command', '(s)', [text]);
        this._showCard({id: 'command', kind: 'command', title: this._t('commandThinking'), text, busy: true, timeout: 120});
    }

    // Super+/ from anywhere, even with the dock hidden.
    _grabCommandKey() {
        const action = global.display.grab_accelerator('<Super>slash', Meta.KeyBindingFlags.IGNORE_AUTOREPEAT);
        if (action === Meta.KeyBindingAction.NONE)
            return;
        this._commandAction = action;
        this._commandBinding = Meta.external_binding_name_for_action(action);
        Main.wm.allowKeybinding(this._commandBinding, Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW);
        this._commandKeyId = global.display.connect('accelerator-activated', (_display, activated) => {
            if (activated === this._commandAction)
                this._openCommandBar();
        });
    }

    _releaseCommandKey() {
        if (!this._commandAction)
            return;
        global.display.disconnect(this._commandKeyId);
        Main.wm.allowKeybinding(this._commandBinding, Shell.ActionMode.NONE);
        global.display.ungrab_accelerator(this._commandAction);
        this._commandAction = 0;
    }

    // ---- D-Bus ----------------------------------------------------------

    _exportDBus() {
        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, {
            SetMode: mode => this._setMode(mode),
            RequestMode: mode => this._requestMode(mode),
            GetState: () => JSON.stringify(this._snapshot()),
            // For the brain's snapshots of the desk, and putting it back after a logout.
            GetWindows: () => JSON.stringify(this._desk.list()),
            PlaceWindow: (id, json) => {
                try {
                    return this._desk.place(id, JSON.parse(json));
                } catch (e) {
                    console.warn(`DeskAI: can't place window ${id}: ${e.message}`);
                    return false;
                }
            },
            SelectTab: (id, index) => this._desk.selectTab(id, index),
            RaiseWindows: json => {
                try {
                    return this._desk.raise(JSON.parse(json));
                } catch (e) {
                    console.warn(`DeskAI: can't raise windows: ${e.message}`);
                    return 0;
                }
            },
            Toast: (text, kind, action, actionLabel) => this._showCard({
                kind,
                text,
                actions: action ? [{id: action, label: actionLabel || this._t('ok'), primary: true}] : [],
            }),
            ShowCard: json => {
                try {
                    this._showCard(JSON.parse(json));
                } catch (e) {
                    console.warn(`DeskAI: bad card: ${e.message}`);
                }
            },
            SurfaceNotification: id => this._surfaceHeld(id),
            // From `deskai hook`: an AI agent in some window is done or needs you.
            Inbox: json => {
                try {
                    if (this._inboxSettings().enabled)
                        this._inbox.takeHook(JSON.parse(json));
                } catch (e) {
                    console.warn(`DeskAI: bad inbox event: ${e.message}`);
                }
            },
            // From the brain: the servers it's watching.
            Servers: json => {
                try {
                    this._setServers(JSON.parse(json));
                } catch (e) {
                    console.warn(`DeskAI: bad servers: ${e.message}`);
                }
            },
            MinimizeFocused: () => this._focusWindow?.minimize(),
            Reload: () => this._reloadConfig(),
        });
        this._dbusImpl.export(Gio.DBus.session, OBJECT_PATH);
        this._busNameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session, BUS_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
        this._brainWatchId = Gio.bus_watch_name(
            Gio.BusType.SESSION, BRAIN_NAME, Gio.BusNameWatcherFlags.NONE,
            () => this._setBrainOnline(true),
            () => this._setBrainOnline(false));
    }

    _setBrainOnline(online) {
        this._brainOnline = online;
        this._updateIndicator();
    }

    _emitSignal(name, signature, args) {
        this._dbusImpl?.emit_signal(name, new GLib.Variant(signature, args));
    }

    _snapshot() {
        return {
            mode: this._mode,
            modes: this._modeOrder,
            pending: this._pending ? {target: this._pending.target, remaining: this._pending.deadline - Date.now()} : null,
            focus: this._focus,
            held: [...this._held].map(([id, h]) => ({id, app: h.app, title: h.notification.title ?? ''})),
            brain: this._brainOnline,
            next: this._places?.suggestion
                ? (({kind, reason, trigger, name, primary}) => ({kind, reason, trigger, name, primary}))(this._places.suggestion)
                : null,
            inbox: this._inbox.list().map(item => ({
                key: item.key, kind: item.kind, title: item.title, app: item.appId, count: item.count,
                window: item.window?.get_title() ?? null,
            })),
            servers: this._servers ?? [],
            // Everything _wantVisible() looks at, for "why won't it hide?".
            dock: {
                shown: this._dockShown,
                hover: this._frame?.hover ?? false,
                pointerRestMs: Date.now() - this._lastDockMotion,
                restHidden: this._restHidden,
                edgeArmed: this._edgeArmed,
                card: this._card?.shown ?? false,
                menu: this._menu?.isOpen ?? false,
                drag: Boolean(this._drag),
                peekMs: Math.max(0, this._peekUntil - Date.now()),
                appWindowOpen: this._appWindowOpen(),
                overview: this._inOverview,
            },
        };
    }
}
