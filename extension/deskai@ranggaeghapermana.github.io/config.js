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

export function isValidConfig(cfg) {
    if (!isObject(cfg) || !isObject(cfg.modes) || Object.keys(cfg.modes).length === 0)
        return false;
    const {dock = {}, inbox = {}, breaks = {}, room_warnings: room = {}, llm = {}} = cfg;
    return [dock, inbox, breaks, room, llm].every(isObject) &&
        (cfg.language === undefined || ['en', 'id'].includes(cfg.language)) &&
        (dock.autohide === undefined || ['windows', 'smart', 'always', 'never'].includes(dock.autohide)) &&
        allAre(dock, ['icon_size'], value => isNumberFrom(value, 0, false)) &&
        allAre(inbox, ['enabled'], isBool) && allAre(inbox, ['messages', 'agent_apps'], isStrings) &&
        allAre(breaks, ['enabled'], isBool) && allAre(breaks, ['after_minutes'], value => isNumberFrom(value, 0, false)) &&
        allAre(room, ['enabled'], isBool) &&
        allAre(llm, ['backend', 'model', 'effort', 'ollama_model', 'ollama_url'], isString) &&
        allAre(llm, ['triage_notifications'], isBool) && allAre(llm, ['exclude_apps'], isStrings) &&
        (cfg.default_mode === undefined || (isString(cfg.default_mode) && Object.hasOwn(cfg.modes, cfg.default_mode))) &&
        Object.entries(cfg.modes).every(([key, mode]) => isValidMode(key, mode));
}
