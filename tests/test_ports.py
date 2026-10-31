"""Who has which port, and what would clash. Run with: python3 -m unittest discover tests"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import commands  # noqa: E402
import compose  # noqa: E402
import ports  # noqa: E402
import servers  # noqa: E402

SS = """LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*
LISTEN 0 511 0.0.0.0:80 0.0.0.0:*
LISTEN 0 4096 127.0.0.1:11434 0.0.0.0:* users:(("ollama",pid=900,fd=3))
LISTEN 0 511 127.0.0.1:5173 0.0.0.0:* users:(("node",pid=4000,fd=20))
LISTEN 0 511 127.0.0.1:41397 0.0.0.0:* users:(("code",pid=5000,fd=9))
"""
DOCKER_PS = "shop_app\t0.0.0.0:8000->80/tcp\nshared-db_mysql\t33060/tcp, 0.0.0.0:3309->3306/tcp, [::]:3309->3306/tcp\n"


class Project(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def write(self, folder, compose_text, env=""):
        (self.root / folder).mkdir(parents=True)
        (self.root / folder / "docker-compose.yml").write_text(compose_text)
        if env:
            (self.root / folder / ".env").write_text(env)
        return str(self.root / folder)

    def test_its_ports_with_their_variables(self):
        folder = self.write("records", 'services:\n  app:\n    ports:\n      - "${APP_BIND_IP:-0.0.0.0}:${APP_PORT:-8085}:80"\n'
                                      '      # - "9999:80"\n      - 8443:443/tcp\n    volumes:\n      - ".:/app"\n')
        self.assertEqual(ports.project_ports(folder), [8085, 8443])

    def test_its_env_wins_over_the_default(self):
        folder = self.write("portal", 'services:\n  app:\n    ports:\n      - "${APP_PORT:-8081}:80"\n', "APP_PORT=9090\n")
        self.assertEqual(ports.project_ports(folder), [9090])

    def test_a_container_port_alone_publishes_nothing_fixed(self):
        folder = self.write("x", 'services:\n  db:\n    ports:\n      - "3306"\n')
        self.assertEqual(ports.project_ports(folder), [])


class Listening(unittest.TestCase):
    def test_from_ss_and_docker(self):
        with mock.patch.object(ports, "_run", return_value=mock.Mock(stdout=SS)):
            self.assertEqual(ports.listening(), {53: "", 80: "", 11434: "ollama", 5173: "node", 41397: "code"})
        with mock.patch.object(ports.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(ports.session, "docker_running", return_value=True), \
                mock.patch.object(ports, "_run", return_value=mock.Mock(stdout=DOCKER_PS)):
            self.assertEqual(ports.published(), {8000: "shop_app", 3309: "shared-db_mysql"})

    def test_apache_from_its_configuration(self):
        conf = Path(tempfile.mkdtemp())
        (conf / "sites-enabled").mkdir()
        (conf / "ports.conf").write_text("Listen 80\n<IfModule ssl_module>\n\tListen 443\n</IfModule>\n# Listen 8888\n")
        (conf / "sites-enabled" / "dev.conf").write_text("Listen 127.0.0.1:8090\n<VirtualHost *:8090>\n</VirtualHost>\n")
        with mock.patch.object(ports, "APACHE_CONFIG", conf):
            self.assertEqual(ports.apache_ports(), [80, 443, 8090])


class Clashes(unittest.TestCase):
    FOUND = {"shop-dev": "/w/shop-dev", "staging": "/w/staging", "infra": "/w/infra"}
    PORTS = {"/w/shop-dev": [8000], "/w/staging": [8080, 8000], "/w/infra": [3309]}
    CONTAINERS = {"/w/shop-dev": {"shop_app"}, "/w/staging": {"staging_app"}, "/w/infra": {"shared-db_mysql"}}

    def setUp(self):
        for name, value in (("project_ports", lambda folder: self.PORTS[folder]), ("apache_ports", lambda: [80, 8080]),
                            ("listening", lambda: {53: "", 80: "", 8080: "", 5173: "node", 41397: "code"}),
                            ("published", lambda: {8000: "shop_app"}), ("compose_folders", lambda: {}),
                            ("_installed_services", lambda: {"mysql.service"}),
                            ("_active", lambda unit: unit == "apache2.service")):
            patcher = mock.patch.object(ports, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("containers_of", lambda folder: self.CONTAINERS[folder]),
                            ("project_for", lambda word, found: {"shop_app": "shop-dev"}.get(word))):
            patcher = mock.patch.object(ports.compose, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_everything_that_wants_a_port(self):
        rows = ports.claims(self.FOUND)
        mine = {(row["port"], row["who"], row["running"]) for row in rows if not row.get("other")}
        self.assertIn((8000, "shop-dev", True), mine)
        self.assertIn((8000, "staging", False), mine)
        self.assertIn((80, "apache2", True), mine)
        self.assertIn((3306, "mysql", False), mine)
        others = {row["port"]: row["who"] for row in rows if row.get("other")}
        self.assertEqual(others, {53: "DNS (systemd-resolved)", 5173: "node"})  # not the editor's own high port

    def test_what_would_clash(self):
        self.assertEqual(ports.clashes(ports.claims(self.FOUND)), {8000: ["shop-dev", "staging"], 8080: ["apache2", "staging"]})

    def test_whats_in_the_way_of_starting_a_project(self):
        self.assertEqual(ports.busy_for("staging", self.FOUND), [(8080, "apache2"), (8000, "shop-dev")])
        self.assertEqual(ports.busy_for("shop-dev", self.FOUND), [])  # its own container holds its port
        self.assertEqual(ports.busy_for("infra", self.FOUND), [])


class NotAClash(unittest.TestCase):
    def test_two_services_that_are_both_off(self):
        rows = [{"port": 80, "who": "apache2", "running": False}, {"port": 80, "who": "nginx", "running": False}]
        self.assertEqual(ports.clashes(rows), {})

    def test_one_of_them_on_is(self):
        rows = [{"port": 80, "who": "apache2", "running": True}, {"port": 80, "who": "nginx", "running": False}]
        self.assertEqual(ports.clashes(rows), {80: ["apache2", "nginx"]})


class UnnamedContainers(unittest.TestCase):
    """Most Compose files don't name their containers: only Compose's label says whose one is."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "docker-compose.yml").write_text('services:\n  web:\n    image: nginx\n    ports:\n      - "8080:80"\n')
        self.found = {"shop": str(self.folder), "blog": "/w/blog"}
        for name, value in (("listening", {8080: ""}), ("published", {8080: "shop-web-1"}), ("_installed_services", set()),
                            ("apache_ports", []), ("project_ports", None)):
            if name == "project_ports":
                patcher = mock.patch.object(ports, name, side_effect=lambda folder: [8080] if folder == str(self.folder) else [])
            else:
                patcher = mock.patch.object(ports, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(ports.compose, "project_for", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def label(self, folder):
        return mock.patch.object(ports, "compose_folders", return_value={"shop-web-1": folder})

    def test_a_running_project_isnt_in_its_own_way(self):
        with self.label(str(self.folder)):
            self.assertEqual(ports.busy_for("shop", self.found), [])

    def test_a_running_project_shows_as_running(self):
        with self.label(str(self.folder)), mock.patch.object(ports, "_active", return_value=False):
            self.assertEqual([row["running"] for row in ports.claims(self.found) if row["who"] == "shop"], [True])

    def test_another_projects_container_is_in_the_way(self):
        with self.label("/w/blog"):
            self.assertEqual(ports.busy_for("shop", self.found), [(8080, "blog")])

    def test_a_stranger_goes_by_its_container_name(self):
        with self.label("/w/not-a-project"):
            self.assertEqual(ports.busy_for("shop", self.found), [(8080, "shop-web-1")])

    def test_the_label_comes_from_docker_ps(self):
        output = "shop-web-1\t/w/shop\nplain-container\t\n"
        with mock.patch.object(ports.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(ports.session, "docker_running", return_value=True), \
                mock.patch.object(ports, "_run", return_value=mock.Mock(stdout=output)) as run:
            self.assertEqual(ports.compose_folders(), {"shop-web-1": "/w/shop"})
        self.assertIn('{{.Label "com.docker.compose.project.working_dir"}}', run.call_args.args[0][-1])

    def test_with_docker_down_nothing_asks_it(self):
        with mock.patch.object(ports.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(ports.session, "docker_running", return_value=False), \
                mock.patch.object(ports, "_run") as run:
            self.assertEqual(ports.compose_folders(), {})
        run.assert_not_called()


class Asking(unittest.TestCase):
    def action(self, text):
        result = commands.quick(text, [], servers.Watch(), {})
        return result["actions"][0] if result else None

    def test_in_plain_words(self):
        self.assertEqual(self.action("cek port"), {"type": "ports"})
        self.assertEqual(self.action("port apa aja yang kepake"), {"type": "ports"})
        self.assertEqual(self.action("port bentrok gak"), {"type": "ports"})
        self.assertEqual(self.action("port 8000 dipake siapa"), {"type": "ports", "port": 8000})
        self.assertEqual(self.action("apache port berapa"), {"type": "ports", "about": "apache"})

    def test_topic_question_goes_to_ai(self):
        self.assertIsNone(self.action("apa itu port"))
        self.assertIsNone(self.action("what is a port"))
        self.assertIsNone(self.action("cara cek port"))

    def test_short_questions_about_clashes(self):
        for text in ("ada yang bentrok gak", "apa yang bentrok", "ada yg clash", "bentrok gak?"):
            self.assertEqual(self.action(text), {"type": "ports"}, text)

    def test_other_things_that_clash_or_are_called_ports(self):
        for text in ("jadwal gua bentrok", "jadwal meeting bentrok sama kuliah", "main clash of clans", "buka clash royale",
                     "usb port laptop rusak", "port forwarding di router gua kenapa", "beli hub usb 4 port"):
            action = self.action(text)
            self.assertNotEqual((action or {}).get("type"), "ports", text)

    def test_a_port_in_words(self):
        rows = [{"port": 8000, "who": "shop-dev", "running": True}, {"port": 8000, "who": "staging", "running": False},
                {"port": 80, "who": "apache2", "running": False}, {"port": 443, "who": "apache2", "running": False},
                {"port": 53, "who": "DNS (systemd-resolved)", "running": True, "other": True}]
        with mock.patch.object(commands.compose, "projects", return_value={}), \
                mock.patch.object(commands.ports, "claims", return_value=rows):
            self.assertEqual(commands.ports_report("id", {}, port=8000).splitlines(),
                             [":8000 shop-dev · jalan", ":8000 staging · mati", "⚠ Bentrok: :8000 diminta shop-dev dan staging"])
            self.assertEqual(commands.ports_report("id", {}, port=9000), ":9000 kosong, aman dipakai")
            self.assertEqual(commands.ports_report("id", {}, about="apache"), ":80, :443  apache2 · mati")
            self.assertEqual(commands.ports_report("en", {}).splitlines()[-2:],
                             ["Others: :53 DNS (systemd-resolved)", "⚠ Clash: :8000 is wanted by shop-dev and staging"])


class States(unittest.TestCase):
    def test_a_project_partly_running_runs(self):
        listed = [{"Name": "infra", "Status": "exited(1), running(1)", "ConfigFiles": "/w/infra/docker-compose.yml"},
                  {"Name": "staging", "Status": "exited(1)", "ConfigFiles": "/w/staging/docker-compose.yml"}]
        with mock.patch.object(compose, "_docker_ls", return_value=listed):
            self.assertEqual(compose.states({"infra": "/w/infra", "staging": "/w/staging"}), {"infra": "running", "staging": "stopped"})


if __name__ == "__main__":
    unittest.main()
