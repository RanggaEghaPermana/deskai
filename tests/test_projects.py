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
import projects  # noqa: E402
import routine  # noqa: E402


class Folder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
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


if __name__ == "__main__":
    unittest.main()
