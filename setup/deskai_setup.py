#!/usr/bin/env python3
"""The setup window: installing DeskAI the way you'd install an app, without a terminal.

Welcome → system check → AI choice → extras → installing → done. Opened again with a newer version
it shows what's new and updates with one click, and otherwise it offers to change your choices or
remove DeskAI. The work itself is in installer.py.
"""

import locale
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer  # noqa: E402
import localai  # noqa: E402

ICON = installer.APP_ID
FLOW = ["welcome", "check", "ai", "extras"]  # the steps you go through by hand
# What an update stops to ask, by the release that brought something new to choose. Apart from these it asks nothing, and
# everything you chose stays as it is. The AI page only comes up for someone who has no AI.
ASKED_ON_UPDATE = {"0.6.0": "ai"}
LOGOUT_WAIT_S = 30  # how long GNOME gets to log you out before the other docks come back


def new_spinner():
    """Adw.Spinner came with libadwaita 1.6, and Ubuntu 24.04 has 1.5."""
    if hasattr(Adw, "Spinner"):
        return Adw.Spinner()
    spinner = Gtk.Spinner()
    spinner.start()
    return spinner


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
        "installed_text": "You can change your choices or remove it.",
        "remove": "Remove DeskAI",
        "step": "Step {n} of {total}",
        "start": "Get started",
        "update": "Update",
        "change": "Change my choices",
        "save": "Save",
        "continue": "Continue",
        "install": "Install",
        "whatsnew_title": "DeskAI {version} is ready",
        "whatsnew_text": "You have version {version} now. Your modes, apps and choices stay as they are.",
        "whatsnew_release": "What's new in {version}",
        "whatsnew_more": "Change my choices or remove DeskAI",
        "check_title": "Checking your computer",
        "check_text": "DeskAI is made for GNOME 46, 50 and 51.",
        "required": "Required",
        "recommended": "Recommended",
        "recommended_text": "DeskAI works without these, with a little less.",
        "checking": "Checking…",
        "check_gnome": "GNOME 46, 50 or 51",
        "check_gnome_bad": "Found {found}. This version of DeskAI only runs on a GNOME 46, 50 or 51 desktop.",
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
        "ai_text": "In the command bar, the AI answers what your lists can't. DeskAI works without one too.",
        "ai_claude": "Claude Code",
        "ai_claude_found": "Uses the Claude login you already have. It also tells whether a window fits your mode, and which notifications matter",
        "ai_claude_missing": "Claude Code isn't installed on this computer",
        "ai_api": "Anthropic API key",
        "ai_api_text": "You pay Anthropic for what you use",
        "ai_key": "API key",
        "ai_key_saved": "API key (saved; leave empty to keep it)",
        "ai_ollama": "Ollama (on this computer)",
        "ai_ollama_get": "Download the local AI (Ollama)",
        "ai_ollama_found": "Free, and nothing leaves your computer. Not as clever as Claude; uses about 2 GB of memory while it thinks",
        "ai_ollama_download": "Free, and nothing leaves your computer. Not as clever as Claude; uses about 2 GB of memory while it thinks. Downloads about {size} in the background, so you can carry on meanwhile",
        "ai_not_this_computer": "Ollama isn't made for this kind of computer",
        "ai_needs_zstd": "Install the zstd package first, then open this window again",
        "ai_small_memory": "This computer doesn't have enough memory for it",
        "ai_no_room": "There isn't enough room on the disk: it downloads about {size}",
        "ai_none": "No AI",
        "ai_none_text": "Nothing to download. Modes and your allow and block lists still work, and you can get the AI later by opening this window again",
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
        "work_update": "Updating",
        "work_save": "Saving",
        "work_remove": "Removing",
        "work_wait": "This takes a moment.",
        "work_failed": "Something went wrong",
        "retry": "Try again",
        "step_app": "Copying files",
        "step_ai": "Preparing the AI connection",
        "step_ollama": "Getting the local AI ready",
        "step_settings": "Saving your choices",
        "step_service": "Starting the background service",
        "step_hooks": "Connecting your AI agents",
        "step_finish": "Turning on the dock",
        "step_extension": "Turning off the dock",
        "step_local_ai": "Removing the local AI",
        "step_files": "Removing files",
        "step_polkit": "Removing the database permission",
        "done_title": "DeskAI is ready",
        "done_text": "Log out and back in once, after saving your work. GNOME loads the dock when you log in, and it stays on from then on, with your apps in each mode and your projects ready to set up. It takes the place of Ubuntu Dock or any other dock you use for as long as it's here, and removing DeskAI brings them back.",
        "updated_title": "DeskAI {version} is installed",
        "updated_text": "Log out and back in once, after saving your work, and the new version takes over.",
        "saved_title": "Your choices are saved",
        "saved_text": "They're in use now.",
        "local_ai_starting": "The local AI's download is starting…",
        "local_ai_getting": "Downloading the local AI: {done} of {total}",
        "local_ai_unpacking": "Unpacking the local AI…",
        "local_ai_waiting": "Waiting for the internet, to download the local AI…",
        "local_ai_metered": "Waiting for a connection that isn't paid by the gigabyte, to download the local AI…",
        "local_ai_ready": "The local AI is ready.",
        "local_ai_note": "You can log out while it downloads: it carries on when you log back in, and the dock says when it's ready.",
        "logout": "Log out now",
        "later": "Later",
        "removed_title": "DeskAI is removed",
        "removed_text": "The dock is off and its files are gone.",
        "removed_deb": "The dock is off and your copy of it is gone. To take the app off this computer too, uninstall it in App Center or run: sudo apt remove deskai",
        "close": "Close",
        "remove_title": "Remove DeskAI?",
        "remove_text": "The dock, its background service and its hooks in your AI agents go away. Any dock it replaced comes back.",
        "remove_local_ai": "The local AI DeskAI downloaded goes too.",
        "remove_purge": "Also delete my modes, presets and stats",
        "cancel": "Cancel",
        "remove_confirm": "Remove",
        "trouble_no_installer": "Install {package} with your package manager.",
        "trouble_package": "Couldn't install {package}.",
        "trouble_package_busy": "Another program is installing software right now. Wait for it to finish, then try again.",
        "trouble_package_missing": "Your software sources don't have this package. Update them, then try again.",
        "trouble_dismissed": "The password window was closed, so nothing changed.",
        "trouble_not_allowed": "Couldn't get permission to do this on this computer.",
        "trouble_no_key": "No API key entered.",
        "trouble_key_spaces": "That API key has spaces in it.",
        "trouble_venv": "Python couldn't make a virtual environment. On Ubuntu and Debian, install python3-venv and try again.",
        "trouble_anthropic": "Couldn't download the anthropic package the API key needs.",
        "trouble_ollama_off": "Ollama isn't running. Start it and try again.",
        "trouble_download": "Couldn't download the local AI.",
        "trouble_bad_download": "The local AI's download came out damaged. Try again.",
        "trouble_cant_write": "DeskAI wasn't allowed to write in its own folder.",
        "trouble_model_missing": "Ollama doesn't have this model. Choose another option under Smart features.",
        "trouble_service": "DeskAI's background service wouldn't start.",
        "trouble_no_session": "Couldn't reach your desktop session's services. Log out and back in, then try again.",
        "trouble_hooks": "Some AI agents may not report to the dock, since their settings couldn't be changed.",
        "trouble_hooks_left": "DeskAI's hooks may still be in some AI agents' settings, since those couldn't be changed.",
        "trouble_polkit_left": "The permission to start your database without a password is still there, since it couldn't be removed.",
        "trouble_no_connection": "Couldn't reach the internet. Check your connection and try again.",
        "trouble_no_room": "There isn't enough room on the disk. Free some up and try again.",
        "trouble_no_permission": "DeskAI wasn't allowed to change a file it needs.",
        "trouble_too_long": "That took too long, so it was stopped. Try again.",
        "trouble_not_there": "A file or program DeskAI needs is missing.",
        "trouble_unknown": "This is a problem DeskAI doesn't recognize.",
        "trouble_log": "Details for a bug report are in {path}.",
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
        "installed_text": "Kamu bisa ganti pilihan atau menghapusnya.",
        "remove": "Hapus DeskAI",
        "step": "Langkah {n} dari {total}",
        "start": "Mulai",
        "update": "Update",
        "change": "Ganti pilihan",
        "save": "Simpan",
        "continue": "Lanjut",
        "install": "Pasang",
        "whatsnew_title": "DeskAI {version} udah siap",
        "whatsnew_text": "Yang terpasang sekarang versi {version}. Mode, app, dan pilihan kamu tetap kayak gini.",
        "whatsnew_release": "Yang baru di {version}",
        "whatsnew_more": "Ganti pilihan atau hapus DeskAI",
        "check_title": "Ngecek komputer kamu",
        "check_text": "DeskAI dibuat untuk GNOME 46, 50, dan 51.",
        "required": "Wajib",
        "recommended": "Disarankan",
        "recommended_text": "DeskAI tetap jalan tanpa ini, cuma fiturnya sedikit berkurang.",
        "checking": "Lagi ngecek…",
        "check_gnome": "GNOME 46, 50, atau 51",
        "check_gnome_bad": "Yang ketemu {found}. Versi DeskAI ini cuma jalan di desktop GNOME 46, 50, atau 51.",
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
        "ai_text": "Di kolom perintah, AI ngejawab yang nggak bisa dijawab daftar kamu. Tanpa AI, DeskAI juga tetap jalan.",
        "ai_claude": "Claude Code",
        "ai_claude_found": "Pakai login Claude yang udah kamu punya. Dia juga bisa nilai apakah sebuah jendela cocok sama mode kamu, dan notifikasi mana yang penting",
        "ai_claude_missing": "Claude Code belum terpasang di komputer ini",
        "ai_api": "API key Anthropic",
        "ai_api_text": "Bayar ke Anthropic sesuai pemakaian",
        "ai_key": "API key",
        "ai_key_saved": "API key (udah tersimpan; kosongin kalau nggak diganti)",
        "ai_ollama": "Ollama (di komputer ini)",
        "ai_ollama_get": "Download AI lokal (Ollama)",
        "ai_ollama_found": "Gratis, dan datanya nggak keluar dari komputer. Nggak sepinter Claude; makan RAM sekitar 2 GB pas lagi mikir",
        "ai_ollama_download": "Gratis, dan datanya nggak keluar dari komputer. Nggak sepinter Claude; makan RAM sekitar 2 GB pas lagi mikir. Download sekitar {size} di belakang, jadi kamu bisa sambil ngerjain yang lain",
        "ai_not_this_computer": "Ollama nggak dibuat buat jenis komputer ini",
        "ai_needs_zstd": "Pasang paket zstd dulu, terus buka jendela ini lagi",
        "ai_small_memory": "RAM komputer ini nggak cukup buat itu",
        "ai_no_room": "Ruang disknya nggak cukup: download-nya sekitar {size}",
        "ai_none": "Tanpa AI",
        "ai_none_text": "Nggak ada yang di-download. Mode dan daftar izin/blokir kamu tetap jalan, dan AI-nya bisa diambil nanti dengan buka jendela ini lagi",
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
        "work_update": "Lagi update",
        "work_save": "Lagi nyimpen",
        "work_remove": "Lagi menghapus",
        "work_wait": "Sebentar ya.",
        "work_failed": "Ada yang gagal",
        "retry": "Coba lagi",
        "step_app": "Nyalin file",
        "step_ai": "Nyiapin koneksi AI",
        "step_ollama": "Nyiapin AI lokal",
        "step_settings": "Nyimpen pilihan kamu",
        "step_service": "Nyalain layanan latar",
        "step_hooks": "Nyambungin AI agent kamu",
        "step_finish": "Nyalain dock",
        "step_extension": "Matiin dock",
        "step_local_ai": "Menghapus AI lokal",
        "step_files": "Menghapus file",
        "step_polkit": "Menghapus izin database",
        "done_title": "DeskAI udah siap",
        "done_text": "Logout terus login lagi sekali, setelah nyimpen kerjaan kamu. GNOME baru muat dock pas login, dan seterusnya nyala sendiri, dengan app kamu di tiap mode dan project kamu siap dipakai. Dock ini gantiin Ubuntu Dock atau dock lain yang kamu pakai selama masih terpasang, dan kalau DeskAI dihapus, dock itu balik lagi.",
        "updated_title": "DeskAI {version} udah terpasang",
        "updated_text": "Logout terus login lagi sekali, setelah nyimpen kerjaan kamu, dan versi barunya langsung jalan.",
        "saved_title": "Pilihan kamu udah disimpan",
        "saved_text": "Sekarang udah kepake.",
        "local_ai_starting": "Download AI lokal lagi dimulai…",
        "local_ai_getting": "Lagi download AI lokal: {done} dari {total}",
        "local_ai_unpacking": "Lagi ngebongkar AI lokal…",
        "local_ai_waiting": "Nunggu internet buat download AI lokal…",
        "local_ai_metered": "Nunggu koneksi yang nggak berkuota buat download AI lokal…",
        "local_ai_ready": "AI lokalnya udah siap.",
        "local_ai_note": "Kamu bisa logout pas lagi download: nanti lanjut lagi begitu login, dan dock ngasih tau kalau udah siap.",
        "logout": "Logout sekarang",
        "later": "Nanti aja",
        "removed_title": "DeskAI udah dihapus",
        "removed_text": "Dock-nya udah mati dan file-nya udah dihapus.",
        "removed_deb": "Dock-nya udah mati dan salinannya udah dihapus. Buat menghapus app-nya dari komputer ini juga, uninstall di App Center atau jalanin: sudo apt remove deskai",
        "close": "Tutup",
        "remove_title": "Hapus DeskAI?",
        "remove_text": "Dock, layanan latar, dan hook di AI agent kamu dihapus. Dock yang tadinya digantiin balik lagi.",
        "remove_local_ai": "AI lokal yang di-download DeskAI ikut dihapus.",
        "remove_purge": "Hapus juga mode, preset dan statistik aku",
        "cancel": "Batal",
        "remove_confirm": "Hapus",
        "trouble_no_installer": "Pasang {package} pakai package manager kamu.",
        "trouble_package": "Gagal masang {package}.",
        "trouble_package_busy": "Ada program lain yang lagi masang software. Tunggu sampai selesai, terus coba lagi.",
        "trouble_package_missing": "Sumber software kamu nggak punya paket ini. Update dulu, terus coba lagi.",
        "trouble_dismissed": "Jendela password-nya ditutup, jadi nggak ada yang berubah.",
        "trouble_not_allowed": "Nggak dapet izin buat ngelakuin ini di komputer ini.",
        "trouble_no_key": "API key belum diisi.",
        "trouble_key_spaces": "API key itu ada spasinya.",
        "trouble_venv": "Python nggak bisa bikin virtual environment. Di Ubuntu dan Debian, pasang python3-venv dulu, terus coba lagi.",
        "trouble_anthropic": "Gagal download paket anthropic yang dibutuhin API key.",
        "trouble_ollama_off": "Ollama lagi nggak jalan. Nyalain dulu, terus coba lagi.",
        "trouble_download": "Gagal download AI lokal.",
        "trouble_bad_download": "Hasil download AI lokalnya rusak. Coba lagi.",
        "trouble_cant_write": "DeskAI nggak diizinin nulis di folder-nya sendiri.",
        "trouble_model_missing": "Ollama nggak punya model ini. Pilih opsi lain di Fitur pintar.",
        "trouble_service": "Layanan latar DeskAI nggak mau nyala.",
        "trouble_no_session": "Nggak bisa nyambung ke layanan sesi desktop kamu. Logout terus login lagi, baru coba lagi.",
        "trouble_hooks": "Sebagian AI agent mungkin nggak ngabarin ke dock, karena pengaturannya nggak bisa diubah.",
        "trouble_hooks_left": "Hook DeskAI mungkin masih ada di pengaturan sebagian AI agent, karena pengaturannya nggak bisa diubah.",
        "trouble_polkit_left": "Izin buat nyalain database tanpa password masih ada, karena nggak bisa dihapus.",
        "trouble_no_connection": "Nggak bisa nyambung ke internet. Cek koneksi kamu, terus coba lagi.",
        "trouble_no_room": "Ruang disknya nggak cukup. Kosongin dulu, terus coba lagi.",
        "trouble_no_permission": "DeskAI nggak diizinin ngubah file yang dia butuhin.",
        "trouble_too_long": "Kelamaan, jadi dihentiin. Coba lagi.",
        "trouble_not_there": "Ada file atau program yang dibutuhin DeskAI, tapi nggak ada.",
        "trouble_unknown": "Ini masalah yang DeskAI nggak kenal.",
        "trouble_log": "Detail buat laporan bug ada di {path}.",
    },
}
LANGUAGES = ["en", "id"]
LANGUAGE_NAMES = ["English", "Bahasa Indonesia"]


