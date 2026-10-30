// Places: the dock remembers where you go and offers the next step.
//
// It follows the folders open in Files (Nautilus reports them over D-Bus), the
// screen recording you just made, the screenshot you just took and the file you
// just downloaded. One suggestion at a time sits in the dock: the folder you
// closed Files on, the recording or download that just landed, or a folder you
// habitually open in the mode you just entered.
//
// Habits are plain counts of what you did next. After a recording or a
// download: did you play it, show it in its folder, or delete it? After
// entering a mode: which folder did you open first? The card's main button is
// your usual choice, and a folder you open in most sessions of a mode is
// offered when you enter it. Everything stays in ~/.local/state/deskai/places.json.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export const FILES_APP = 'org.gnome.Nautilus.desktop';
const FOLLOW_UP_MS = 10 * 60 * 1000; // what you do within 10 minutes is the follow-up
const FILE_SUGGESTION_MS = 30 * 60 * 1000;
const FOLDER_SUGGESTION_MS = 4 * 60 * 60 * 1000;
const MAX_FOLDERS = 200;
// Browsers' in-progress names, hidden files, editor backups.
const TEMP_RE = /\.(crdownload|part|partial|download|tmp|opdownload)$|^\.|~$/i;
const IN_PROGRESS = ['.crdownload', '.part'];
const ACTIONS = ['open', 'show', 'delete'];
// Before there's a habit: you go see where it landed.
const DEFAULT_PRIMARY = {recording: 'show', download: 'show', screenshot: 'show'};
const FILE_ATTRIBUTES = 'standard::type,standard::size,standard::icon,standard::symbolic-icon,time::modified';

function launchContext() {
    return global.create_app_launch_context(0, -1);
}

export function displayPath(uri) {
    const file = Gio.File.new_for_uri(uri);
    const path = file.get_path();
    if (!path)
        return uri;
    const home = GLib.get_home_dir();
    return path === home ? '~' : path.startsWith(`${home}/`) ? `~${path.slice(home.length)}` : path;
}

export function baseName(uri) {
    return Gio.File.new_for_uri(uri).get_basename() ?? uri;
}

function parentUri(uri) {
    return Gio.File.new_for_uri(uri).get_parent()?.get_uri() ?? '';
}

export class Places {
    // changed(): the suggestion or the history changed; announce(suggestion): worth a card.
    constructor({changed, announce}) {
        this._changed = changed;
        this._announce = announce;
    }

