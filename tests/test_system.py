"""Memory, disk space and the caches safe to clear. Run with: python3 -m unittest discover tests"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import commands  # noqa: E402
import system  # noqa: E402

GB = 1000**3


class Sizes(unittest.TestCase):
    def test_sizes_read_like_files_and_docker_say_them(self):
        self.assertEqual(system.human(1_600_000_000), "1.6 GB")
        self.assertEqual(system.human(3 * GB), "3 GB")
        self.assertEqual(system.human(13_600_000), "13.6 MB")
        self.assertEqual(system.human(512), "512 B")


class Memory(unittest.TestCase):
    MEMINFO = "MemTotal: 20000000 kB\nMemFree: 1000 kB\nMemAvailable: 1500000 kB\nSwapTotal: 4000000 kB\nSwapFree: 3000000 kB\n"

    def test_from_the_kernel(self):
        with mock.patch.object(system.Path, "read_text", return_value=self.MEMINFO):
            info = system.memory()
        self.assertEqual(info, {"total": 20000000 * 1024, "available": 1500000 * 1024,
                                "swap_total": 4000000 * 1024, "swap_used": 1000000 * 1024})
        self.assertTrue(system.memory_low(info))
        self.assertFalse(system.memory_low({**info, "available": info["total"] // 2}))

    def test_an_apps_processes_count_together(self):
        ps = mock.Mock(stdout="1000000 brave\n500000 brave\n2400000 llama-server\n10 bash\nnot a line\n")
        with mock.patch.object(system, "run", return_value=ps):
            apps = system.heaviest_apps()
        self.assertEqual(apps, [("Ollama", 2400000 * 1024), ("brave", 1500000 * 1024), ("bash", 10 * 1024)])


class Disks(unittest.TestCase):
    def test_running_out_is_little_left_or_a_small_share(self):
        disks = [{"path": "/", "total": 160 * GB, "free": 109 * GB},
                 {"path": "/home", "total": 88 * GB, "free": 4 * GB},
                 {"path": "/data", "total": 4000 * GB, "free": 150 * GB}]
        with mock.patch.object(system, "disks", return_value=disks):
            self.assertEqual([disk["path"] for disk in system.low_disks()], ["/home", "/data"])


class Clearing(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.npm = self.home / "npm"
        (self.npm / "index").mkdir(parents=True)
        (self.npm / "index" / "blob").write_bytes(b"x" * 2_000_000)
        (self.npm / "small").write_bytes(b"x")
        self.mine = self.home / "Documents"
        self.mine.mkdir()
        (self.mine / "thesis.docx").write_bytes(b"x" * 2_000_000)
        dirs = [("npm", "npm", self.npm), ("pip", "pip", self.home / "pip"), ("link", "link", self.home / "link")]
        (self.home / "link").symlink_to(self.mine)
        for patcher in (mock.patch.object(system, "CACHE_DIRS", dirs), mock.patch.object(system, "_docker_build_cache", return_value=0)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_only_caches_worth_it_are_offered(self):
        found = system.clearable()
        self.assertEqual([item["id"] for item in found], ["npm"])  # pip has none, and a link isn't a cache
        self.assertGreaterEqual(found[0]["size"], 2_000_000)

    def test_clearing_empties_the_cache_and_keeps_its_folder(self):
        freed = system.clear(["npm", "link"])
        self.assertGreaterEqual(freed, 2_000_000)
        self.assertTrue(self.npm.is_dir())
        self.assertEqual(list(self.npm.iterdir()), [])
        self.assertTrue((self.mine / "thesis.docx").exists())  # a link out of the cache is never followed


class DockerBuildCache(unittest.TestCase):
    def test_what_docker_says_can_go(self):
        done = mock.Mock(returncode=0, stdout="ID\tRECLAIMABLE\nabc\ttrue\nShared:\t\t0B\nPrivate:\t0B\nReclaimable:\t1.592GB\nTotal:\t\t1.592GB\n")
        with mock.patch.object(system.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(system, "run", return_value=done):
            self.assertEqual(system._docker_build_cache(), 1_592_000_000)

    def test_no_docker(self):
        with mock.patch.object(system.shutil, "which", return_value=None):
            self.assertEqual(system._docker_build_cache(), 0)


class Reports(unittest.TestCase):
    def test_what_isnt_committed_or_pushed(self):
        states = {"/w/toko": (3, 1), "/w/gudang": (0, 0), "/w/blog": None, "/w/kasir": (0, 2)}
        with mock.patch.object(commands.servers, "git_state", side_effect=states.get), \
                mock.patch.object(commands.servers, "_git", return_value="main\n"):
            text = commands.git_report("id", {"toko": "/w/toko", "gudang": "/w/gudang", "blog": "/w/blog", "kasir": "/w/kasir"})
        self.assertEqual(text.splitlines(), ["toko (main): 3 file belum di-commit, 1 commit belum di-push",
                                             "kasir (main): 2 commit belum di-push"])

    def test_all_clean(self):
        with mock.patch.object(commands.servers, "git_state", return_value=(0, 0)):
            self.assertEqual(commands.git_report("en", {"toko": "/w/toko"}), "Every project is committed and pushed.")

    def test_memory_in_words(self):
        info = {"total": 20 * GB, "available": 12 * GB, "swap_total": 4 * GB, "swap_used": 5_500_000}
        with mock.patch.object(commands.system, "memory", return_value=info), \
                mock.patch.object(commands.system, "heaviest_apps", return_value=[("brave", 3 * GB), ("Ollama", 2_400_000_000)]):
            self.assertEqual(commands.memory_report("id"),
                             "RAM kepakai 8 GB dari 20 GB (40%), swap 5.5 MB. Paling berat: brave 3 GB, Ollama 2.4 GB")


class Asking(unittest.TestCase):
    def action(self, text):
        result = commands.quick(text, [], commands.servers.Watch(), {"gudang": "/w/gudang"})
        return result["actions"][0] if result else None

    def test_plain_words_need_no_ai(self):
        for text, wanted in (("cek ram", {"type": "system", "what": "memory"}),
                             ("ram gua berapa", {"type": "system", "what": "memory"}),
                             ("aplikasi apa yang berat", {"type": "system", "what": "memory"}),
                             ("storage gua penuh gak", {"type": "system", "what": "storage"}),
                             ("cek disk", {"type": "system", "what": "storage"}),
                             ("bersihin storage", {"type": "system", "what": "clear"}),
                             ("bersih-bersih", {"type": "system", "what": "clear"}),
                             ("hapus cache", {"type": "system", "what": "clear"}),
                             ("project mana yang belum di-push", {"type": "git_report"}),
                             ("cek project gua", {"type": "git_report"}),
                             ("cek git", {"type": "git_report"})):
            self.assertEqual(self.action(text), wanted, text)

    def test_what_it_doesnt_take_over(self):
        self.assertIsNone(self.action("hapus project gudang"))  # the model's, and it says no
        self.assertEqual(self.action("matiin semua server")["type"], "server")
        self.assertEqual(self.action("buka youtube")["type"], "open_url")


if __name__ == "__main__":
    unittest.main()
