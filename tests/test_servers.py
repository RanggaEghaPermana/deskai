"""Watching the servers on the desk. Run with: python3 -m unittest discover tests"""

import sys
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
        self.running, self.databases, self.open_ports = [], [], set()
        for name, target in (("servers", lambda: list(self.running)), ("databases", lambda: list(self.databases))):
            patcher = mock.patch.object(servers.session, name, side_effect=target)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(servers.routine, "port_open", side_effect=lambda port: port in self.open_ports)
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


if __name__ == "__main__":
    unittest.main()
