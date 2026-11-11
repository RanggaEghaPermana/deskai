// Liquid Glass: what sits behind a glass surface (the dock, a card, a menu…) and makes it look like glass.
//
// Each surface gets a layer of its own, behind it, that draws the glass in full: what's on the screen
// behind it, then one shader that rounds the shape, bends the view through a bevel along the edge like
// a lens, frosts the middle, lifts the color, holds the view to a brightness the ink on it can be read
// against, tints, lights the rim from the top left in the colors around it, and casts the shadow. The
// surface itself stays an ordinary St widget with a clear background, painted on top of its layer.
//
// What's behind comes from the screen itself ("live"): while the layer is painted, everything under it
// already is, so the layer copies those pixels out of the frame being drawn, only where something
// changed, keeps them sharp for the bevel and blurred at half size for the frost. GNOME's own blur
// can't do this with round corners or two pictures at once. Without windows ("wall", for the setting
// that turns off blurring windows, and if the live path ever fails) it shows a blurred copy of the
// wallpaper instead.

import Clutter from 'gi://Clutter';
import Cogl from 'gi://Cogl';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Mtk from 'gi://Mtk';
import Shell from 'gi://Shell';

import * as Background from 'resource:///org/gnome/shell/ui/background.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {Follower, SPRINGS, animationsOn} from './spring.js';

// Room around the glass for its shadow, for the bevel to see past the edge, and for the blur.
export const MARGIN = 40;

const FRAGMENT = Shell.SnippetHook?.FRAGMENT ?? Cogl.SnippetHook.FRAGMENT; // GNOME 46 kept the hooks in Shell

// Mutter 48 renamed cogl_blit_framebuffer() to cogl_framebuffer_blit().
const blit = Cogl.Framebuffer.prototype.blit
    ? (source, ...args) => source.blit(...args)
    : (source, ...args) => Cogl.blit_framebuffer(source, ...args);

// The stage view being painted, between its before-paint and after-paint. Outside that (a screenshot or
// a screencast painting the stage on its own), layers show what they last saw instead of copying.
let paintedView = null;

// Paint nodes and pipelines copy what they're given: one box and one color serve every paint, which
// keeps garbage out of the paint (GNOME 46 skips JS paint callbacks while it collects).
const BOX = new Clutter.ActorBox();
const COLOR = new Cogl.Color();

const COMMON = `
uniform vec2 size;
uniform vec4 shape;
uniform float radius;
uniform vec4 tint;
uniform float saturation;
uniform float brightness;
uniform float backdrop;
uniform float rim;
uniform float sheen;
uniform float bend;
uniform float clarity;
uniform vec2 adapt;
uniform float shadow;
uniform vec4 line;
uniform vec4 valid;
uniform vec4 pill;
uniform vec4 pill_color;
uniform float pill_power;
uniform vec3 pointer;
uniform vec4 sat;
uniform float sat_r;
uniform float sat_k;

float glass_box(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + vec2(r);
    return length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - r;
}

float glass_smin(float a, float b, float k) {
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

float glass_sat(vec2 at) {
    vec2 sh = sat.zw * 0.5;
    return glass_box(at - (sat.xy + sh), sh, min(sat_r, min(sh.x, sh.y)));
}

// The glass's outline at a point: its own shape, melted into a satellite's (a menu, a card) while one
// is budding off it or flowing back in.
float glass_field(vec2 at) {
    vec2 hs = shape.zw * 0.5;
    float d = glass_box(at - (shape.xy + hs), hs, min(radius, min(hs.x, hs.y)));
    return sat_k > 0.5 ? glass_smin(d, glass_sat(at), sat_k) : d;
}

// How bright a color is to the eye, as the gray that's as bright: its luminance in linear light, back in
// the screen's own scale. Weighing the screen's values as they are calls a strong purple or green darker
// than it is, and ink is read against what the eye sees.
float glass_light(vec3 c) {
    return pow(dot(pow(c, vec3(2.2)), vec3(0.2126, 0.7152, 0.0722)), 1.0 / 2.2);
}

// Glass takes on what's behind it, and what's written on it doesn't change: white ink on a dark dock is
// lost once a white page is under it. So the view is held to a range the ink reads against: adapt is the
// darkest and the brightest it may be. It's measured at each point as it's drawn, the sharp view through
// the bevel included, so a white line behind the edge is held too. Past the range it's pressed back toward
// it and not cut off, so a white window behind still shows as the lighter part.
vec3 glass_adapt(vec3 c) {
    float m = glass_light(c);
    if (m > adapt.y)
        return c * ((adapt.y + (m - adapt.y) * 0.2) / m);
    if (m < adapt.x)
        return vec3(1.0) - (vec3(1.0) - c) * ((1.0 - adapt.x + (adapt.x - m) * 0.2) / (1.0 - m));
    return c;
}

vec2 glass_normal(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + vec2(r);
    vec2 s = vec2(p.x < 0.0 ? -1.0 : 1.0, p.y < 0.0 ? -1.0 : 1.0);
    if (q.x > 0.0 && q.y > 0.0)
        return normalize(q) * s;
    return q.x > q.y ? vec2(s.x, 0.0) : vec2(0.0, s.y);
}
`;

// Live: the screen as it was, sharp, and blurred. `valid` is the part of the layer that was on screen.
const LIVE_SAMPLING = `
uniform sampler2D glass_sharp_tex;
uniform sampler2D glass_soft_tex;
uniform vec2 fill_sharp;
uniform vec2 fill_soft;

// The textures are kept a little larger than the layer, so they last while it grows: fill is the
// part of each in use.
vec3 glass_take(sampler2D t, vec2 at, vec2 fill) {
    vec4 c = texture2D(t, clamp(at, valid.xy, valid.zw) / size * fill);
    return c.a > 0.001 ? c.rgb / c.a : vec3(0.0);
}

vec3 glass_sharp(vec2 at) {
    return glass_take(glass_sharp_tex, at, fill_sharp);
}

vec3 glass_soft(vec2 at) {
    return glass_take(glass_soft_tex, at, fill_soft);
}
`;

// Wall: one picture, the wallpaper already blurred.
const WALL_SAMPLING = `
uniform sampler2D tex;

vec3 glass_take(vec2 at) {
    vec4 c = texture2D(tex, clamp(at, vec2(0.0), size) / size);
    return c.a > 0.001 ? c.rgb / c.a : vec3(0.0);
}

vec3 glass_sharp(vec2 at) {
    return glass_take(at);
}

vec3 glass_soft(vec2 at) {
    return glass_take(at);
}
`;

