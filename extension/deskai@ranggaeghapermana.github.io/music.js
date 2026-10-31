// What's playing, from GNOME Shell's own list of media players: anything that speaks MPRIS,
// like Spotify, VLC, or a browser for whatever one of its tabs is playing.

import GLib from 'gi://GLib';

import {MprisSource} from 'resource:///org/gnome/shell/ui/mpris.js';

// Paused this long, a player stops taking up room on the dock until it plays again.
export const PAUSED_KEEP_S = 30 * 60;

// The shell's list has no way to stop, so there's one for as long as the shell runs.
let source = null;

function statusOf(player) {
    try {
        return player.status;
    } catch {
        return 'Stopped'; // left the bus, and the list hasn't heard yet
    }
}

function secondsNow() {
    return GLib.get_monotonic_time() / 1e6;
}

// A file with no tags still has a name.
function fileName(player) {
    try {
        const url = player._playerProxy.Metadata['xesam:url'].deepUnpack();
        if (url.startsWith('file:'))
            return GLib.uri_unescape_string(url.split('/').pop(), null).replace(/\.[^.]+$/, '');
    } catch {
        // no such thing, or a player that says it some other way
    }
    return '';
}

// What to call what's playing. The shell's list fills in "Unknown title" and "Unknown artist",
// which say less than the name of a file or a player.
export function describe(player) {
    const app = player.source.title ?? '';
    const known = (value, unknown) => (value && value !== _(unknown) ? value : '');
    const title = known(player.trackTitle, 'Unknown title') || fileName(player) || app;
    const artists = player.trackArtists.filter(artist => known(artist, 'Unknown artist')).join(', ');
    return {title, artists: artists || (title === app ? '' : app)};
}

export class Music {
    constructor(changed) {
        this._players = new Map(); // player → {status, since}
        this._wake = 0;
        source ??= new MprisSource();
        source.connectObject(
            'player-added', (_source, player) => this._add(player),
            'player-removed', (_source, player) => this._remove(player),
            this);
        source.players.forEach(player => this._add(player));
        this._changed = changed;
    }

    // The one to show: the last one started of those playing, or else the last one paused.
    get current() {
        const newest = status => [...this._players]
            .filter(([, state]) => state.status === status)
            .reduce((best, entry) => (!best || entry[1].since > best[1].since ? entry : best), null);
        const playing = newest('Playing');
        if (playing)
            return playing[0];
        const paused = newest('Paused');
        return paused && secondsNow() - paused[1].since < PAUSED_KEEP_S ? paused[0] : null;
    }

    _add(player) {
        this._players.set(player, {status: null, since: 0});
        player.connectObject('changed', () => this._update(), this);
        this._update();
    }

    _remove(player) {
        player.disconnectObject(this);
        this._players.delete(player);
        this._update();
    }

    _update() {
        const now = secondsNow();
        for (const [player, state] of this._players) {
            const status = statusOf(player);
            if (status !== state.status)
                Object.assign(state, {status, since: now});
        }
        this._wakeWhenPauseEnds();
        this._changed?.();
    }

    _wakeWhenPauseEnds() {
        if (this._wake)
            GLib.source_remove(this._wake);
        this._wake = 0;
        const paused = [...this._players.values()].filter(state => state.status === 'Paused').map(state => state.since);
        if (!paused.length)
            return;
        const left = Math.max(...paused) + PAUSED_KEEP_S - secondsNow();
        this._wake = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, Math.max(1, Math.ceil(left)), () => {
            this._wake = 0;
            this._changed?.();
            return GLib.SOURCE_REMOVE;
        });
    }

    destroy() {
        if (this._wake)
            GLib.source_remove(this._wake);
        this._wake = 0;
        source.disconnectObject(this);
        for (const player of this._players.keys())
            player.disconnectObject(this);
        this._players.clear();
    }
}
