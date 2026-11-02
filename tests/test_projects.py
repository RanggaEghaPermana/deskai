"""Knowing your projects and what they run with no AI and no Claude Code history.
Run with: python3 -m unittest discover tests"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import commands  # noqa: E402
import compose  # noqa: E402
import projects  # noqa: E402
import routine  # noqa: E402
import servers  # noqa: E402


class Folder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(routine, "_shell_path", ""), mock.patch.object(projects, "WEB_ROOTS", ())):
            patcher.start()
            self.addCleanup(patcher.stop)
        projects.forget()
        self.addCleanup(projects.forget)

    def make(self, path, text=""):
        target = self.home / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def repo(self, path):
        (self.home / path / ".git").mkdir(parents=True)
        return str(self.home / path)


class Finding(Folder):
    def names(self):
        return [Path(path).name for path in projects.find()]

    def test_by_what_a_project_has_in_it(self):
        self.repo("Documents/toko")
        self.make("Projects/gudang/composer.json")
        self.make("code/kasir/package.json")
        self.make("dev/blog/manage.py")
        self.make("workspace/api/go.mod")
        self.make("Documents/antrean/docker-compose.yml")
        self.assertEqual(sorted(self.names()), ["antrean", "api", "blog", "gudang", "kasir", "toko"])

    def test_anywhere_in_your_home_folder(self):
        self.repo("skripsi")
        self.repo("kerja/klien/toko")
        self.assertEqual(sorted(self.names()), ["skripsi", "toko"])

    def test_not_where_you_wouldnt_keep_one(self):
        self.repo("Downloads/unduhan")
        self.repo("flutter")
        self.repo(".cache/alat")
        self.assertEqual(self.names(), [])

    def test_where_a_web_server_serves_from(self):
        with tempfile.TemporaryDirectory() as www, mock.patch.object(projects, "WEB_ROOTS", (www,)):
            (Path(www) / "html" / "toko" / ".git").mkdir(parents=True)
            self.assertEqual(self.names(), ["toko"])

    def test_inside_a_folder_of_projects(self):
        self.repo("Documents/kerja/klien/toko")
        self.assertEqual(self.names(), ["toko"])

    def test_not_too_deep(self):
        self.repo("Documents/a/b/c/d/toko")
        self.assertEqual(self.names(), [])

    def test_a_project_ends_the_search_there(self):
        self.repo("Documents/toko")
        self.make("Documents/toko/packages/inti/package.json")
        self.assertEqual(self.names(), ["toko"])

    def test_what_a_project_is_not(self):
        self.make("Documents/skripsi/bab1.docx")
        self.repo("Documents/.tersembunyi/toko")
        self.make("Documents/lain/node_modules/paket/package.json")
        self.make("Downloads/unduhan/package.json")
        self.assertEqual(self.names(), [])

    def test_the_folder_itself_isnt_a_project(self):
        self.repo("Documents")
        self.repo("Documents/toko")
        self.assertEqual(self.names(), ["toko"])

    def test_newest_first(self):
        old, new = self.repo("Documents/lama"), self.repo("Documents/baru")
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))
        self.assertEqual(self.names(), ["baru", "lama"])

    def test_a_loop_of_links_ends(self):
        (self.home / "Documents").mkdir()
        (self.home / "Documents/loop").symlink_to(self.home / "Documents")
        self.assertEqual(self.names(), [])

    def test_it_stops_looking_after_enough_folders(self):
        for index in range(5):
            self.repo(f"Documents/p{index}")
        with mock.patch.object(projects, "MAX_FOLDERS", 3):
            self.assertLess(len(self.names()), 5)

    def test_it_doesnt_search_again_right_away(self):
        self.repo("Documents/toko")
        self.names()
        self.repo("Documents/baru")
        self.assertEqual(self.names(), ["toko"])
        projects.forget()
        self.assertEqual(sorted(self.names()), ["baru", "toko"])


class ByName(Folder):
    def known(self, *from_claude):
        with mock.patch.object(routine, "project_folders", return_value={path: None for path in from_claude}):
            return commands.projects()

    def test_more_than_the_model_reads_can_still_be_named(self):
        for n in range(commands.MAX_PROJECTS + 5):
            self.repo(f"Documents/toko{n}")
        with mock.patch.object(routine, "project_folders", return_value={}):
            self.assertEqual(len(commands.projects()), commands.MAX_PROJECTS)
            self.assertEqual(len(commands.all_projects()), commands.MAX_PROJECTS + 5)

    def test_outside_home_only_if_found_where_a_web_server_serves_from(self):
        with tempfile.TemporaryDirectory() as www, mock.patch.object(projects, "WEB_ROOTS", (www,)), \
                mock.patch.object(routine, "project_folders", return_value={"/tmp/scratch": None}):
            (Path(www) / "toko" / ".git").mkdir(parents=True)
            self.assertEqual(commands.all_projects(), {"toko": str(Path(www) / "toko")})

    def test_found_ones_count_with_no_claude_at_all(self):
        path = self.repo("Documents/toko")
        self.assertEqual(self.known(), {"toko": path})

    def test_claude_sessions_come_first(self):
        found = self.repo("Documents/toko")
        os.utime(found, (9000000000, 9000000000))
        claude = str(self.home / "lain/gudang")
        self.assertEqual(list(self.known(claude)), ["gudang", "toko"])

    def test_the_same_name_is_one_project(self):
        self.repo("Documents/toko")
        claude = str(self.home / "lain/toko")
        self.assertEqual(self.known(claude), {"toko": claude})

    def test_a_limit(self):
        for index in range(commands.MAX_PROJECTS + 5):
            self.repo(f"Documents/p{index}")
        self.assertEqual(len(self.known()), commands.MAX_PROJECTS)


class Servers(Folder):
    def package(self, path="Documents/app", **scripts):
        self.make(f"{path}/package.json", json.dumps({"scripts": scripts}))
        return self.home / path

    def found(self, folder):
        return [(s["command"], s["port"]) for s in projects.servers(str(folder))]

    def test_laravel_with_vite(self):
        folder = self.package(dev="vite")
        self.make("Documents/app/artisan")
        self.assertEqual(self.found(folder), [("php artisan serve", 8000), ("npm run dev", 5173)])

    def test_each_tool_on_its_own_port(self):
        for script, port in (("next dev", 3000), ("nuxt dev", 3000), ("astro dev", 4321), ("ng serve", 4200),
                             ("vite --port 4000", 4000), ("next dev -p 4100", 4100), ("node server.js", None)):
            folder = self.package(dev=script)
            self.assertEqual(self.found(folder), [("npm run dev", port)], script)

    def test_start_when_theres_no_dev(self):
        self.assertEqual(self.found(self.package(start="react-scripts start")), [("npm start", 3000)])

    def test_a_library_has_no_server(self):
        self.assertEqual(self.found(self.package(test="jest", build="tsc")), [])

    def test_the_package_manager_it_uses(self):
        for lockfile, command in (("pnpm-lock.yaml", "pnpm run dev"), ("yarn.lock", "yarn dev"), ("bun.lockb", "bun run dev")):
            path = f"Documents/{lockfile}"
            self.make(f"{path}/{lockfile}")
            self.assertEqual(self.found(self.package(path, dev="vite")), [(command, 5173)])

    def test_django_with_its_own_python(self):
        self.make("Documents/web/manage.py")
        self.assertEqual(self.found(self.home / "Documents/web"), [("python3 manage.py runserver", 8000)])
        self.make("Documents/web/.venv/bin/python")
        self.assertEqual(self.found(self.home / "Documents/web"), [(".venv/bin/python manage.py runserver", 8000)])

    def test_a_frontend_in_its_own_folder(self):
        folder = self.home / "Documents/app"
        self.package("Documents/app/frontend", dev="vite")
        self.make("Documents/app/backend/manage.py")
        servers = projects.servers(str(folder))
        self.assertEqual(sorted((Path(s["cwd"]).name, s["port"]) for s in servers), [("backend", 8000), ("frontend", 5173)])

    def test_a_broken_package_file(self):
        self.make("Documents/app/package.json", "{")
        self.assertEqual(self.found(self.home / "Documents/app"), [])


class PlanFromFiles(Folder):
    def setUp(self):
        super().setUp()
        self.project = self.repo("Documents/app")
        self.make("Documents/app/artisan")
        for name, value in (("project_folders", {}), ("database_service", None), ("usual_mode", None), ("scan_sessions", []),
                            ("read_state", {})):
            patcher = mock.patch.object(routine, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(routine, "load", return_value=dict(routine.SETTINGS))
        patcher.start()
        self.addCleanup(patcher.stop)

    def kinds(self, plan):
        return [step["type"] for step in plan["steps"]]

    def test_a_project_claude_never_saw(self):
        plan = routine.learn(project=self.project)
        self.assertEqual(self.kinds(plan), ["server", "url", "terminal"])
        self.assertEqual(plan["steps"][1]["url"], "http://127.0.0.1:8000")
        self.assertEqual(plan["steps"][2]["cwd"], self.project)
        self.assertEqual(plan["project"], self.project)

    def test_its_database_and_its_mode(self):
        routine.database_service.return_value = {"unit": "postgresql.service", "port": 5432}
        routine.usual_mode.return_value = "building"
        self.assertEqual(self.kinds(routine.learn(project=self.project)), ["mode", "service", "server", "url", "terminal"])

    def test_nothing_to_start_still_opens_a_terminal(self):
        project = self.repo("Documents/pustaka")
        self.assertEqual(self.kinds(routine.learn(project=project)), ["terminal"])

    def test_a_workday_without_claude_goes_by_the_last_project_you_set_up(self):
        routine.read_state.return_value = {"last_project": self.project}
        self.assertEqual(routine.learn()["project"], self.project)

    def test_a_workday_with_nothing_to_go_on(self):
        self.assertIsNone(routine.learn())

    def test_a_last_project_thats_gone(self):
        routine.read_state.return_value = {"last_project": str(self.home / "gone")}
        self.assertIsNone(routine.learn())


class TerminalStep(unittest.TestCase):
    STEP = {"type": "terminal", "cwd": "/w/app"}

    def test_it_opens_a_terminal_in_the_project(self):
        progress = []
        with mock.patch.object(routine.subprocess, "Popen") as popen, mock.patch.object(routine, "write_state"):
            routine.run({"steps": [self.STEP]}, lambda _step, state: progress.append(state), lambda _mode: None)
        self.assertEqual(popen.call_args.kwargs["cwd"], "/w/app")
        self.assertEqual(progress, ["running", "done"])

    def test_its_label(self):
        self.assertEqual(routine.label(self.STEP), "terminal")


class ToolPath(Folder):
    def test_node_from_nvm_without_claude(self):
        for version in ("v18.0.0", "v22.1.0"):
            (self.home / ".nvm/versions/node" / version / "bin").mkdir(parents=True)
        path = routine.tool_path("claude").split(":")
        self.assertIn(str(self.home / ".nvm/versions/node/v22.1.0/bin"), path)
        self.assertNotIn(str(self.home / ".nvm/versions/node/v18.0.0/bin"), path)

    def test_what_your_terminal_has_comes_before_the_guesses(self):
        (self.home / ".nvm/versions/node/v22.1.0/bin").mkdir(parents=True)
        with mock.patch.object(routine, "_shell_path", "/volta/bin:/usr/bin"):
            path = routine.tool_path("claude").split(":")
        self.assertLess(path.index("/volta/bin"), path.index(str(self.home / ".nvm/versions/node/v22.1.0/bin")))

    def test_what_the_daemon_has_comes_last(self):
        with mock.patch.dict(os.environ, {"PATH": "/snap/bin:/usr/bin"}):
            path = routine.tool_path("claude").split(":")
        self.assertEqual(path[-1], "/snap/bin")

    def shell(self, script):
        path = self.make("shell", f"#!/bin/sh\n{script}\n")
        path.chmod(0o755)
        return mock.patch.object(routine.pwd, "getpwuid", return_value=mock.Mock(pw_shell=str(path)))

    def test_your_shell_answers_past_what_it_prints(self):
        with self.shell('echo "welcome back"\nprintf %s "/fnm/bin:/usr/bin" > "$DESKAI_ANSWER"'), \
                mock.patch.object(routine, "_shell_path", None):
            self.assertEqual(routine.shell_path(), "/fnm/bin:/usr/bin")

    def test_it_is_asked_once(self):
        with self.shell('printf %s "/fnm/bin" > "$DESKAI_ANSWER"') as asked, mock.patch.object(routine, "_shell_path", None):
            routine.shell_path()
            routine.shell_path()
        self.assertEqual(asked.call_count, 1)

    def test_a_shell_that_never_answers(self):
        with self.shell("sleep 5"), mock.patch.object(routine, "_shell_path", None), \
                mock.patch.object(routine, "SHELL_WAIT_S", 0.2):
            self.assertEqual(routine.shell_path(), "")


class ServerThatDied(unittest.TestCase):
    def crashed(self, active, said=""):
        def run(args, **_):
            return mock.Mock(returncode=0 if active else 3, stdout=said)
        with mock.patch.object(routine.subprocess, "run", side_effect=run):
            return routine.crashed("deskai-app-dev")

    def test_ended_with_an_error(self):
        self.assertTrue(self.crashed(False, "Started deskai\nbash: npm: command not found\nFailed with result 'exit-code'.\n"))

    def test_still_running(self):
        self.assertFalse(self.crashed(True, "Failed with result 'exit-code'.\n"))

    def test_a_command_that_starts_something_and_returns(self):
        self.assertFalse(self.crashed(False, "Started deskai\nDeactivated successfully.\n"))

    def test_an_earlier_run_doesnt_count(self):
        self.assertFalse(self.crashed(False, "Failed with result 'exit-code'.\nStarted deskai\nDeactivated successfully.\n"))

    def test_no_user_systemd_to_ask(self):
        with mock.patch.object(routine.subprocess, "run", side_effect=OSError):
            self.assertFalse(routine.crashed("deskai-app-dev"))

    def test_waiting_stops_as_soon_as_it_has(self):
        with mock.patch.object(routine, "port_open", return_value=False), mock.patch.object(routine, "crashed", return_value=True), \
                mock.patch.object(routine.time, "sleep") as sleep:
            self.assertFalse(routine.wait_port(5173, 90, "deskai-app-dev"))
        sleep.assert_not_called()

    def run_server(self, step, crashed):
        states = []
        with mock.patch.object(routine, "start_server", return_value="deskai-app-dev"), mock.patch.object(routine, "write_state"), \
                mock.patch.object(routine, "port_open", return_value=False), mock.patch.object(routine, "crashed", return_value=crashed), \
                mock.patch.object(routine.time, "sleep"):
            routine.run({"steps": [step]}, lambda _step, state: states.append(state), lambda _mode: None)
        return states

    def test_one_with_a_port(self):
        self.assertEqual(self.run_server({"type": "server", "cwd": "/w/app", "command": "npm run dev", "port": 5173}, True),
                         ["running", "failed"])

    def test_one_with_no_port_to_ask(self):
        step = {"type": "server", "cwd": "/w/app", "command": "node server.js", "port": None}
        self.assertEqual(self.run_server(step, True), ["running", "failed"])
        self.assertEqual(self.run_server(step, False), ["running", "done"])

    def test_the_page_doesnt_wait_for_it_again(self):
        steps = [{"type": "server", "cwd": "/w/app", "command": "npm run dev", "port": 5173},
                 {"type": "url", "url": "http://127.0.0.1:5173"}]
        states = []
        with mock.patch.object(routine, "start_server", return_value="deskai-app-dev"), mock.patch.object(routine, "write_state"), \
                mock.patch.object(routine, "port_open", return_value=False), mock.patch.object(routine, "crashed", return_value=True), \
                mock.patch.object(routine.subprocess, "Popen") as opened, mock.patch.object(routine.time, "sleep") as sleep:
            routine.run({"steps": steps}, lambda step, state: states.append((step["type"], state)), lambda _mode: None)
        self.assertEqual(states[-1], ("url", "failed"))
        opened.assert_not_called()
        sleep.assert_not_called()


class ProjectThatRunsThroughDocker(Folder):
    def setUp(self):
        super().setUp()
        self.project = str(self.make("Documents/toko/docker-compose.yml").parent)

    def kinds(self):
        with mock.patch.object(routine, "project_folders", return_value={}), mock.patch.object(routine, "usual_mode", return_value=None):
            return [step["type"] for step in routine.learn(project=self.project)["steps"]]

    def test_docker_starts_it(self):
        self.assertEqual(self.kinds(), ["compose", "terminal"])

    def test_not_when_it_has_servers_of_its_own(self):
        self.make("Documents/toko/artisan")
        self.assertNotIn("compose", self.kinds())

    def run_step(self):
        states = []
        with mock.patch.object(routine, "write_state"):
            routine.run({"steps": [{"type": "compose", "cwd": self.project}]}, lambda _step, state: states.append(state), lambda _mode: None)
        return states

    def test_with_no_docker(self):
        with mock.patch.object(routine.shutil, "which", return_value=None):
            self.assertEqual(self.run_step(), ["running", "failed"])

    def test_with_docker(self):
        with mock.patch.object(routine.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(compose, "_docker_ls", return_value=[]), \
                mock.patch.object(compose, "start", return_value=(True, "", [])) as start:
            self.assertEqual(self.run_step(), ["running", "done"])
        self.assertEqual(start.call_args.args[0], "toko")

    def test_when_what_it_needs_from_another_project_fails(self):
        with mock.patch.object(routine.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(compose, "_docker_ls", return_value=[]), \
                mock.patch.object(compose, "start", return_value=(False, "manifest unknown", [], "infra")), \
                self.assertLogs("deskai", "WARNING") as logged:
            self.assertEqual(self.run_step(), ["running", "failed"])
        self.assertIn("manifest unknown", logged.output[0])

    def test_its_label(self):
        self.assertEqual(routine.label({"type": "compose", "cwd": self.project}), "docker compose")


class EverydayWords(unittest.TestCase):
    """Folders named like things you'd say anyway."""

    KNOWN = {name: f"/w/{name}" for name in ("chrome-extension", "wifi-tools", "music-player", "toko-online", "calendar")}

    def act(self, text):
        with mock.patch.object(commands, "dev_service_unit", return_value=None), \
                mock.patch.object(commands.compose, "projects", return_value={}), \
                mock.patch.object(commands, "_is_an_app", side_effect=lambda name: name.lower() in ("chrome", "calendar")):
            result = commands.quick(text, [], servers.Watch(), self.KNOWN)
        return result["actions"][0] if result else None

    def test_they_mean_the_thing(self):
        for text in ("tutup chrome", "matiin chrome", "matiin wifi", "nyalain wifi", "stop music", "matiin musik"):
            self.assertIsNone(self.act(text), text)

    def test_a_projects_whole_name_means_the_project(self):
        self.assertEqual(self.act("tutup chrome-extension"), {"type": "close_project", "project": "chrome-extension"})
        self.assertEqual(self.act("matiin calendar"), {"type": "close_project", "project": "calendar"})

    def test_part_of_a_name_that_isnt_anything_else_still_works(self):
        self.assertEqual(self.act("matiin toko"), {"type": "close_project", "project": "toko"})
        self.assertEqual(self.act("nyalain toko"), {"type": "setup_project", "project": "toko"})

    def test_what_you_say_about_other_things_doesnt_touch_a_project(self):
        self.KNOWN = {name: f"/w/{name}" for name in ("youtube-clone", "database-tugas", "postgres-notes", "sistem-gudang", "shopping-cart",
                                                      "main-app", "docker-lab", "kalkulator-app")}
        for text in ("tutup youtube", "matiin youtube", "tutup tab gudang", "matiin database", "matiin db", "stop postgres", "matiin docker",
                     "tutup server", "tutup kalkulator", "tutup art", "tutup mainan", "youtube udah kelar", "database selesai", "nyalain database",
                     "siapin youtube"):
            action = self.act(text)
            self.assertNotIn(action and action["type"], ("close_project", "setup_project"), text)

    def test_their_own_names_still_mean_them(self):
        self.KNOWN = {name: f"/w/{name}" for name in ("youtube-clone", "database-tugas", "postgres-notes", "sistem-gudang", "shopping-cart",
                                                      "main-app", "MyBakery")}
        for text, project in (("tutup youtube-clone", "youtube-clone"), ("tutup youtube clone", "youtube clone"), ("matiin database tugas", "database tugas"),
                              ("stop postgres-notes", "postgres-notes"), ("tutup gudang", "gudang"), ("tutup gudnag", "gudnag"), ("tutup gudang aja", "gudang"),
                              ("tutup shopping", "shopping"), ("tutup cart", "cart"), ("tutup shopping cart", "shopping cart"), ("tutup main", "main"),
                              ("tutup bakery", "bakery"), ("tutup the gudang", "the gudang")):
            self.assertEqual(self.act(text), {"type": "close_project", "project": project}, text)

    def test_indonesian_endings_on_a_name(self):
        self.KNOWN = {name: f"/w/{name}" for name in ("sistem-gudang", "absensi", "toko-online")}
        for text, project in (("matiin gudangnya", "gudangnya"), ("stop absensinya", "absensinya"), ("tutup project gudangnya", "gudangnya"),
                              ("tutup gudangku", "gudangku"), ("tutup tokonya", "tokonya"), ("tutup absensimu", "absensimu")):
            action = self.act(text)
            self.assertEqual(action, {"type": "close_project", "project": project}, text)
            self.assertEqual(commands.find_project(project, self.KNOWN), f"/w/{next(k for k in self.KNOWN if project[:4] in k)}", text)
        self.assertTrue(commands._names_project({"type": "close_project", "project": "sistem-gudang"}, "matiin gudangnya", self.KNOWN))

    def test_the_same_endings_on_what_isnt_a_project(self):
        self.KNOWN = {name: f"/w/{name}" for name in ("youtube-clone", "database-tugas", "chrome-extension")}
        for text in ("tutup youtubenya", "matiin databasenya", "tutup chromenya"):
            self.assertIsNone(self.act(text), text)

    def test_names_with_the_word_project_in_them(self):
        self.KNOWN = {name: f"/w/{name}" for name in ("project-x", "my-project", "projectzero")}
        for text, project in (("tutup project-x", "project-x"), ("matiin project-x", "project-x"), ("tutup projectzero", "projectzero")):
            self.assertEqual(self.act(text), {"type": "close_project", "project": project}, text)

    def test_words_in_a_name_written_together(self):
        self.KNOWN = {name: f"/w/{name}" for name in ("ERPSystem", "APIGateway", "myShopApp", "webshop")}
        for text, project in (("tutup system", "system"), ("tutup gateway", "gateway"), ("tutup shop app", "shop app"), ("tutup erp", "erp")):
            self.assertEqual(self.act(text), {"type": "close_project", "project": project}, text)
        self.assertIsNone(self.act("tutup hop"), "letters from inside a word aren't a name")

    def test_nor_does_the_model_get_to_close_it(self):
        action = {"type": "close_project", "project": "chrome-extension"}
        with mock.patch.object(commands, "_is_an_app", side_effect=lambda name: name.lower() == "chrome"):
            self.assertFalse(commands._names_project(action, "tutup chrome", self.KNOWN))
            self.assertTrue(commands._names_project(action, "tutup chrome-extension", self.KNOWN))

    def test_a_word_in_an_apps_name(self):
        app = mock.Mock(should_show=lambda: True)
        app.get_name.return_value = "Google Chrome"
        with mock.patch.object(commands, "Gio") as gio:
            gio.AppInfo.get_all.return_value = [app]
            self.assertTrue(commands._is_an_app("chrome"))
            self.assertTrue(commands._is_an_app("Google Chrome"))
            self.assertFalse(commands._is_an_app("chro"))
            self.assertFalse(commands._is_an_app("toko"))