// One pass of the frost: nine taps along blur_step (a gaussian two taps wide), shrinking the picture
// to half its size on the first pass. Two passes, across then down, make the blur.
const BLUR_DECLARATIONS = `
uniform sampler2D blur_src;
uniform vec2 blur_step;
`;

const BLUR_CODE = `
vec2 uv = cogl_tex_coord_in[0].xy;
vec4 sum = texture2D(blur_src, uv) * 0.2042;
sum += (texture2D(blur_src, uv + blur_step) + texture2D(blur_src, uv - blur_step)) * 0.1802;
sum += (texture2D(blur_src, uv + 2.0 * blur_step) + texture2D(blur_src, uv - 2.0 * blur_step)) * 0.1238;
sum += (texture2D(blur_src, uv + 3.0 * blur_step) + texture2D(blur_src, uv - 3.0 * blur_step)) * 0.0663;
sum += (texture2D(blur_src, uv + 4.0 * blur_step) + texture2D(blur_src, uv - 4.0 * blur_step)) * 0.0276;
cogl_color_out = sum;
`;

// Everything in pixels of the layer. `shape` is the glass inside it: x, y, width, height.
const CODE = `
vec2 px = cogl_tex_coord_in[0].xy * size;
vec2 hs = shape.zw * 0.5;
vec2 p = px - (shape.xy + hs);
float r = min(radius, min(hs.x, hs.y));
float d = glass_box(p, hs, r);
vec2 n = glass_normal(p, hs, r);
// A satellite budding off: a neck of glass joins the two, thinning as it moves away. The satellite
// draws itself, so its own area is left out here; the outline and its light run along the neck.
float apart = 1.0;
if (sat_k > 0.5) {
    float ds = glass_sat(px);
    apart = clamp(0.5 + ds, 0.0, 1.0);
    d = glass_smin(d, ds, sat_k);
    vec2 e = vec2(1.0, 0.0);
    vec2 g = vec2(glass_field(px + e.xy) - glass_field(px - e.xy), glass_field(px + e.yx) - glass_field(px - e.yx));
    if (dot(g, g) > 0.000001)
        n = normalize(g);
}
float inside = clamp(0.5 - d, 0.0, 1.0) * apart;

float spread = 1.0 - smoothstep(-12.0, 30.0, glass_box(p - vec2(0.0, 8.0), hs, r));
float tight = 1.0 - smoothstep(-1.0, 4.0, glass_box(p - vec2(0.0, 1.0), hs, r));
float shade = shadow * (0.7 * spread * spread + 0.3 * tight) * apart;

float depth = max(-d, 0.0);

// The glass is a slab with a rounded bevel along its edge. Through the flat middle you see what's
// behind, frosted; through the bevel the view bends outward and clears up, so what lies just past the
// edge (a window, the wallpaper's color) wraps around the rim, split a little into its colors.
vec3 col = tint.rgb;
vec3 around = vec3(1.0);
if (backdrop > 0.5 && inside > 0.0) {
    float b = min(bend, min(hs.x, hs.y) * 0.8); // a small capsule bends less than the dock
    float bevel = max(min(b * 1.4, min(hs.x, hs.y)), 1.0);
    float t = clamp(depth / bevel, 0.0, 1.0);
    float lens = (1.0 - t) * (1.0 - t);
    vec2 base = px + n * b * lens;
    vec2 split = n * b * 0.07 * lens;
    vec3 bent = vec3(glass_sharp(base + split).r, glass_sharp(base).g, glass_sharp(base - split).b);
    vec3 c = mix(glass_soft(base), bent, clamp(lens * 1.8, 0.0, 1.0) * clarity);
    float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
    c = clamp(mix(vec3(l), c, saturation) * brightness, 0.0, 1.0);
    c = clamp(glass_adapt(c), 0.0, 1.0);
    col = mix(c, tint.rgb, tint.a * (1.0 - 0.35 * lens));
    around = glass_soft(px + n * (b + 8.0)); // what the rim catches: the light just outside it
}

float down = clamp((px.y - shape.y) / max(shape.w, 1.0), 0.0, 1.0);
float across = clamp((px.x - shape.x) / max(shape.z, 1.0), 0.0, 1.0);
col += sheen * (1.0 - smoothstep(0.0, 0.6, down)) * (1.0 - 0.5 * across);

float facing = dot(n, vec2(-0.7071, -0.7071));
float edge = clamp(1.0 - depth / 1.6, 0.0, 1.0);
float glow = 1.0 - smoothstep(0.0, 9.0, depth);

// The selection: a capsule of the mode's color in the glass, with a ring and its own light on top.
if (pill.z > 0.5) {
    vec2 ph = pill.zw * 0.5;
    vec2 pp = px - (pill.xy + ph);
    float pd = glass_box(pp, ph, min(ph.x, ph.y));
    float pin = clamp(0.5 - pd, 0.0, 1.0);
    float ring = clamp(1.0 - abs(pd + 0.7) / 0.9, 0.0, 1.0);
    // Moving, it's a drop of thicker glass: what's behind it swells toward its middle.
    if (pill_power > 0.001 && backdrop > 0.5 && pin > 0.0) {
        vec3 sc = glass_sharp(pill.xy + ph + pp / (1.0 + 0.25 * pill_power));
        sc = clamp(mix(vec3(dot(sc, vec3(0.2126, 0.7152, 0.0722))), sc, saturation) * brightness, 0.0, 1.0);
        // Tinted like the glass around it, or the ink on the capsule would fade for as long as it moves.
        sc = mix(clamp(glass_adapt(sc), 0.0, 1.0), tint.rgb, tint.a);
        col = mix(col, sc, pin * pill_power);
    }
    col = mix(col, pill_color.rgb, pill_color.a * pin);
    col = mix(col, mix(pill_color.rgb, vec3(1.0), 0.6 * pill_power), (0.45 + 0.4 * pill_power) * ring);
    col += (0.1 + 0.12 * pill_power) * pin * (1.0 - smoothstep(0.0, pill.w, pp.y + ph.y));
}

// Light where the pointer is, strongest on the rim near it.
if (pointer.z > 0.001) {
    vec2 off = (px - pointer.xy) / vec2(110.0, 70.0);
    float near = exp(-dot(off, off) * 2.0);
    col += pointer.z * near * (0.07 + 0.55 * edge) * (1.0 - col);
}

// The rim lights up from the top left (and a little from the bottom right), in the color of what's
// around it: white light, tinted by the surroundings it reflects.
float light = edge * (0.3 + 0.7 * max(facing, 0.0) + 0.45 * max(-facing, 0.0)) + 0.18 * glow * max(facing, 0.0);
vec3 shine = mix(vec3(1.0), clamp(around * 1.35 + 0.15, 0.0, 1.0), 0.45);
col = mix(col, shine, clamp(rim * light, 0.0, 1.0));
col = mix(col, line.rgb, line.a * edge);

vec4 glass = vec4(clamp(col, 0.0, 1.0), 1.0) * inside;
cogl_color_out = (glass + vec4(0.0, 0.0, 0.0, shade * (1.0 - inside))) * cogl_color_in.a;
`;

