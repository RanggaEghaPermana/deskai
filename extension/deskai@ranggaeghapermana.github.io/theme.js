// Which look DeskAI wears, from modes.json's "appearance": Liquid Glass (the default) in light or dark,
// or the classic look it had before. Surfaces (the dock, cards, menus, dialogs…) are registered here
// once; a change of theme or of GNOME's color scheme restyles them all in place.

import Gio from 'gi://Gio';

import {Glass} from './glass.js';

export const APPEARANCE_DEFAULTS = {
    theme: 'glass',
    scheme: 'auto',
    glass: 'clear',
    blur_windows: true,
    rim: true,
    reduce_transparency: false,
};

const CLASSES = ['deskai-lg', 'deskai-lg-light', 'deskai-lg-dark', 'deskai-lg-tinted', 'deskai-lg-sheet', 'deskai-lg-solid'];

const rgb = (r, g, b, a) => [r / 255, g / 255, b / 255, a];

// The material, as the design's spec sheet has it. blur: the frost's sigma in pixels, like a CSS blur;
// bend: how many pixels from past the edge the rim shows; clarity: how sharp the view is through it;
// adapt: the darkest and the brightest the view through it may get, so the ink on it stays readable
// whatever is behind (glass_adapt in glass.js). Dark ink needs a light view, and light ink a dark one.
// sheet is for menus and dialogs: thicker glass of the same kind, since they're all text.
const MATERIAL = {
    light: {
        clear: {tint: rgb(255, 255, 255, 0.18), blur: 6, saturation: 1.6, brightness: 1.06, rim: 0.85, sheen: 0.16, bend: 22, clarity: 1, adapt: [0.74, 1]},
        tinted: {tint: rgb(246, 246, 249, 0.74), blur: 18, saturation: 1.6, brightness: 1, rim: 0.85, sheen: 0.12, bend: 14, clarity: 0.6, adapt: [0.5, 1]},
        sheet: {tint: rgb(246, 246, 249, 0.5), blur: 20, saturation: 1.6, brightness: 1, rim: 0.85, sheen: 0.12, bend: 14, clarity: 0.6, adapt: [0.82, 1]},
        solid: {tint: rgb(242, 242, 246, 1), blur: 0, saturation: 1, brightness: 1, rim: 0, sheen: 0, bend: 0},
        shadow: 0.2,
        line: rgb(60, 60, 67, 0.18),
        float: 0.76, // the least a card's or the command bar's view may be: their text is smaller than the dock's
    },
    dark: {
        clear: {tint: rgb(22, 22, 26, 0.2), blur: 6, saturation: 1.5, brightness: 0.92, rim: 0.6, sheen: 0.06, bend: 22, clarity: 1, adapt: [0, 0.28]},
        tinted: {tint: rgb(32, 32, 37, 0.78), blur: 18, saturation: 1.5, brightness: 1, rim: 0.55, sheen: 0.05, bend: 14, clarity: 0.6, adapt: [0, 0.5]},
        sheet: {tint: rgb(30, 30, 35, 0.5), blur: 20, saturation: 1.5, brightness: 1, rim: 0.55, sheen: 0.05, bend: 14, clarity: 0.6, adapt: [0, 0.24]},
        solid: {tint: rgb(44, 44, 49, 1), blur: 0, saturation: 1, brightness: 1, rim: 0, sheen: 0, bend: 0},
        shadow: 0.45,
        line: rgb(235, 235, 245, 0.16),
        float: 0.26, // the most
    },
};

export class Theme {
    constructor(onSchemeChanged) {
        this._onSchemeChanged = onSchemeChanged;
        this._glass = new Glass(() => this._restyleAll()); // live glass broke: restyle onto the wallpaper
        this._surfaces = new Map();
        this._appearance = {...APPEARANCE_DEFAULTS};
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._interface.connectObject('changed::color-scheme', () => {
            if (this._appearance.scheme !== 'auto')
                return;
            this._restyleAll();
            this._onSchemeChanged?.();
        }, this);
    }

    get appearance() {
        return this._appearance;
    }

    get isGlass() {
        return this._appearance.theme !== 'classic';
    }

    get dark() {
        const {scheme} = this._appearance;
        if (scheme === 'auto')
            return this._interface.get_string('color-scheme') === 'prefer-dark';
        return scheme === 'dark';
    }

    // appearance: modes.json's "appearance", whole or in part; what's missing is the default. Returns whether
    // anything changed: modes.json is read again on every save of anything in it, and restyling every surface
    // for the look it already has is work for nothing.
    set(appearance) {
        const wanted = {...APPEARANCE_DEFAULTS, ...(appearance ?? {})};
        if (this._applied && Object.keys(wanted).length === Object.keys(this._appearance).length &&
            Object.entries(wanted).every(([key, value]) => this._appearance[key] === value))
            return false;
        this._applied = true; // the first one always goes through: it's what turns the live glass on or off
        this._appearance = wanted;
        this._restyleAll();
        return true;
    }

