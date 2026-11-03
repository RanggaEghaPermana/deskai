// What modes.json has to look like for DeskAI to go by it. The daemon goes by the same rules, in
// brain.validate_config: a file only one of them takes leaves the dock and the daemon on different modes.

const COLOR = /^(#[0-9a-fA-F]{3,8}|rgba?\([\d\s.,%]+\))$/;
const MODE_ID = /^[a-z0-9_-]+$/;

const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const isBool = value => typeof value === 'boolean';
const isString = value => typeof value === 'string';
const isStrings = value => Array.isArray(value) && value.every(isString);
const isNumberFrom = (value, above, orEqual) => typeof value === 'number' && (value > above || (orEqual && value === above));
const allAre = (object, fields, check) => fields.every(field => object[field] === undefined || check(object[field]));

function isValidMode(key, mode) {
    if (!MODE_ID.test(key) || !isObject(mode))
        return false;
    const {notifications = {}, exit = {}} = mode;
    return isObject(notifications) && isObject(exit) &&
        allAre(mode, ['label', 'description', 'icon'], isString) &&
        (mode.color === undefined || (isString(mode.color) && COLOR.test(mode.color))) &&
        allAre(mode, ['apps', 'allow', 'block'], isStrings) &&
        ['all', 'filter', 'none'].includes(notifications.policy ?? 'all') && notifications.policy !== null &&
        allAre(notifications, ['allow_apps', 'keywords'], isStrings) &&
        (mode.on_distraction === undefined || ['nudge', 'minimize'].includes(mode.on_distraction)) &&
        allAre(mode, ['nudge_after', 'renudge_after'], value => isNumberFrom(value, 0, false)) &&
        allAre(exit, ['delay', 'per_press'], value => isNumberFrom(value, 0, true));
}

// Minutes from what's typed in the two boxes of the break dialog, or null if that isn't a time.
export function breakMinutes(hours, minutes) {
    const typed = [hours, minutes].map(text => text.trim());
    if (!typed.every(text => /^\d*$/.test(text)))
        return null;
    const total = Number(typed[0]) * 60 + Number(typed[1]);
    return total >= 1 ? total : null;
}

export function isValidConfig(cfg) {
    if (!isObject(cfg) || !isObject(cfg.modes) || Object.keys(cfg.modes).length === 0)
        return false;
    const {dock = {}, inbox = {}, breaks = {}, room_warnings: room = {}, appearance = {}, llm = {}} = cfg;
    return [dock, inbox, breaks, room, appearance, llm].every(isObject) &&
        (cfg.language === undefined || ['en', 'id'].includes(cfg.language)) &&
        (dock.autohide === undefined || ['windows', 'smart', 'always', 'never'].includes(dock.autohide)) &&
        allAre(dock, ['icon_size'], value => isNumberFrom(value, 0, false)) &&
        allAre(inbox, ['enabled'], isBool) && allAre(inbox, ['messages', 'agent_apps'], isStrings) &&
        allAre(breaks, ['enabled'], isBool) && allAre(breaks, ['after_minutes'], value => isNumberFrom(value, 0, false)) &&
        allAre(room, ['enabled'], isBool) &&
        (appearance.theme === undefined || ['glass', 'classic'].includes(appearance.theme)) &&
        (appearance.scheme === undefined || ['auto', 'light', 'dark'].includes(appearance.scheme)) &&
        (appearance.glass === undefined || ['clear', 'tinted'].includes(appearance.glass)) &&
        allAre(appearance, ['blur_windows', 'rim', 'reduce_transparency'], isBool) &&
        allAre(llm, ['backend', 'model', 'effort', 'ollama_model', 'ollama_url'], isString) &&
        allAre(llm, ['triage_notifications'], isBool) && allAre(llm, ['exclude_apps'], isStrings) &&
        (cfg.default_mode === undefined || (isString(cfg.default_mode) && Object.hasOwn(cfg.modes, cfg.default_mode))) &&
        Object.entries(cfg.modes).every(([key, mode]) => isValidMode(key, mode));
}