const UNIFORMS = ['size', 'shape', 'radius', 'tint', 'saturation', 'brightness', 'backdrop', 'rim', 'sheen', 'bend', 'clarity', 'adapt',
    'shadow', 'line', 'valid', 'pill', 'pill_color', 'pill_power', 'pointer', 'sat', 'sat_r', 'sat_k'];

// Every uniform starts out set: GNOME shares one pipeline among all effects of a class, and a new one
// would start with whatever the first had (the dock's selection showing in every card).
const STARTING = {pill: [0, 0, 0, 0], pill_color: [0, 0, 0, 0], pill_power: [0], pointer: [0, 0, 0], line: [0, 0, 0, 0], clarity: [1], adapt: [0, 1],
    sat: [0, 0, 0, 0], sat_r: [0], sat_k: [0]};

// How far apart (in pixels) a satellite and the dock still melt together while it's budding off.
const MELT = 34;

// The wall mode's shader, on GNOME's own offscreen effect.
const WallEffect = GObject.registerClass(
class DeskAIGlassEffect extends Shell.GLSLEffect {
    _init() {
        super._init();
        this._where = Object.fromEntries(UNIFORMS.map(name => [name, this.get_uniform_location(name)]));
        this._values = {};
        for (const [name, values] of Object.entries(STARTING))
            this.set(name, values);
    }

    vfunc_build_pipeline() {
        this.add_glsl_snippet(FRAGMENT, COMMON + WALL_SAMPLING, CODE, true);
    }

    // Only what changed goes to the GPU: a uniform set again redraws the layer.
    set(name, values) {
        const key = values.join(',');
        if (this._values[name] === key)
            return;
        this._values[name] = key;
        this.set_uniform_float(this._where[name], values.length, values);
        this.queue_repaint();
    }
});

// The live mode's painting, handed to the layer: it draws the glass itself instead of the actor.
const LiveEffect = GObject.registerClass(
class DeskAIGlassLiveEffect extends Clutter.Effect {
    _init(layer) {
        super._init();
        this._layer = layer;
    }

    vfunc_paint_node(node, paintContext) {
        try {
            this._layer.paintLive(node, paintContext);
        } catch (e) {
            this._layer.liveFailed(e);
        }
    }
});

// Over the surface's own content (the dock's icons): while the selection moves, what's under it is
// magnified like through a drop of glass. Only on while moving, so icons and text stay sharp at rest.
const LENS_DECLARATIONS = `
uniform sampler2D tex;
uniform vec4 box;
uniform vec4 pill;
uniform float power;

float lens_box(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + vec2(r);
    return length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - r;
}
`;

// box: where the effect's texture sits on the stage; pill: the selection on the stage.
const LENS_CODE = `
vec2 at = box.xy + cogl_tex_coord_in[0].xy * box.zw;
vec2 ph = pill.zw * 0.5;
vec2 mid = pill.xy + ph;
vec2 pp = at - mid;
float pd = lens_box(pp, ph, min(ph.x, ph.y));
float inside = clamp(0.5 - pd, 0.0, 1.0);
vec2 seen = mix(at, mid + pp / (1.0 + 0.2 * power), inside);
cogl_color_out = cogl_color_in * texture2D(tex, clamp((seen - box.xy) / box.zw, vec2(0.0), vec2(1.0)));
`;

const LensEffect = GObject.registerClass(
class DeskAIGlassLensEffect extends Shell.GLSLEffect {
    _init() {
        super._init();
        this._where = Object.fromEntries(['box', 'pill', 'power'].map(name => [name, this.get_uniform_location(name)]));
        this._values = {};
        this.set('power', [0]);
        this.set('pill', [0, 0, 0, 0]);
        this.set('box', [0, 0, 1, 1]);
    }

    vfunc_build_pipeline() {
        this.add_glsl_snippet(FRAGMENT, LENS_DECLARATIONS, LENS_CODE, true);
    }

    set(name, values) {
        const key = values.join(',');
        if (this._values[name] === key)
            return;
        this._values[name] = key;
        this.set_uniform_float(this._where[name], values.length, values);
        this.queue_repaint();
    }
});

// "#rrggbb" or "rgb(a)(…)" as GLSL wants it: 0–1, alpha left out.
function parseColor(color) {
    // Six digits first: tried the other way round, #3b82f6 is taken for #3b8, and a blue mode gets a green capsule.
    const hex = color?.match(/^#([0-9a-f]{6}|[0-9a-f]{3})/i)?.[1];
    if (hex) {
        const full = hex.length === 3 ? [...hex].map(c => c + c).join('') : hex;
        return [0, 2, 4].map(i => parseInt(full.slice(i, i + 2), 16) / 255);
    }
    const parts = color?.match(/rgba?\(([^)]*)\)/)?.[1]?.split(',').map(Number);
    return parts?.length >= 3 ? parts.slice(0, 3).map(v => v / 255) : [0.5, 0.5, 0.5];
}

// GNOME 46 named its blur's size "radius"; before, a sigma half as big.
function blurProperty(effect) {
    return 'radius' in effect ? 'radius' : 'sigma';
}

function blurValue(effect, sigma) {
    return blurProperty(effect) === 'radius' ? sigma * 2 : sigma;
}

function overlaps(a, b) {
    return a.origin.x < b.origin.x + b.size.width && b.origin.x < a.origin.x + a.size.width &&
        a.origin.y < b.origin.y + b.size.height && b.origin.y < a.origin.y + a.size.height;
}

// One snippet of each kind for every layer: Cogl keeps a compiled program per distinct snippet, so a
// new one for each menu opened would compile the shader again every time and pile programs up.
const SNIPPETS = new Map();

function snippet(name) {
    let made = SNIPPETS.get(name);
    if (!made) {
        const [declarations, code] = name === 'glass' ? [COMMON + LIVE_SAMPLING, CODE] : [BLUR_DECLARATIONS, BLUR_CODE];
        made = Cogl.Snippet.new(FRAGMENT, declarations, null);
        made.set_replace(code);
        SNIPPETS.set(name, made);
    }
    return made;
}