    enable() {
        this._stateFile = Gio.File.new_for_path(
            GLib.build_filenamev([GLib.get_user_state_dir(), 'deskai', 'places.json']));
        this._data = this._load();
        this._home = Gio.File.new_for_path(GLib.get_home_dir()).get_uri();
        this._open = new Map(); // folder uri → open since (ms)
        this._lastActive = null; // the folder you were in last
        this._hadWindows = false;
        this._pending = []; // triggers waiting for their follow-up
        this._suggestion = null;
        this._seq = 0;
        this._timeouts = new Map();
        this._announced = new Set();
        this._cancellable = new Gio.Cancellable();

        this._watchFiles();
        this._watchDownloads();
        this._watchCapture();
        this._tick = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, 60, () => {
            this._expire();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        this._cancellable?.cancel();
        if (this._tick)
            GLib.source_remove(this._tick);
        this._tick = 0;
        for (const id of this._timeouts.values())
            GLib.source_remove(id);
        this._timeouts.clear();
        this._files?.disconnectObject(this);
        this._files = null;
        this._downloads?.cancel();
        this._downloads = null;
        Main.screenshotUI?.disconnectObject(this);
        this._account(Date.now());
        this._save();
    }

    get suggestion() {
        return this._suggestion;
    }

    // ---- what you do ------------------------------------------------------

    // Folders you've been in lately, newest first, that still exist and aren't open now.
    recentFolders(limit = 6) {
        return Object.entries(this._data.folders)
            .filter(([uri]) => !this._open.has(uri))
            .sort((a, b) => b[1].last - a[1].last)
            .map(([uri]) => uri)
            .filter(uri => Gio.File.new_for_uri(uri).query_exists(null))
            .slice(0, limit);
    }

    // What the dock suggests doing with a file now: your usual follow-up, or a sensible default.
    primary(trigger) {
        const counts = this._data.habits[trigger] ?? {};
        const [best, count] = ACTIONS.map(a => [a, counts[a] ?? 0]).sort((a, b) => b[1] - a[1])[0];
        return count >= 2 ? best : DEFAULT_PRIMARY[trigger] ?? 'show';
    }

    // Acting on the current suggestion: open, show (in its folder), delete, copy, or go (a folder).
    act(action) {
        const s = this._suggestion;
        if (!s)
            return;
        if (s.kind === 'folder') {
            this.openFolder(s.uri);
            this._clear();
            return;
        }
        if (action !== 'copy')
            this._learn(s, action);
        switch (action) {
        case 'open':
            this._launch(s.uri);
            break;
        case 'show':
            this.showItem(s.uri);
            break;
        case 'delete':
            this._trash(s);
            return;
        case 'copy':
            // As a file list, so Files (and apps that take pasted files) paste the file itself.
            St.Clipboard.get_default().set_content(St.ClipboardType.CLIPBOARD, 'text/uri-list',
                new GLib.Bytes(new TextEncoder().encode(`${s.uri}\r\n`)));
            return;
        }
        this._clear();
    }

    dismiss() {
        this._clear();
    }

    openFolder(uri) {
        this._launch(uri);
    }

    // Opens the file's folder in Files with the file selected.
    showItem(uri) {
        const info = Gio.DesktopAppInfo.new(FILES_APP);
        const token = info ? launchContext().get_startup_notify_id(info, []) : '';
        Gio.DBus.session.call('org.freedesktop.FileManager1', '/org/freedesktop/FileManager1',
            'org.freedesktop.FileManager1', 'ShowItems', new GLib.Variant('(ass)', [[uri], token ?? '']),
            null, Gio.DBusCallFlags.NONE, 5000, null, (bus, result) => {
                try {
                    bus.call_finish(result);
                } catch (e) {
                    console.warn(`DeskAI: ShowItems failed, opening the folder: ${e.message}`);
                    this._launch(parentUri(uri));
                }
            });
    }

    _launch(uri) {
        try {
            Gio.AppInfo.launch_default_for_uri(uri, launchContext());
        } catch (e) {
            Main.notifyError('DeskAI', e.message);
        }
    }

    _trash(s) {
        const file = Gio.File.new_for_uri(s.uri);
        file.trash_async(GLib.PRIORITY_DEFAULT, null, (_f, result) => {
            try {
                file.trash_finish(result);
            } catch (e) {
                Main.notifyError('DeskAI', e.message);
                return;
            }
            this._trashed = s;
            this._clear();
            this._announce({...s, kind: 'trashed'});
        });
    }

    // Puts the file you just deleted back where it was. Files in your home go to
    // ~/.local/share/Trash: files/<name> plus info/<name>.trashinfo saying where it came from.
    undoDelete() {
        const s = this._trashed;
        this._trashed = null;
        const path = s ? Gio.File.new_for_uri(s.uri).get_path() : null;
        if (!path)
            return;
        const trash = GLib.build_filenamev([GLib.get_user_data_dir(), 'Trash']);
        try {
            const infoDir = Gio.File.new_for_path(GLib.build_filenamev([trash, 'info']));
            const children = infoDir.enumerate_children('standard::name,time::modified', Gio.FileQueryInfoFlags.NONE, null);
            let best = null;
            for (let info = children.next_file(null); info; info = children.next_file(null)) {
                const name = info.get_name();
                if (!name.endsWith('.trashinfo'))
                    continue;
                const keyFile = new GLib.KeyFile();
                keyFile.load_from_file(GLib.build_filenamev([trash, 'info', name]), GLib.KeyFileFlags.NONE);
                const orig = GLib.uri_unescape_string(keyFile.get_string('Trash Info', 'Path'), null);
                const when = info.get_modification_date_time()?.to_unix() ?? 0;
                if (orig === path && (!best || when > best.when))
                    best = {name, when};
            }
            children.close(null);
            if (!best)
                return;
            const stored = best.name.slice(0, -'.trashinfo'.length);
            this._announced.add(`${s.uri}:${s.size}`);
            Gio.File.new_for_path(GLib.build_filenamev([trash, 'files', stored]))
                .move(Gio.File.new_for_path(path), Gio.FileCopyFlags.NONE, null, null);
            Gio.File.new_for_path(GLib.build_filenamev([trash, 'info', best.name])).delete(null);
        } catch (e) {
            Main.notifyError('DeskAI', e.message);
            return;
        }
        this._suggest({...s, id: ++this._seq, until: Date.now() + FILE_SUGGESTION_MS}, false);
    }

    // ---- where you are --------------------------------------------------------

    // Looking at a Files window tells which of its folders is in front (its title is the folder's name).
    sawWindow(win) {
        if (!win || Shell.WindowTracker.get_default().get_window_app(win)?.get_id() !== FILES_APP)
            return;
        const title = win.get_title() ?? '';
        const match = [...this._open.keys()].find(uri => baseName(uri) === title);
        if (match)
            this._lastActive = match;
    }

    // Opening a file yourself (its name shows up in a window title) counts as "open".
    sawTitle(title) {
        const s = this._suggestion;
        if (s?.kind !== 'file' || !title)
            return;
        const stem = s.name.replace(/\.[^.]+$/, '');
        if (stem.length >= 4 && title.includes(stem)) {
            this._learn(s, 'open');
            this._clear();
        }
    }

    onMode(key) {
        const trigger = `mode:${key}`;
        this._trigger(trigger, null);
        const folder = this._habitFolder(trigger);
        if (folder && !this._open.has(folder)) {
            this._suggest({kind: 'folder', reason: 'habit', mode: key, uri: folder, name: baseName(folder),
                id: ++this._seq, until: Date.now() + FILE_SUGGESTION_MS}, false);
        } else if (this._suggestion?.reason === 'habit') {
            this._clear();
        }
    }

    // A folder you opened first in at least half the sessions of a mode (and 3 times at least).
    _habitFolder(trigger) {
        const counts = this._data.habits[trigger];
        if (!counts?.n)
            return null;
        const [best] = Object.entries(counts)
            .filter(([k]) => k.startsWith('folder:'))
            .sort((a, b) => b[1] - a[1]);
        if (!best || best[1] < 3 || best[1] / counts.n < 0.5)
            return null;
        const uri = best[0].slice('folder:'.length);
        return Gio.File.new_for_uri(uri).query_exists(null) ? uri : null;
    }

    _countable(uri) {
        return uri.startsWith('file://') && uri !== this._home && uri !== `${this._home}/`;
    }

    _watchFiles() {
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, null,
            'org.freedesktop.FileManager1', '/org/freedesktop/FileManager1', 'org.freedesktop.FileManager1',
            this._cancellable, (_o, result) => {
                try {
                    this._files = Gio.DBusProxy.new_for_bus_finish(result);
                } catch (e) {
                    if (!e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        console.warn(`DeskAI: can't follow Files: ${e.message}`);
                    return;
                }
                this._files.connectObject(
                    'g-properties-changed', () => this._syncOpen(false),
                    'notify::g-name-owner', () => this._syncOpen(false),
                    this);
                this._syncOpen(true);
            });
    }

    _syncOpen(initial) {
        const windows = this._files?.get_cached_property('OpenWindowsWithLocations')?.deepUnpack() ?? {};
        const anyWindow = Object.keys(windows).length > 0;
        const current = new Set(Object.values(windows).flat().filter(uri => this._countable(uri)));
        const now = Date.now();
        for (const [uri, since] of this._open) {
            if (!current.has(uri)) {
                this._addTime(uri, now - since);
                this._open.delete(uri);
            }
        }
        for (const uri of current) {
            if (this._open.has(uri))
                continue;
            this._open.set(uri, now);
            this._lastActive = uri;
            if (!initial)
                this._visit(uri, now);
        }

        if (anyWindow && this._suggestion?.kind === 'folder' && this._suggestion.reason === 'closed')
            this._clear(); // Files is open again: you're already on your way
        if (this._hadWindows && !anyWindow && this._lastActive) {
            // You closed Files: keep the way back to where you were.
            this._suggest({kind: 'folder', reason: 'closed', uri: this._lastActive, name: baseName(this._lastActive),
                id: ++this._seq, until: now + FOLDER_SUGGESTION_MS}, false);
        }
        this._hadWindows = anyWindow;
        this._changed();
    }

    _visit(uri, now) {
        const entry = this._data.folders[uri] ??= {visits: 0, last: 0, seconds: 0};
        entry.visits++;
        entry.last = now;
        // The first folder you open after a trigger is its follow-up.
        for (const p of this._pending) {
            if (p.resolved || now - p.at > FOLLOW_UP_MS)
                continue;
            p.resolved = true;
            const counts = this._data.habits[p.trigger] ??= {n: 0};
            const outcome = p.uri && parentUri(p.uri) === uri ? 'show' : `folder:${uri}`;
            counts[outcome] = (counts[outcome] ?? 0) + 1;
        }
        const s = this._suggestion;
        if (s && (s.kind === 'folder' ? s.uri === uri : parentUri(s.uri) === uri))
            this._clear(); // you went there yourself
        this._trim();
        this._queueSave();
    }

    _addTime(uri, ms) {
        const entry = this._data.folders[uri];
        if (entry) {
            entry.seconds += Math.round(ms / 1000);
            entry.last = Date.now();
        }
    }

    _account(now) {
        for (const [uri, since] of this._open) {
            this._addTime(uri, now - since);
            this._open.set(uri, now);
        }
    }

    // ---- what just landed -------------------------------------------------------

    _watchDownloads() {
        const dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD);
        if (!dir)
            return;
        try {
            this._downloads = Gio.File.new_for_path(dir).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, null);
        } catch (e) {
            console.warn(`DeskAI: can't watch ${dir}: ${e.message}`);
            return;
        }
        const E = Gio.FileMonitorEvent;
        this._downloads.connect('changed', (_m, file, other, event) => {
            // Browsers write "name.crdownload" / "name.part" and rename it when the download is done.
            let target = null;
            if (event === E.RENAMED)
                target = other;
            else if (event === E.CREATED || event === E.CHANGES_DONE_HINT || event === E.MOVED_IN)
                target = file;
            if (target && !TEMP_RE.test(target.get_basename() ?? ''))
                this._later(target.get_uri(), 1500, () => this._checkDownload(target));
        });
    }

    _checkDownload(file) {
        let info;
        try {
            info = file.query_info(FILE_ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, null);
        } catch {
            return; // gone again
        }
        if (info.get_file_type() !== Gio.FileType.REGULAR || info.get_size() === 0)
            return;
        const name = file.get_basename();
        if (IN_PROGRESS.some(suffix => file.get_parent().get_child(name + suffix).query_exists(null)))
            return; // still coming in
        // Something you moved here from elsewhere isn't a download.
        const age = GLib.DateTime.new_now_local().to_unix() - (info.get_modification_date_time()?.to_unix() ?? 0);
        if (age > 120)
            return;
        this._fileEvent('download', file, info, true);
    }

    _watchCapture() {
        const ui = Main.screenshotUI;
        if (!ui)
            return;
        ui.connectObject(
            'notify::screencast-in-progress', () => {
                if (ui.screencast_in_progress)
                    return;
                // The recorder sets the path when it starts and finishes the file just after stopping.
                this._later('screencast', 1500, () => {
                    if (ui._screencastPath)
                        this._fileEvent('recording', Gio.File.new_for_path(ui._screencastPath), null, true);
                });
            },
            // GNOME already shows a banner with the picture; the dock just keeps it at hand.
            'screenshot-taken', (_ui, file) => this._fileEvent('screenshot', file, null, false),
            this);
    }

    _fileEvent(trigger, file, info, announce) {
        try {
            info ??= file.query_info(FILE_ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, null);
        } catch {
            return;
        }
        const uri = file.get_uri();
        const key = `${uri}:${info.get_size()}`;
        if (this._announced.has(key))
            return;
        this._announced.add(key);
        this._trigger(trigger, uri);
        // The card shows the picture (or the file type's icon); the dock pill, the plain symbol.
        const gicon = trigger === 'screenshot' ? new Gio.FileIcon({file}) : info.get_icon();
        this._suggest({
            kind: 'file', trigger, uri, name: file.get_basename(), size: info.get_size(), gicon,
            symbolic: info.get_symbolic_icon(),
            time: Date.now(), primary: this.primary(trigger), id: ++this._seq, until: Date.now() + FILE_SUGGESTION_MS,
        }, announce);
    }

    // ---- habits --------------------------------------------------------------

    _trigger(trigger, uri) {
        const now = Date.now();
        this._pending = this._pending.filter(p => !p.resolved && now - p.at < FOLLOW_UP_MS);
        this._pending.push({trigger, uri, at: now, resolved: false});
        const counts = this._data.habits[trigger] ??= {n: 0};
        counts.n++;
        this._queueSave();
    }

    _learn(s, outcome) {
        const p = this._pending.find(q => !q.resolved && q.trigger === s.trigger && q.uri === s.uri);
        if (!p)
            return; // already counted (you went to its folder yourself, say)
        p.resolved = true;
        const counts = this._data.habits[s.trigger] ??= {n: 0};
        counts[outcome] = (counts[outcome] ?? 0) + 1;
        this._queueSave();
    }

    // ---- the suggestion ----------------------------------------------------------

    _suggest(s, announce) {
        this._suggestion = s;
        this._changed();
        if (announce)
            this._announce(s);
    }

    _clear() {
        if (!this._suggestion)
            return;
        this._suggestion = null;
        this._changed();
    }

    _expire() {
        const s = this._suggestion;
        if (s && (Date.now() > s.until || (s.uri && !Gio.File.new_for_uri(s.uri).query_exists(null))))
            this._clear();
        this._account(Date.now());
        this._queueSave();
    }

    _later(key, ms, fn) {
        const old = this._timeouts.get(key);
        if (old)
            GLib.source_remove(old);
        this._timeouts.set(key, GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._timeouts.delete(key);
            fn();
            return GLib.SOURCE_REMOVE;
        }));
    }

    // ---- storage -------------------------------------------------------------------

    _load() {
        try {
            const [, bytes] = this._stateFile.load_contents(null);
            const data = JSON.parse(new TextDecoder().decode(bytes));
            if (data && typeof data.folders === 'object' && typeof data.habits === 'object')
                return data;
        } catch (e) {
            if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND))
                console.warn(`DeskAI: places.json unreadable, starting over: ${e.message}`);
        }
        return {version: 1, folders: {}, habits: {}};
    }

    _trim() {
        const entries = Object.entries(this._data.folders);
        if (entries.length <= MAX_FOLDERS)
            return;
        entries.sort((a, b) => b[1].last - a[1].last);
        this._data.folders = Object.fromEntries(entries.slice(0, MAX_FOLDERS));
    }

    _queueSave() {
        this._later('save', 5000, () => this._save());
    }

    _save() {
        try {
            this._stateFile.get_parent().make_directory_with_parents(null);
        } catch (e) {
            if (!e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.EXISTS))
                return;
        }
        try {
            const bytes = new TextEncoder().encode(`${JSON.stringify(this._data)}\n`);
            this._stateFile.replace_contents(bytes, null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        } catch (e) {
            console.warn(`DeskAI: can't save places.json: ${e.message}`);
        }
    }
}
