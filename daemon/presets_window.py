#!/usr/bin/env python3
"""The window for writing your presets. What they are and how they run is in presets.py. Nothing here runs a step:
trying one out asks the daemon, the same way typing its keyword does."""

import copy
import sys
import threading

import gi

gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

import brain  # noqa: E402
import commands  # noqa: E402
import presets  # noqa: E402
import servers  # noqa: E402
from brain import BRAIN_NAME, BUS_NAME, OBJECT_PATH  # noqa: E402

ICONS = {"app": "application-x-executable-symbolic", "url": "web-browser-symbolic", "mode": "emblem-default-symbolic",
         "service": "emblem-system-symbolic", "command": "utilities-terminal-symbolic", "hand": "input-mouse-symbolic"}


def in_background(work, finished):
    """Runs work() off the window's thread, and finished(result, failed) back on it."""
    def run():
        try:
            result, failed = work(), False
        except Exception as e:  # docker, systemctl, your projects: it's only a check, and it can't leave the window stuck
            brain.log.warning("presets window: %s", e)
            result, failed = None, True
        GLib.idle_add(lambda: finished(result, failed) and False)
    threading.Thread(target=run, daemon=True).start()


def flat_button(icon, tooltip, clicked):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.connect("clicked", lambda _button: clicked())
    return button


def boxed_list():
    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    rows.add_css_class("boxed-list")
    return rows


def group(title, text, *children):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    heading = Gtk.Label(label=title, xalign=0)
    heading.add_css_class("heading")
    box.append(heading)
    if text:
        words = Gtk.Label(label=text, xalign=0, wrap=True)
        words.add_css_class("dim-label")
        box.append(words)
    for child in children:
        box.append(child)
    return box


def apps_installed():
    """[(name, desktop id)], by name."""
    found = [(app.get_name(), app.get_id()) for app in Gio.AppInfo.get_all() if app.should_show() and app.get_id()]
    return sorted(found, key=lambda app: app[0].lower())


class PresetsWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=880, default_height=640)
        self.set_size_request(720, 460)
        self.config = brain.Config()
        self.config.refresh()
        self.items, broken = presets.read()
        self.saved = copy.deepcopy(self.items)
        self.remember_names()
        self.current = None
        self.set_title(self.t("presets_title"))
        self.build(broken)
        self.show_preset(0 if self.items else None)
        self.connect("close-request", self.on_close)

    def remember_names(self):
        """What each preset is called on file, whatever it's been renamed to here since."""
        self.names_on_file = {id(preset): preset["name"] for preset in self.items}

    def t(self, key, **fields):
        return brain.card_text(self.config.language, key, **fields)

    # ---- the frame ----------------------------------------------------------

    def build(self, broken):
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        split = Adw.NavigationSplitView()
        self.toasts.set_child(split)

        self.sidebar = Gtk.ListBox()
        self.sidebar.add_css_class("navigation-sidebar")
        self.sidebar.connect("row-selected", self.on_row_selected)
        side = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.pack_start(flat_button("list-add-symbolic", self.t("presets_new"), self.new_preset))
        side.add_top_bar(header)
        side.set_content(Gtk.ScrolledWindow(child=self.sidebar, hscrollbar_policy=Gtk.PolicyType.NEVER))
        split.set_sidebar(Adw.NavigationPage.new(side, self.t("presets_title")))

        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=24, margin_bottom=24,
                            margin_start=12, margin_end=12)
        self.name_row = Adw.EntryRow(title=self.t("presets_name"))
        self.name_row.connect("changed", self.on_name_changed)
        names = boxed_list()
        names.append(self.name_row)
        self.keyword_rows = boxed_list()
        add_keyword = Gtk.Button(label=self.t("presets_add_keyword"), halign=Gtk.Align.START)
        add_keyword.connect("clicked", lambda _button: self.add_keyword())
        self.step_rows = boxed_list()
        add_step = Gtk.MenuButton(label=self.t("presets_add_step"), halign=Gtk.Align.START, menu_model=self.step_menu())
        self.form.append(names)
        self.form.append(group(self.t("presets_keywords"), self.t("presets_keywords_text"), self.keyword_rows, add_keyword))
        self.form.append(group(self.t("presets_steps"), self.t("presets_steps_text"), self.step_rows, add_step))

        empty = Adw.StatusPage(icon_name="view-list-bullet-symbolic", title=self.t("presets_empty_title"),
                               description=self.t("presets_empty_text"))
        create = Gtk.Button(label=self.t("presets_create"), halign=Gtk.Align.CENTER)
        create.add_css_class("suggested-action")
        create.add_css_class("pill")
        create.connect("clicked", lambda _button: self.new_preset())
        empty.set_child(create)

        self.pages = Gtk.Stack()
        self.pages.add_named(empty, "empty")
        self.pages.add_named(Gtk.ScrolledWindow(child=Adw.Clamp(child=self.form, maximum_size=640),
                                                hscrollbar_policy=Gtk.PolicyType.NEVER), "form")
        self.actions = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        delete = Gtk.Button(label=self.t("presets_delete"))
        delete.add_css_class("destructive-action")
        delete.connect("clicked", lambda _button: self.confirm_delete())
        try_it = Gtk.Button(label=self.t("presets_try"), hexpand=True, halign=Gtk.Align.END)
        try_it.connect("clicked", lambda _button: self.save(then=self.try_it))
        save = Gtk.Button(label=self.t("presets_save"))
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _button: self.save())
        for button in (delete, try_it, save):
            self.actions.append(button)

        content = Adw.ToolbarView()
        content.add_top_bar(Adw.HeaderBar())
        if broken:
            content.add_top_bar(Adw.Banner(title=self.t("presets_file_bad"), revealed=True))
        content.set_content(self.pages)
        content.add_bottom_bar(self.actions)
        self.content_page = Adw.NavigationPage.new(content, self.t("presets_title"))
        split.set_content(self.content_page)

    def step_menu(self):
        menu = Gio.Menu()
        for kind in presets.KINDS:
            menu.append(self.t(f"presets_kind_{kind}"), f"win.add-step::{kind}")
        action = Gio.SimpleAction.new("add-step", GLib.VariantType.new("s"))
        action.connect("activate", lambda _action, kind: self.edit_step(kind.get_string()))
        self.add_action(action)
        return menu

    @property
    def preset(self):
        return self.items[self.current] if self.current is not None else None

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    # ---- what's on show -----------------------------------------------------

    def refresh_sidebar(self):
        self.sidebar.remove_all()
        for preset in self.items:
            self.sidebar.append(Gtk.Label(label=preset["name"] or self.t("presets_new"), xalign=0, ellipsize=Pango.EllipsizeMode.END))
        if self.current is not None:
            self.sidebar.select_row(self.sidebar.get_row_at_index(self.current))

    def show_preset(self, at):
        self.current = at
        self.showing = True  # filling in the form isn't you changing anything
        self.pages.set_visible_child_name("empty" if at is None else "form")
        self.actions.set_visible(at is not None)
        if at is not None:
            self.name_row.set_text(self.preset["name"])
            self.refresh_keywords()
            self.refresh_steps()
        self.content_page.set_title(self.preset["name"] if at is not None and self.preset["name"] else self.t("presets_title"))
        self.showing = False
        self.refresh_sidebar()

    def on_row_selected(self, _rows, row):
        if row is not None and row.get_index() != self.current and not self.showing:
            self.show_preset(row.get_index())

    def on_name_changed(self, row):
        if self.showing or self.preset is None:
            return
        self.preset["name"] = row.get_text()
        self.content_page.set_title(row.get_text() or self.t("presets_title"))
        self.sidebar.get_row_at_index(self.current).get_child().set_label(row.get_text() or self.t("presets_new"))

    def refresh_keywords(self):
        self.keyword_rows.remove_all()
        for at, keyword in enumerate(self.preset["keywords"]):
            row = Adw.EntryRow(title=self.t("presets_keyword"), text=keyword)
            row.connect("changed", self.on_keyword_changed, at)
            row.add_suffix(flat_button("edit-delete-symbolic", self.t("presets_remove"), lambda at=at: self.remove_keyword(at)))
            self.keyword_rows.append(row)

    def on_keyword_changed(self, row, at):
        if not self.showing:
            self.preset["keywords"][at] = row.get_text()

    def add_keyword(self):
        self.preset["keywords"].append("")
        self.refresh_keywords()
        self.keyword_rows.get_row_at_index(len(self.preset["keywords"]) - 1).grab_focus()

    def remove_keyword(self, at):
        del self.preset["keywords"][at]
        self.refresh_keywords()

    def refresh_steps(self):
        self.step_rows.remove_all()
        steps = self.preset["steps"]
        for at, step in enumerate(steps):
            kind = step["type"]
            row = Adw.ActionRow(title=presets.label(step, self.t, self.mode_label), subtitle=self.t(f"presets_kind_{kind}"),
                                use_markup=False, activatable=True)
            row.add_prefix(Gtk.Image(icon_name=ICONS[kind]))
            up = flat_button("go-up-symbolic", self.t("presets_up"), lambda at=at: self.move_step(at, -1))
            down = flat_button("go-down-symbolic", self.t("presets_down"), lambda at=at: self.move_step(at, 1))
            up.set_sensitive(at > 0)
            down.set_sensitive(at < len(steps) - 1)
            row.add_suffix(up)
            row.add_suffix(down)
            row.add_suffix(flat_button("edit-delete-symbolic", self.t("presets_remove"), lambda at=at: self.remove_step(at)))
            row.connect("activated", lambda _row, at=at: self.edit_step(self.preset["steps"][at]["type"], at))
            self.step_rows.append(row)
        self.step_rows.set_visible(bool(steps))

    def mode_label(self, mode):
        return (self.config.mode(mode) or {"label": mode})["label"]

    def move_step(self, at, by):
        steps = self.preset["steps"]
        steps[at], steps[at + by] = steps[at + by], steps[at]
        self.refresh_steps()

    def remove_step(self, at):
        del self.preset["steps"][at]
        self.refresh_steps()

    # ---- presets and steps --------------------------------------------------

    def new_preset(self):
        self.items.append({"name": "", "keywords": [""], "steps": []})
        self.show_preset(len(self.items) - 1)
        self.name_row.grab_focus()

    def choices_for(self, kind):
        """[(what you see, what's saved)] for a step you pick from a list, or None for one you type."""
        if kind == "app":
            return apps_installed()
        if kind == "mode":
            return [(mode["label"], key) for key, mode in self.config.modes()]
        return None

    def edit_step(self, kind, at=None):
        field = presets.FIELDS[kind]
        now = self.preset["steps"][at][field] if at is not None else ""
        dialog = Adw.AlertDialog(heading=self.t(f"presets_kind_{kind}"), body=self.t(f"presets_hint_{kind}"))
        dialog.add_response("cancel", self.t("preset_cancel"))
        dialog.add_response("ok", self.t("presets_step_ok"))
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")
        rows = boxed_list()
        choices = self.choices_for(kind)
        if choices is None:
            row = Adw.EntryRow(title=self.t(f"presets_field_{kind}"), text=now, activates_default=True)
            read = row.get_text
            row.connect("changed", lambda _row: dialog.set_response_enabled("ok", bool(presets.clean_step(kind, read()))))
        else:
            if now and now not in [value for _shown, value in choices]:  # an app that's gone: it's still what the step says
                choices = [(now, now), *choices]
            row = Adw.ComboRow(title=self.t(f"presets_field_{kind}"), enable_search=True,
                               model=Gtk.StringList.new([shown for shown, _value in choices]),
                               expression=Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
            row.set_selected(next((n for n, (_shown, value) in enumerate(choices) if value == now), 0))
            read = lambda: choices[row.get_selected()][1] if choices else ""  # noqa: E731
        rows.append(row)
        dialog.set_extra_child(rows)
        dialog.set_response_enabled("ok", bool(presets.clean_step(kind, read())))
        dialog.connect("response", self.on_step_answer, kind, at, read)
        dialog.present(self)

    def on_step_answer(self, _dialog, answer, kind, at, read):
        step = presets.clean_step(kind, read()) if answer == "ok" else None
        if not step:
            return
        if at is None:
            self.preset["steps"].append(step)
        else:
            self.preset["steps"][at] = step
        self.refresh_steps()

    def confirm_delete(self):
        name = self.preset["name"] or self.t("presets_new")
        dialog = Adw.AlertDialog(heading=self.t("presets_delete_title", name=name), body=self.t("presets_delete_text"))
        dialog.add_response("cancel", self.t("preset_cancel"))
        dialog.add_response("delete", self.t("presets_delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_delete_answer)
        dialog.present(self)

    def on_delete_answer(self, _dialog, answer):
        if answer != "delete":
            return
        name = self.names_on_file.get(id(self.preset))
        remaining = [preset for preset in self.saved if preset["name"] != name]
        if len(remaining) != len(self.saved):  # right away, whatever else isn't finished yet
            try:
                presets.save(remaining)
            except OSError as e:
                brain.log.warning("presets window: %s", e)
                self.toast(self.t("presets_cant_save"))
                return
            self.saved = remaining
        del self.items[self.current]
        self.show_preset(min(self.current, len(self.items) - 1) if self.items else None)

    # ---- saving -------------------------------------------------------------

    def tidied(self):
        """The presets as they'd be saved: no empty keywords, no spaces around anything."""
        return [{**preset, "name": preset["name"].strip(),
                 "keywords": [keyword.strip() for keyword in preset["keywords"] if keyword.strip()]} for preset in self.items]

    def save(self, then=None):
        tidy = self.tidied()
        wrong = presets.problems(tidy)
        if wrong:
            at, key, fields = wrong[0]
            if at != self.current:
                self.show_preset(at)
            self.toast(self.t(key, **fields))
            return
        had = {presets._joined(keyword) for preset in self.saved for keyword in preset["keywords"]}
        new = [keyword for preset in tidy for keyword in preset["keywords"] if presets._joined(keyword) not in had]
        modes = [(key, mode["label"]) for key, mode in self.config.modes()]
        self.toasts.set_sensitive(False)  # it's saving these as they are now
        in_background(lambda: presets.understood_otherwise(new, modes, servers.Watch(), commands.all_projects()),
                      lambda taken, failed: self.checked_keywords(tidy, taken or [], then))

    def checked_keywords(self, tidy, taken, then):
        self.toasts.set_sensitive(True)
        if not taken:
            self.write(tidy, then)
            return
        said = ", ".join(f"“{keyword}”" for keyword in taken)
        dialog = Adw.AlertDialog(heading=self.t("presets_means_else_title"), body=self.t("presets_means_else_text", keywords=said))
        dialog.add_response("cancel", self.t("preset_cancel"))
        dialog.add_response("keep", self.t("presets_means_else_keep"))
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, answer: self.write(tidy, then) if answer == "keep" else None)
        dialog.present(self)

    def write(self, tidy, then):
        try:
            presets.save(tidy)
        except OSError as e:
            brain.log.warning("presets window: %s", e)
            self.toast(self.t("presets_cant_save"))
            return
        at = self.current
        self.items, self.saved = copy.deepcopy(tidy), tidy
        self.remember_names()
        self.show_preset(at)
        if then:
            then()
        else:
            self.toast(self.t("presets_saved"))

    def try_it(self):
        def answered(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error as e:
                brain.log.warning("presets window: the daemon didn't answer: %s", e.message)
                self.toast(self.t("presets_no_daemon"))

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call(BRAIN_NAME, OBJECT_PATH, BRAIN_NAME, "RunPreset", GLib.Variant("(s)", (self.preset["name"],)), None,
                 Gio.DBusCallFlags.NONE, 5000, None, answered)

    def on_close(self, _window):
        if self.tidied() == self.saved:
            return False
        dialog = Adw.AlertDialog(heading=self.t("presets_unsaved"))
        dialog.add_response("cancel", self.t("preset_cancel"))
        dialog.add_response("discard", self.t("presets_discard"))
        dialog.add_response("save", self.t("presets_save"))
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_close_answer)
        dialog.present(self)
        return True

    def on_close_answer(self, _dialog, answer):
        if answer == "discard":
            self.destroy()
        elif answer == "save":
            self.save(then=self.destroy)  # a problem to put right keeps it open, with a toast saying what it is


class PresetsApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=f"{BUS_NAME}.Presets")
        self.connect("activate", self.on_activate)

    def on_activate(self, _app):
        window = self.get_active_window() or PresetsWindow(self)
        window.present()


if __name__ == "__main__":
    sys.exit(PresetsApp().run(sys.argv))