// Live glass needs these; a GNOME without them gets the wallpaper glass from the start.
const LIVE_POSSIBLE = Boolean(Cogl.Snippet?.new && Clutter.LayerNode?.new_to_framebuffer && Clutter.PipelineNode);

// The rectangles of a redraw clip: a region of Mutter's own, or a cairo one before GNOME 47.
function rectanglesOf(clip) {
    const out = [];
    if (typeof clip.numRectangles === 'function') {
        for (let i = 0; i < clip.numRectangles(); i++)
            out.push(clip.getRectangle(i));
        return out;
    }
    const iterator = new Mtk.RegionIterator();
    for (iterator.init(clip); !iterator.at_end(); iterator.next()) {
        const r = iterator.rectangle;
        out.push({x: r.x, y: r.y, width: r.width, height: r.height});
    }
    return out;
}

// Textures come in steps of 64 pixels and are kept while they still fit (and aren't far too big), so a
// growing or shrinking surface doesn't get new ones every frame.
const roomFor = n => Math.ceil(n / 64) * 64;
const fits = (target, width, height) => Boolean(target) && target.width >= width && target.height >= height &&
    target.width <= roomFor(width) + 128 && target.height <= roomFor(height) + 128;

// A texture to draw into, with pixel coordinates.
function makeTarget(context, width, height) {
    const texture = Cogl.Texture2D.new_with_size(context, width, height);
    texture.set_components(Cogl.TextureComponents.RGBA);
    texture.allocate();
    const framebuffer = Cogl.Offscreen.new_with_texture(texture);
    framebuffer.allocate();
    framebuffer.orthographic(0, 0, width, height, -1, 1);
    framebuffer.clear4f(Cogl.BufferBit.COLOR, 0, 0, 0, 0);
    return {texture, framebuffer, width, height};
}

function smoothTexture(pipeline, layer, texture) {
    pipeline.set_layer_texture(layer, texture);
    pipeline.set_layer_filters(layer, Cogl.PipelineFilter.LINEAR, Cogl.PipelineFilter.LINEAR);
    pipeline.set_layer_wrap_mode(layer, Cogl.PipelineWrapMode.CLAMP_TO_EDGE);
}

// The wallpaper, for the wall mode: GNOME's own leaves out what windows cover, so this is a copy of it,
// fully transparent itself; the layers copy it.
class Walls {
    constructor() {
        this.actor = new Clutter.Actor({name: 'deskaiGlassWalls', opacity: 0, reactive: false});
        this.actor.add_constraint(new Clutter.BindConstraint({source: global.stage, coordinate: Clutter.BindCoordinate.SIZE}));
        Main.layoutManager.uiGroup.insert_child_at_index(this.actor, 0);
        this._managers = [];
        this._make();
        Main.layoutManager.connectObject('monitors-changed', () => this._make(), this);
    }

    _make() {
        this._managers.forEach(manager => manager.destroy());
        this._managers = Main.layoutManager.monitors.map((_monitor, monitorIndex) => new Background.BackgroundManager({
            container: this.actor,
            monitorIndex,
            vignette: false,
            controlPosition: true,
        }));
    }

    destroy() {
        if (this.destroyed)
            return;
        this.destroyed = true;
        Main.layoutManager.disconnectObject(this);
        this._managers.forEach(manager => manager.destroy());
        this.actor.destroy();
    }
}

