#!/usr/bin/env python3
"""The setup window: installing DeskAI the way you'd install an app, without a terminal.

Welcome → system check → AI choice → extras → installing → done. Opened again later it offers
to update or remove. The work itself is in installer.py.
"""

import locale
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer  # noqa: E402

ICON = installer.APP_ID
FLOW = ["welcome", "check", "ai", "extras"]  # the steps you go through by hand

STRINGS = {
    "en": {
        "window": "DeskAI Setup",
        "tagline": "A dock that helps you stay on what you're doing.",
        "version": "Version {version} · Free software (GPL-3.0-or-later)",
        "feature_modes": "Focus modes",
        "feature_modes_text": "Writing, Building, Studying: each with its own apps and rules",
        "feature_inbox": "Inbox on the dock",
        "feature_inbox_text": "WhatsApp messages and AI agents reach you right there",
        "feature_command": "Just say what you want",
        "feature_command_text": "Press Super+/ and type: set up my project, restart the server…",
        "feature_desk": "Pick up where you left off",
        "feature_desk_text": "Windows, terminals and servers come back after a logout",
        "language": "Language",
        "installed": "DeskAI {version} is installed",
        "installed_text": "You can update it, change your choices, or remove it.",
        "remove": "Remove DeskAI",
        "step": "Step {n} of {total}",
        "start": "Get started",
        "update": "Update",
        "continue": "Continue",
        "install": "Install",
        "check_title": "Checking your computer",
        "check_text": "DeskAI is made for GNOME 50 and 51.",
        "required": "Required",
        "recommended": "Recommended",
        "recommended_text": "DeskAI works without these, with a little less.",
        "checking": "Checking…",
        "check_gnome": "GNOME 50 or 51",
        "check_gnome_bad": "Found {found}. This version of DeskAI only runs on a GNOME 50 or 51 desktop.",
        "check_systemd": "Background services (systemd)",
        "check_systemd_bad": "DeskAI's background service needs systemd for your user.",
        "check_atspi": "Accessibility library",
        "check_atspi_text": "Reads terminal tabs, so each one comes back after a logout",
        "check_ptyxis": "Ptyxis terminal",
        "check_ptyxis_text": "The terminal that can come back after a logout",
        "check_chrome": "Google Chrome",
        "check_chrome_text": "The browser that can come back with its tabs after a logout",
        "install_package": "Install",
        "blocked": "DeskAI can't be installed here until the required items are sorted out.",
        "ai_title": "Smart features",
        "ai_text": "Claude can tell whether a window fits your mode, and which notifications matter. Your lists do the job without it.",
        "ai_claude": "Claude Code",
        "ai_claude_found": "Uses the Claude login you already have",
        "ai_claude_missing": "Claude Code isn't installed on this computer",
        "ai_api": "Anthropic API key",
        "ai_api_text": "You pay Anthropic for what you use",
        "ai_key": "API key",
        "ai_key_saved": "API key (saved; leave empty to keep it)",
        "ai_ollama": "Ollama (on this computer)",
        "ai_ollama_found": "Free, and nothing leaves your computer. Not as clever as Claude; uses about 2 GB of memory while it thinks",
        "ai_ollama_missing": "Install Ollama from ollama.com first, then open this window again",
        "ai_none": "No AI",
        "ai_none_text": "Modes and your allow and block lists still work",
        "ai_privacy": "With Claude, the titles of the windows you use and the notifications held back are sent to it, and so is what you type in the command bar, with the names of your modes, projects, servers and apps. Password managers are left out. With Ollama, none of it leaves this computer.",
        "extras_title": "Extras",
        "extras_text": "You can change these later by opening this window again.",
        "extra_agents": "AI agents report to the dock",
        "extra_agents_text": "{agents}: they tell you when they're done or need you. This adds hooks to their settings and keeps a backup.",
        "extra_routine": "Workday routine",
        "extra_routine_text": "On workday mornings the dock offers to set up your project like last time.",
        "extra_restore": "Pick up where you left off",
        "extra_restore_text": "Always on, and it asks first. Claude Code sessions resume with the same flags they had, including any that skip permission checks.",
        "work_install": "Installing",
        "work_remove": "Removing",
        "work_wait": "This takes a moment.",
        "work_failed": "Something went wrong",
        "retry": "Try again",
        "step_app": "Copying files",
        "step_ai": "Preparing the AI connection",
        "step_ollama": "Downloading the local AI model (about 1.4 GB)",
        "step_settings": "Saving your choices",
        "step_service": "Starting the background service",
        "step_hooks": "Connecting your AI agents",
        "step_finish": "Turning on the dock",
        "step_extension": "Turning off the dock",
        "step_files": "Removing files",
        "step_polkit": "Removing the database permission",
        "done_title": "DeskAI is ready",
        "done_text": "Log out and back in once. GNOME loads the dock when you log in, and it stays on from then on, with your apps in each mode and your projects ready to set up. It takes the place of Ubuntu Dock or any other dock you use, and removing DeskAI brings them back.",
        "logout": "Log out now",
        "later": "Later",
        "removed_title": "DeskAI is removed",
        "removed_text": "The dock is off and its files are gone.",
        "removed_deb": "The dock is off and your copy of it is gone. To take the app off this computer too, uninstall it in App Center or run: sudo apt remove deskai",
        "close": "Close",
        "remove_title": "Remove DeskAI?",
        "remove_text": "The dock, its background service and its hooks in your AI agents go away. Any dock it replaced comes back.",
        "remove_purge": "Also delete my modes and stats",
        "cancel": "Cancel",
        "remove_confirm": "Remove",
    },
    "id": {
        "window": "Setup DeskAI",
        "tagline": "Dock yang bantu kamu tetap fokus sama kerjaan.",
        "version": "Versi {version} · Software bebas (GPL-3.0-or-later)",
        "feature_modes": "Mode fokus",
        "feature_modes_text": "Writing, Building, Studying: tiap mode punya app dan aturan sendiri",
        "feature_inbox": "Inbox di dock",
        "feature_inbox_text": "Pesan WhatsApp dan AI agent langsung ngabarin di situ",
        "feature_command": "Tinggal ketik maunya apa",
        "feature_command_text": "Tekan Super+/ terus ketik: siapin project, restart server…",
        "feature_desk": "Lanjutin dari yang terakhir",
        "feature_desk_text": "Jendela, terminal, dan server balik lagi setelah logout",
        "language": "Bahasa",
        "installed": "DeskAI {version} udah terpasang",
        "installed_text": "Kamu bisa update, ganti pilihan, atau menghapusnya.",
        "remove": "Hapus DeskAI",
        "step": "Langkah {n} dari {total}",
        "start": "Mulai",
        "update": "Update",
        "continue": "Lanjut",
        "install": "Pasang",
        "check_title": "Ngecek komputer kamu",
        "check_text": "DeskAI dibuat untuk GNOME 50 dan 51.",
        "required": "Wajib",
        "recommended": "Disarankan",
        "recommended_text": "DeskAI tetap jalan tanpa ini, cuma fiturnya sedikit berkurang.",
        "checking": "Lagi ngecek…",
        "check_gnome": "GNOME 50 atau 51",
        "check_gnome_bad": "Yang ketemu {found}. Versi DeskAI ini cuma jalan di desktop GNOME 50 atau 51.",
        "check_systemd": "Layanan latar belakang (systemd)",
        "check_systemd_bad": "Layanan latar DeskAI butuh systemd untuk user kamu.",
        "check_atspi": "Library aksesibilitas",
        "check_atspi_text": "Baca tab terminal, biar tiap tab balik lagi setelah logout",
        "check_ptyxis": "Terminal Ptyxis",
        "check_ptyxis_text": "Terminal yang bisa balik lagi setelah logout",
        "check_chrome": "Google Chrome",
        "check_chrome_text": "Browser yang bisa balik lagi sama tab-tabnya setelah logout",
        "install_package": "Pasang",
        "blocked": "DeskAI belum bisa dipasang di sini sampai yang wajib beres.",
        "ai_title": "Fitur pintar",
        "ai_text": "Claude bisa nilai apakah sebuah jendela cocok sama mode kamu, dan notifikasi mana yang penting. Tanpa itu, daftar kamu sendiri yang dipakai.",
        "ai_claude": "Claude Code",
        "ai_claude_found": "Pakai login Claude yang udah kamu punya",
        "ai_claude_missing": "Claude Code belum terpasang di komputer ini",
        "ai_api": "API key Anthropic",
        "ai_api_text": "Bayar ke Anthropic sesuai pemakaian",
        "ai_key": "API key",
        "ai_key_saved": "API key (udah tersimpan; kosongin kalau nggak diganti)",
        "ai_ollama": "Ollama (di komputer ini)",
        "ai_ollama_found": "Gratis, dan datanya nggak keluar dari komputer. Nggak sepinter Claude; makan RAM sekitar 2 GB pas lagi mikir",
        "ai_ollama_missing": "Pasang Ollama dari ollama.com dulu, terus buka jendela ini lagi",
        "ai_none": "Tanpa AI",
        "ai_none_text": "Mode dan daftar izin/blokir kamu tetap jalan",
        "ai_privacy": "Kalau pakai Claude, judul jendela yang kamu pakai dan notifikasi yang ditahan dikirim ke sana. Yang kamu ketik di kolom perintah juga, bareng nama mode, project, server, dan app kamu. Aplikasi password nggak ikut. Kalau pakai Ollama, semuanya tetap di komputer ini.",
        "extras_title": "Tambahan",
        "extras_text": "Bisa diganti nanti dengan buka jendela ini lagi.",
        "extra_agents": "AI agent ngabarin ke dock",
        "extra_agents_text": "{agents}: ngabarin pas selesai atau butuh kamu. Ini nambahin hook ke pengaturan mereka, dan backup-nya disimpan.",
        "extra_routine": "Rutinitas kerja",
        "extra_routine_text": "Tiap pagi hari kerja, dock nawarin nyiapin project kamu kayak terakhir kali.",
        "extra_restore": "Lanjutin dari yang terakhir",
        "extra_restore_text": "Selalu nyala, dan selalu nanya dulu. Sesi Claude Code lanjut dengan flag yang sama, termasuk flag yang melewati izin.",
        "work_install": "Lagi masang",
        "work_remove": "Lagi menghapus",
        "work_wait": "Sebentar ya.",
        "work_failed": "Ada yang gagal",
        "retry": "Coba lagi",
        "step_app": "Nyalin file",
        "step_ai": "Nyiapin koneksi AI",
        "step_ollama": "Download model AI lokal (sekitar 1,4 GB)",
        "step_settings": "Nyimpen pilihan kamu",
        "step_service": "Nyalain layanan latar",
        "step_hooks": "Nyambungin AI agent kamu",
        "step_finish": "Nyalain dock",
        "step_extension": "Matiin dock",
        "step_files": "Menghapus file",
        "step_polkit": "Menghapus izin database",
        "done_title": "DeskAI udah siap",
        "done_text": "Logout terus login lagi sekali. GNOME baru muat dock pas login, dan seterusnya nyala sendiri, dengan app kamu di tiap mode dan project kamu siap dipakai. Dock ini gantiin Ubuntu Dock atau dock lain yang kamu pakai, dan kalau DeskAI dihapus, dock itu balik lagi.",
        "logout": "Logout sekarang",
        "later": "Nanti aja",
        "removed_title": "DeskAI udah dihapus",
        "removed_text": "Dock-nya udah mati dan file-nya udah dihapus.",
        "removed_deb": "Dock-nya udah mati dan salinannya udah dihapus. Buat menghapus app-nya dari komputer ini juga, uninstall di App Center atau jalanin: sudo apt remove deskai",
        "close": "Tutup",
        "remove_title": "Hapus DeskAI?",
        "remove_text": "Dock, layanan latar, dan hook di AI agent kamu dihapus. Dock yang tadinya digantiin balik lagi.",
        "remove_purge": "Hapus juga mode dan statistik aku",
        "cancel": "Batal",
        "remove_confirm": "Hapus",
    },
}
LANGUAGES = ["en", "id"]
LANGUAGE_NAMES = ["English", "Bahasa Indonesia"]


