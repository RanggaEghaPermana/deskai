// The dock's inbox: messages and AI agents that want you back.
//
// Items come from two places. A notification from a messaging app (WhatsApp,
// even as a Chrome tab) or an AI app (the Claude app, ChatGPT…) skips its
// banner and lands here. Agents in a terminal (Claude Code, Codex, Gemini CLI)
// report through `deskai hook`, which sends its process tree along so the
// item knows the window it came from. Opening an item takes you there, and
// looking at that window yourself settles it too.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export const INBOX_DEFAULTS = {
    enabled: true,
    // A notification mentioning one of these (in its app, title, or the site a browser names) is a message.
    messages: ['WhatsApp'],
    // Notifications from these apps are an AI saying it's done or stuck.
    agent_apps: ['com.anthropic.Claude.desktop', 'chatgpt.desktop', 'antigravity.desktop',
        'antigravity-ide.desktop', 'cursor.desktop'],
};

// Agents that report through hooks: their name, and apps that stand for them
// (the icon to show, and where to go when their terminal can't be found).
const AGENTS = {
    'claude-code': {label: 'Claude Code', apps: ['com.anthropic.Claude.desktop']},
    'codex': {label: 'Codex', apps: ['chatgpt.desktop', 'codex.desktop']},
    'gemini': {label: 'Gemini CLI', apps: ['antigravity.desktop', 'antigravity-ide.desktop']},
};

const ATTENTION_RE = /permission|approv|waiting for|needs your|requires your|input needed|izin|persetujuan|menunggu/i;
export const RANK = {attention: 0, done: 1, message: 2};

// Notification bodies are markup, and Chrome puts the site first: "web.whatsapp.com\n\nHi!".
export function cleanBody(body) {
    return (body ?? '')
        .replace(/<[^>]*>/g, '')
        .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
        .replace(/&amp;/g, '&')
        .replace(/^\s*[\w-]+(\.[\w-]+)+\s*\n\s*\n/, '')
        .trim();
}

function appFor(id) {
    return id ? Shell.AppSystem.get_default().lookup_app(id) : null;
}

function windowApp(win) {
    return win ? Shell.WindowTracker.get_default().get_window_app(win) : null;
}

function sourceAppId(source) {
    return source.app?.get_id?.() ?? (source._appId ? `${source._appId}.desktop` : '');
}

// Every workspace, most recently used first.
function liveWindows() {
    return global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null);
}

export class Inbox {
    // changed(): the list changed; announce(item): something new to show;
    // hush(notification): keep its banner from showing.
    constructor({changed, announce, hush}) {
        this._items = new Map();
        this._sessions = new Map(); // agent session → the window it runs in
        this._changed = changed;
        this._announce = announce;
        this._hush = hush;
    }

    destroy() {
        for (const item of this._items.values())
            item.notification?.disconnectObject(this);
        this._items.clear();
        this._sessions.clear();
    }

    get size() {
        return this._items.size;
    }

    get(key) {
        return this._items.get(key) ?? null;
    }

    // Most pressing first: needs you, then done, then messages; newest first within each.
    list(appId = null) {
        return [...this._items.values()]
            .filter(item => !appId || item.appId === appId)
            .sort((a, b) => RANK[a.kind] - RANK[b.kind] || b.time - a.time);
    }

    remove(key) {
        if (!this._drop(key))
            return false;
        this._changed();
        return true;
    }

    clear() {
        if (!this._items.size)
            return;
        for (const key of [...this._items.keys()])
            this._drop(key);
        this._changed();
    }

    _drop(key) {
        const item = this._items.get(key);
        if (!item)
            return false;
        this._items.delete(key);
        item.notification?.disconnectObject(this);
        return true;
    }

    _put(fields) {
        const old = this._items.get(fields.key);
        if (old?.notification && old.notification !== fields.notification)
            old.notification.disconnectObject(this);
        const item = {...old, ...fields, count: (old?.count ?? 0) + 1, time: Date.now()};
        this._items.set(item.key, item);
        this._changed();
        this._announce(item);
        return item;
    }

    // ---- notifications ------------------------------------------------

    // Takes a notification that belongs here and keeps its banner from showing.
    // Returns the item, or null to leave the notification alone.
    takeNotification(source, notification, settings) {
        const appId = sourceAppId(source);
        const title = notification.title ?? '';
        const raw = `${source.title ?? ''}\n${title}\n${notification.body ?? ''}\n${appId}`.toLowerCase();
        const message = settings.messages.find(k => k && raw.includes(k.toLowerCase()));
        let fields;
        if (message) {
            // You're already in that chat: the app shows it, nothing to add.
            if (this._looking(win => (win.get_title() ?? '').toLowerCase().includes(message.toLowerCase())))
                return null;
            fields = {key: `msg:${appId}:${message}:${title}`, kind: 'message', label: message, match: message};
        } else if (settings.agent_apps.includes(appId)) {
            if (this._looking(win => windowApp(win)?.get_id() === appId))
                return null;
            const kind = ATTENTION_RE.test(`${title} ${notification.body ?? ''}`) ? 'attention' : 'done';
            fields = {key: `app:${appId}`, kind, label: appFor(appId)?.get_name() ?? source.title ?? '', match: ''};
        } else {
            return null;
        }

        this._hush(notification);
        const item = this._put({
            ...fields,
            appId,
            title,
            project: fields.kind === 'message' ? '' : fields.label,
            body: cleanBody(notification.body),
            gicon: notification.gicon ?? source.icon ?? appFor(appId)?.get_icon() ?? null,
            notification,
            window: null,
        });
        this._follow(item.key, notification);
        return item;
    }

