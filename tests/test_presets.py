"""Presets: which one was meant, what can be saved, and doing the steps. Run with: python3 -m unittest discover tests"""

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))

import brain  # noqa: E402
import commands  # noqa: E402
import launcher  # noqa: E402
import presets  # noqa: E402
import routine  # noqa: E402
import servers  # noqa: E402
from test_flows import Base  # noqa: E402


def preset(name="Discord", keywords=("setup discord",), steps=None):
    return {"name": name, "keywords": list(keywords), "steps": steps if steps is not None else [{"type": "app", "app": "discord.desktop"}]}


class WhichOneWasMeant(unittest.TestCase):
    items = [preset(), preset("Rekam", ["gas rekam"])]

    def named(self, text):
        found = presets.named(text, self.items)
        return found and found["name"]

    def near(self, text):
        found = presets.near(text, self.items)
        return found and found[1]

    def test_its_own_words_however_theyre_typed(self):
        for text in ("setup discord", "Setup Discord", "  setup   discord ", "setup discord.", "set up discord", "gas rekam"):
            self.assertIsNotNone(self.named(text), text)

    def test_politeness_around_it(self):
        self.assertEqual(self.named("tolong setup discord dong"), "Discord")

    def test_a_keyword_can_be_a_word_the_command_bar_trims(self):
        self.assertEqual(self.named("gas rekam"), "Rekam")

    def test_words_with_nothing_left_in_them_arent_an_empty_keyword(self):
        items = [preset(keywords=["setup discord", "  "])]
        for text in ("dong", "tolong", "   ", "."):
            self.assertIsNone(presets.named(text, items), text)

    def test_nothing_else_runs_it(self):
        for text in ("setup", "discord", "buka discord", "setup discord dan rekam", "jangan setup discord", "setup discord?"):
            self.assertIsNone(self.named(text), text)

    def test_a_slip_of_the_fingers_only_asks(self):
        for text in ("setup disocrd", "setup discrod", "stup discord", "setup in discord", "setup di discord", "set up discrod", "setup discord?"):
            self.assertEqual(self.near(text), "setup discord", text)
            self.assertIsNone(self.named(text), text)

    def test_what_only_looks_like_one(self):
        for text in ("tutup discord", "buka discord", "matiin discord", "setup disney", "setup", "discord", "halo", "setup shopify",
                     "setup project gudang", "buka youtube di chrome", "cek ram", "gas sekarang"):
            self.assertIsNone(self.near(text), text)

    def test_being_told_not_to_or_asking_about_it_isnt_one(self):
        for text in ("gak setup discord", "jangan setup discord", "gimana cara setup discord", "cara setup discord", "tadi setup discord",
                     "how setup discord", "why setup discord", "who setup discord", "apa setup discord", "g setup discord"):
            self.assertIsNone(self.near(text), text)

    def test_a_word_of_its_own_isnt_a_slip(self):
        self.assertIsNone(presets.near("disconnect vpn", [preset("A", ["connect vpn"])]))
        self.assertIsNone(presets.near("uninstall tool", [preset("A", ["install tool"])]))
        self.assertIsNotNone(presets.near("conect vpn", [preset("A", ["connect vpn"])]))

    def test_an_empty_keyword_in_a_file_is_never_offered(self):
        for text in ("hi", "in", "the"):
            self.assertIsNone(presets.near(text, [preset(keywords=["  "])]), text)

    def test_two_that_fit_means_neither(self):
        items = [preset("A", ["setup gudang"]), preset("B", ["setup gudeng"])]
        self.assertIsNone(presets.near("setup gudung", items))

    def test_one_preset_with_two_keywords_that_fit_is_still_one(self):
        self.assertEqual(presets.near("setup gudung", [preset("A", ["setup gudang", "setup gudeng"])])[0]["name"], "A")

    def test_a_short_word_has_no_slips(self):
        self.assertIsNone(presets.near("buka ab", [preset("A", ["buka ac"])]))

    def test_a_mode_of_yours_comes_before_the_window(self):
        watch = servers.Watch()
        self.assertEqual(commands.quick("preset", [("preset", "Preset")], watch, {})["actions"], [{"type": "mode", "mode": "preset"}])
        self.assertEqual(commands.quick("preset", [("social", "Social")], watch, {})["actions"], [{"type": "presets_window"}])

    def test_what_the_command_bar_already_understands(self):
        watch = servers.Watch()
        modes = [("social", "Social")]
        with mock.patch.object(commands, "find_app", return_value=None), mock.patch.object(commands, "dev_service_unit", return_value=None):
            taken = presets.understood_otherwise(["setup toko", "social", "setup discord", "jangan matiin server"], modes, watch, {"toko": "/projects/toko"})
        self.assertEqual(taken, ["setup toko", "social"])