class SlipsOfTheFingers(unittest.TestCase):
    """Names as they really get typed, in a hurry, over and over. With a small local model, what the lists miss takes half a minute."""

    KNOWN = {name: f"/w/{name}" for name in ("pabrik-inventory", "sistem-gudang", "string-utils", "main-app")}

    def act(self, text):
        with mock.patch.object(commands, "dev_service_unit", return_value=None), \
                mock.patch.object(commands.compose, "projects", return_value={}), \
                mock.patch.object(commands, "_is_an_app", return_value=False):
            result = commands.quick(text, [], servers.Watch(), self.KNOWN)
        return result["actions"][0] if result else None

    def test_more_than_one_letter_missing(self):
        for text, kind, name in (("setup invntry", "setup_project", "invntry"), ("set up invntry", "setup_project", "invntry"),
                                 ("siapin invntry", "setup_project", "invntry"), ("siapin project invntry", "setup_project", "invntry"),
                                 ("matiin invntry", "close_project", "invntry"), ("matikan invntry", "close_project", "invntry"),
                                 ("matikan invntr", "close_project", "invntr"), ("matiin project invntry", "close_project", "invntry"),
                                 ("tutup inventry", "close_project", "inventry")):
            self.assertEqual(self.act(text), {"type": kind, "project": name}, text)
            self.assertEqual(commands.find_project(name, self.KNOWN), "/w/pabrik-inventory", text)

    def test_a_little_word_before_the_name(self):
        for text, kind, name in (("setup in invntry", "setup_project", "invntry"), ("setup in gudang", "setup_project", "gudang"),
                                 ("setup on gudang", "setup_project", "gudang"), ("setup for gudang", "setup_project", "gudang"),
                                 ("setup ke gudang", "setup_project", "gudang"), ("siapin buat gudang", "setup_project", "gudang"),
                                 ("siapin untuk gudang", "setup_project", "gudang"), ("tutup in gudang", "close_project", "gudang")):
            self.assertEqual(self.act(text), {"type": kind, "project": name}, text)

    def test_the_same_goes_for_what_a_model_answers(self):
        action = {"type": "setup_project", "project": "pabrik-inventory"}
        self.assertTrue(commands._names_project(action, "setup pin invntry", self.KNOWN))

    def test_still_not_letters_it_only_shares(self):
        for text in ("tutup ring", "tutup mainan", "tutup tabgudang", "matiin inventorynyanya"):
            self.assertIsNone(self.act(text), text)



