"""Docker for the command bar: images, and Compose projects by name. Run with: python3 -m unittest discover tests"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import commands  # noqa: E402
import compose  # noqa: E402
import servers  # noqa: E402


class Projects(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for folder in ("shop-dev", "infra", "team", "team/infra", "notes", ".hidden/app"):
            (self.root / folder).mkdir(parents=True)
        for folder, name in (("shop-dev", "docker-compose.yml"), ("infra", "compose.yaml"), ("team", "docker-compose.yml"),
                             ("team/infra", "docker-compose.yml"), (".hidden/app", "compose.yml")):
            (self.root / folder / name).write_text("services: {}\n")
        for patcher in (mock.patch.object(compose, "ROOTS", [self.root]), mock.patch.object(compose, "_docker_ls", return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_folders_with_a_compose_file(self):
        self.assertEqual(sorted(compose.projects()), ["infra", "shop-dev", "team", "team/infra"])

    def test_running_is_what_docker_says(self):
        found = compose.projects()
        running = [{"Name": "infra", "Status": "running(1)", "ConfigFiles": str(self.root / "infra" / "compose.yaml")},
                   {"Name": "shop_dev", "Status": "exited(1)", "ConfigFiles": str(self.root / "shop-dev" / "docker-compose.yml")}]
        with mock.patch.object(compose, "_docker_ls", return_value=running):
            states = compose.states(found)
        self.assertEqual((states["infra"], states["shop-dev"], states["team"]), ("running", "stopped", "stopped"))

    def test_by_name_in_the_command_bar(self):
        watch = servers.Watch()
        with mock.patch.object(commands, "dev_service_unit", return_value=None):
            act = lambda text: (commands.quick(text, [], watch, {}) or {"actions": [None]})["actions"][0]  # noqa: E731
            self.assertEqual(act("nyalain shop"), {"type": "compose", "op": "up", "project": "shop-dev"})
            self.assertEqual(act("matiin shop dong"), {"type": "compose", "op": "stop", "project": "shop-dev"})
            self.assertEqual(act("restart docker infra"), {"type": "compose", "op": "restart", "project": "infra"})
            self.assertEqual(act("nyalain team infra"), {"type": "compose", "op": "up", "project": "team/infra"})
            self.assertIsNone(act("nyalain notes"))  # no compose file there
            self.assertEqual(act("matiin semua server")["type"], "server")

    def test_starting_runs_compose_in_the_project(self):
        with mock.patch.object(compose.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr=" Container x  Started\n")) as run:
            self.assertEqual(compose.act("/w/infra", "up"), (True, "Container x  Started"))
        self.assertEqual(run.call_args.args[0], ["docker", "compose", "up", "-d"])
        self.assertEqual(run.call_args.kwargs["cwd"], "/w/infra")


class AProjectThatAlsoHasAComposeFile(unittest.TestCase):
    """Your own project, usually run with its servers, that happens to have a Compose file too."""

    def setUp(self):
        self.folder = str(Path(tempfile.mkdtemp()) / "shop-dev")
        Path(self.folder).mkdir()
        (Path(self.folder) / "docker-compose.yaml").write_text("services:\n  db:\n    container_name: shop-db\n")
        self.known = {"shop-dev": self.folder}
        for patcher in (mock.patch.object(compose, "ROOTS", []), mock.patch.object(compose, "_docker_ls", return_value=[]),
                        mock.patch.object(commands, "dev_service_unit", return_value=None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def act(self, text, docker):
        with mock.patch.object(commands.shutil, "which", return_value="/usr/bin/docker" if docker else None), \
                mock.patch.object(compose.shutil, "which", return_value="/usr/bin/docker" if docker else None), \
                mock.patch.object(compose.session, "docker_running", return_value=docker):
            return commands.quick(text, [], servers.Watch(), self.known)["actions"][0]

    def test_with_no_docker_stopping_means_its_servers(self):
        self.assertEqual(self.act("matiin shop", docker=False), {"type": "close_project", "project": "shop"})

    def test_with_no_docker_starting_means_setting_it_up(self):
        self.assertEqual(self.act("nyalain shop", docker=False), {"type": "setup_project", "project": "shop"})

    def test_with_docker_but_none_of_it_up_stopping_means_its_servers(self):
        self.assertEqual(self.act("matiin shop", docker=True), {"type": "close_project", "project": "shop"})

    def test_with_its_containers_up_stopping_goes_through_docker(self):
        up = [{"Name": "shop", "Status": "running(1)", "ConfigFiles": str(Path(self.folder) / "docker-compose.yaml")}]
        with mock.patch.object(compose, "_docker_ls", return_value=up):
            self.assertEqual(self.act("matiin shop", docker=True), {"type": "compose", "op": "stop", "project": "shop-dev"})

    def test_docker_of_your_own_counts_as_running(self):
        for user_side, up in ((0, True), (3, False)):
            with mock.patch.object(compose.session.subprocess, "run",
                                   side_effect=lambda args, **_: mock.Mock(returncode=user_side if "--user" in args else 3)):
                self.assertEqual(compose.session.docker_running(), up)

    def test_with_docker_starting_still_goes_through_it(self):
        self.assertEqual(self.act("nyalain shop", docker=True), {"type": "compose", "op": "up", "project": "shop-dev"})


class Needs(unittest.TestCase):
    """shop and BLOG reach their database in infra by its container's name: starting them starts infra first."""

    INFRA = "services:\n  mysql57:\n    image: mysql:5.7\n    container_name: shared-db_mysql\n" \
            "  postgres:\n    image: postgres:15-alpine\n    container_name: shared_postgres\n"
    SHOP = "name: shop_dev\nservices:\n  app:\n    image: shop-app:latest\n    container_name: shop_app\n"
    BLOG = "services:\n  app:\n    build: .\n    container_name: blog_app\n    environment:\n      DB_HOST: ${DB_HOST:-shared-db_mysql}\n"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for folder, compose_text, env in (("infra", self.INFRA, ""), ("team/infra", self.INFRA, ""),
                                          ("shop-dev", self.SHOP, "DB_HOST=shared-db_mysql\nDB_NAME=shop\n"),
                                          ("BLOG-SITE", self.BLOG, ""), ("notes", "services: {}\n", "")):
            (self.root / folder).mkdir(parents=True)
            (self.root / folder / "docker-compose.yml").write_text(compose_text)
            if env:
                (self.root / folder / ".env").write_text(env)
        for patcher in (mock.patch.object(compose, "ROOTS", [self.root]), mock.patch.object(compose, "_docker_ls", return_value=[]),
                        mock.patch.object(compose.session, "docker_running", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.found = compose.projects()

    def test_who_needs_whom(self):
        self.assertEqual(compose.needs("shop-dev", self.found), ["infra"])  # from .env
        self.assertEqual(compose.needs("BLOG-SITE", self.found), ["infra"])   # from the Compose file's default
        self.assertEqual(compose.needs("infra", self.found), [])
        self.assertEqual(compose.needs("notes", self.found), [])
        (self.root / "notes" / ".env").write_text("CACHE_HOST=old-shared-db_mysql\n")
        self.assertEqual(compose.needs("notes", self.found), [])  # another name that only ends the same
        self.assertEqual(sorted(compose.dependents("infra", self.found)), ["BLOG-SITE", "shop-dev"])

    def needs_with(self, compose_text="", env=""):
        (self.root / "notes" / "docker-compose.yml").write_text(f"services:\n  app:\n    build: .\n{compose_text}")
        (self.root / "notes" / ".env").write_text(env)
        return compose.needs("notes", self.found)

    def test_saying_how_it_connects_counts(self):
        for compose_text, env in (("", "DB_HOST=shared-db_mysql\n"), ("", "export DB_HOST=shared-db_mysql\n"), ("", 'db_host="shared-db_mysql"\n'),
                                  ("", "DATABASE_URL=postgres://me:pw@shared_postgres:5432/notes\n"),
                                  ("", "KAFKA_BOOTSTRAP_SERVERS=shared-db_mysql:9092\n"),
                                  ("    environment:\n      - DB_HOST=shared-db_mysql\n", ""),
                                  ('    environment:\n      "DB_HOST": "shared-db_mysql"\n', ""),
                                  ("    external_links:\n      - shared-db_mysql:db\n", ""),
                                  ("    links:\n\n      - shared-db_mysql\n", ""),
                                  ("    external_links: [shared-db_mysql]\n", ""),
                                  ("    network_mode: container:shared-db_mysql\n", ""),
                                  ("    environment: {DB_HOST: shared-db_mysql}\n", ""),
                                  ("    environment: {OTHER: 1, DB_HOST: shared-db_mysql, MORE: 2}\n", ""),
                                  ("", "ELASTICSEARCH_NODE=http://shared-db_mysql:9200\n"), ("", "AMQP=amqp://me:pw@shared-db_mysql:5672/vhost\n"),
                                  ("", "MONGO=mongodb://other:27017,shared-db_mysql:27017/db\n"), ("", "JDBC=jdbc:mysql://shared-db_mysql:3306/x\n"),
                                  ("    command: wait-for shared-db_mysql:3306 -- run\n", ""),
                                  ("    entrypoint: dockerize -wait tcp://shared-db_mysql:3306\n", ""),
                                  ('    healthcheck:\n      test: ["CMD", "mysqladmin", "ping", "-h", "shared-db_mysql"]\n', ""),
                                  ("    command:\n      - wait-for\n      - shared-db_mysql:3306\n", ""),
                                  ("    command: php artisan queue --host=shared-db_mysql\n", "")):
            self.assertEqual(self.needs_with(compose_text, env), ["infra"], compose_text + env)

    def test_the_same_letters_elsewhere_dont(self):
        for compose_text, env in (("    image: shared-db_mysql:7\n", ""), ("    volumes:\n      - ./shared-db_mysql:/data\n", ""),
                                  ("    ports:\n      - 3306:3306 # shared-db_mysql\n", ""),
                                  ("    # DB_HOST: shared-db_mysql\n", ""), ("    hostname: shared-db_mysql\n", ""),
                                  ("    labels:\n      - owner=shared-db_mysql\n", ""),
                                  ("    external_links:\n      - other\n    volumes:\n      - shared-db_mysql:/data\n", ""),
                                  ("", "DB_CONNECTION=shared-db_mysql\n"), ("", "DB_DATABASE=shared_postgres\n"), ("", "# DB_HOST=shared-db_mysql\n"),
                                  ("", "DB_HOST=db # was shared-db_mysql\n"), ("", "MAIL_FROM_ADDRESS=shared_postgres@example.com\n"),
                                  ("", "# see http://shared-db_mysql:3306\n"), ("    build: . # from http://shared-db_mysql:1\n", ""),
                                  ("    network_mode: service:shared-db_mysql\n", ""), ("", "SOCKET_HOST=./shared-db_mysql\n"),
                                  ("    image: registry-host:5000/shared-db_mysql:7\n", ""), ("    command: shared-db_mysql --port 1\n", ""),
                                  ("    command:\n      - shared-db_mysql\n", ""), ("    build: https://example.com/shared-db_mysql.git\n", "")):
            self.assertEqual(self.needs_with(compose_text, env), [], compose_text + env)

    def test_a_url_is_about_its_host(self):
        (self.root / "infra" / "docker-compose.yml").write_text(
            "services:\n  a:\n    container_name: redis\n  b:\n    container_name: postgres\n  c:\n    container_name: app\n")
        found = compose.projects()
        for env, wanted in (("REDIS_URL=redis://localhost:6379\n", {}), ("CACHE_URL=redis://127.0.0.1:6379/0\n", {}),
                            ("DATABASE_URL=postgres://me:pw@localhost:5432/app\n", {}), ("DATABASE_URL=postgres://postgres@localhost/x\n", {}),
                            ("REDIS_URL=redis://redis:6379\n", {"infra": ["redis"]}),
                            ("DATABASE_URL=postgres://me:pw@postgres:5432/app\n", {"infra": ["postgres"]})):
            (self.root / "notes" / ".env").write_text(env)
            self.assertEqual(compose.needed("notes", found), wanted, env)

    def test_the_one_on_top_owns_a_container_two_define(self):
        self.assertEqual(compose._owner("shared-db_mysql", ["team/infra", "infra"], self.found), "infra")

    def test_starting_starts_only_the_containers_it_needs(self):
        calls = []

        def run(args, **_kwargs):
            calls.append(args)
            return mock.Mock(returncode=0, stdout="")
        with mock.patch.object(compose, "_running_containers", return_value={"shared_postgres"}), \
                mock.patch.object(compose, "_run", side_effect=run), \
                mock.patch.object(compose, "act", return_value=(True, "")) as act:
            self.assertEqual(compose.start("shop-dev", self.found), (True, "", ["infra"]))
        self.assertEqual(calls, [["docker", "start", "shared-db_mysql"]])  # not infra's PostgreSQL
        self.assertEqual(act.call_args.args, (self.found["shop-dev"], "up"))

    def test_what_runs_already_isnt_started_again(self):
        with mock.patch.object(compose, "_running_containers", return_value={"shared-db_mysql"}), \
                mock.patch.object(compose, "_run") as run, \
                mock.patch.object(compose, "act", return_value=(True, "")) as act:
            self.assertEqual(compose.start("BLOG-SITE", self.found, "restart"), (True, "", []))
        run.assert_not_called()
        self.assertEqual(act.call_args.args[1], "restart")

    def test_a_container_never_created_comes_up_with_its_project(self):
        with mock.patch.object(compose, "_running_containers", return_value=set()), \
                mock.patch.object(compose, "_run", return_value=mock.Mock(returncode=1, stdout="")), \
                mock.patch.object(compose, "act", side_effect=[(False, "port 3309 in use")]) as act:
            self.assertEqual(compose.start("shop-dev", self.found), (False, "port 3309 in use", [], "infra"))
        self.assertEqual(act.call_args.args, (self.found["infra"], "up"))

    def test_by_container_or_image(self):
        self.assertEqual(compose.project_for("shared-db_mysql", self.found), "infra")
        self.assertEqual(compose.project_for("shop-app", self.found), "shop-dev")
        self.assertEqual(compose.project_for("shop-app:latest", self.found), "shop-dev")
        self.assertEqual(compose.project_for("mysql:5.7", self.found), "infra")
        self.assertIsNone(compose.project_for("composer:2", self.found))


class Services(unittest.TestCase):
    UNITS = "apache2.service enabled enabled\nmysql.service enabled enabled\nphp8.1-fpm.service disabled enabled\nphp8.3-fpm.service enabled enabled\n"

    def test_a_word_means_the_installed_service(self):
        with mock.patch.object(commands.subprocess, "run", return_value=mock.Mock(stdout=self.UNITS)):
            self.assertEqual(commands.dev_service_unit("mysql"), "mysql.service")
            self.assertEqual(commands.dev_service_unit("apache"), "apache2.service")
            self.assertEqual(commands.dev_service_unit("php"), "php8.3-fpm.service")  # the newest
            self.assertIsNone(commands.dev_service_unit("redis"))  # not installed
            self.assertIsNone(commands.dev_service_unit("spotify"))

    def test_services_left_off_until_wanted(self):
        units = ("cloudflared.service enabled enabled\nstrongswan-starter.service disabled enabled\n"
                 "me.proton.vpn.split_tunneling.service disabled enabled\ngnome-remote-desktop.service disabled enabled\n"
                 "qemu-kvm.service disabled enabled\n")
        with mock.patch.object(commands.subprocess, "run", return_value=mock.Mock(stdout=units)):
            for word, unit, label in (("tunnel", "cloudflared.service", "Cloudflare Tunnel"),
                                      ("vpn", "strongswan-starter.service", "VPN strongSwan"),
                                      ("proton vpn", "me.proton.vpn.split_tunneling.service", "Proton VPN"),
                                      ("remote desktop", "gnome-remote-desktop.service", "Remote Desktop"),
                                      ("kvm", "qemu-kvm.service", "KVM")):
                self.assertEqual(commands.dev_service_unit(word), unit, word)
                self.assertEqual(commands.service_label(unit), label)
        self.assertEqual(commands.service_label("mysql.service"), "mysql")

    def test_a_service_word_wins_over_a_project_containing_it(self):
        with mock.patch.object(commands.compose, "projects", return_value={"vpn-dashboard": "/w/vpn-dashboard"}), \
                mock.patch.object(commands, "dev_service_unit", side_effect=lambda name: "strongswan-starter.service" if name == "vpn" else None):
            act = commands.quick("nyalain vpn", [], servers.Watch(), {})["actions"][0]
            self.assertEqual(act, {"type": "dev_service", "op": "start", "unit": "strongswan-starter.service"})
            act = commands.quick("nyalain vpn dashboard", [], servers.Watch(), {})["actions"][0]
            self.assertEqual(act["type"], "compose")

    def test_in_the_command_bar(self):
        with mock.patch.object(commands.compose, "projects", return_value={}), \
                mock.patch.object(commands.subprocess, "run", return_value=mock.Mock(stdout=self.UNITS)):
            result = commands.quick("nyalain mysql", [], servers.Watch(), {})
        self.assertEqual(result["actions"][0], {"type": "dev_service", "op": "start", "unit": "mysql.service"})


class ImageExists(unittest.TestCase):
    def test_never_asks_docker_while_it_is_down(self):
        """"start chrome" comes through here, and asking would start a Docker left to start on first use."""
        with (mock.patch.object(compose.shutil, "which", return_value="/usr/bin/docker"),
              mock.patch.object(compose.session, "docker_running", return_value=False),
              mock.patch.object(compose, "_run") as run):
            self.assertFalse(compose.image_exists("chrome"))
        run.assert_not_called()

    def test_asks_when_it_is_up(self):
        done = compose.subprocess.CompletedProcess([], 0, "", "")
        with (mock.patch.object(compose.shutil, "which", return_value="/usr/bin/docker"),
              mock.patch.object(compose.session, "docker_running", return_value=True),
              mock.patch.object(compose, "_run", return_value=done) as run):
            self.assertTrue(compose.image_exists("mysql"))
        self.assertEqual(run.call_args.args[0], ["docker", "image", "inspect", "mysql:latest"])


class Images(unittest.TestCase):
    def test_which_are_used(self):
        images = "\n".join(json.dumps(i) for i in ({"Repository": "shop-app", "Tag": "latest", "ID": "a1", "Size": "744MB"},
                                                   {"Repository": "mysql", "Tag": "5.7", "ID": "b2", "Size": "562MB"},
                                                   {"Repository": "node", "Tag": "22", "ID": "c3", "Size": "1.64GB"}))
        containers = "\n".join(json.dumps(c) for c in ({"Image": "shop-app:latest", "State": "running"},
                                                       {"Image": "mysql:5.7", "State": "exited"}))

        def run(args, **_kwargs):
            return mock.Mock(returncode=0, stdout=images if args[1] == "images" else containers)
        with mock.patch.object(compose.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(compose, "_run", side_effect=run), \
                mock.patch.object(commands.compose, "projects", return_value={}), \
                mock.patch.object(commands.compose, "project_for", side_effect=lambda image, found: "shop-dev" if image.startswith("shop") else None):
            self.assertEqual(commands.images_report("id").splitlines(),
                             ["shop-app:latest 744MB (jalan) → shop-dev", "mysql:5.7 562MB (ada container-nya)", "node:22 1.64GB (nggak dipakai)"])

    def test_a_containers_image_without_its_tag_is_the_latest(self):
        images = json.dumps({"Repository": "blog-site-app", "Tag": "latest", "ID": "a1", "Size": "744MB"})
        containers = json.dumps({"Image": "blog-site-app", "State": "running"})

        def run(args, **_kwargs):
            return mock.Mock(returncode=0, stdout=images if args[1] == "images" else containers)
        with mock.patch.object(compose.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(compose, "_run", side_effect=run):
            self.assertEqual(compose.images(), [{"name": "blog-site-app:latest", "size": "744MB", "used": True, "running": True}])

    def test_asking(self):
        for text in ("cek image docker", "docker images", "list image", "image docker apa aja"):
            self.assertEqual(commands.quick(text, [], servers.Watch(), {})["actions"][0], {"type": "docker_images"}, text)
        for text in ("docker apa aja yang bisa dinyalain", "project docker apa aja", "compose apa aja"):
            self.assertEqual(commands.quick(text, [], servers.Watch(), {})["actions"][0], {"type": "compose_list"}, text)


if __name__ == "__main__":
    unittest.main()
