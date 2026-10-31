"""Watching the servers on the desk. Run with: python3 -m unittest discover tests"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import servers  # noqa: E402

VITE = {"unit": "deskai-app-dev", "cwd": "/work/app", "command": "npm run dev", "port": 5173}
DB = {"type": "service", "unit": "postgresql@18-main.service", "port": 5432}


class Scan(unittest.TestCase):
    def setUp(self):
        self.watch = servers.Watch()
        self.running, self.databases, self.containers, self.open_ports = [], [], [], set()
        self.crashed = set()  # containers that exited on their own, not by `docker stop`
        for name, target in (("servers", lambda: list(self.running)), ("databases", lambda: list(self.databases)),
                             ("containers", lambda: list(self.containers)),
                             ("container_stopped_by_you", lambda name: name not in self.crashed)):
            patcher = mock.patch.object(servers.session, name, side_effect=target)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(servers.routine, "port_open", side_effect=lambda port: port in self.open_ports)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(servers.session, "hand_started", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def state(self, unit=VITE["unit"]):
        return self.watch.get(unit)["state"]

    def test_a_running_server_that_answers_is_up(self):
        self.running, self.open_ports = [VITE], {5173}
        self.assertEqual(self.watch.scan(), [])
        self.assertEqual(self.state(), "up")
        self.assertEqual(self.watch.get(VITE["unit"])["label"], "vite :5173")

    def test_a_new_server_gets_time_to_start(self):
        self.running = [VITE]
        self.assertEqual(self.watch.scan(), [])
        self.assertEqual(self.state(), "starting")

    def test_a_server_that_exits_is_down_at_once_and_reported_once(self):
        self.running, self.open_ports = [VITE], {5173}
        self.watch.scan()
        self.running, self.open_ports = [], set()
        self.assertEqual([item["unit"] for item in self.watch.scan()], [VITE["unit"]])
        self.assertEqual(self.state(), "down")
        self.assertEqual(self.watch.scan(), [])

    def test_running_but_not_answering_is_down_after_the_grace_and_a_second_look(self):
        self.running, self.open_ports = [VITE], {5173}
        self.watch.scan()
        self.open_ports = set()
        with mock.patch.object(servers.time, "time", return_value=time.time() + servers.GRACE_S + 1):
            self.assertEqual(self.watch.scan(), [])
            self.assertEqual(len(self.watch.scan()), 1)

    def test_it_coming_back_by_itself_settles_it(self):
        self.running, self.open_ports = [VITE], {5173}
        self.watch.scan()
        self.running, self.open_ports = [], set()
        self.watch.scan()
        self.running, self.open_ports = [VITE], {5173}
        self.watch.scan()
        self.assertEqual(self.state(), "up")

    def test_a_database_is_watched_only_once_seen_running(self):
        self.watch.scan()
        self.assertIsNone(self.watch.get(DB["unit"]))
        self.databases, self.open_ports = [DB], {5432}
        self.watch.scan()
        self.assertEqual(self.state(DB["unit"]), "up")
        self.databases, self.open_ports = [], set()
        self.assertEqual(len(self.watch.scan()), 1)

    def test_a_stopped_server_is_no_longer_expected(self):
        self.running, self.open_ports = [VITE], {5173}
        self.watch.scan()
        with mock.patch.object(servers.subprocess, "run", return_value=mock.Mock(returncode=0)):
            self.assertTrue(self.watch.stop(VITE["unit"]))
        self.running, self.open_ports = [], set()
        self.assertEqual(self.watch.scan(), [])
        self.assertEqual(self.watch.summary(), [])

    def test_a_look_that_still_sees_a_stopped_server_doesnt_bring_it_back(self):
        self.running, self.open_ports = [VITE], {5173}
        self.watch.scan()
        with mock.patch.object(servers.subprocess, "run", return_value=mock.Mock(returncode=0)):
            self.watch.stop(VITE["unit"])
        self.watch.scan()  # systemd hasn't caught up
        self.assertEqual(self.watch.summary(), [])
        self.running = []
        self.assertEqual(self.watch.scan(), [])

    def test_finding_servers_by_name(self):
        self.running, self.databases, self.open_ports = [VITE], [DB], {5173, 5432}
        self.watch.scan()
        self.assertEqual(self.watch.match("vite"), [VITE["unit"]])
        self.assertEqual(self.watch.match("5173"), [VITE["unit"]])
        self.assertEqual(self.watch.match("server postgresql"), [DB["unit"]])
        self.assertEqual(len(self.watch.match("all")), 2)
        self.assertEqual(self.watch.match("nope"), [])


class ClosingAProject(unittest.TestCase):
    PHP = {"unit": "deskai-app-php.service", "cwd": "/work/app", "command": "php -S 127.0.0.1:8000", "port": 8000}
    NODE = {"unit": "deskai-app-dev.service", "cwd": "/work/app/web", "command": "npm run dev", "port": 5173}
    OTHER = {"unit": "deskai-shop-php.service", "cwd": "/work/shop", "command": "php -S 127.0.0.1:9000", "port": 9000}
    DB = {"unit": "postgresql@18-main.service", "port": 5432}

    def close(self, running, databases):
        watch = servers.Watch()
        self.stopped = []

        def run(command, **_kwargs):
            if "stop" in command:
                self.stopped.append(command[-1])
            return mock.Mock(returncode=0, stdout="")
        with mock.patch.object(servers.session, "servers", return_value=running), \
                mock.patch.object(servers.session, "databases", return_value=[]), \
                mock.patch.object(servers.routine, "port_open", return_value=True), \
                mock.patch.object(servers.routine, "database_service", side_effect=lambda folder: databases.get(folder)), \
                mock.patch.object(servers.subprocess, "run", side_effect=run):
            return servers.close_project(watch, "/work/app")

    def test_its_servers_and_database_stop(self):
        report = self.close([self.PHP, self.NODE], {"/work/app": self.DB})
        self.assertEqual(sorted(self.stopped), sorted([self.PHP["unit"], self.NODE["unit"], self.DB["unit"]]))
        self.assertEqual({state for _label, state, _other in report}, {"stopped"})

    def test_another_projects_server_keeps_running(self):
        self.close([self.PHP, self.OTHER], {"/work/app": self.DB})
        self.assertNotIn(self.OTHER["unit"], self.stopped)

    def test_a_database_another_project_uses_stays_on(self):
        report = self.close([self.PHP, self.OTHER], {"/work/app": self.DB, "/work/shop": self.DB})
        self.assertNotIn(self.DB["unit"], self.stopped)
        self.assertIn(("postgresql@18-main", "kept", "shop"), report)

    def test_nothing_running(self):
        self.assertEqual(self.close([], {}), [])

    def test_a_folder_with_a_similar_name_isnt_inside(self):
        self.assertFalse(servers.session.inside("/work/app-old", "/work/app"))
        self.assertTrue(servers.session.inside("/work/app/web", "/work/app"))


class GitState(unittest.TestCase):
    """On real repositories in a temporary folder. Git's own variables are cleared first: a hook sets
    them, and they would point these commands at the real repository."""

    def setUp(self):
        env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
        patcher = mock.patch.dict(os.environ, {**env, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.repo = self.root / "app"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                       cwd=self.repo, check=True, capture_output=True)

    def write(self, name, text="x"):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, name="a.txt"):
        self.write(name, name)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)

    def remote(self):
        self.git("init", "-q", "--bare", str(self.root / "remote.git"))
        self.git("remote", "add", "origin", str(self.root / "remote.git"))

    def state(self, folder=None):
        return servers.git_state(str(folder or self.repo))

    def test_nothing_waiting(self):
        self.remote()
        self.commit()
        self.git("push", "-q", "origin", "main")
        self.assertEqual(self.state(), (0, 0))

    def test_changed_and_new_files_count_one_by_one(self):
        self.commit()
        self.write("a.txt", "changed")
        self.write("new/one.txt")
        self.write("new/two.txt")
        self.assertEqual(self.state(), (3, 0))

    def test_ignored_files_dont_count(self):
        self.write(".gitignore", "vendor/\n")
        self.commit("b.txt")
        self.write("vendor/lib.php")
        self.assertEqual(self.state(), (0, 0))

    def test_a_project_inside_a_bigger_repository_counts_its_own_files(self):
        self.commit("web/a.txt")
        self.write("web/b.txt")
        self.write("api/c.txt")
        self.write("api/d.txt")
        self.assertEqual(self.state(self.repo / "web"), (1, 0))

    def test_commits_that_arent_on_a_remote(self):
        self.remote()
        self.commit()
        self.git("push", "-q", "origin", "main")
        self.commit("b.txt")
        self.commit("c.txt")
        self.assertEqual(self.state(), (0, 2))

    def test_a_new_branch_counts_what_it_added(self):
        self.remote()
        self.commit()
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", "-b", "feature")
        self.commit("b.txt")
        self.assertEqual(self.state(), (0, 1))

    def test_commits_on_another_remote_branch_are_safe(self):
        self.remote()
        self.commit()
        self.git("push", "-q", "origin", "main:elsewhere")
        self.assertEqual(self.state(), (0, 0))

    def test_without_a_remote_nothing_counts_as_not_pushed(self):
        self.commit()
        self.commit("b.txt")
        self.assertEqual(self.state(), (0, 0))

    def test_no_commits_yet(self):
        self.remote()
        self.write("a.txt")
        self.assertEqual(self.state(), (1, 0))

    def test_a_file_name_that_isnt_utf8(self):
        self.commit()
        self.git("config", "core.quotePath", "false")
        (self.repo / b"caf\xe9.txt".decode("utf-8", "surrogateescape")).write_text("x")
        self.assertEqual(self.state(), (1, 0))

    def test_a_folder_git_doesnt_know(self):
        self.assertIsNone(self.state(self.root))

    def test_a_folder_thats_gone(self):
        self.assertIsNone(self.state(self.root / "gone"))

    def test_no_git_installed(self):
        with mock.patch.object(servers.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertIsNone(self.state())

    def test_git_taking_too_long(self):
        with mock.patch.object(servers.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 5)):
            self.assertIsNone(self.state())


APP = {"unit": "docker:shop_app", "name": "shop_app", "command": "shop-app:latest", "port": 8000,
       "cwd": "/work/shop"}
MYSQL = {"unit": "docker:ubp_mysql", "name": "ubp_mysql", "command": "mysql:5.7", "port": 3309, "cwd": ""}


class Containers(Scan):
    def test_a_running_container_is_listed_with_its_port(self):
        self.containers, self.open_ports = [APP], {8000}
        self.watch.scan()
        row = self.watch.summary()[0]
        self.assertEqual((row["kind"], row["state"], row["label"], row["project"]), ("container", "up", "shop_app :8000", "shop"))

    def test_one_you_stopped_just_leaves(self):
        self.containers, self.open_ports = [APP], {8000}
        self.watch.scan()
        self.containers = []
        self.assertEqual(self.watch.scan(), [])
        self.assertIsNone(self.watch.get(APP["unit"]))

    def test_one_that_crashed_is_down_and_reported_once(self):
        self.containers, self.open_ports = [MYSQL], {3309}
        self.watch.scan()
        self.containers, self.open_ports, self.crashed = [], set(), {"ubp_mysql"}
        self.assertEqual([item["unit"] for item in self.watch.scan()], [MYSQL["unit"]])
        self.assertEqual(self.state(MYSQL["unit"]), "down")
        self.assertEqual(self.watch.scan(), [])

    def test_docker_finds_them_all(self):
        self.containers = [APP, MYSQL]
        self.watch.scan()
        self.assertEqual(sorted(self.watch.match("docker")), [APP["unit"], MYSQL["unit"]])
        self.assertEqual(self.watch.match("mysql"), [MYSQL["unit"]])

    def test_stop_and_restart_go_through_docker(self):
        self.containers, self.open_ports = [APP], {8000}
        self.watch.scan()
        with mock.patch.object(servers.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                mock.patch.object(servers.routine, "wait_port", return_value=True):
            self.assertTrue(self.watch.restart(APP["unit"]))
            self.assertEqual(run.call_args.args[0], ["docker", "restart", "shop_app"])
            self.assertTrue(self.watch.stop(APP["unit"]))
            self.assertEqual(run.call_args.args[0], ["docker", "stop", "shop_app"])

    def test_its_log_is_dockers(self):
        item = {"unit": APP["unit"], "kind": "container"}
        self.assertEqual(servers.log_command(item), "docker logs -n 200 -f shop_app")


class DockerOutput(unittest.TestCase):
    PS = ('{"Names":"shop_app","Image":"shop-app:latest","Ports":"0.0.0.0:8000->80/tcp",'
          '"Labels":"com.docker.compose.project=shop,com.docker.compose.project.working_dir=/work/shop"}\n'
          '{"Names":"shared-db_mysql","Image":"mysql:5.7","Ports":"33060/tcp, 0.0.0.0:3309->3306/tcp, [::]:3309->3306/tcp",'
          '"Labels":""}\n'
          '{"Names":"worker","Image":"busybox","Ports":"","Labels":""}\n')

    def test_containers_from_docker_ps(self):
        with mock.patch.object(servers.session.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(servers.session, "docker_running", return_value=True), \
                mock.patch.object(servers.session.subprocess, "run", return_value=mock.Mock(stdout=self.PS)):
            found = servers.session.containers()
        self.assertEqual([(c["unit"], c["port"], c["cwd"]) for c in found],
                         [("docker:shop_app", 8000, "/work/shop"), ("docker:shared-db_mysql", 3309, ""),
                          ("docker:worker", None, "")])

    def test_no_docker_no_containers(self):
        with mock.patch.object(servers.session.shutil, "which", return_value=None):
            self.assertEqual(servers.session.containers(), [])

    def test_docker_left_to_start_on_first_use_isnt_woken(self):
        with mock.patch.object(servers.session.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(servers.session, "docker_running", return_value=False), \
                mock.patch.object(servers.session.subprocess, "run") as run:
            self.assertEqual(servers.session.containers(), [])
            self.assertTrue(servers.session.container_stopped_by_you("shop_app"))
        run.assert_not_called()

    def test_system_databases(self):
        units = ("mysql.service loaded active running MySQL\npostgresql.service loaded active exited PostgreSQL\n"
                 "postgresql@16-main.service loaded active running PostgreSQL Cluster 16-main\n"
                 "redis-server.service loaded active running Redis\nnginx.service loaded active running nginx\n")
        with mock.patch.object(servers.session.subprocess, "run", return_value=mock.Mock(stdout=units)):
            self.assertEqual(servers.session.system_databases(), ["mysql.service", "postgresql@16-main.service", "redis-server.service"])

    def test_how_a_container_ended(self):
        for out, yours in (("0 false", True), ("137 false", True), ("143 false", True), ("1 false", False),
                           ("137 true", False), ("", True)):
            with mock.patch.object(servers.session, "docker_running", return_value=True), \
                    mock.patch.object(servers.session.subprocess, "run", return_value=mock.Mock(stdout=out)):
                self.assertEqual(servers.session.container_stopped_by_you("x"), yours, out)


HAND = {"unit": "manual-8000", "cwd": "/work/app", "command": "php artisan serve", "port": 8000, "pid": 4321}


class HandStarted(unittest.TestCase):
    def setUp(self):
        self.watch = servers.Watch()
        self.found = [HAND]
        for name, target in (("servers", list), ("databases", list), ("containers", list),
                             ("hand_started", lambda: list(self.found))):
            patcher = mock.patch.object(servers.session, name, side_effect=target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_it_shows_up_as_yours(self):
        self.watch.scan()
        row = self.watch.summary()[0]
        self.assertEqual((row["kind"], row["state"], row["label"], row["project"]), ("manual", "up", "server :8000", "app"))

    def test_ctrl_c_isnt_an_alarm(self):
        self.watch.scan()
        self.found = []
        self.assertEqual(self.watch.scan(), [])
        self.assertEqual(self.watch.summary(), [])

    def test_stopping_it_goes_by_its_process_not_systemd(self):
        self.watch.scan()
        with mock.patch.object(servers.session, "stop_hand_started", return_value=True) as stop, \
                mock.patch.object(servers.subprocess, "run") as systemctl:
            self.assertTrue(self.watch.stop(HAND["unit"]))
        self.assertEqual(stop.call_args[0][0]["pid"], 4321)
        systemctl.assert_not_called()

    def test_a_stopped_one_doesnt_flicker_back(self):
        self.watch.scan()
        with mock.patch.object(servers.session, "stop_hand_started", return_value=True):
            self.watch.stop(HAND["unit"])
        self.watch.scan()  # a look that started before the stop
        self.assertEqual(self.watch.summary(), [])

    def test_restarting_it_makes_it_deskais_own(self):
        self.watch.scan()
        with mock.patch.object(servers.session, "stop_hand_started", return_value=True), \
                mock.patch.object(servers.routine, "load", return_value={}), \
                mock.patch.object(servers.routine, "wait_port", return_value=True), \
                mock.patch.object(servers.routine, "start_server") as start:
            self.assertTrue(self.watch.restart(HAND["unit"]))
        self.assertEqual(start.call_args[0][0]["command"], "php artisan serve")

    def test_if_it_wont_stop_it_isnt_started_twice(self):
        self.watch.scan()
        with mock.patch.object(servers.session, "stop_hand_started", return_value=False), \
                mock.patch.object(servers.routine, "start_server") as start:
            self.assertFalse(self.watch.restart(HAND["unit"]))
        start.assert_not_called()

    def test_only_this_projects(self):
        self.watch.scan()
        self.assertEqual(len(self.watch.hand_started_in("/work/app")), 1)
        self.assertEqual(self.watch.hand_started_in("/work/other"), [])

    def test_it_has_no_log_of_ours(self):
        self.watch.scan()
        self.assertEqual(servers.last_log_line(self.watch.get(HAND["unit"])), "")


class ProjectRoot(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name).resolve()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def folder(self, *parts, mark=None):
        path = self.home.joinpath(*parts)
        path.mkdir(parents=True)
        if mark:
            (path / mark).touch()
        return path

    def test_found_from_a_folder_inside_it(self):
        app = self.folder("work", "app", mark="composer.json")
        public = self.folder("work", "app", "public")
        self.assertEqual(servers.session.project_root(str(public)), str(app))

    def test_home_itself_or_a_plain_folder_isnt_one(self):
        self.assertIsNone(servers.session.project_root(str(self.home)))
        self.assertIsNone(servers.session.project_root(str(self.folder("Downloads"))))

    def test_outside_home_or_hidden_isnt_one(self):
        self.assertIsNone(servers.session.project_root("/usr/lib"))
        self.assertIsNone(servers.session.project_root(str(self.folder(".cache", "tool", mark="package.json"))))


def free_port():
    for port in range(23000, 24000):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise OSError("no free port")


class RealProcesses(unittest.TestCase):
    """Against a real server in a real project: finding it, and stopping only it."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        home = Path(folder.name).resolve()
        patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.project = home / "work" / "app"
        self.project.mkdir(parents=True)
        (self.project / "package.json").touch()
        self.port = free_port()

    def serve(self, **how):
        process = subprocess.Popen([sys.executable, "-m", "http.server", str(self.port), "--bind", "127.0.0.1"], cwd=self.project,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **how)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        deadline = time.monotonic() + 5
        while self.port not in servers.session.listeners():
            self.assertLess(time.monotonic(), deadline, "the server never came up")
            time.sleep(0.05)
        return process

    def found(self):
        return next((s for s in servers.session.hand_started() if s["port"] == self.port), None)

    def test_finding_it(self):
        process = self.serve()
        server = self.found()
        self.assertEqual((server["pid"], server["cwd"], server["unit"]), (process.pid, str(self.project), f"manual-{self.port}"))

    def test_found_after_what_started_it_has_gone(self):
        process = self.serve()
        group, real = os.getpgid(process.pid), servers.session._command_line

        def command_line(pid):
            if pid == group:
                raise FileNotFoundError(pid)
            return real(pid)

        with mock.patch.object(servers.session, "_command_line", side_effect=command_line):
            server = self.found()
        self.assertEqual(server["pid"], process.pid)
        self.assertIn("http.server", server["command"])

    def test_stopping_it(self):
        process = self.serve(start_new_session=True)
        self.assertTrue(servers.session.stop_hand_started(self.found()))
        self.assertEqual(process.wait(timeout=5), -15)

    def test_a_job_of_its_own_stops_whole(self):
        self.assertTrue(self.serve(process_group=0))
        self.assertTrue(servers.session._whole_job_is_safe(self.found()["pid"]))

    def test_a_group_shared_with_a_tool_doesnt_go_down_with_it(self):
        pid = self.serve(process_group=0).pid
        with mock.patch.object(servers.session, "_command_line", return_value=["claude", "--resume"]):
            self.assertFalse(servers.session._whole_job_is_safe(pid))

    def test_a_session_leader_is_stopped_alone(self):
        pid = self.serve(start_new_session=True).pid
        self.assertFalse(servers.session._whole_job_is_safe(pid))

    def test_someone_elses_process_on_the_port_is_left_alone(self):
        process = self.serve()
        stale = {**self.found(), "pid": process.pid + 99999}
        self.assertFalse(servers.session.stop_hand_started(stale))
        self.assertIsNone(process.poll())


class MatchingTypos(unittest.TestCase):
    def test_a_slip_of_the_fingers(self):
        watch = servers.Watch()
        watch.items[VITE["unit"]] = {"unit": VITE["unit"], "kind": "server", "label": "vite :5173", "command": "npm run dev",
                                     "cwd": "/work/gudang", "port": 5173, "state": "up"}
        self.assertEqual(watch.match("gudnag"), [VITE["unit"]])
        self.assertEqual(watch.match("nope"), [])


if __name__ == "__main__":
    unittest.main()