class WhereClaudeOpensWords(unittest.TestCase):
    KNOWN = {"pabrik-app": "/w/pabrik-app", "toko-online": "/w/toko-online", "my-terminal": "/w/my-terminal",
             "claude-desktop": "/w/claude-desktop"}

    def act(self, text):
        result = commands.quick(text, [("building", "Building")], servers.Watch(), self.KNOWN)
        return result and result["actions"][0]

    def test_saying_claude_desktop(self):
        for text in ("setup pabrik yang terminal ganti dengan claude desktop", "setup pabrik ganti terminal jadi claude desktop",
                     "setup pabrik terminalnya diganti claude desktop", "setup pabrik pakai claude desktop",
                     "setup pabrik pake calude desktop dong", "setup pabrik di claude dekstop", "setup pabrik buka di claude desktop",
                     "setup pabrik claude desktop", "setup pabrik yang kebuka itu bukan terminal tapi claude desktop",
                     "setup pabrik pakai claude desktop bukan terminal", "setup pabrik pakai app claude aja",
                     "set up pabrik in claude desktop", "setup pabrik open in claude desktop instead of a terminal",
                     "setup pabrik replace the terminal with claude desktop"):
            self.assertEqual(self.act(text), {"type": "setup_project", "project": "pabrik", "claude_in": "desktop"}, text)

    def test_saying_a_terminal(self):
        for text in ("setup pabrik pakai terminal", "setup pabrik di terminal", "setup pabrik balik ke terminal",
                     "setup pabrik yang claude desktop ganti terminal", "setup pabrik in a terminal"):
            self.assertEqual(self.act(text), {"type": "setup_project", "project": "pabrik", "claude_in": "terminal"}, text)

    def test_any_way_of_asking_for_a_setup(self):
        self.assertEqual(self.act("mau kerja di toko, pakai claude desktop"), {"type": "setup_project", "project": "toko", "claude_in": "desktop"})
        self.assertEqual(self.act("siapin toko pakai claude desktop"), {"type": "setup_project", "project": "toko", "claude_in": "desktop"})

    def test_saying_nothing_about_it_changes_nothing(self):
        self.assertEqual(self.act("setup pabrik"), {"type": "setup_project", "project": "pabrik"})

    def test_a_project_with_those_words_in_its_name(self):
        self.assertEqual(self.act("setup my terminal"), {"type": "setup_project", "project": "my terminal"})
        self.assertEqual(self.act("setup my-terminal"), {"type": "setup_project", "project": "my-terminal"})
        self.assertEqual(self.act("setup claude desktop"), {"type": "setup_project", "project": "claude desktop"})

    def test_a_word_on_its_own_isnt_a_choice(self):
        self.assertIsNone(self.act("setup pabrik terminal"))

    def test_nothing_it_doesnt_know_as_a_project(self):
        self.assertIsNone(self.act("setup gudang pakai claude desktop"))
        self.assertIsNone(self.act("lanjut kerja di terminal"))

    def test_being_told_not_to(self):
        self.assertEqual(self.act("jangan setup pabrik pakai claude desktop"), {"type": "left_alone"})

    def test_the_model_cant_change_it(self):
        self.assertNotIn("claude_in", json.dumps(commands.COMMAND_SCHEMA))