class WhatCanBeSaved(unittest.TestCase):
    def keys(self, items):
        return [key for _at, key, _fields in presets.problems(items)]

    def test_a_good_one(self):
        self.assertEqual(self.keys([preset(), preset("Rekam", ["gas rekam"])]), [])

    def test_what_a_preset_needs(self):
        self.assertEqual(self.keys([preset("  ")]), ["presets_no_name"])
        self.assertEqual(self.keys([preset(keywords=[])]), ["presets_no_keyword"])
        self.assertEqual(self.keys([preset(keywords=["  "])]), ["presets_no_keyword"])
        self.assertEqual(self.keys([preset(keywords=["x"])]), ["presets_short_keyword"])
        self.assertEqual(self.keys([preset(steps=[])]), ["presets_no_steps"])
        self.assertEqual(self.keys([preset(keywords=["ok", "  "])]), ["presets_bad_preset"], "the window leaves empty ones out")

    def test_nothing_twice(self):
        self.assertEqual(self.keys([preset(), preset("discord ", ["lain"])]), ["presets_same_name"])
        self.assertEqual(self.keys([preset(), preset("B", ["Set Up  Discord"])]), ["presets_keyword_taken"])

    def test_it_says_which(self):
        self.assertEqual(presets.problems([preset(), preset("B", steps=[{"type": "url", "url": "ftp://x"}], keywords=["bb"])]),
                         [(1, "presets_bad_step", {"n": 1, "name": "B"})])

    def test_a_file_written_by_hand_cant_get_past_this(self):
        for bad in ("text", 5, None, {}, {"name": 5, "keywords": [], "steps": []}, {"name": "a", "keywords": "ab", "steps": []},
                    {"name": "a", "keywords": [5], "steps": []}, {"name": "a", "keywords": ["ab"], "steps": {}}):
            self.assertEqual(self.keys([bad]), ["presets_bad_preset"], bad)
        for step in ("text", {}, {"type": "nothing"}, {"type": "app"}, {"type": "app", "app": 5}, {"type": "app", "app": " "}):
            self.assertEqual(self.keys([preset(steps=[step])]), ["presets_bad_step"], step)

    def test_a_card_can_show_all_of_it(self):
        for sneaky in ("echo a\nrm x", "echo a\rrm x", "echo \x1b[8mhidden", "echo a\u202erm x", "echo a\u200b", "echo a\x00"):
            self.assertFalse(presets.step_is_fine({"type": "command", "command": sneaky}), repr(sneaky))
            self.assertFalse(presets.step_is_fine({"type": "hand", "text": sneaky}), repr(sneaky))
            self.assertEqual(self.keys([preset(sneaky)]), ["presets_bad_preset"], repr(sneaky))
            self.assertEqual(self.keys([preset(keywords=[sneaky])]), ["presets_bad_preset"], repr(sneaky))
        self.assertTrue(presets.step_is_fine({"type": "command", "command": "echo “héllo” 日本語 | tr a b"}))

    def test_a_step_is_checked_for_what_it_is(self):
        fine = presets.step_is_fine
        self.assertTrue(fine({"type": "url", "url": "https://discord.com/app"}))
        self.assertFalse(fine({"type": "url", "url": "file:///etc/passwd"}))
        self.assertTrue(fine({"type": "service", "unit": "postgresql@18-main.service"}))
        self.assertFalse(fine({"type": "service", "unit": "--now"}), "it would be read as an option")
        self.assertFalse(fine({"type": "service", "unit": "a b"}))
        self.assertTrue(fine({"type": "command", "command": "x" * presets.MAX_COMMAND}))
        self.assertFalse(fine({"type": "command", "command": "x" * (presets.MAX_COMMAND + 1)}), "all of it has to fit on a card")
        self.assertFalse(fine({"type": "hand", "text": "x" * (presets.MAX_HAND + 1)}))

    def test_what_you_enter_gets_tidied_up(self):
        self.assertEqual(presets.clean_step("url", " discord.com/app "), {"type": "url", "url": "https://discord.com/app"})
        self.assertEqual(presets.clean_step("url", "http://localhost:8000"), {"type": "url", "url": "http://localhost:8000"})
        self.assertEqual(presets.clean_step("command", "  echo hi "), {"type": "command", "command": "echo hi"})
        self.assertIsNone(presets.clean_step("command", "   "))
        self.assertIsNone(presets.clean_step("url", ""))

    def test_every_kind_of_step_has_its_words(self):
        for language, table in brain.CARD_STRINGS.items():
            for kind in presets.KINDS:
                for key in (f"presets_kind_{kind}", f"presets_field_{kind}", f"presets_hint_{kind}"):
                    self.assertIn(key, table, language)
        for key in {key for key in brain.CARD_STRINGS["en"] if key.startswith(("preset_", "presets_"))}:
            used = "".join((ROOT / "daemon" / name).read_text() for name in ("presets.py", "presets_window.py", "deskaid.py"))
            self.assertTrue(key in used or key.startswith(("presets_kind_", "presets_field_", "presets_hint_")), f"{key} isn't used")