def system_language():
    code = (locale.getlocale()[0] or "").split("_")[0]
    return code if code in STRINGS else "en"


def label(text, *classes, **props):
    widget = Gtk.Label(label=text, wrap=True, xalign=props.pop("xalign", 0), **props)
    for css in classes:
        widget.add_css_class(css)
    return widget


def in_background(work, finished):
    """Runs work() on a thread, then finished(result or None, error or None) on the UI thread."""
    def run():
        result, error = None, None
        try:
            result = work()
        except (installer.SetupError, OSError, subprocess.SubprocessError) as e:
            error = str(e)
        GLib.idle_add(lambda: finished(result, error) and False)
    threading.Thread(target=run, daemon=True).start()


class SetupWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=620, default_height=720)
        choices = installer.current_choices()
        self.installed = installer.is_installed()
        self.lang = choices["language"] if choices["language"] in STRINGS else system_language()
        self.claude = installer.find_claude()
        self.ollama = installer.find_ollama()
        self.saved_key = installer.has_api_key()
        self.agents = installer.detected_agents()
        self.ai = choices["ai"] or ("claude" if self.claude else "none")
        if (self.ai == "claude" and not self.claude) or (self.ai == "ollama" and not self.ollama):
            self.ai = "none"
        self.api_key = ""
        self.hooks = installer.hooks_installed() if self.installed else bool(self.agents)
        self.routine = bool(choices["routine"])
        self.checks = None
        self.page = "welcome"
        self.primary_action = None

        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(Path(__file__).resolve().parent))
        self.toast = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.back = Gtk.Button(icon_name="go-previous-symbolic")
        self.back.connect("clicked", lambda _b: self.go_back())
        header.pack_start(self.back)
        toolbar.add_top_bar(header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, transition_duration=220, hhomogeneous=True, vhomogeneous=False)
        self.toast.set_child(self.stack)
        toolbar.set_content(self.toast)

        bar = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10, margin_start=16, margin_end=16)
        self.step_label = label("", "dim-label", "caption", hexpand=True)
        self.secondary = Gtk.Button(visible=False)
        self.secondary.add_css_class("pill")
        self.primary = Gtk.Button()
        self.primary.add_css_class("suggested-action")
        self.primary.add_css_class("pill")
        self.primary.connect("clicked", lambda _b: self.primary_action and self.primary_action())
        bar.append(self.step_label)
        bar.append(self.secondary)
        bar.append(self.primary)
        toolbar.add_bottom_bar(bar)
        self.set_content(toolbar)
        self.build_pages()
        self.show_page("welcome")

    def t(self, key, **params):
        return STRINGS[self.lang][key].format(**params)

    # ---- pages ---------------------------------------------------------------------------

    def build_pages(self):
        while (child := self.stack.get_first_child()):
            self.stack.remove(child)
        self.set_title(self.t("window"))
        self.stack.add_named(self.welcome_page(), "welcome")
        self.stack.add_named(self.check_page(), "check")
        self.stack.add_named(self.ai_page(), "ai")
        self.stack.add_named(self.extras_page(), "extras")
        self.stack.add_named(self.work_page(), "work")
        self.stack.add_named(self.done_page(), "done")

    def scrolled(self, *children, spacing=18):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, margin_top=18, margin_bottom=24,
                      margin_start=18, margin_end=18)
        for child in children:
            box.append(child)
        clamp = Adw.Clamp(maximum_size=540, child=box)
        return Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)

    def heading(self, title, text):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(label(title, "title-1"))
        box.append(label(text, "dim-label"))
        return box

    def welcome_page(self):
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER)
        top.append(Gtk.Image(icon_name=ICON, pixel_size=112))
        top.append(label("DeskAI", "title-1", xalign=0.5, justify=Gtk.Justification.CENTER))
        top.append(label(self.t("tagline"), "dim-label", xalign=0.5, justify=Gtk.Justification.CENTER))
        features = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        features.add_css_class("boxed-list")
        for icon, name in (("find-location-symbolic", "modes"), ("mail-unread-symbolic", "inbox"),
                           ("system-search-symbolic", "command"), ("view-restore-symbolic", "desk")):
            row = Adw.ActionRow(title=self.t(f"feature_{name}"), subtitle=self.t(f"feature_{name}_text"))
            row.add_prefix(Gtk.Image(icon_name=icon))
            features.append(row)
        options = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        options.add_css_class("boxed-list")
        language = Adw.ComboRow(title=self.t("language"), model=Gtk.StringList.new(LANGUAGE_NAMES),
                                selected=LANGUAGES.index(self.lang))
        language.connect("notify::selected", self.on_language)
        options.append(language)
        children = [top]
        if self.installed:
            status = Adw.ActionRow(title=self.t("installed", version=installer.installed_version() or installer.version()),
                                   subtitle=self.t("installed_text"))
            status.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic"))
            remove = Gtk.Button(label=self.t("remove"), valign=Gtk.Align.CENTER)
            remove.add_css_class("destructive-action")
            remove.connect("clicked", lambda _b: self.confirm_remove())
            status.add_suffix(remove)
            box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            box.add_css_class("boxed-list")
            box.append(status)
            children.append(box)
        children += [features, options, label(self.t("version", version=installer.version()), "dim-label", "caption",
                                              xalign=0.5, justify=Gtk.Justification.CENTER)]
        return self.scrolled(*children)

    def check_page(self):
        self.check_rows = {}
        required = Adw.PreferencesGroup(title=self.t("required"))
        for check_id, title in (("gnome", "check_gnome"), ("systemd", "check_systemd")):
            required.add(self.check_row(check_id, title, ""))
        recommended = Adw.PreferencesGroup(title=self.t("recommended"), description=self.t("recommended_text"))
        for check_id in ("atspi", "ptyxis", "chrome"):
            recommended.add(self.check_row(check_id, f"check_{check_id}", self.t(f"check_{check_id}_text")))
        self.blocked = Adw.Banner(title=self.t("blocked"), revealed=False)
        return self.scrolled(self.heading(self.t("check_title"), self.t("check_text")), self.blocked, required, recommended)

    def check_row(self, check_id, title, subtitle):
        row = Adw.ActionRow(title=self.t(title), subtitle=subtitle)
        spinner = Adw.Spinner()
        row.add_suffix(spinner)
        self.check_rows[check_id] = (row, spinner)
        return row

    def ai_page(self):
        group = Adw.PreferencesGroup(description=self.t("ai_privacy"))
        lead = None
        self.ai_buttons = {}

        def choice(key, title, subtitle, sensitive=True):
            nonlocal lead
            button = Gtk.CheckButton(active=self.ai == key, group=lead, valign=Gtk.Align.CENTER, sensitive=sensitive)
            lead = lead or button
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable_widget=button, sensitive=sensitive)
            row.add_prefix(button)
            button.connect("toggled", self.on_ai, key)
            self.ai_buttons[key] = button
            return row

        group.add(choice("claude", self.t("ai_claude"),
                         self.t("ai_claude_found") if self.claude else self.t("ai_claude_missing"), bool(self.claude)))
        group.add(choice("api", self.t("ai_api"), self.t("ai_api_text")))
        self.key_row = Adw.PasswordEntryRow(title=self.t("ai_key_saved") if self.saved_key else self.t("ai_key"),
                                            visible=self.ai == "api")
        self.key_row.connect("changed", self.on_key)
        group.add(self.key_row)
        group.add(choice("ollama", self.t("ai_ollama"),
                         self.t("ai_ollama_found") if self.ollama else self.t("ai_ollama_missing"), bool(self.ollama)))
        group.add(choice("none", self.t("ai_none"), self.t("ai_none_text")))
        return self.scrolled(self.heading(self.t("ai_title"), self.t("ai_text")), group)

    def extras_page(self):
        group = Adw.PreferencesGroup()
        if self.agents:
            hooks = Adw.SwitchRow(title=self.t("extra_agents"), active=self.hooks,
                                  subtitle=self.t("extra_agents_text", agents=", ".join(self.agents)))
            hooks.connect("notify::active", lambda row, _p: setattr(self, "hooks", row.get_active()))
            group.add(hooks)
        routine = Adw.SwitchRow(title=self.t("extra_routine"), subtitle=self.t("extra_routine_text"), active=self.routine)
        routine.connect("notify::active", lambda row, _p: setattr(self, "routine", row.get_active()))
        group.add(routine)
        group.add(Adw.ActionRow(title=self.t("extra_restore"), subtitle=self.t("extra_restore_text")))
        return self.scrolled(self.heading(self.t("extras_title"), self.t("extras_text")), group)

    def work_page(self):
        self.work_title = label("", "title-1")
        self.work_text = label("", "dim-label")
        self.work_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.work_list.add_css_class("boxed-list")
        self.work_error = label("", "error", visible=False, selectable=True)
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        title.append(self.work_title)
        title.append(self.work_text)
        return self.scrolled(title, self.work_list, self.work_error)

    def done_page(self):
        self.done_status = Adw.StatusPage(icon_name="emblem-ok-symbolic", vexpand=True)
        return self.done_status

    # ---- moving between pages ------------------------------------------------------------

    def show_page(self, page):
        self.page = page
        self.stack.set_visible_child_name(page)
        self.back.set_visible(page in FLOW[1:])
        self.secondary.set_visible(False)
        if page in FLOW[1:]:
            self.step_label.set_label(self.t("step", n=FLOW.index(page), total=len(FLOW) - 1))
        else:
            self.step_label.set_label("")
        if page == "welcome":
            self.set_primary(self.t("update") if self.installed else self.t("start"), lambda: self.show_page("check"))
        elif page == "check":
            self.set_primary(self.t("continue"), lambda: self.show_page("ai"))
            self.run_checks()
        elif page == "ai":
            self.set_primary(self.t("continue"), lambda: self.show_page("extras"))
            self.update_ai_primary()
        elif page == "extras":
            self.set_primary(self.t("update") if self.installed else self.t("install"), self.start_install)

    def set_primary(self, text, action, sensitive=True):
        self.primary.set_label(text)
        self.primary.set_sensitive(sensitive)
        self.primary.set_visible(True)
        self.primary_action = action

    def go_back(self):
        if self.page in FLOW[1:]:
            self.show_page(FLOW[FLOW.index(self.page) - 1])

    def on_language(self, row, _param):
        chosen = LANGUAGES[row.get_selected()]
        if chosen != self.lang:
            self.lang = chosen
            GLib.idle_add(self.apply_language)

    def apply_language(self):
        self.build_pages()
        self.show_page("welcome")
        return False

    # ---- the system check ----------------------------------------------------------------

    def run_checks(self):
        self.primary.set_sensitive(False)
        in_background(installer.check_system, self.show_checks)

    def show_checks(self, checks, _error):
        self.checks = {check.id: check for check in checks or []}
        for check_id, (row, old) in self.check_rows.items():
            check = self.checks.get(check_id)
            row.remove(old)
            suffix = self.check_suffix(check_id, check)
            row.add_suffix(suffix)
            self.check_rows[check_id] = (row, suffix)
            if check and not check.ok and check.required:
                bad = "check_gnome_bad" if check_id == "gnome" else "check_systemd_bad"
                row.set_subtitle(self.t(bad, found=check.detail))
        blocked = any(check.required and not check.ok for check in self.checks.values())
        self.blocked.set_revealed(blocked)
        if self.page == "check":
            self.primary.set_sensitive(not blocked)

    def check_suffix(self, check_id, check):
        if check and check.ok:
            icon = Gtk.Image(icon_name="emblem-ok-symbolic")
            icon.add_css_class("success")
            return icon
        if check and check.package:
            button = Gtk.Button(label=self.t("install_package"), valign=Gtk.Align.CENTER)
            button.connect("clicked", self.on_install_package, check.package)
            return button
        icon = Gtk.Image(icon_name="dialog-error-symbolic" if check and check.required else "dialog-information-symbolic")
        icon.add_css_class("error" if check and check.required else "dim-label")
        return icon

    def on_install_package(self, button, package):
        button.set_sensitive(False)

        def done(_result, error):
            if error:
                self.toast.add_toast(Adw.Toast(title=error, timeout=8))
            self.run_checks()
        in_background(lambda: installer.install_package(package), done)

    # ---- the AI choice -------------------------------------------------------------------

    def on_ai(self, button, key):
        if button.get_active():
            self.ai = key
            self.key_row.set_visible(key == "api")
            self.update_ai_primary()

    def on_key(self, row):
        self.api_key = row.get_text().strip()
        self.update_ai_primary()

    def update_ai_primary(self):
        if self.page == "ai":
            self.primary.set_sensitive(self.ai != "api" or bool(self.api_key) or self.saved_key)

    # ---- doing the work ------------------------------------------------------------------

    def start_install(self):
        options = installer.Options(language=self.lang, ai=self.ai, api_key=self.api_key, hooks=self.hooks and bool(self.agents),
                                    routine=self.routine)
        skipped = set()
        if not options.hooks and not installer.hooks_installed():
            skipped.add("hooks")
        if options.ai != "api":
            skipped.add("ai")
        if options.ai != "ollama":
            skipped.add("ollama")
        steps = [step for step in installer.install_steps(options) if step[0] not in skipped]
        self.run_work(steps, self.t("work_install"), self.install_finished, self.start_install)

    def install_finished(self):
        self.installed = True
        self.done_status.set_title(self.t("done_title"))
        self.done_status.set_description(self.t("done_text"))
        self.done_status.set_icon_name(ICON)
        self.show_page("done")
        self.set_primary(self.t("logout"), self.log_out)
        self.secondary.set_label(self.t("later"))
        self.secondary.set_visible(True)
        self.connect_secondary(self.close)

    def connect_secondary(self, action):
        if getattr(self, "secondary_handler", None):
            self.secondary.disconnect(self.secondary_handler)
        self.secondary_handler = self.secondary.connect("clicked", lambda _b: action())

    def log_out(self):
        subprocess.Popen(["gnome-session-quit", "--logout"], start_new_session=True)

    def confirm_remove(self):
        dialog = Adw.AlertDialog(heading=self.t("remove_title"), body=self.t("remove_text"))
        dialog.add_response("cancel", self.t("cancel"))
        dialog.add_response("remove", self.t("remove_confirm"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        purge = Gtk.CheckButton(label=self.t("remove_purge"))
        dialog.set_extra_child(purge)

        def answered(_dialog, response):
            if response == "remove":
                self.start_remove(purge.get_active())
        dialog.connect("response", answered)
        dialog.present(self)

    def start_remove(self, purge):
        steps = installer.uninstall_steps(purge)
        if not installer.POLKIT_RULE.exists():
            steps = [step for step in steps if step[0] != "polkit"]
        if not purge:
            steps = [step for step in steps if step[0] != "settings"]
        self.run_work(steps, self.t("work_remove"), self.remove_finished, lambda: self.start_remove(purge))

    def remove_finished(self):
        self.installed = False
        self.done_status.set_title(self.t("removed_title"))
        self.done_status.set_description(self.t("removed_deb") if installer.is_system_payload() else self.t("removed_text"))
        self.done_status.set_icon_name("user-trash-symbolic")
        self.show_page("done")
        self.set_primary(self.t("close"), self.close)

    def run_work(self, steps, title, finished, retry):
        self.work_title.set_label(title)
        self.work_text.set_label(self.t("work_wait"))
        self.work_error.set_visible(False)
        while (child := self.work_list.get_first_child()):
            self.work_list.remove(child)
        rows = {}
        for step_id, _fn in steps:
            key = f"step_{step_id}"
            row = Adw.ActionRow(title=self.t(key) if key in STRINGS[self.lang] else step_id)
            rows[step_id] = row
            self.work_list.append(row)
        self.show_page("work")
        self.primary.set_visible(False)
        marks = {}

        def mark(step_id, state, error):
            row = rows[step_id]
            if step_id in marks:
                row.remove(marks[step_id])
            if state == "running":
                widget = Adw.Spinner()
            else:
                widget = Gtk.Image(icon_name="emblem-ok-symbolic" if state == "done" else "dialog-error-symbolic")
                widget.add_css_class("success" if state == "done" else "error")
            row.add_suffix(widget)
            marks[step_id] = widget
            if error:
                self.work_error.set_label(error)
                self.work_error.set_visible(True)
            return False

        def work():
            return installer.run_steps(steps, lambda *args: GLib.idle_add(mark, *args))

        def done(ok, error):
            if ok:
                finished()
                return
            self.work_title.set_label(self.t("work_failed"))
            self.work_text.set_label("")
            if error:
                self.work_error.set_label(error)
                self.work_error.set_visible(True)
            self.set_primary(self.t("retry"), retry)
        in_background(work, done)


class SetupApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=f"{installer.APP_ID}.Setup")
        self.connect("activate", self.on_activate)

    def on_activate(self, _app):
        window = self.get_active_window() or SetupWindow(self)
        window.present()


if __name__ == "__main__":
    sys.exit(SetupApp().run(sys.argv))