    // kind: "bar" for the dock, "float" for what pops over the desktop (cards, the tooltip, the command
    // bar), "sheet" for menus and dialogs, which are always tinted so their text reads over anything.
    // radius: the glass's corners; a capsule just asks for more than half its height.
    // shapeOf: the actor inside `actor` with the glass's shape, when that's not all of it.
    add(actor, {kind = 'float', radius = 22, shapeOf = null} = {}) {
        if (!actor || this._surfaces.has(actor))
            return;
        this._surfaces.set(actor, {kind, radius, shapeOf, layer: null});
        actor.connectObject('destroy', () => this._forget(actor), this);
        this._restyle(actor);
    }

    // A capsule in `actor`'s glass that follows target() and stretches as it moves (the active mode);
    // a null target takes it away.
    select(actor, target, color) {
        const surface = this._surfaces.get(actor);
        if (!surface)
            return;
        surface.selection = target ? {target, color} : null;
        if (surface.layer?.alive)
            surface.layer.select(target, color);
    }

    // The pointer over `actor`, in stage coordinates, lighting its glass; null when it left.
    point(actor, x, y) {
        const layer = this._surfaces.get(actor)?.layer;
        if (layer?.alive)
            layer.point(x, y);
    }

    // After `actor` was raised to the top of its parent: its glass goes back right under it.
    restack(actor) {
        const layer = this._surfaces.get(actor)?.layer;
        const parent = actor.get_parent();
        if (layer?.alive && layer.get_parent() === parent && actor.get_previous_sibling() !== layer)
            parent.set_child_below_sibling(layer, actor);
    }

    // Takes the glass off a surface about to be destroyed.
    release(actor) {
        this._forget(actor);
        for (const name of CLASSES)
            actor?.remove_style_class_name(name);
    }

    _forget(actor) {
        const surface = this._surfaces.get(actor);
        if (!surface)
            return;
        this._surfaces.delete(actor);
        this._glass.detach(surface.layer);
        actor.disconnectObject(this);
    }

    _restyleAll() {
        this._glass.setLive(this._appearance.blur_windows);
        for (const actor of this._surfaces.keys())
            this._restyle(actor);
        this._glass.trimWalls();
    }

    _variant(kind) {
        const {glass, reduce_transparency: solid} = this._appearance;
        if (solid)
            return 'solid';
        if (glass === 'tinted')
            return 'tinted';
        return kind === 'sheet' ? 'sheet' : 'clear';
    }

    _restyle(actor) {
        const surface = this._surfaces.get(actor);
        for (const name of CLASSES)
            actor.remove_style_class_name(name);
        if (!this.isGlass) {
            this._glass.detach(surface.layer);
            surface.layer = null;
            return;
        }
        const scheme = this.dark ? 'dark' : 'light';
        const variant = this._variant(surface.kind);
        actor.add_style_class_name('deskai-lg');
        actor.add_style_class_name(`deskai-lg-${scheme}`);
        if (variant !== 'clear')
            actor.add_style_class_name(`deskai-lg-${variant}`);

        if (!surface.layer?.alive) {
            surface.layer = this._glass.attach(actor, surface.shapeOf, surface.kind === 'bar');
            if (surface.selection)
                surface.layer?.select(surface.selection.target, surface.selection.color);
        }
        surface.layer?.setLook(this._look(scheme, variant, surface.radius, surface.kind));
    }

    _look(scheme, variant, radius, kind) {
        const material = MATERIAL[scheme];
        const {rim} = this._appearance;
        const look = {...material[variant], radius, shadow: material.shadow, backdrop: variant !== 'solid'};
        // Text over clear glass needs a little more frost than the dock's icons do (like a notification).
        if (variant === 'clear' && kind === 'float') {
            look.tint = [...look.tint.slice(0, 3), look.tint[3] + 0.16];
            look.blur = 10;
            look.adapt = scheme === 'dark' ? [0, material.float] : [material.float, 1];
        }
        if (!rim || variant === 'solid') {
            look.rim = 0;
            look.sheen = 0;
            look.line = material.line;
        } else {
            look.line = [0, 0, 0, 0];
        }
        return look;
    }

    destroy() {
        this._interface.disconnectObject(this);
        for (const [actor, surface] of this._surfaces) {
            actor.disconnectObject(this);
            for (const name of CLASSES)
                actor.remove_style_class_name(name);
            this._glass.detach(surface.layer);
        }
        this._surfaces.clear();
        this._glass.destroy();
    }
}