class TheFile(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.file = self.folder / "presets.json"

    def test_no_file_is_no_presets(self):
        self.assertEqual(presets.read(self.file), ([], False))

    def test_saved_and_read_back_and_only_yours_to_read(self):
        presets.save([preset()], self.file)
        self.assertEqual(presets.read(self.file), ([preset()], False))
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600, "commands can hold anything")

    def test_a_broken_file(self):
        for text in ("{", "[]", '{"presets": 5}', json.dumps({"presets": [preset(steps=[])]}), "\x00", "[" * 200_000):
            self.file.write_text(text)
            self.assertEqual(presets.read(self.file), ([], True), text)

    def test_saving_over_a_broken_one_keeps_it(self):
        self.file.write_text("{ someone's work")
        presets.save([preset()], self.file)
        kept = list(self.folder.glob("presets.json.broken-*"))
        self.assertEqual([path.read_text() for path in kept], ["{ someone's work"])
        self.assertEqual(presets.read(self.file), ([preset()], False))

    def test_saving_over_a_good_one_keeps_nothing(self):
        presets.save([preset()], self.file)
        presets.save([], self.file)
        self.assertEqual(list(self.folder.glob("*.broken-*")), [])

    def test_the_store_goes_by_the_last_good_copy(self):
        store = presets.Store(self.file)
        store.refresh()
        self.assertEqual((store.items, store.broken), ([], False))
        presets.save([preset()], self.file)
        store.refresh()
        self.assertEqual((store.items, store.broken), ([preset()], False))
        self.file.write_text("{")
        store.refresh()
        self.assertEqual((store.items, store.broken), ([preset()], True))
        store.told = True
        store.refresh()
        self.assertTrue(store.told, "nothing changed: it stays told")
        presets.save([], self.file)
        store.refresh()
        self.assertEqual((store.items, store.broken, store.told), ([], False, False))
        self.file.unlink()
        store.refresh()
        self.assertEqual((store.items, store.broken), ([], False))