class WhereClaudeOpens(unittest.TestCase):
    SESSION = "11111111-2222-4333-8444-555555555555"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        for patcher in (mock.patch.object(routine, "ROUTINE_FILE", self.home / "routine.json"),
                        mock.patch.object(routine.launcher, "handles", return_value=True),
                        mock.patch.object(routine, "write_state"), mock.patch.object(routine.Path, "home", return_value=self.home)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def claude(self, **more):
        return {"type": "claude", "cwd": "/w/app", "command": ["claude", "--resume", self.SESSION], **more}

    def run_step(self, step, opening=None):
        states = []
        with mock.patch.object(routine.launcher, "open_uri", side_effect=opening) as opened, \
                mock.patch.object(routine.subprocess, "Popen") as popen:
            routine.run({"steps": [step]}, lambda _step, state: states.append(state), lambda _mode: None)
        return states[-1], opened, popen

    def live(self, entrypoint):
        sessions = self.home / ".claude" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / f"{os.getpid()}.json").write_text(json.dumps({"sessionId": self.SESSION, "entrypoint": entrypoint}))

    def test_a_choice_is_kept_by_project(self):
        self.assertTrue(routine.choose_claude_in("/w/app", "desktop"))
        self.assertTrue(routine.choose_claude_in("/w/other", "desktop"))
        self.assertTrue(routine.choose_claude_in("/w/other", "terminal"))
        self.assertEqual(routine.load()["claude_in"], {"/w/app": "desktop"})

    def test_it_keeps_the_rest_of_the_file(self):
        routine.save({**routine.load(), "pinned": True, "steps": [{"type": "url", "url": "http://127.0.0.1:1"}]})
        routine.choose_claude_in("/w/app", "desktop")
        self.assertTrue(routine.load()["pinned"])
        self.assertEqual(len(routine.load()["steps"]), 1)

    def test_no_claude_desktop_to_choose(self):
        routine.launcher.handles.return_value = False
        self.assertFalse(routine.choose_claude_in("/w/app", "desktop"))
        self.assertFalse((self.home / "routine.json").exists())
        self.assertTrue(routine.choose_claude_in("/w/app", "terminal"))

    def test_a_file_edited_by_hand_into_something_else(self):
        routine.save({**routine.load(), "claude_in": "desktop"})
        self.assertFalse(routine.with_claude_in({"steps": [self.claude()]})["steps"][0]["desktop"])
        self.assertTrue(routine.choose_claude_in("/w/app", "desktop"))

    def test_a_plan_is_marked_with_it(self):
        routine.choose_claude_in("/w/app", "desktop")
        steps = routine.with_claude_in({"steps": [self.claude(), {"type": "terminal", "cwd": "/w/other"}, {"type": "mode", "mode": "x"}]})["steps"]
        self.assertEqual([step.get("desktop") for step in steps], [True, False, None])
        self.assertEqual(routine.label(steps[0]), "Claude Desktop")
        self.assertEqual(routine.label(steps[1]), "terminal")

    def test_the_link_for_a_conversation(self):
        self.assertEqual(routine.desktop_link(self.claude()), f"claude://resume?session={self.SESSION}")

    def test_the_link_for_a_folder(self):
        self.assertEqual(routine.desktop_link({"type": "terminal", "cwd": "/w/my app&x"}), "claude://code/new?folder=%2Fw%2Fmy+app%26x")

    def test_a_session_that_isnt_one_gets_its_folder(self):
        step = {"type": "claude", "cwd": "/w/app", "command": ["claude", "--resume", "x&folder=/etc"]}
        self.assertEqual(routine.desktop_link(step), "claude://code/new?folder=%2Fw%2Fapp")

    def test_it_opens_in_claude_desktop(self):
        state, opened, popen = self.run_step(self.claude(desktop=True))
        self.assertEqual(state, "done")
        opened.assert_called_once_with(f"claude://resume?session={self.SESSION}")
        popen.assert_not_called()

    def test_a_project_claude_never_saw(self):
        state, opened, popen = self.run_step({"type": "terminal", "cwd": "/w/app", "desktop": True})
        self.assertEqual(state, "done")
        opened.assert_called_once_with("claude://code/new?folder=%2Fw%2Fapp")
        popen.assert_not_called()

    def test_without_a_choice_it_stays_in_a_terminal(self):
        state, opened, popen = self.run_step(self.claude())
        self.assertEqual(state, "done")
        opened.assert_not_called()
        popen.assert_called_once()

    def test_when_it_wont_open_a_terminal_does_it(self):
        for step in (self.claude(desktop=True), {"type": "terminal", "cwd": "/w/app", "desktop": True}):
            state, _opened, popen = self.run_step(step, opening=OSError("no"))
            self.assertEqual(state, "done")
            popen.assert_called_once()
            self.assertTrue(step["no_desktop"])
            self.assertFalse(step["desktop"])

    def test_open_in_a_terminal_it_stays_there(self):
        self.live("cli")
        state, opened, popen = self.run_step(self.claude(desktop=True))
        self.assertEqual(state, "skipped")
        opened.assert_not_called()
        popen.assert_not_called()

    def test_open_in_claude_desktop_it_shows_it(self):
        self.live("claude-desktop")
        state, opened, _popen = self.run_step(self.claude(desktop=True))
        self.assertEqual(state, "done")
        opened.assert_called_once()

    def test_open_in_claude_desktop_a_terminal_wouldnt_fork_it(self):
        self.live("claude-desktop")
        state, _opened, popen = self.run_step(self.claude(desktop=True), opening=OSError("no"))
        self.assertEqual(state, "skipped")
        popen.assert_not_called()
        state, _opened, popen = self.run_step(self.claude())
        self.assertEqual(state, "skipped")
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