def system_language():
    code = (locale.getlocale()[0] or "").split("_")[0]
    return code if code in STRINGS else "en"


def in_gigabytes(language, size):
    """1.4 GB, or 1,4 GB. Under a gigabyte, in megabytes."""
    words = f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"
    return words.replace(".", ",") if language == "id" else words


def label(text, *classes, **props):
    widget = Gtk.Label(label=text, wrap=True, xalign=props.pop("xalign", 0), **props)
    for css in classes:
        widget.add_css_class(css)
    return widget


def in_background(work, finished):
    """Runs work() on a thread, then finished(result or None, the SetupError or None) on the UI thread."""
    def run():
        result, error = None, None
        try:
            result = work()
        except Exception as e:  # anything: the window would wait for this thread forever
            error = installer.report(e)
        GLib.idle_add(lambda: finished(result, error) and False)
    threading.Thread(target=run, daemon=True).start()


class SetupWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=620, default_height=720)
        choices = installer.current_choices()
        self.installed = bool(installer.installed_version())
        self.update_from = installer.update_from()
        self.lang = choices["language"] if choices["language"] in STRINGS else system_language()
        self.claude = installer.find_claude()
        # With it already your choice, nothing's up for offer, even if Ollama happens to be off right now and can't show what it has.
        self.local_ai = {"download": 0, "why_not": ""} if choices["ai"] == "ollama" else localai.offer()
        self.saved_key = installer.has_api_key()
        self.agents = installer.detected_agents()
        self.ai = choices["ai"] or self.usual_ai()
        if (self.ai == "claude" and not self.claude) or (self.ai == "ollama" and self.local_ai["why_not"]):
            self.ai = "none"
        self.flow = FLOW
        self.update_flow = ["whatsnew", *self.asked_on_update(choices["ai"])]
        self.docks_timer = 0
        self.connect("close-request", lambda _window: self.give_docks_back() or False)
        self.api_key = ""
        self.hooks = installer.hooks_installed()  # it changes another program's settings, so it's off until you turn it on
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
        if self.update_from is None:
            self.show_page("welcome")
        else:
            self.flow = self.update_flow
            self.show_page("whatsnew")

    def usual_ai(self):
        """The local AI, unless it can't be had here, or getting it would eat into a connection that's paid by the gigabyte."""
        metered = self.local_ai["download"] and Gio.NetworkMonitor.get_default().get_network_metered()
        return "none" if self.local_ai["why_not"] or metered else "ollama"

    def asked_on_update(self, ai):
        if self.update_from is None:
            return []
        return [page for release, page in ASKED_ON_UPDATE.items()
                if installer.newer(release, self.update_from) and (page != "ai" or ai == "none")]

    def t(self, key, **params):
        return STRINGS[self.lang][key].format(**params)

    def trouble(self, error):
        """Why something failed, in plain words. The tool's own are in the log."""
        return self.t(error.key, **error.fields)

    def log_note(self, error):
        """Where those are, if they're there: a full disk can't take them, and deleting your stats takes them too."""
        return self.t("trouble_log", path=installer.log_path()) if error.logged and installer.SETUP_LOG.exists() else ""

    def why(self, error):
        return f"{self.trouble(error)} {self.log_note(error)}".strip()

    def done_text(self, key):
        """What the done page says, and what a step that could fail without stopping the rest didn't get done."""
        return "\n\n".join([self.t(key), *map(self.why, self.left_undone)])

    # ---- pages ---------------------------------------------------------------------------

    def build_pages(self):
        while (child := self.stack.get_first_child()):
            self.stack.remove(child)
        self.set_title(self.t("window"))
        if self.update_from is not None:
            self.stack.add_named(self.whatsnew_page(), "whatsnew")
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

    def whatsnew_page(self):
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER)
        top.append(Gtk.Image(icon_name=ICON, pixel_size=96))
        top.append(label(self.t("whatsnew_title", version=installer.version()), "title-1", xalign=0.5, justify=Gtk.Justification.CENTER))
        top.append(label(self.t("whatsnew_text", version=self.update_from), "dim-label", xalign=0.5, justify=Gtk.Justification.CENTER))
        children = [top]
        for version, items in installer.whats_new(self.update_from, self.lang):
            release = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            release.append(label(self.t("whatsnew_release", version=version), "heading"))
            for item in items:
                line = Gtk.Box(spacing=8)
                line.append(label("•", valign=Gtk.Align.START))
                line.append(label(item, hexpand=True))
                release.append(line)
            children.append(release)
        more = Gtk.Button(label=self.t("whatsnew_more"), halign=Gtk.Align.CENTER)
        more.add_css_class("flat")
        more.connect("clicked", lambda _b: self.other_options())
        children.append(more)
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
        spinner = new_spinner()
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

        why_not, download = self.local_ai["why_not"], self.local_ai["download"]
        size = in_gigabytes(self.lang, download)
        group.add(choice("ollama", self.t("ai_ollama_get" if download and not why_not else "ai_ollama"),
                         self.t(why_not, size=size) if why_not else self.t("ai_ollama_download", size=size) if download else self.t("ai_ollama_found"),
                         not why_not))
        group.add(choice("claude", self.t("ai_claude"),
                         self.t("ai_claude_found") if self.claude else self.t("ai_claude_missing"), bool(self.claude)))
        group.add(choice("api", self.t("ai_api"), self.t("ai_api_text")))
        self.key_row = Adw.PasswordEntryRow(title=self.t("ai_key_saved") if self.saved_key else self.t("ai_key"),
                                            visible=self.ai == "api")
        self.key_row.connect("changed", self.on_key)
        group.add(self.key_row)
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
        self.work_log = label("", "dim-label", "caption", visible=False, selectable=True)
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        title.append(self.work_title)
        title.append(self.work_text)
        return self.scrolled(title, self.work_list, self.work_error, self.work_log)

    def done_page(self):
        self.done_status = Adw.StatusPage(icon_name="emblem-ok-symbolic", vexpand=True)
        return self.done_status

    # ---- moving between pages ------------------------------------------------------------

    def show_page(self, page):
        self.page = page
        self.stack.set_visible_child_name(page)
        at = self.flow.index(page) if page in self.flow else -1
        self.back.set_visible(at > 0 or (page == "welcome" and self.update_from is not None))
        self.secondary.set_visible(False)
        self.step_label.set_label(self.t("step", n=at, total=len(FLOW) - 1) if self.flow == FLOW and at > 0 else "")
        if at < 0:
            return
        if at + 1 < len(self.flow):
            following = self.flow[at + 1]
            self.set_primary(self.forward_label(page), lambda: self.show_page(following))
        elif page == "whatsnew":
            self.set_primary(self.t("update"), self.start_update)
        else:
            self.set_primary(self.t("install" if not self.installed else "save" if self.update_from is None else "update"), self.start_install)
        if page == "check":
            self.run_checks()
        elif page == "ai":
            self.update_ai_primary()

    def forward_label(self, page):
        if page == "welcome":
            return self.t("change" if self.installed else "start")
        return self.t("continue")

    def set_primary(self, text, action, sensitive=True):
        self.primary.set_label(text)
        self.primary.set_sensitive(sensitive)
        self.primary.set_visible(True)
        self.primary_action = action

    def go_back(self):
        if self.page == "welcome":
            self.flow = self.update_flow
            self.show_page("whatsnew")
        elif self.page in self.flow:
            self.show_page(self.flow[self.flow.index(self.page) - 1])

    def other_options(self):
        self.flow = FLOW
        self.show_page("welcome")

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

    def show_checks(self, checks, error):
        if error:
            self.toast.add_toast(Adw.Toast(title=self.trouble(error), timeout=8))
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
            button.connect("clicked", self.on_install_package, check_id, check.package)
            return button
        icon = Gtk.Image(icon_name="dialog-error-symbolic" if check and check.required else "dialog-information-symbolic")
        icon.add_css_class("error" if check and check.required else "dim-label")
        return icon

    def on_install_package(self, button, check_id, package):
        button.set_sensitive(False)
        row = self.check_rows[check_id][0]

        def done(_result, error):
            # On its row, where it stays and wraps: a toast cuts a sentence this long at one line, and goes away.
            row.set_subtitle(self.why(error) if error else self.t(f"check_{check_id}_text"))
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
        # With an update waiting, what's running stays as it is until the next login, whichever way you got here.
        options = installer.Options(language=self.lang, ai=self.ai, api_key=self.api_key, hooks=self.hooks and bool(self.agents),
                                    routine=self.routine, update=self.update_from is not None)
        skipped = set()
        if not options.hooks and not installer.hooks_installed():
            skipped.add("hooks")
        if options.ai != "api":
            skipped.add("ai")
        if options.ai != "ollama":
            skipped.add("ollama")
        steps = [step for step in installer.install_steps(options) if step[0] not in skipped]
        title = "work_install" if not self.installed else "work_save" if self.update_from is None else "work_update"
        self.run_work(steps, self.t(title), self.install_finished, self.start_install)

    def start_update(self):
        """One click: everything you chose stays as it is, unless this computer can't run the new version."""
        self.primary.set_sensitive(False)
        in_background(installer.check_system, self.update_checked)

    def update_checked(self, checks, _error):
        if any(check.required and not check.ok for check in checks or []):
            self.flow = FLOW
            self.show_page("check")
            return
        options = installer.Options(language=self.lang, ai=self.ai, keep_config=True, update=True)
        skipped = {"hooks", "ollama"} | (set() if options.ai == "api" else {"ai"})
        steps = [step for step in installer.install_steps(options) if step[0] not in skipped]
        self.run_work(steps, self.t("work_update"), self.install_finished, self.start_update)

    def install_finished(self):
        updated, first_time = self.update_from is not None, not self.installed
        self.installed = True
        self.update_from = None
        self.follow_local_ai()
        if updated:
            title, text = self.t("updated_title", version=installer.version()), self.done_text("updated_text")
        elif first_time or not installer.dock_is_running():
            title, text = self.t("done_title"), self.done_text("done_text")
        else:
            self.done_status.set_title(self.t("saved_title"))
            self.done_status.set_description(self.done_text("saved_text"))
            self.done_status.set_icon_name(ICON)
            self.show_page("done")
            self.set_primary(self.t("close"), self.close)
            return
        self.done_status.set_title(title)
        self.done_status.set_description(text)
        self.done_status.set_icon_name(ICON)
        self.show_page("done")
        self.set_primary(self.t("logout"), self.log_out)
        self.secondary.set_label(self.t("later"))
        self.secondary.set_visible(True)
        self.connect_secondary(self.close)

    # ---- the local AI, downloading in the background -------------------------------------

    def follow_local_ai(self):
        """It goes on with or without this window, so this only shows how it's going while the window's there."""
        if localai.percent() is None or getattr(self, "ai_line", None):
            return
        self.ai_bar = Gtk.ProgressBar()
        self.ai_line = label("", "dim-label", xalign=0.5, justify=Gtk.Justification.CENTER)
        self.ai_note = label(self.t("local_ai_note"), "dim-label", "caption", xalign=0.5, justify=Gtk.Justification.CENTER)
        self.ai_retry = Gtk.Button(label=self.t("retry"), halign=Gtk.Align.CENTER, visible=False)
        self.ai_retry.connect("clicked", lambda _b: localai.try_again())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for child in (self.ai_bar, self.ai_line, self.ai_note, self.ai_retry):
            box.append(child)
        self.done_status.set_child(box)
        self.show_local_ai()
        GLib.timeout_add_seconds(1, self.show_local_ai)

    def show_local_ai(self):
        state = localai.read()
        stage, done, total = state.get("stage"), state.get("done", 0), state.get("total", 0)
        # With nothing left to get and no word of how it went, the state couldn't be written: a disk with no room at all, say.
        failed = stage == "failed" or (stage != "ready" and localai.percent() is None)
        self.ai_retry.set_visible(failed)
        self.ai_note.set_visible(not failed and stage != "ready")
        if stage == "ready":
            self.ai_bar.set_fraction(1)
            self.ai_line.set_label(self.t("local_ai_ready"))
            return False
        if failed:
            self.ai_line.set_label(self.t(state.get("trouble") or "trouble_download"))
        elif stage == "waiting":
            self.ai_line.set_label(self.t("local_ai_waiting"))
        elif stage == "metered":
            self.ai_line.set_label(self.t("local_ai_metered"))
        elif stage == "unpacking":
            self.ai_line.set_label(self.t("local_ai_unpacking"))
        elif total:
            self.ai_bar.set_fraction(min(1, done / total))
            self.ai_line.set_label(self.t("local_ai_getting", done=in_gigabytes(self.lang, done), total=in_gigabytes(self.lang, total)))
        else:
            self.ai_line.set_label(self.t("local_ai_starting"))
        return True

    def connect_secondary(self, action):
        if getattr(self, "secondary_handler", None):
            self.secondary.disconnect(self.secondary_handler)
        self.secondary_handler = self.secondary.connect("clicked", lambda _b: action())

    def log_out(self):
        """Without GNOME's second question: you just said so. It still stops for an app with unsaved work."""
        try:
            if installer.replace_docks_for_next_login():
                self.docks_timer = GLib.timeout_add_seconds(LOGOUT_WAIT_S, self.still_logged_in)
        except (OSError, ValueError, GLib.Error) as e:
            print(f"Leaving the other docks for the next login: {e}", file=sys.stderr)
        subprocess.Popen(["gnome-session-quit", "--logout", "--no-prompt"], start_new_session=True)

    def still_logged_in(self):
        """You didn't log out after all, over an app with unsaved work, say. The dock isn't here yet, so the old ones come back."""
        self.docks_timer = 0
        installer.docks.restore()
        return False

    def give_docks_back(self):
        """You're closing the window before finding that out, so it won't be here to do it then."""
        if self.docks_timer:
            GLib.source_remove(self.docks_timer)
            self.still_logged_in()

    def confirm_remove(self):
        body = self.t("remove_text") + (f" {self.t('remove_local_ai')}" if localai.read().get("ollama_ours") else "")
        dialog = Adw.AlertDialog(heading=self.t("remove_title"), body=body)
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
        if not localai.there_is_something():
            steps = [step for step in steps if step[0] != "local_ai"]
        if not installer.has_polkit_rule():
            steps = [step for step in steps if step[0] != "polkit"]
        if not purge:
            steps = [step for step in steps if step[0] != "settings"]
        self.run_work(steps, self.t("work_remove"), self.remove_finished, lambda: self.start_remove(purge), keep_going=True)

    def remove_finished(self):
        self.installed = False
        self.done_status.set_title(self.t("removed_title"))
        self.done_status.set_description(self.done_text("removed_deb" if installer.is_system_payload() else "removed_text"))
        self.done_status.set_icon_name("user-trash-symbolic")
        self.show_page("done")
        self.set_primary(self.t("close"), self.close)

    def run_work(self, steps, title, finished, retry, keep_going=False):
        self.work_title.set_label(title)
        self.work_text.set_label(self.t("work_wait"))
        self.work_error.set_visible(False)
        self.work_log.set_visible(False)
        self.left_undone = []
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

        def show_trouble(error):
            self.work_error.set_label(self.trouble(error))
            self.work_error.set_visible(True)
            self.work_log.set_label(self.log_note(error))
            self.work_log.set_visible(bool(self.log_note(error)))

        def mark(step_id, state, error):
            row = rows[step_id]
            if step_id in marks:
                row.remove(marks[step_id])
            if state == "running":
                widget = new_spinner()
            else:
                widget = Gtk.Image(icon_name="emblem-ok-symbolic" if state == "done" else "dialog-error-symbolic")
                widget.add_css_class("success" if state == "done" else "error")
            row.add_suffix(widget)
            marks[step_id] = widget
            if error:
                show_trouble(error)
                self.left_undone.append(error)
            return False

        def work():
            return installer.run_steps(steps, lambda *args: GLib.idle_add(mark, *args), keep_going=keep_going)

        def done(ok, error):
            if ok:
                finished()
                return
            self.work_title.set_label(self.t("work_failed"))
            self.work_text.set_label("")
            if error:
                show_trouble(error)
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