// Takes no room in its parent's layout: it is placed by constraints on the surface it backs.
const GlassLayer = GObject.registerClass(
class DeskAIGlassLayer extends Clutter.Actor {
    _init(surface, glass, shapeOf) {
        super._init({reactive: false, clip_to_allocation: true});
        this.surface = surface;
        this._glass = glass;
        this._shapeOf = shapeOf;
        this._uniforms = {...STARTING};

        // wall mode: a blurred copy of the wallpaper, shaded by GNOME's offscreen effect
        this._wallEffect = new WallEffect();
        this.add_effect_with_name('deskai-glass', this._wallEffect);
        this._holder = new Clutter.Actor({clip_to_allocation: true});
        this._holder.add_constraint(new Clutter.BindConstraint({source: this, coordinate: Clutter.BindCoordinate.SIZE}));
        this._blur = new Shell.BlurEffect({mode: Shell.BlurMode.ACTOR, brightness: 1});
        this._holder.add_effect_with_name('blur', this._blur);
        this.add_child(this._holder);
        this._clone = null;
        this._placed = '';

        // live mode: drawn by hand from the screen
        this._liveEffect = new LiveEffect(this);
        this.add_effect_with_name('deskai-glass-live', this._liveEffect);
        this._live = null;
        this._mode = null;

        this._edges = null;
        this._pointer = {x: 0, y: 0, on: false, strength: new Follower(SPRINGS.smooth, 0.01)};
        this._lastSync = 0;
        this._frost = 1; // grows from 0 when the glass comes into being
    }

    vfunc_get_preferred_width() {
        return [0, 0];
    }

    vfunc_get_preferred_height() {
        return [0, 0];
    }

    _set(name, values) {
        const key = values.join(',');
        if (this._uniforms[name]?.join(',') === key)
            return;
        this._uniforms[name] = values;
        if (this._mode === 'wall')
            this._wallEffect.set(name, values);
        else
            this.queue_redraw();
    }

    // look: what theme.js worked out for this surface (tint, blur, rim…).
    setLook(look) {
        this._look = look;
        this._setMode(look.backdrop && this._glass.live ? 'live' : 'wall');
        if (this._mode === 'wall') {
            const walls = look.backdrop ? this._glass.walls.actor : null;
            if (walls !== this._clone?.source) {
                this._clone?.destroy();
                this._clone = walls ? new Clutter.Clone({source: walls}) : null;
                if (this._clone) {
                    this._clone.set_pivot_point(0, 0);
                    this._holder.add_child(this._clone);
                }
                this._placed = '';
            }
            this._holder.visible = Boolean(walls);
            this._blur.enabled = Boolean(walls);
            if (walls && !this._holder.get_transition(`@effects.blur.${blurProperty(this._blur)}`))
                this._blur[blurProperty(this._blur)] = blurValue(this._blur, look.blur);
        } else {
            this._softStale = true; // the blur may have changed
        }
        this._set('radius', [look.radius]);
        this._set('tint', look.tint);
        this._set('saturation', [look.saturation]);
        this._set('brightness', [look.brightness]);
        this._set('backdrop', [look.backdrop ? 1 : 0]);
        this._set('rim', [look.rim]);
        this._set('sheen', [look.sheen]);
        this._set('bend', [look.bend]);
        this._set('clarity', [look.clarity ?? 1]);
        this._set('adapt', look.adapt ?? [0, 1]);
        this._set('shadow', [look.shadow]);
        this._set('line', look.line);
        this.sync();
        this.queue_redraw();
    }

    get inWallMode() {
        return this._mode === 'wall' && Boolean(this._clone);
    }

    _setMode(mode) {
        if (mode === this._mode)
            return;
        this._mode = mode;
        const live = mode === 'live';
        this._liveEffect.enabled = live;
        this._wallEffect.enabled = !live;
        this._holder.visible = !live;
        if (live) {
            this._clone?.destroy();
            this._clone = null;
        } else {
            this._live = null; // its textures aren't needed by the wallpaper glass
            for (const [name, values] of Object.entries(this._uniforms))
                this._wallEffect.set(name, values);
        }
        this._fresh = false;
    }

    // Something in the live path broke (an old driver, a GNOME that changed): every glass falls back to
    // the wallpaper for the rest of the session, rather than drawing nothing.
    liveFailed(error) {
        this._glass.liveBroken(error);
    }

    // Glass doesn't fade in, it comes into being: what's behind it goes from sharp to frosted as it grows.
    materialize() {
        if (!this._look?.backdrop || !animationsOn())
            return;
        if (this._mode === 'wall') {
            const property = blurProperty(this._blur);
            this._holder.remove_transition(`@effects.blur.${property}`);
            this._blur[property] = 0;
            this._holder.ease_property(`@effects.blur.${property}`, blurValue(this._blur, this._look.blur), {
                duration: 380,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            });
            return;
        }
        this._materializing?.stop();
        const timeline = new Clutter.Timeline({actor: this, duration: 380, progress_mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        timeline.connect('new-frame', () => {
            this._frost = timeline.get_progress();
            this._softStale = true;
            this.queue_redraw();
        });
        timeline.connect('stopped', () => {
            this._frost = 1;
            this._softStale = true;
            this._materializing = null;
            this.queue_redraw();
        });
        this._frost = 0;
        this._materializing = timeline;
        timeline.start();
    }

    // Draws the glass from the frame being painted. Runs while the layer paints, when everything under
    // it is already on the screen.
    paintLive(node, paintContext) {
        const [width, height] = [this.width, this.height];
        if (width < 1 || height < 1 || !this._look)
            return;
        const view = paintedView;
        if (view && !this.is_in_clone_paint())
            this._capture(view, paintContext);
        const live = this._live;
        if (!live?.sharp)
            return; // nothing seen yet: a frame with no glass beats one with a wrong one

        if (this._softStale)
            this._blurInto(node, live);

        const pipeline = live.pipeline;
        pipeline.set_uniform_float(live.where.fill_sharp, 2, 1, [live.used[0] / live.sharp.width, live.used[1] / live.sharp.height]);
        pipeline.set_uniform_float(live.where.fill_soft, 2, 1, [live.half[0] / live.soft.width, live.half[1] / live.soft.height]);
        for (const [name, values] of Object.entries(this._uniforms)) {
            const location = live.where[name];
            if (values.length === 1)
                pipeline.set_uniform_1f(location, values[0]);
            else
                pipeline.set_uniform_float(location, values.length, 1, values);
        }
        const opacity = this.get_paint_opacity() / 255;
        COLOR.init_from_4f(opacity, opacity, opacity, opacity);
        pipeline.set_color(COLOR);
        const glassNode = new Clutter.PipelineNode(pipeline);
        BOX.init(0, 0, width, height);
        glassNode.add_texture_rectangle(BOX, 0, 0, 1, 1);
        node.add_child(glassNode);
        this._glass.liveWorked();
    }

    // Copies what's under the layer out of the view's framebuffer: only the parts repainted this frame,
    // since the rest hasn't changed (and holds last frame's glass, which mustn't be read back).
    _capture(view, paintContext) {
        const source = view.get_framebuffer();
        const layout = view.layout;
        const scale = view.get_scale();
        const corners = this.get_abs_allocation_vertices().map(v => [(v.x - layout.x) * scale, (v.y - layout.y) * scale]);
        if (!corners.flat().every(Number.isFinite))
            return;
        const xs = corners.map(c => c[0]);
        const ys = corners.map(c => c[1]);
        const sx = Math.floor(Math.min(...xs) + 0.001);
        const sy = Math.floor(Math.min(...ys) + 0.001);
        const gw = Math.max(1, Math.ceil(Math.max(...xs) - 0.001) - sx);
        const gh = Math.max(1, Math.ceil(Math.max(...ys) - 0.001) - sy);
        // the part of it on this view
        const cx = Math.max(0, sx);
        const cy = Math.max(0, sy);
        const cw = Math.min(sx + gw, source.get_width()) - cx;
        const ch = Math.min(sy + gh, source.get_height()) - cy;
        if (cw <= 0 || ch <= 0)
            return;

        const context = source.get_context();
        this._ensureLive(context, gw, gh);
        const live = this._live;
        const place = `${sx},${sy},${gw},${gh},${scale}`;
        if (place !== live.place) {
            live.place = place;
            this._fresh = false; // what was copied before belongs somewhere else
        }

        // At a fractional scale, the edges of the clip that fall inside the layer step in by a logical pixel
        // (see _repainted). Whether it all got copied is judged without that, or it never would be.
        const bounds = {x: cx, y: cy, width: cw, height: ch};
        const inset = Number.isInteger(scale) ? 0 : 1;
        const rects = this._repainted(paintContext, layout, scale, bounds, inset);
        for (const rect of rects)
            blit(source, live.sharp.framebuffer, rect.x, rect.y, rect.x - sx, rect.y - sy, rect.width, rect.height);
        if (rects.length > 0)
            this._softStale = true;
        const covered = inset ? this._repainted(paintContext, layout, scale, bounds, 0) : rects;
        const whole = covered.reduce((sum, rect) => sum + rect.width * rect.height, 0) >= cw * ch;
        if (whole)
            this._fresh = true;
        else if (rects.length > 0 || !this._fresh)
            this._glass.redrawAround(this); // the rest next frame, or the blur's edge goes stale

        const [w, h] = [this.width, this.height];
        this._set('valid', [(cx - sx + 0.5) / gw * w, (cy - sy + 0.5) / gh * h, (cx - sx + cw - 0.5) / gw * w, (cy - sy + ch - 0.5) / gh * h]);
        live.sigma = (this._look.blur ?? 6) * scale / 2;
    }

    // The rectangles of `bounds` (device pixels) that this frame repaints. Mutter grows its clip out to
    // whole logical pixels; at a fractional scale an edge of it inside the bounds can land on pixels left
    // from an older frame, so those edges step in by `inset` logical pixels.
    _repainted(paintContext, layout, scale, bounds, inset) {
        const clip = paintContext.get_redraw_clip();
        if (!clip)
            return [bounds];
        const right = bounds.x + bounds.width;
        const bottom = bounds.y + bounds.height;
        const start = (logical, limit) => (Math.floor(logical * scale) <= limit ? limit : Math.ceil((logical + inset) * scale));
        const end = (logical, limit) => (Math.ceil(logical * scale) >= limit ? limit : Math.floor((logical - inset) * scale));
        const rects = [];
        for (const r of rectanglesOf(clip)) {
            const x1 = start(r.x - layout.x, bounds.x);
            const y1 = start(r.y - layout.y, bounds.y);
            const x2 = end(r.x + r.width - layout.x, right);
            const y2 = end(r.y + r.height - layout.y, bottom);
            if (x2 > x1 && y2 > y1)
                rects.push({x: x1, y: y1, width: x2 - x1, height: y2 - y1});
        }
        return rects;
    }

    _ensureLive(context, width, height) {
        let live = this._live;
        if (!live || live.context !== context) {
            const shader = name => {
                const pipeline = Cogl.Pipeline.new(context);
                pipeline.add_snippet(snippet(name));
                return pipeline;
            };
            const pipeline = shader('glass');
            live = {
                context,
                pipeline,
                blurAcross: shader('blur'),
                blurDown: shader('blur'),
                nothing: Cogl.Pipeline.new(context),
                where: Object.fromEntries([...UNIFORMS, 'fill_sharp', 'fill_soft']
                    .map(name => [name, pipeline.get_uniform_location(name)])),
                sharp: null,
                across: null,
                soft: null,
            };
            pipeline.set_uniform_1i(pipeline.get_uniform_location('glass_sharp_tex'), 0);
            pipeline.set_uniform_1i(pipeline.get_uniform_location('glass_soft_tex'), 1);
            for (const blur of [live.blurAcross, live.blurDown]) {
                blur.set_uniform_1i(blur.get_uniform_location('blur_src'), 0);
                blur.step = blur.get_uniform_location('blur_step');
            }
            this._live = live;
        }
        const [halfWidth, halfHeight] = [Math.max(1, Math.ceil(width / 2)), Math.max(1, Math.ceil(height / 2))];
        let renewed = false;
        if (!fits(live.sharp, width, height)) {
            live.sharp = makeTarget(context, roomFor(width), roomFor(height));
            renewed = true;
        }
        if (!fits(live.soft, halfWidth, halfHeight)) {
            live.across = makeTarget(context, roomFor(halfWidth), roomFor(halfHeight));
            live.soft = makeTarget(context, roomFor(halfWidth), roomFor(halfHeight));
            renewed = true;
        }
        live.used = [width, height];
        live.half = [halfWidth, halfHeight];
        if (renewed) {
            smoothTexture(live.pipeline, 0, live.sharp.texture);
            smoothTexture(live.pipeline, 1, live.soft.texture);
            smoothTexture(live.blurAcross, 0, live.sharp.texture);
            smoothTexture(live.blurDown, 0, live.across.texture);
            live.place = null;
            this._fresh = false;
            this._softStale = true;
        }
    }

    // The frosted picture, at half size: the sharp one blurred across into one texture while it shrinks,
    // then down into another. Paint nodes run after this returns, in order, so it's ready before the
    // glass reads it. sigma is in the half-size picture's pixels; each tap is half a sigma apart.
    _blurInto(node, live) {
        const [width, height] = live.used;
        const [halfWidth, halfHeight] = live.half;
        const spacing = Math.max(0.01, (live.sigma ?? 3) * this._frost / 2);
        live.blurAcross.set_uniform_float(live.blurAcross.step, 2, 1, [spacing * 2 / live.sharp.width, 0]);
        live.blurDown.set_uniform_float(live.blurDown.step, 2, 1, [0, spacing / live.across.height]);
        const passes = [
            [live.blurAcross, live.across, width / live.sharp.width, height / live.sharp.height],
            [live.blurDown, live.soft, halfWidth / live.across.width, halfHeight / live.across.height],
        ];
        for (const [pipeline, target, u, v] of passes) {
            const into = Clutter.LayerNode.new_to_framebuffer(target.framebuffer, live.nothing);
            node.add_child(into);
            const pass = new Clutter.PipelineNode(pipeline);
            BOX.init(0, 0, halfWidth, halfHeight);
            pass.add_texture_rectangle(BOX, 0, 0, u, v);
            into.add_child(pass);
        }
        this._softStale = false;
    }

    // A capsule of `color` that follows `target()`, stretching as it goes: the edge in front leads and the
    // one behind catches up. target() gives an actor inside the surface, or where one will end up as
    // {x, y, width, height} on the stage, or null.
    select(target, color) {
        this._selection = target ? {target, color: [...parseColor(color), 0.22]} : null;
        if (target && !this._lens) {
            this._lens = new LensEffect();
            this._lens.enabled = false;
            this.surface.add_effect_with_name('deskai-lens', this._lens);
        }
        if (!target) {
            this._set('pill', [0, 0, 0, 0]);
            if (this._lens)
                this._lens.enabled = false;
        }
        this.queue_redraw();
    }

    // Where the pointer is on the surface, in stage coordinates, or null once it's gone.
    point(x, y) {
        const pointer = this._pointer;
        pointer.on = x !== null;
        if (pointer.on)
            Object.assign(pointer, {x, y});
        this.queue_redraw();
    }

    _stepSelection(x, y, scale, seconds) {
        let rect = this._selection?.target();
        if (rect instanceof Clutter.Actor) {
            const box = rect.is_mapped() ? rect.get_transformed_extents() : null;
            rect = box && {x: box.origin.x, y: box.origin.y, width: box.size.width, height: box.size.height};
        }
        if (!rect) {
            this._set('pill', [0, 0, 0, 0]);
            return false;
        }
        const want = [(rect.x - x) / scale, (rect.y - y) / scale];
        want.push(want[0] + rect.width / scale, want[1] + rect.height / scale);
        if (!want.every(Number.isFinite))
            return false;
        this._edges ??= want.map(() => new Follower(SPRINGS.smooth));
        const [left, top, right, bottom] = this._edges;
        const center = (left.value ?? want[0]) + (right.value ?? want[2]);
        const goingRight = want[0] + want[2] > center + 0.5;
        const goingLeft = want[0] + want[2] < center - 0.5;
        // The leading edge quicker, not bouncier: a capsule that swings past the mode and comes back reads
        // as the dock bouncing.
        left.setKind(goingLeft ? SPRINGS.snappy : SPRINGS.smooth);
        right.setKind(goingRight ? SPRINGS.snappy : SPRINGS.smooth);
        let moving = false;
        this._edges.forEach((edge, i) => {
            moving = edge.step(want[i], seconds) || moving;
        });
        // How hard it's moving, 0–1: the faster, the more it swells and bends.
        const power = moving ? Math.min(Math.max(Math.abs(left.speed), Math.abs(right.speed)) / 450, 1) : 0;
        const pill = [left.value, top.value, right.value - left.value, bottom.value - top.value];
        this._set('pill', pill);
        this._set('pill_color', this._selection.color);
        this._set('pill_power', [power]);
        this._lensOver(pill, x, y, scale, power);
        return moving;
    }

    _lensOver(pill, x, y, scale, power) {
        const lens = this._lens;
        if (!lens)
            return;
        const [ok, box] = power > 0.02 ? this.surface.get_paint_box() : [false, null];
        lens.enabled = ok && box.get_width() > 0;
        if (!lens.enabled)
            return;
        lens.set('box', [box.x1, box.y1, box.get_width(), box.get_height()]);
        lens.set('pill', [x + pill[0] * scale, y + pill[1] * scale, pill[2] * scale, pill[3] * scale]);
        lens.set('power', [power]);
    }

    _stepPointer(x, y, scale, seconds) {
        const pointer = this._pointer;
        const moving = pointer.strength.step(pointer.on ? 1 : 0, seconds);
        this._set('pointer', [(pointer.x - x) / scale, (pointer.y - y) / scale, Math.max(pointer.strength.value ?? 0, 0)]);
        return moving;
    }

    // A satellite budding off this glass (or flowing back into it), on the stage, or null: the two melt
    // together while it's small and close, through a neck that thins as it grows and moves away.
    setSatellite(satellite) {
        const origin = this.origin;
        if (!satellite || !origin) {
            this._set('sat_k', [0]);
            return;
        }
        const {scaleX, scaleY} = origin;
        this._set('sat', [(satellite.x - origin.x) / scaleX, (satellite.y - origin.y) / scaleY,
            satellite.width / scaleX, satellite.height / scaleY]);
        this._set('sat_r', [satellite.radius / Math.min(scaleX, scaleY)]);
        this._set('sat_k', [MELT * satellite.strength]);
    }

    // Lines the glass up with the surface (and, in the wall mode, the wallpaper with the screen). Runs
    // before each frame is drawn, once animations and layout have put things where they'll be seen.
    sync() {
        if (!this.is_mapped() || !this._look)
            return;
        const [width, height] = [this.width, this.height];
        if (width < 1 || height < 1)
            return;
        const extents = this.get_transformed_extents();
        // A menu growing out of its button is squeezed more one way than the other.
        const scale = extents.size.width / width || 1;
        const scaleY = extents.size.height / height || 1;
        const [x, y] = [extents.origin.x, extents.origin.y];

        // Where the surface is now, which the layer's own place can trail by a frame when the surface
        // just moved or grew: the layer is laid out before it.
        let shape = [MARGIN, MARGIN, width - 2 * MARGIN, height - 2 * MARGIN];
        const inner = this._shapeOf?.() ?? this.surface;
        if (inner?.is_mapped()) {
            const box = inner.get_transformed_extents();
            const found = [(box.origin.x - x) / scale, (box.origin.y - y) / scaleY, box.size.width / scale, box.size.height / scaleY];
            if (found.every(Number.isFinite) && found[2] > 0 && found[3] > 0)
                shape = found;
        }
        this._set('size', [width, height]);
        this._set('shape', shape);
        // For the dock to melt with: where this glass is on the stage, and how far it has grown.
        const least = Math.min(scale, scaleY);
        this.origin = {x, y, scaleX: scale, scaleY};
        this.stageShape = {
            x: x + shape[0] * scale,
            y: y + shape[1] * scaleY,
            width: shape[2] * scale,
            height: shape[3] * scaleY,
            radius: this._look.radius * least,
            scale: least,
            opacity: this.get_paint_opacity() / 255,
        };
        if (this._mode === 'wall')
            this._set('valid', [0, 0, width, height]);

        const now = GLib.get_monotonic_time();
        const seconds = this._lastSync ? (now - this._lastSync) / 1e6 : 0;
        this._lastSync = now;
        const selecting = this._selection ? this._stepSelection(x, y, scale, seconds) : false;
        const pointing = this._stepPointer(x, y, scale, seconds);
        if (selecting || pointing)
            this.queue_redraw(); // keeps frames coming until the springs settle

        const placed = `${x},${y},${scale},${scaleY}`;
        if (this._clone && placed !== this._placed) {
            this._placed = placed;
            this._clone.set_scale(1 / scale, 1 / scaleY);
            this._clone.set_translation(-x / scale, -y / scaleY, 0);
        }
    }

    destroyGlass() {
        this._materializing?.stop();
        this._materializing = null;
        this._live = null;
    }
});

// The glass layers of all surfaces, kept in step with them.
export class Glass {
    constructor(onChanged) {
        this._onChanged = onChanged;
        this._layers = new Set();
        this._walls = null;
        this.live = LIVE_POSSIBLE;
        this._broken = !LIVE_POSSIBLE;
        this._failures = 0;
        this._restyleLater = 0;
        this._lastSync = 0;
        global.stage.connectObject(
            // After layout and before drawing: sizes are this frame's, and what's set now is what gets drawn.
            'before-paint', (_stage, view) => {
                paintedView = view;
                // Once a frame, not once for each monitor painted in it (those come microseconds apart).
                const now = GLib.get_monotonic_time();
                if (now - this._lastSync > 2000) {
                    for (const layer of this._layers)
                        layer.sync();
                    this._meld();
                }
                this._lastSync = now;
            },
            'after-paint', () => {
                paintedView = null;
            },
            this);
    }

    // The wallpaper copy, made the first time a layer needs it.
    get walls() {
        this._walls ??= new Walls();
        return this._walls;
    }

    // Live glass, or the wallpaper only; the theme restyles every layer after changing this.
    setLive(on) {
        this.live = on && !this._broken;
    }

    // Glass budding off the dock: a menu, card or command bar still small (growing in, or shrinking back)
    // just above it melts into it. Strength 0–1 from how far it has grown and faded in.
    _meld() {
        const layers = [...this._layers].filter(layer => layer.is_mapped() && layer.stageShape);
        for (const bar of layers.filter(layer => layer.isBar)) {
            const dock = bar.stageShape;
            let best = null;
            for (const other of layers) {
                if (other.isBar)
                    continue;
                const shape = other.stageShape;
                const gap = dock.y - (shape.y + shape.height);
                const across = shape.x < dock.x + dock.width && dock.x < shape.x + shape.width;
                const grown = shape.scale / Math.max(dock.scale, 0.01);
                const strength = Math.min(1, Math.max(0, (1 - grown) / 0.7)) * shape.opacity;
                if (across && gap > -12 && gap < MELT * 2 && strength > (best?.strength ?? 0.02))
                    best = {...shape, strength};
            }
            bar.setSatellite(best);
        }
    }

    // Redraws a layer whole next frame, with every layer overlapping it: one redrawn alone would repaint
    // part of its neighbor, which would then ask for the same, back and forth for good.
    redrawAround(layer) {
        const boxes = new Map();
        for (const other of this._layers) {
            if (other.is_mapped())
                boxes.set(other, other.get_transformed_extents());
        }
        const affected = [layer];
        const queued = new Set(affected);
        for (let i = 0; i < affected.length; i++) {
            const a = boxes.get(affected[i]) ?? affected[i].get_transformed_extents();
            for (const [other, b] of boxes) {
                if (queued.has(other) || !overlaps(a, b))
                    continue;
                queued.add(other);
                affected.push(other);
            }
        }
        affected.forEach(other => other.queue_redraw());
    }

    liveWorked() {
        this._failures = 0;
    }

    // A paint that failed once may be a fluke (a texture the driver couldn't give right then); three in a
    // row isn't, and the glass shows the wallpaper for the rest of the session instead of drawing nothing.
    liveBroken(error) {
        if (this._broken)
            return;
        if (++this._failures < 3) {
            console.warn(`DeskAI: live glass failed to paint: ${error.message}`);
            return;
        }
        this._broken = true;
        this.live = false;
        logError(error, 'DeskAI: live glass keeps failing, showing the wallpaper through it instead');
        // Not from inside a paint: restyling adds and removes effects.
        this._restyleLater = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            this._restyleLater = 0;
            this._onChanged?.();
            return GLib.SOURCE_REMOVE;
        });
    }

    // The wallpaper copy goes once no glass shows it any more.
    trimWalls() {
        if (this._walls && ![...this._layers].some(layer => layer.inWallMode))
            this._walls.destroy();
        if (this._walls?.destroyed)
            this._walls = null;
    }

    // Puts a glass layer behind `surface`, which has to sit in a parent that lets children be placed by
    // constraints (St.Widget, a BinLayout, the shell's uiGroup). shapeOf: the actor inside `surface` that
    // has the glass's shape, when that's not all of it (a menu's content inside its pointer box).
    // bar: the dock, which other glass buds off and melts back into.
    attach(surface, shapeOf = null, bar = false) {
        const parent = surface.get_parent();
        if (!parent)
            return null;
        const layer = new GlassLayer(surface, this, shapeOf);
        layer.isBar = bar;
        parent.insert_child_below(layer, surface);
        layer.add_constraint(new Clutter.BindConstraint({source: surface, coordinate: Clutter.BindCoordinate.POSITION, offset: -MARGIN}));
        layer.add_constraint(new Clutter.BindConstraint({source: surface, coordinate: Clutter.BindCoordinate.SIZE, offset: 2 * MARGIN}));
        const bindings = ['translation-x', 'translation-y', 'scale-x', 'scale-y', 'opacity', 'visible']
            .map(property => surface.bind_property(property, layer, property, GObject.BindingFlags.SYNC_CREATE));
        const pivot = () => {
            if (!surface.get_stage()) // St can't size a widget before it's on the stage
                return;
            const {x: px, y: py} = surface.pivot_point;
            const [w, h] = [Math.max(surface.width, 1), Math.max(surface.height, 1)];
            layer.set_pivot_point((MARGIN + px * w) / (w + 2 * MARGIN), (MARGIN + py * h) / (h + 2 * MARGIN));
        };
        pivot();
        // Something raised to the top (a tooltip over an open menu) takes its glass along.
        const keepBelow = () => {
            if (layer.get_parent() === surface.get_parent() && surface.get_previous_sibling() !== layer)
                parent.set_child_below_sibling(layer, surface);
        };
        // Laid out before its surface, the layer would keep where the surface was: catch up next frame.
        const follow = () => {
            if (layer._later)
                return;
            layer._later = global.compositor.get_laters().add(Meta.LaterType.BEFORE_REDRAW, () => {
                layer._later = 0;
                if (layer.get_stage()) // a closed menu or dialog leaves the stage before it's destroyed
                    layer.queue_relayout();
                return GLib.SOURCE_REMOVE;
            });
        };
        surface.connectObject(
            'notify::allocation', follow,
            'notify::pivot-point', pivot,
            'notify::visible', () => {
                if (surface.visible)
                    layer.materialize();
            },
            'notify::width', pivot,
            'notify::height', pivot,
            'notify::mapped', () => {
                pivot();
                keepBelow();
                if (surface.is_mapped())
                    layer.materialize();
            },
            'destroy', () => {
                layer.surfaceGone = true;
                this.detach(layer);
            },
            layer);
        layer._bindings = bindings;
        layer.alive = true;
        // Gone with its parent (a dialog closing) or on its own: either way, let go of it.
        layer.connect('destroy', () => {
            layer.alive = false;
            this._layers.delete(layer);
            layer.destroyGlass();
            layer._bindings.forEach(binding => binding.unbind());
            layer.surface.disconnectObject(layer);
            // The surface keeps no lens once its glass is gone (the classic theme).
            if (layer._lens && !layer.surfaceGone)
                layer.surface.remove_effect(layer._lens);
            layer._lens = null;
            if (layer._later)
                global.compositor.get_laters().remove(layer._later);
            layer._later = 0;
        });
        this._layers.add(layer);
        return layer;
    }

    detach(layer) {
        if (layer?.alive)
            layer.destroy();
    }

    destroy() {
        global.stage.disconnectObject(this);
        if (this._restyleLater)
            GLib.source_remove(this._restyleLater);
        this._restyleLater = 0;
        paintedView = null;
        for (const layer of [...this._layers])
            this.detach(layer);
        this._walls?.destroy();
        this._walls = null;
    }
}