    _follow(key, notification) {
        notification.disconnectObject(this);
        notification.connectObject(
            // Apps replace a notification in place for the next message in the same chat.
            'notify::body', () => {
                const item = this._items.get(key);
                if (item?.notification === notification)
                    this._put({...item, body: cleanBody(notification.body), gicon: notification.gicon ?? item.gicon});
            },
            // Dismissed in the tray, or the app took it back (you read it elsewhere).
            'destroy', () => {
                if (this._items.get(key)?.notification === notification)
                    this.remove(key);
            },
            this);
    }

    _looking(test) {
        const win = global.display.focus_window;
        return Boolean(win) && test(win);
    }

    // ---- agent hooks ----------------------------------------------------

    // event: {source, event: start|prompt|done|attention|end, session, cwd, text, pids}
    takeHook(event) {
        const agent = AGENTS[event.source] ?? {label: event.source || 'Agent', apps: []};
        const session = `${event.source}:${event.session || event.cwd || 'default'}`;
        const key = `agent:${session}`;
        const pids = new Set((event.pids ?? []).map(Number));

        if (event.event === 'start' || event.event === 'prompt') {
            this._sessions.set(session, this._findWindow(session, pids, true));
            this.remove(key);
            return null;
        }
        if (event.event === 'end') {
            this._sessions.delete(session);
            this.remove(key);
            return null;
        }
        if (event.event !== 'done' && event.event !== 'attention')
            return null;

        const win = this._findWindow(session, pids, false);
        const app = windowApp(win) ?? agent.apps.map(appFor).find(a => a?.get_windows().length) ?? null;
        if (this._looking(focused => focused === win || (!win && windowApp(focused) === app))) {
            this.remove(key); // it's right in front of you
            return null;
        }
        const iconApp = agent.apps.map(appFor).find(Boolean) ?? app;
        return this._put({
            key,
            kind: event.event,
            label: agent.label,
            title: agent.label,
            project: event.cwd ? GLib.path_get_basename(event.cwd) : '',
            body: String(event.text ?? '').trim(),
            appId: app?.get_id() ?? '',
            gicon: iconApp?.get_icon() ?? new Gio.ThemedIcon({name: 'utilities-terminal-symbolic'}),
            notification: null,
            window: win,
            match: '',
        });
    }

    // The window an agent runs in. When you send it a prompt it's the one in
    // front (checked against the windows it runs under, if any); later, the
    // one remembered then, or the most recent window it runs under.
    _findWindow(session, pids, atPrompt) {
        const windows = liveWindows();
        const known = this._sessions.get(session);
        const own = windows.filter(win => pids.has(win.get_pid()));
        const focused = global.display.focus_window;
        if (atPrompt) {
            if (focused && (own.includes(focused) || !own.length))
                return focused;
            return own.includes(known) ? known : own[0] ?? null;
        }
        if (known && windows.includes(known) && (!own.length || own.includes(known)))
            return known;
        return own[0] ?? null;
    }

    // ---- settling -------------------------------------------------------

    // You looked at the window an item points to: it's settled.
    sawWindow(win) {
        if (!win || !this._items.size)
            return;
        const appId = windowApp(win)?.get_id();
        const title = (win.get_title() ?? '').toLowerCase();
        let changed = false;
        for (const item of [...this._items.values()]) {
            let seen;
            if (item.window)
                seen = item.window === win;
            else if (item.match)
                seen = title.includes(item.match.toLowerCase());
            else
                seen = Boolean(item.appId) && item.appId === appId;
            if (seen)
                changed = this._drop(item.key) || changed;
        }
        if (changed)
            this._changed();
    }

    // Takes you to what the item is about: the chat, the terminal, the app.
    open(item) {
        const current = this._items.get(item.key) === item;
        this.remove(item.key);
        if (Main.overview.visible)
            Main.overview.hide();
        // The app's own click action knows best (Chrome opens the right tab).
        if (current && item.notification) {
            item.notification.activate();
            return;
        }
        if (item.window && liveWindows().includes(item.window)) {
            Main.activateWindow(item.window);
            return;
        }
        const app = appFor(item.appId);
        const match = item.match?.toLowerCase();
        const win = match && app?.get_windows().find(w => (w.get_title() ?? '').toLowerCase().includes(match));
        if (win)
            Main.activateWindow(win);
        else
            app?.activate();
    }
}