class CommandsYouveSeen(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.seen = Path(folder.name) / "seen.json"
        patcher = mock.patch.object(presets, "SEEN_FILE", self.seen)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.preset = preset(steps=[{"type": "command", "command": "echo a"}, {"type": "app", "app": "a.desktop"},
                                    {"type": "command", "command": "echo b"}, {"type": "command", "command": "echo a"}])

    def test_each_is_asked_about_once(self):
        self.assertEqual(presets.unseen_commands(self.preset), ["echo a", "echo b"])
        presets.allow("echo a")
        self.assertEqual(presets.unseen_commands(self.preset), ["echo b"])
        presets.allow("echo b")
        self.assertEqual(presets.unseen_commands(self.preset), [])

    def test_a_changed_one_is_a_new_one(self):
        presets.allow("echo a")
        self.assertEqual(presets.unseen_commands(preset(steps=[{"type": "command", "command": "echo a; rm x"}])), ["echo a; rm x"])

    def test_no_commands_means_nothing_to_ask(self):
        self.assertEqual(presets.unseen_commands(preset()), [])

    def test_what_it_keeps_isnt_the_command(self):
        presets.allow("echo secret")
        self.assertNotIn("secret", self.seen.read_text())

    def test_a_broken_note_asks_again_rather_than_lets_through(self):
        for text in ("{", '{"a": 1}', "[1]"):
            self.seen.write_text(text)
            self.assertEqual(presets.unseen_commands(self.preset), ["echo a", "echo b"], text)


class DoingTheSteps(unittest.TestCase):
    def run_it(self, steps, your_turn=lambda _at, _step: True, set_mode=lambda _mode: True, cancelled=lambda: False):
        seen = []
        presets.run(preset(steps=steps), lambda at, state, why: seen.append((at, state, why)), set_mode, your_turn, cancelled)
        return seen

    def test_in_order(self):
        with mock.patch.object(launcher, "open_app", return_value=True) as opened, mock.patch.object(launcher, "open_uri") as uri:
            seen = self.run_it([{"type": "app", "app": "a.desktop"}, {"type": "url", "url": "https://a.b"}])
        opened.assert_called_once_with("a.desktop")
        uri.assert_called_once_with("https://a.b")
        self.assertEqual(seen, [(0, "running", ""), (0, "done", ""), (1, "running", ""), (1, "done", "")])

    def test_it_stops_at_the_first_that_fails(self):
        with mock.patch.object(launcher, "open_app", return_value=False) as opened:
            seen = self.run_it([{"type": "app", "app": "gone.desktop"}, {"type": "app", "app": "next.desktop"}])
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(seen[-1], (0, "failed", "preset_why_app"))

    def test_what_goes_wrong_is_said_plainly(self):
        with mock.patch.object(launcher, "open_uri", side_effect=OSError("[Errno 2] gio")):
            self.assertEqual(self.run_it([{"type": "url", "url": "https://a.b"}])[-1], (0, "failed", "preset_why_step"))
        self.assertEqual(self.run_it([{"type": "mode", "mode": "gone"}], set_mode=lambda _mode: False)[-1], (0, "failed", "preset_why_mode"))

    def test_a_step_by_hand_waits_for_you(self):
        asked = []
        seen = self.run_it([{"type": "hand", "text": "do it"}], your_turn=lambda at, step: asked.append((at, step["text"])) or True)
        self.assertEqual(asked, [(0, "do it")])
        self.assertEqual(seen, [(0, "running", ""), (0, "done", "")])

    def test_saying_no_to_it_ends_there(self):
        seen = self.run_it([{"type": "hand", "text": "do it"}, {"type": "mode", "mode": "m"}], your_turn=lambda _at, _step: False)
        self.assertEqual(seen, [(0, "running", "")])

    def test_cancelled_before_the_next_step(self):
        self.assertEqual(self.run_it([{"type": "mode", "mode": "m"}], cancelled=lambda: True), [])

    def test_a_service_already_on_is_left_alone(self):
        with mock.patch.object(commands, "service_active", return_value=True), mock.patch.object(commands, "systemctl") as systemctl:
            self.assertEqual(self.run_it([{"type": "service", "unit": "a"}])[-1], (0, "skipped", ""))
        systemctl.assert_not_called()

    def test_a_service_that_wont_start(self):
        with mock.patch.object(commands, "service_active", return_value=False), \
                mock.patch.object(commands, "systemctl", return_value=(False, "Unit a.service not found.")):
            self.assertEqual(self.run_it([{"type": "service", "unit": "a"}])[-1], (0, "failed", commands.trouble("Unit a.service not found.")))


class RunningACommand(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        for patcher in (mock.patch.object(launcher, "_user_systemd", return_value=None),
                        mock.patch.object(presets.shutil, "which", return_value=None),
                        mock.patch.object(routine, "shell_path", return_value="")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_how_it_ends_is_how_the_step_ends(self):
        self.assertEqual(presets.run_command("true"), ("done", ""))
        self.assertEqual(presets.run_command("exit 3"), ("failed", "preset_why_command"))
        self.assertEqual(presets.run_command("no-such-program-here"), ("failed", "preset_why_command"))

    def test_shell_syntax_works_as_in_a_terminal(self):
        out = self.folder / "out"
        self.assertEqual(presets.run_command(f"echo a | tr a b > {out}"), ("done", ""))
        self.assertEqual(out.read_text(), "b\n")

    def test_it_starts_from_your_home_folder(self):
        out = self.folder / "out"
        presets.run_command(f"pwd > {out}")
        self.assertEqual(out.read_text().strip(), str(Path.home()))

    def test_it_doesnt_get_deskais_api_key(self):
        out = self.folder / "out"
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret", "SOMETHING_ELSE": "kept"}):
            presets.run_command(f'echo "$ANTHROPIC_API_KEY/$SOMETHING_ELSE" > {out}')
        self.assertEqual(out.read_text().strip(), "/kept")

    def test_what_keeps_running_is_left_running(self):
        out = self.folder / "out"
        with mock.patch.object(presets, "COMMAND_WAIT_S", 0.2):
            started = time.monotonic()
            self.assertEqual(presets.run_command(f"sleep 0.6; touch {out}"), ("done", ""))
        self.assertLess(time.monotonic() - started, 0.5)
        for _ in range(50):
            if out.exists():
                break
            time.sleep(0.05)
        self.assertTrue(out.exists(), "it wasn't stopped")

    def test_it_gets_a_scope_and_goes_in_the_journal(self):
        with mock.patch.object(presets.shutil, "which", return_value="/usr/bin/systemd-cat"), \
                mock.patch.object(launcher, "own_scope", side_effect=lambda argv: ["scoped", *argv]) as scope, \
                mock.patch.object(presets.subprocess, "Popen") as popen:
            popen.return_value.wait.return_value = 0
            presets.run_command("echo hi")
        scope.assert_called_once_with(["systemd-cat", "-t", presets.LOG_TAG, "bash", "-c", "echo hi"])
        self.assertEqual(popen.call_args.args[0][0], "scoped")


class InWords(unittest.TestCase):
    def say(self, key, **fields):
        return brain.card_text("id", key, **fields)

    def test_a_line_for_each_kind_of_step(self):
        self.assertEqual(presets.label({"type": "url", "url": "https://discord.com/app"}, self.say), "Buka discord.com/app")
        self.assertEqual(presets.label({"type": "mode", "mode": "social"}, self.say, lambda _mode: "Social"), "Pindah ke Social")
        self.assertEqual(presets.label({"type": "service", "unit": "postgresql.service"}, self.say), "Nyalain postgresql")
        self.assertEqual(presets.label({"type": "command", "command": "echo hi"}, self.say), "$ echo hi")
        self.assertEqual(presets.label({"type": "hand", "text": "Geser\nitu"}, self.say), "Geser itu")
        self.assertLessEqual(len(presets.label({"type": "command", "command": "x" * 400}, self.say)), 64)

    def test_an_app_by_its_name_or_its_id_if_its_gone(self):
        with mock.patch.object(launcher, "app_info", return_value=mock.Mock(get_name=lambda: "Discord")):
            self.assertEqual(presets.label({"type": "app", "app": "d.desktop"}, self.say), "Buka Discord")
        with mock.patch.object(launcher, "app_info", return_value=None):
            self.assertEqual(presets.label({"type": "app", "app": "d.desktop"}, self.say), "Buka d.desktop")

    def test_a_long_preset_shows_the_part_its_on(self):
        labels = [f"s{n}" for n in range(20)]
        self.assertEqual(presets.progress_lines(labels[:3], {0: "done", 1: "running"}), ["✓ s0", "… s1", "· s2"])
        self.assertEqual(presets.progress_lines(labels, {})[0], "· s0")
        middle = presets.progress_lines(labels, {at: "done" for at in range(10)})
        self.assertEqual(len(middle), presets.ROOM_ON_A_CARD)
        self.assertIn("✓ s9", middle)
        self.assertEqual(presets.progress_lines(labels, {19: "failed"})[-1], "✗ s19")


class FromTheCommandBar(Base):
    COMMAND = {"type": "command", "command": "echo a"}

    def setUp(self):
        super().setUp()
        self.file = self.folder / "presets.json"
        for patcher in (mock.patch.object(presets, "SEEN_FILE", self.folder / "seen.json"),
                        mock.patch.object(commands, "all_projects", return_value={"toko": "/projects/toko"}),
                        mock.patch.object(commands, "find_app", return_value=None),
                        mock.patch.object(commands, "dev_service_unit", return_value=None),
                        mock.patch.object(sys.modules["deskaid"].GLib, "idle_add", side_effect=lambda work, *args: work(*args)),
                        mock.patch.object(sys.modules["deskaid"].GLib, "timeout_add", side_effect=lambda _ms, work, *args: work(*args))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def wrote(self, *items):
        presets.save(list(items), self.file)

    def titles(self):
        return [card["title"] for card in self.brain.cards]

    def finished(self):
        for _ in range(200):
            if not self.brain.preset_run:
                return
            time.sleep(0.01)
        self.fail("the preset never finished")

    def test_its_keyword_comes_before_anything_else(self):
        self.wrote(preset("Toko", ["setup toko"], steps=[{"type": "mode", "mode": "social"}]))
        with mock.patch.object(commands, "quick") as quick:
            self.brain.on_command("setup toko")
            self.finished()
        quick.assert_not_called()
        self.assertIn(("SetMode", ("social",)), self.brain.calls)
        self.assertEqual(self.titles()[-1], "Toko udah siap")

    def test_the_model_never_hears_of_it(self):
        self.wrote(preset(steps=[{"type": "mode", "mode": "social"}]))
        self.brain.on_command("setup discord")
        self.finished()
        self.brain.on_command("setup disocrd")
        self.brain.llm.ask.assert_not_called()
        action = commands.COMMAND_SCHEMA["properties"]["actions"]["items"]["properties"]["type"]["enum"]
        self.assertFalse([kind for kind in action if "preset" in kind], "the model can't pick one")
        self.assertNotIn("presets", json.dumps(commands.COMMAND_SCHEMA))

    def test_without_a_preset_nothing_changes(self):
        with mock.patch.object(self.brain, "_setup_project") as setting_up:
            self.brain.on_command("setup toko")
        setting_up.assert_called_once_with("toko")

    def test_a_command_bar_request_still_comes_before_a_slip(self):
        self.wrote(preset("Tako", ["setup tako"]))
        with mock.patch.object(self.brain, "_setup_project") as setting_up:
            self.brain.on_command("setup toko")
        setting_up.assert_called_once_with("toko")
        self.assertIsNone(self.brain.preset_meant)

    def test_a_slip_asks_first(self):
        self.wrote(preset(steps=[{"type": "mode", "mode": "social"}]))
        self.brain.on_command("setup disocrd")
        self.assertEqual(self.brain.cards[-1]["title"], "Maksudnya “setup discord”?")
        self.assertEqual(self.brain.calls, [])
        self.brain.on_card_action("command", "preset:meant")
        self.finished()
        self.assertIn(("SetMode", ("social",)), self.brain.calls)

    def test_it_goes_by_the_preset_as_it_is_now(self):
        self.wrote(preset(steps=[{"type": "mode", "mode": "social"}]))
        self.brain.on_command("setup disocrd")
        self.wrote(preset("Lain", ["lain lagi"]))
        self.brain.on_card_action("command", "preset:meant")
        self.assertEqual(self.brain.cards[-1]["title"], "Preset itu udah nggak ada.")
        self.assertEqual(self.brain.calls, [])

    def test_only_its_own_card_says_it_was_meant(self):
        self.wrote(preset(steps=[{"type": "mode", "mode": "social"}]))
        self.brain.on_command("setup disocrd")
        self.brain.on_card_action("preset", "preset:meant")
        self.assertEqual(self.brain.calls, [])

    def test_saying_it_wasnt_that(self):
        self.wrote(preset(steps=[{"type": "mode", "mode": "social"}]))
        self.brain.on_command("setup disocrd")
        self.brain.on_card_action("command", "preset:not-meant")
        self.brain.on_card_action("command", "preset:meant")
        self.assertEqual(self.brain.calls, [])

    def test_nothing_runs_until_youve_seen_every_command(self):
        two = {"type": "command", "command": "echo b"}
        self.wrote(preset(steps=[{"type": "mode", "mode": "social"}, self.COMMAND, two]))
        with mock.patch.object(presets, "run_command", return_value=("done", "")) as running:
            self.brain.on_command("setup discord")
            self.assertEqual(self.brain.cards[-1]["id"], "preset-ask")
            self.assertIn("$ echo a", self.brain.cards[-1]["text"])
            self.assertEqual(self.brain.calls, [])
            self.brain.on_card_action("preset-ask", "preset:allow")
            self.assertIn("$ echo b", self.brain.cards[-1]["text"])
            self.assertEqual(self.brain.calls, [])
            running.assert_not_called()
            self.brain.on_card_action("preset-ask", "preset:allow")
            self.finished()
            self.assertEqual([call.args[0] for call in running.call_args_list], ["echo a", "echo b"])
            # Not asked again.
            cards = len(self.brain.cards)
            self.brain.on_command("setup discord")
            self.finished()
            self.assertNotIn("preset-ask", [card["id"] for card in self.brain.cards[cards:]])

    def test_the_second_click_of_a_double_click_answers_nothing(self):
        self.wrote(preset(steps=[self.COMMAND, {"type": "command", "command": "echo b"}]))
        waiting = []
        with mock.patch.object(presets, "run_command") as running, \
                mock.patch.object(sys.modules["deskaid"].GLib, "timeout_add", side_effect=lambda _ms, work, *args: waiting.append((work, args))):
            self.brain.on_command("setup discord")
            self.assertIn("$ echo a", self.brain.cards[-1]["text"])
            cards = len(self.brain.cards)
            self.brain.on_card_action("preset-ask", "preset:allow")
            self.brain.on_card_action("preset-ask", "preset:allow")
            self.assertEqual(len(self.brain.cards), cards, "the next one isn't up yet")
            self.assertEqual(presets.unseen_commands(self.brain.preset_asking["preset"]), ["echo b"], "and only the one you read counts")
            work, args = waiting.pop()
            work(*args)
            self.assertIn("$ echo b", self.brain.cards[-1]["text"])
            running.assert_not_called()

    def test_nor_does_it_answer_the_card_after_saying_it_was_meant(self):
        self.wrote(preset(steps=[self.COMMAND]))
        waiting = []
        with mock.patch.object(sys.modules["deskaid"].GLib, "timeout_add", side_effect=lambda _ms, work, *args: waiting.append((work, args))):
            self.brain.on_command("setup disocrd")
            self.brain.on_card_action("command", "preset:meant")
            self.assertNotEqual(self.brain.cards[-1]["id"], "preset-ask")

    def test_nor_a_step_done_by_hand_right_after_another(self):
        self.wrote(preset(steps=[{"type": "hand", "text": "Satu"}, {"type": "hand", "text": "Dua"}]))
        with mock.patch.object(sys.modules["deskaid"], "PRESET_CLICK_GAP_S", 0.2):
            self.brain.on_command("setup discord")
            self.waited_for_hand()
            self.brain.on_card_action("preset", "preset:next:0")
            self.brain.on_card_action("preset", "preset:next:0")
            self.brain.on_card_action("preset", "preset:next:1")
            self.waited_for_card("Dua")
            self.assertTrue(self.brain.preset_run, "it's waiting for you")
            self.brain.on_card_action("preset", "preset:stop")
            self.finished()

    def waited_for_card(self, text):
        for _ in range(200):
            if self.brain.cards[-1]["text"] == text:
                return
            time.sleep(0.01)
        self.fail(f"no card saying {text}")

    def test_the_card_shows_all_of_the_command(self):
        long = "x" * presets.MAX_COMMAND
        self.wrote(preset(steps=[{"type": "command", "command": long}]))
        self.brain.on_command("setup discord")
        self.assertIn(long, self.brain.cards[-1]["text"])

    def test_saying_no_runs_nothing_and_asks_again_next_time(self):
        self.wrote(preset(steps=[self.COMMAND]))
        with mock.patch.object(presets, "run_command") as running:
            self.brain.on_command("setup discord")
            self.brain.on_card_action("preset-ask", "preset:refuse")
            self.brain.on_card_action("preset-ask", "preset:allow")
            running.assert_not_called()
            self.brain.on_command("setup discord")
            self.assertEqual(self.brain.cards[-1]["id"], "preset-ask")

    def test_an_allow_from_another_card_counts_for_nothing(self):
        self.wrote(preset(steps=[self.COMMAND]))
        with mock.patch.object(presets, "run_command") as running:
            self.brain.on_command("setup discord")
            self.brain.on_card_action("command", "preset:allow")
            running.assert_not_called()

    def test_a_step_by_hand_waits_for_its_own_button(self):
        self.wrote(preset(steps=[{"type": "hand", "text": "Geser itu"}, {"type": "mode", "mode": "social"}]))
        self.brain.on_command("setup discord")
        self.waited_for_hand()
        self.assertEqual(self.brain.cards[-1]["text"], "Geser itu")
        self.assertEqual(self.brain.calls, [])
        self.brain.on_card_action("preset", "preset:next:7")
        time.sleep(0.05)
        self.assertEqual(self.brain.calls, [], "a button left over from something else")
        self.brain.on_card_action("preset", "preset:next:0")
        self.finished()
        self.assertIn(("SetMode", ("social",)), self.brain.calls)

    def waited_for_hand(self):
        for _ in range(200):
            if self.brain.cards and any(action["id"].startswith("preset:next:") for action in self.brain.cards[-1].get("actions", [])):
                return
            time.sleep(0.01)
        self.fail("it never asked")

    def test_cancelling_a_step_by_hand_ends_it(self):
        self.wrote(preset(steps=[{"type": "hand", "text": "Geser itu"}, {"type": "mode", "mode": "social"}]))
        self.brain.on_command("setup discord")
        self.waited_for_hand()
        self.brain.on_card_action("preset", "preset:stop")
        self.finished()
        self.assertEqual(self.brain.calls, [])
        self.assertEqual(self.titles()[-1], "Discord dihentikan")

    def test_nobody_answering_ends_it_and_says_so(self):
        self.wrote(preset(steps=[{"type": "hand", "text": "Geser itu"}, {"type": "mode", "mode": "social"}]))
        deskaid = sys.modules["deskaid"]
        with mock.patch.object(deskaid, "PRESET_TURN_WAIT_S", 1), mock.patch.object(deskaid, "PRESET_TURN_SHOW_S", 1):
            self.brain.on_command("setup discord")
            self.finished()
        self.assertEqual(self.brain.calls, [])
        self.assertEqual(self.titles()[-1], "Discord berhenti: langkah 1 nggak dijawab")

    def test_a_card_that_took_its_place_doesnt_lose_the_step(self):
        self.wrote(preset(steps=[{"type": "hand", "text": "Geser itu"}]))
        deskaid = sys.modules["deskaid"]
        with mock.patch.object(deskaid, "PRESET_TURN_WAIT_S", 2), mock.patch.object(deskaid, "PRESET_TURN_SHOW_S", 1):
            self.brain.on_command("setup discord")
            self.finished()
        self.assertGreaterEqual(len([card for card in self.brain.cards if card["text"] == "Geser itu"]), 2)

    def test_a_failed_step_says_which_and_why(self):
        self.wrote(preset(steps=[{"type": "mode", "mode": "social"}, {"type": "mode", "mode": "gone"}, {"type": "mode", "mode": "writing"}]))
        self.brain.on_command("setup discord")
        self.finished()
        last = self.brain.cards[-1]
        self.assertEqual(last["title"], "Discord berhenti di langkah 2")
        self.assertIn("✗", last["text"])
        self.assertIn("Mode itu udah nggak ada.", last["text"])
        self.assertNotIn(("SetMode", ("writing",)), self.brain.calls)
        self.assertEqual(last["actions"] if "actions" in last else [], [])

    def test_one_at_a_time(self):
        self.wrote(preset(steps=[{"type": "hand", "text": "Geser itu"}]))
        self.brain.on_command("setup discord")
        self.waited_for_hand()
        self.brain.on_command("setup discord")
        self.assertEqual(self.brain.cards[-1]["title"], "Discord masih jalan")
        self.brain.on_card_action("preset", "preset:stop")
        self.finished()

    def test_no_card_has_more_than_three_buttons(self):
        self.wrote(preset(steps=[{"type": "hand", "text": "a"}, self.COMMAND]))
        self.brain.on_command("setup discord")
        self.brain.on_card_action("preset-ask", "preset:allow")
        self.waited_for_hand()
        self.brain.on_card_action("preset", "preset:stop")
        self.finished()
        self.assertLessEqual(max(len(card.get("actions", [])) for card in self.brain.cards), 3)

    def test_a_broken_file_is_said_once_and_gets_in_nobodys_way(self):
        self.file.write_text("{")
        self.brain.llm.available = False
        self.brain.on_command("apa kabar")
        self.assertEqual(self.brain.cards[-1]["text"], brain.card_text("id", "command_needs_ai"), "that was about something else")
        self.brain._tell_presets_broken()
        self.assertEqual(self.brain.cards[-1]["text"], brain.card_text("id", "command_needs_ai"), "no dock to say it on yet")
        self.brain.dock_seen = True
        self.brain._tell_presets_broken()
        self.assertEqual(self.brain.cards[-1]["text"], brain.card_text("id", "preset_file_bad"))
        cards = len(self.brain.cards)
        self.brain._tell_presets_broken()
        self.assertEqual(len(self.brain.cards), cards)
        self.wrote(preset())
        self.brain._tell_presets_broken()
        self.file.write_text("{ again")
        self.brain._tell_presets_broken()
        self.assertEqual(len(self.brain.cards), cards + 1, "broken again is news again")

    def test_the_window_opens_from_words_and_from_the_dock(self):
        with mock.patch.object(sys.modules["deskaid"].subprocess, "Popen") as popen, \
                mock.patch.object(launcher, "own_scope", side_effect=lambda argv: argv):
            self.brain.on_command("buka preset")
            self.brain.on_card_action("presets", "presets:open")
        self.assertEqual(popen.call_count, 2)
        self.assertTrue(popen.call_args.args[0][-1].endswith("presets_window.py"))
        self.assertEqual([card["title"] for card in self.brain.cards], [brain.card_text("id", "command_done")],
                         "the dock's waiting to hear back, from the command bar and not from the menu")

    def test_trying_one_from_the_window(self):
        self.wrote(preset(steps=[{"type": "mode", "mode": "social"}]))
        self.brain._run_preset_named("Discord")
        self.finished()
        self.assertIn(("SetMode", ("social",)), self.brain.calls)
        self.brain._run_preset_named("Nothing")
        self.assertEqual(self.brain.cards[-1]["title"], "Preset itu udah nggak ada.")


class OnlyWhatTheyShould(unittest.TestCase):
    def test_a_hook_stays_quick(self):
        source = (ROOT / "daemon" / "deskai.py").read_text()
        self.assertNotIn("\nimport presets", source, "it brings in the command bar, and an AI agent waits on `deskai hook`")

    def test_the_window_runs_nothing(self):
        source = (ROOT / "daemon" / "presets_window.py").read_text()
        self.assertNotIn("subprocess", source)
        self.assertNotIn("run_command", source)

    def test_nothing_else_runs_a_command_from_a_preset(self):
        users = [path.name for path in (ROOT / "daemon").glob("*.py") if "run_command" in path.read_text()]
        self.assertEqual(users, ["presets.py"])


if __name__ == "__main__":
    unittest.main()
