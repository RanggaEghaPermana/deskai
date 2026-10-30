// Only for the throwaway shell started by dev/nested.sh: unsafe mode lets
// the test scripts use org.gnome.Shell.Eval and the screenshot API.
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class TestHelper extends Extension {
    enable() {
        global.context.unsafe_mode = true;
    }

    disable() {
        global.context.unsafe_mode = false;
    }
}
