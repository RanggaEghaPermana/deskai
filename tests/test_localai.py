"""Getting the local AI in the background, against a small web server here in place of GitHub and Ollama. Nothing is
really downloaded. Run with: python3 -m unittest discover tests"""

import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import urllib.error
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import localai  # noqa: E402

MODEL = "small:1b"
REAL_FILES = dict(localai.OLLAMA_FILES)
# What Ollama really says, in the error of a pull that failed.
NO_MODEL = "pull model manifest: file does not exist"  # measured
OFFLINE = ('pull model manifest: Get "https://registry.ollama.ai/v2/library/qwen3/manifests/1.7b": dial tcp: lookup '
           "registry.ollama.ai on 127.0.0.53:53: read udp 127.0.0.1:44053->127.0.0.53:53: read: connection refused")  # measured
DISK_FULL = "write /home/you/.ollama/models/blobs/sha256-aa-partial-0: no space left on device"
BAD_DOWNLOAD = "digest mismatch, file must be downloaded again: want sha256:aa, got sha256:bb"  # measured


def archive(*names):
    """A real .tar.zst with these files in it."""
    plain = io.BytesIO()
    with tarfile.open(fileobj=plain, mode="w") as tar:
        for name in names:
            data = b"#!/bin/sh\n" if name.startswith("bin/") else b"library"
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    return subprocess.run(["zstd", "-q", "-c"], input=plain.getvalue(), capture_output=True, check=True).stdout


class Server(http.server.BaseHTTPRequestHandler):
    """GitHub and Ollama both: files with Range, and the two calls DeskAI makes of Ollama."""

    def log_message(self, *_args):
        pass

    def send(self, code, body=b"", **headers):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        site = self.server.site
        site.asked.append((self.path, self.headers.get("Range")))
        if self.path == "/api/tags":
            if site.models is None:
                self.close_connection = True  # the nearest thing here to nothing listening
            else:
                self.send(200, json.dumps({"models": [{"name": name} for name in site.models]}).encode())
        elif self.path.endswith(".tar.zst"):
            data = site.file
            wanted = self.headers.get("Range")
            if wanted and site.ranges:
                start = int(wanted.removeprefix("bytes=").removesuffix("-"))
                self.send(206, data[start:], Content_Range=f"bytes {start}-{len(data) - 1}/{len(data)}")
            else:
                self.send(200, data)
        else:
            self.send(404)

    def do_POST(self):
        site = self.server.site
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        site.asked.append((self.path, body))
        if site.pull_code != 200:
            self.send(site.pull_code, json.dumps({"error": site.pull_error}).encode())
            return
        lines = [{"status": "pulling manifest"}, {"status": "pulling aa", "digest": "sha256:aa", "total": 100, "completed": 40},
                 {"status": "pulling aa", "digest": "sha256:aa", "total": 100, "completed": 100},
                 {"status": "pulling bb", "digest": "sha256:bb", "total": 50, "completed": 50}]
        lines += [{"error": site.pull_error}] if site.pull_error else [{"status": "success"}]
        self.send(200, "".join(json.dumps(line) + "\n" for line in lines).encode())
        if not site.pull_error:
            site.models = [*(site.models or []), body["model"]]

    def do_DELETE(self):
        site = self.server.site
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        site.asked.append((self.path, body))
        self.send(200)


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
        self.server.site = self
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.asked, self.models, self.ranges = [], [], True
        self.pull_error, self.pull_code = "", 200
        self.file = archive("bin/ollama", "lib/ollama/libggml.so")
        self.use_file(self.file)
        share = self.root / "share"
        for name, value in (("SHARE", share), ("FOLDER", share / "ollama"), ("BINARY", share / "ollama" / "bin" / "ollama"),
                            ("STATE_FILE", share / "local-ai.json"), ("WANTED", share / "local-ai.wanted"),
                            ("UNITS", self.root / "units"), ("MODES_FILE", self.root / "modes.json"), ("DEFAULTS_FILE", self.root / "defaults.json"),
                            ("HOME", self.root), ("OLLAMA_URL", f"{self.url}/"), ("NVIDIA_DEVICE", self.root / "nvidia0"),
                            ("DEFAULT_URL", self.url), ("MODEL", MODEL), ("SERVER_WAIT_S", 0.2)):
            self.patch(localai, name, value)
        self.patch(localai.platform, "machine", return_value="x86_64")
        self.patch(localai.shutil, "which", side_effect=lambda name: "/usr/bin/zstd" if name == "zstd" else None)
        self.systemctl = self.patch(localai, "_systemctl", return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.metered = self.patch(localai, "metered", return_value=False)
        (self.root / "modes.json").write_text(json.dumps({"llm": {"ollama_url": self.url, "ollama_model": MODEL}}))
        share.mkdir()
        localai.WANTED.touch()
        localai._saved.update(at=0.0, stage="")

    def patch(self, *args, **kwargs):
        patcher = mock.patch.object(*args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def use_file(self, data, sha256=None):
        files = {"x86_64": ("o.tar.zst", sha256 or hashlib.sha256(data).hexdigest(), len(data))}
        self.patch(localai, "OLLAMA_FILES", files)

    def nothing_answers(self):
        """No server here, until starting DeskAI's own brings one up."""
        self.models = None

        def start(*args):
            if args[:2] == ("start", "--no-block") and args[2] == localai.SERVER:
                self.models = []
            return subprocess.CompletedProcess([], 0, "", "")
        self.systemctl.side_effect = start

    def get(self):
        with redirect_stderr(io.StringIO()):
            return localai.get()


class GettingEverything(Base):
    def test_ollama_and_the_model(self):
        self.nothing_answers()
        self.assertEqual(self.get(), 0)
        self.assertTrue(localai.BINARY.exists())
        self.assertTrue(os.access(localai.BINARY, os.X_OK))
        self.assertTrue((localai.FOLDER / "lib" / "ollama" / "libggml.so").exists())
        self.assertEqual(sorted(os.listdir(localai.FOLDER)), ["bin", "lib"], "the archive and what it was unpacked in are gone")
        self.assertIn(("/api/pull", {"model": MODEL, "stream": True}), self.asked)
        state = localai.read()
        self.assertEqual((state["stage"], state["said"], state["ollama_ours"], state["model_ours"]), ("ready", False, True, MODEL))
        self.assertFalse(localai.WANTED.exists(), "nothing's left to start at the next login")

    def test_its_server_only_listens_here_and_keeps_to_its_folder(self):
        self.nothing_answers()
        self.get()
        unit = (localai.UNITS / localai.SERVER).read_text()
        self.assertIn(f"ExecStart={localai.BINARY} serve", unit)
        self.assertIn(f"Environment=HOME={localai.FOLDER}", unit)
        self.assertIn("OLLAMA_HOST=127.0.0.1:11434", unit)
        self.assertIn("OLLAMA_NO_CLOUD=1", unit)
        self.systemctl.assert_any_call("enable", localai.SERVER)

    def test_only_the_model_if_ollama_is_up(self):
        self.models = ["other:1b"]
        self.assertEqual(self.get(), 0)
        self.assertFalse(localai.FOLDER.exists())
        state = localai.read()
        self.assertEqual((state["stage"], state.get("ollama_ours"), state["model_ours"], state["model_url"]), ("ready", None, MODEL, self.url))

    def test_nothing_if_its_all_here(self):
        self.models = [MODEL]
        self.assertEqual(self.get(), 0)
        self.assertEqual([path for path, _ in self.asked], ["/api/tags"])
        self.assertIsNone(localai.read().get("model_ours"), "removing DeskAI leaves a model it didn't download")

    def test_a_name_with_latest_counts_as_the_same_model(self):
        self.models = [f"{MODEL}:latest"]
        self.assertEqual(self.get(), 0)
        self.assertEqual([path for path, _ in self.asked], ["/api/tags"])

    def test_your_own_ollama_is_used_not_replaced(self):
        mine = self.root / "bin" / "ollama"
        mine.parent.mkdir()
        mine.touch(mode=0o755)
        self.patch(localai.shutil, "which", side_effect=lambda name: str(mine) if name == "ollama" else "/usr/bin/zstd")
        self.models = None

        def start(*args):
            if args[:2] == ("start", "--no-block") and args[2] == "ollama.service":
                self.models = []
            return subprocess.CompletedProcess([], 0, "", "")
        self.systemctl.side_effect = start
        self.assertEqual(self.get(), 0)
        self.assertFalse(localai.BINARY.exists())
        self.assertFalse((localai.UNITS / localai.SERVER).exists())
        self.assertIsNone(localai.read().get("ollama_ours"))

    def test_your_own_ollama_that_wont_start(self):
        mine = self.root / "ollama"
        mine.touch(mode=0o755)
        self.patch(localai.shutil, "which", side_effect=lambda name: str(mine) if name == "ollama" else "/usr/bin/zstd")
        self.models = None
        self.assertEqual(self.get(), 0)
        self.assertEqual(localai.read()["trouble"], "trouble_ollama_off")

    def test_a_server_somewhere_else_isnt_for_deskai_to_start(self):
        self.models = None
        self.patch(localai, "DEFAULT_URL", "http://127.0.0.1:1")
        self.assertEqual(self.get(), 0)
        self.assertEqual(localai.read()["trouble"], "trouble_ollama_off")
        self.assertFalse(localai.FOLDER.exists())

    def test_a_program_already_unpacked_isnt_downloaded_again(self):
        self.nothing_answers()
        localai.BINARY.parent.mkdir(parents=True)
        localai.BINARY.touch(mode=0o755)
        self.assertEqual(self.get(), 0)
        self.assertFalse(any(path.endswith(".tar.zst") for path, _ in self.asked))


class Downloading(Base):
    def setUp(self):
        super().setUp()
        self.nothing_answers()
        localai.FOLDER.mkdir()
        self.part = localai.FOLDER / "o.tar.zst"

    def test_it_carries_on_from_where_it_was(self):
        self.part.write_bytes(self.file[:20])
        self.assertEqual(self.get(), 0)
        self.assertIn(("/o.tar.zst", "bytes=20-"), self.asked)
        self.assertTrue(localai.BINARY.exists())

    def test_a_site_that_wont_carry_on_starts_over(self):
        self.ranges = False
        self.part.write_bytes(self.file[:20])
        self.assertEqual(self.get(), 0)
        self.assertTrue(localai.BINARY.exists())

    def test_all_of_it_there_already(self):
        self.part.write_bytes(self.file)
        self.assertEqual(self.get(), 0)
        self.assertFalse(any(path.endswith(".tar.zst") for path, _ in self.asked))
        self.assertTrue(localai.BINARY.exists())

    def test_a_file_that_isnt_the_right_one_is_never_unpacked(self):
        self.use_file(self.file, sha256="0" * 64)
        self.assertEqual(self.get(), 0)
        state = localai.read()
        self.assertEqual((state["stage"], state["trouble"], state["said"]), ("failed", "trouble_bad_download", False))
        self.assertFalse(localai.BINARY.exists())
        self.assertFalse(self.part.exists(), "it isn't carried on from next time")
        self.assertFalse(localai.WANTED.exists(), "it doesn't go on using up someone's connection by itself")

    def test_a_connection_that_closes_partway_keeps_what_it_got(self):
        self.use_file(self.file + b"more that never comes")
        self.assertEqual(self.get(), 75)
        self.assertEqual(self.part.read_bytes(), self.file)
        self.assertEqual(localai.read()["stage"], "waiting")
        self.assertTrue(localai.WANTED.exists())

    def test_it_says_how_far_it_is_of_all_of_it(self):
        with mock.patch.object(localai, "_progress") as progress:
            self.get()
        size = len(self.file)
        self.assertEqual(progress.call_args_list[0].args, ("ollama", size, size + localai.MODEL_BYTES))
        self.assertEqual(progress.call_args_list[-1].args, ("model", size + 150, size + 150))


class NvidiaLibraries(Base):
    def setUp(self):
        super().setUp()
        self.file = archive("bin/ollama", "lib/ollama/libggml.so", "lib/ollama/cuda_v12/libcublas.so", "lib/ollama/mlx_cuda_v13/libmlx.so",
                            "lib/ollama/vulkan/libggml-vulkan.so")
        self.use_file(self.file)
        self.nothing_answers()

    def test_left_out_without_the_gpu(self):
        self.get()
        self.assertEqual(sorted(path.name for path in (localai.FOLDER / "lib" / "ollama").iterdir()), ["libggml.so", "vulkan"])

    def test_unpacked_with_it(self):
        localai.NVIDIA_DEVICE.touch()
        self.get()
        self.assertEqual(sorted(path.name for path in (localai.FOLDER / "lib" / "ollama").iterdir()),
                         ["cuda_v12", "libggml.so", "mlx_cuda_v13", "vulkan"])


class WhenItGoesWrong(Base):
    def went(self, exit_code, stage, trouble):
        self.assertEqual(self.get(), exit_code)
        state = localai.read()
        self.assertEqual((state["stage"], state["trouble"]), (stage, trouble))

    def test_no_connection_tries_again_by_itself(self):
        self.models = []
        with mock.patch.object(localai, "_pull", side_effect=urllib.error.URLError("Temporary failure in name resolution")):
            self.went(75, "waiting", "trouble_no_connection")
        self.assertTrue(localai.WANTED.exists(), "there's still something to get")
        self.assertIsNone(localai.news(), "nothing to say about that on a card")

    def test_a_connection_dropped_halfway(self):
        self.models = []
        for error in (ConnectionResetError(104, "reset"), TimeoutError("timed out"), localai.http.client.IncompleteRead(b"")):
            with self.subTest(error), mock.patch.object(localai, "_pull", side_effect=error):
                self.went(75, "waiting", "trouble_no_connection")

    def http_error(self, code):
        error = urllib.error.HTTPError("u", code, "said", {}, io.BytesIO())
        self.addCleanup(error.close)
        return error

    def test_github_having_a_bad_moment(self):
        self.nothing_answers()
        with mock.patch.object(localai, "_download_ollama", side_effect=self.http_error(503)):
            self.went(75, "waiting", "trouble_no_connection")

    def test_a_file_thats_gone_from_github(self):
        self.nothing_answers()
        with mock.patch.object(localai, "_download_ollama", side_effect=self.http_error(404)):
            self.went(0, "failed", "trouble_download")
        self.assertFalse(localai.WANTED.exists())

    def test_a_full_disk_stops_it_and_says_so(self):
        self.nothing_answers()
        with mock.patch.object(localai, "_download_ollama", side_effect=OSError(28, "No space left on device")):
            self.went(0, "failed", "trouble_no_room")
        self.assertEqual(localai.news()["trouble"], "trouble_no_room")

    def test_a_full_disk_gets_back_what_it_took(self):
        self.nothing_answers()
        blobs = localai.FOLDER / ".ollama" / "models" / "blobs"
        blobs.mkdir(parents=True)
        (localai.FOLDER / "o.tar.zst").write_bytes(b"part")
        (localai.FOLDER / "unpacking").mkdir()
        (blobs / "sha256-aa-partial-0").write_bytes(b"part")
        (blobs / "sha256-bb").write_bytes(b"whole")
        with mock.patch.object(localai, "_download_ollama", side_effect=OSError(28, "No space left on device")):
            self.went(0, "failed", "trouble_no_room")
        self.assertEqual(sorted(os.listdir(blobs)), ["sha256-bb"])
        self.assertEqual(sorted(os.listdir(localai.FOLDER)), [".ollama"])

    def test_a_disk_so_full_it_cant_even_say_so_still_stops(self):
        with mock.patch.object(localai, "_get", side_effect=OSError(28, "No space left on device")), \
                mock.patch.object(localai, "_write", side_effect=OSError(28, "No space left on device")):
            self.assertEqual(self.get(), 0, "or it tries again every minute, for ever")
        self.assertFalse(localai.WANTED.exists())
        self.assertIsNone(localai.percent())
        self.assertEqual(os.listdir(localai.SHARE), [], "nothing left behind by trying")

    def test_writing_the_state_leaves_nothing_behind_when_it_fails(self):
        with mock.patch.object(localai.Path, "replace", side_effect=OSError(28, "No space left on device")), self.assertRaises(OSError):
            localai._write(stage="x")
        self.assertEqual([name for name in os.listdir(localai.SHARE) if name.startswith(".")], [])

    def test_a_folder_it_cant_write_in(self):
        self.nothing_answers()
        with mock.patch.object(localai, "_download_ollama", side_effect=PermissionError(13, "Permission denied")):
            self.went(0, "failed", "trouble_cant_write")

    def test_a_bug_still_ends_up_saying_something(self):
        with mock.patch.object(localai, "_get", side_effect=KeyError("bug")):
            self.went(0, "failed", "trouble_download")

    def test_what_ollama_says_while_pulling(self):
        for said, code, stage, trouble in ((NO_MODEL, 0, "failed", "trouble_model_missing"), (OFFLINE, 75, "waiting", "trouble_no_connection"),
                                           (DISK_FULL, 0, "failed", "trouble_no_room"), (BAD_DOWNLOAD, 0, "failed", "trouble_download")):
            with self.subTest(said):
                self.models, self.pull_error = [], said
                localai.WANTED.touch()
                self.went(code, stage, trouble)

    def test_or_answering_with_an_error_code(self):
        self.models, self.pull_error, self.pull_code = [], NO_MODEL, 500
        self.went(0, "failed", "trouble_model_missing")
        self.pull_error = OFFLINE
        localai.WANTED.touch()
        self.went(75, "waiting", "trouble_no_connection")

    def test_an_archive_that_wont_unpack(self):
        self.use_file(b"not an archive at all")
        self.file = b"not an archive at all"
        self.nothing_answers()
        self.went(0, "failed", "trouble_download")


class MeteredConnections(Base):
    def test_it_waits_for_another_connection(self):
        self.metered.return_value = True
        self.assertEqual(self.get(), 75)
        self.assertEqual(localai.read()["stage"], "metered")
        self.assertEqual(self.asked, [], "not one byte")
        self.assertTrue(localai.WANTED.exists())
        self.assertIsNone(localai.news(), "nothing to say about that on a card")

    def test_and_carries_on_on_one(self):
        self.metered.return_value = True
        self.get()
        self.metered.return_value = False
        self.models = []
        self.assertEqual(self.get(), 0)
        self.assertEqual(localai.read()["stage"], "ready")

    def test_unless_thats_the_connection_you_said_yes_on(self):
        self.metered.return_value = True
        self.models = []
        localai.want("python3", "/app")
        self.assertEqual(self.get(), 0)
        self.assertEqual(localai.read()["stage"], "ready")

    def test_saying_yes_on_another_connection_doesnt_count_for_this_one(self):
        self.models = []
        localai.want("python3", "/app")
        self.metered.return_value = True
        self.assertEqual(self.get(), 75)

    def test_trying_again_on_one_is_saying_yes(self):
        self.metered.return_value = True
        localai.try_again()
        self.models = []
        self.assertEqual(self.get(), 0)


class WhatItTakes(Base):
    def setUp(self):
        super().setUp()
        self.patch(localai, "_memory", return_value=8_000_000_000)
        self.patch(localai, "_free_room", return_value=100_000_000_000)
        self.models = None
        self.size = localai.OLLAMA_FILES["x86_64"][2]

    def test_everything(self):
        self.assertEqual(localai.offer(), {"download": localai.MODEL_BYTES + self.size, "why_not": ""})

    def test_only_the_model(self):
        self.models = []
        self.assertEqual(localai.offer(), {"download": localai.MODEL_BYTES, "why_not": ""})

    def test_your_own_ollama_only_needs_the_model(self):
        (self.root / "ollama").touch(mode=0o755)
        self.patch(localai.shutil, "which", side_effect=lambda name: str(self.root / "ollama") if name == "ollama" else "/usr/bin/zstd")
        self.assertEqual(localai.offer(), {"download": localai.MODEL_BYTES, "why_not": ""})

    def test_nothing(self):
        self.models = [MODEL]
        self.assertEqual(localai.offer(), {"download": 0, "why_not": ""})

    def test_a_computer_ollama_isnt_made_for(self):
        with mock.patch.object(localai.platform, "machine", return_value="riscv64"):
            self.assertEqual(localai.offer()["why_not"], "ai_not_this_computer")

    def test_no_zstd(self):
        self.patch(localai.shutil, "which", return_value=None)
        self.assertEqual(localai.offer()["why_not"], "ai_needs_zstd")

    def test_too_little_memory(self):
        with mock.patch.object(localai, "_memory", return_value=3_000_000_000):
            self.assertEqual(localai.offer()["why_not"], "ai_small_memory")

    def test_too_little_memory_doesnt_matter_if_its_all_here(self):
        self.models = [MODEL]
        with mock.patch.object(localai, "_memory", return_value=3_000_000_000):
            self.assertEqual(localai.offer()["why_not"], "")

    def test_too_little_room(self):
        with mock.patch.object(localai, "_free_room", return_value=2_000_000_000):
            offer = localai.offer()
        self.assertEqual(offer["why_not"], "ai_no_room")
        self.assertGreater(offer["download"], 0, "it says how much it would have taken")

    def test_nvidias_libraries_need_room_too(self):
        room = self.size + localai.MODEL_BYTES + localai.SPARE_BYTES + localai.UNPACKED_BYTES
        with mock.patch.object(localai, "_free_room", return_value=room):
            self.assertEqual(localai.offer()["why_not"], "")
            localai.NVIDIA_DEVICE.touch()
            self.assertEqual(localai.offer()["why_not"], "ai_no_room")

    def test_the_real_numbers_are_what_people_get_told(self):
        self.assertAlmostEqual((REAL_FILES["x86_64"][2] + localai.MODEL_BYTES) / 1e9, 2.9, delta=0.05)


class HowItsGoing(Base):
    def test_no_memory_figure_doesnt_stand_in_the_way(self):
        with mock.patch.object(localai.os, "sysconf", side_effect=OSError()):
            self.assertEqual(localai._memory(), localai.MEMORY_NEEDED)

    def test_nothing_going_on(self):
        localai.WANTED.unlink()
        self.assertIsNone(localai.percent())

    def test_just_starting(self):
        self.assertEqual(localai.percent(), 0)

    def test_halfway(self):
        localai._write(done=50, total=100)
        self.assertEqual(localai.percent(), 50)

    def test_it_never_says_all_done_while_its_going(self):
        localai._write(done=100, total=100)
        self.assertEqual(localai.percent(), 99)

    def test_a_state_that_isnt_one(self):
        localai.STATE_FILE.write_text("[1]")
        self.assertEqual(localai.read(), {})
        localai.STATE_FILE.write_text("{")
        self.assertEqual(localai.read(), {})
        self.assertEqual(localai.percent(), 0)

    def test_what_the_dock_has_to_say_it_says_once(self):
        localai._write(stage="ready", said=False)
        self.assertEqual(localai.news()["stage"], "ready")
        localai.told()
        self.assertIsNone(localai.news())

    def test_nothing_to_say_while_its_still_going(self):
        localai._write(stage="model", said=False)
        self.assertIsNone(localai.news())

    def test_settings_go_by_what_you_set(self):
        (self.root / "modes.json").write_text(json.dumps({"llm": {"ollama_url": "http://there:1/", "ollama_model": "mine:1b"}}))
        self.assertEqual(localai.settings(), ("http://there:1", "mine:1b"))

    def test_or_the_defaults_if_you_havent(self):
        (self.root / "modes.json").write_text(json.dumps({"modes": {}}))
        (self.root / "defaults.json").write_text(json.dumps({"llm": {"ollama_model": "usual:1b"}}))
        self.assertEqual(localai.settings(), (self.url, "usual:1b"))

    def test_with_neither_there(self):
        (self.root / "modes.json").unlink()
        self.assertEqual(localai.settings(), (self.url, MODEL))

    def test_the_real_defaults_are_the_same_model(self):
        real = Path(localai.__file__).resolve().parents[1] / "extension" / "deskai@ranggaeghapermana.github.io" / "modes.default.json"
        self.assertEqual(json.loads(real.read_text())["llm"].get("ollama_model", "qwen3:1.7b"), "qwen3:1.7b")


class StartingAndStopping(Base):
    def test_it_starts_a_service_that_comes_back_at_the_next_login(self):
        localai.WANTED.unlink()
        self.models = []
        self.assertTrue(localai.want("/usr/bin/python3", "/usr/share/deskai"))
        unit = (localai.UNITS / localai.GETTER).read_text()
        self.assertIn("ExecStart=/usr/bin/python3 /usr/share/deskai/daemon/localai.py", unit)
        self.assertIn(f"ConditionPathExists={localai.WANTED}", unit)
        self.assertIn("WantedBy=default.target", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertTrue(localai.WANTED.exists())
        self.assertEqual([call.args for call in self.systemctl.call_args_list],
                         [("daemon-reload",), ("enable", localai.GETTER), ("start", "--no-block", localai.GETTER)])

    def test_with_everything_here_theres_nothing_to_start(self):
        localai.WANTED.unlink()
        self.models = [MODEL]
        self.assertTrue(localai.want("python3", "/app"))
        self.assertFalse(localai.WANTED.exists())
        self.systemctl.assert_not_called()

    def test_a_service_that_wont_start_is_said(self):
        self.models = []
        self.systemctl.return_value = subprocess.CompletedProcess([], 1, "", "Failed")
        self.assertFalse(localai.want("python3", "/app"))

    def test_trying_again_keeps_what_it_got(self):
        localai.FOLDER.mkdir()
        (localai.FOLDER / "o.tar.zst").write_bytes(b"part")
        localai.WANTED.unlink()
        localai._write(stage="failed", trouble="trouble_no_room", said=True)
        localai.try_again()
        self.assertTrue(localai.WANTED.exists())
        self.assertEqual(localai.read()["trouble"], "")
        self.assertTrue((localai.FOLDER / "o.tar.zst").exists())
        self.systemctl.assert_called_once_with("start", "--no-block", localai.GETTER)

    def test_choosing_something_else_stops_it(self):
        localai.UNITS.mkdir()
        (localai.UNITS / localai.GETTER).touch()
        (localai.UNITS / localai.SERVER).touch()
        localai.stop()
        self.assertFalse(localai.WANTED.exists())
        self.assertEqual([call.args for call in self.systemctl.call_args_list], [("stop", localai.GETTER)])

    def test_with_no_ai_at_all_its_server_stops_too(self):
        localai.UNITS.mkdir()
        (localai.UNITS / localai.SERVER).touch()
        localai.stop(server=True)
        self.assertEqual([call.args for call in self.systemctl.call_args_list], [("disable", "--now", localai.SERVER)])

    def test_nothing_of_it_here_nothing_to_stop(self):
        localai.stop(server=True)
        self.systemctl.assert_not_called()


class Removing(Base):
    def everything(self, **state):
        localai.UNITS.mkdir(exist_ok=True)
        for unit in (localai.GETTER, localai.SERVER):
            (localai.UNITS / unit).touch()
        localai.BINARY.parent.mkdir(parents=True, exist_ok=True)
        localai.BINARY.touch()
        localai._write(stage="ready", **state)

    def test_what_it_got_goes(self):
        self.everything(ollama_ours=True, model_ours=MODEL, model_url=self.url)
        self.assertTrue(localai.there_is_something())
        localai.remove()
        self.assertFalse(localai.FOLDER.exists())
        self.assertFalse(localai.STATE_FILE.exists())
        self.assertFalse(localai.WANTED.exists())
        self.assertFalse((localai.UNITS / localai.SERVER).exists())
        self.assertFalse((localai.UNITS / localai.GETTER).exists())
        self.systemctl.assert_any_call("disable", "--now", localai.SERVER)
        self.systemctl.assert_any_call("disable", "--now", localai.GETTER)
        self.assertFalse(localai.there_is_something())
        self.assertFalse(any(path == "/api/delete" for path, _ in self.asked), "its own Ollama went with the model inside")

    def test_a_model_it_put_in_your_own_ollama_goes_but_not_your_ollama(self):
        localai._write(stage="ready", model_ours=MODEL, model_url=self.url)
        localai.remove()
        self.assertIn(("/api/delete", {"model": MODEL}), self.asked)

    def test_the_model_it_downloaded_not_the_one_you_use_now(self):
        localai._write(stage="ready", model_ours="downloaded:1b", model_url=self.url)
        (self.root / "modes.json").write_text(json.dumps({"llm": {"ollama_url": "http://127.0.0.1:1", "ollama_model": "mine:1b"}}))
        localai.remove()
        self.assertIn(("/api/delete", {"model": "downloaded:1b"}), self.asked)
        self.assertNotIn(("/api/delete", {"model": "mine:1b"}), self.asked)

    def test_it_stops_downloading_before_it_deletes(self):
        order = []
        localai.UNITS.mkdir()
        (localai.UNITS / localai.GETTER).touch()
        self.systemctl.side_effect = lambda *args: order.append(args[0]) or subprocess.CompletedProcess([], 0, "", "")
        localai._write(stage="model", model_ours=MODEL, model_url=self.url)
        with mock.patch.object(localai, "_delete_model", side_effect=lambda *_: order.append("delete")):
            localai.remove()
        self.assertEqual(order, ["disable", "daemon-reload", "delete"])

    def test_a_model_you_had_already_stays(self):
        localai._write(stage="ready")
        localai.remove()
        self.assertFalse(any(path == "/api/delete" for path, _ in self.asked))

    def test_your_ollama_being_off_doesnt_stop_it(self):
        localai._write(stage="ready", model_ours=MODEL, model_url="http://127.0.0.1:1")
        with redirect_stderr(io.StringIO()):
            localai.remove()
        self.assertFalse(localai.STATE_FILE.exists())

    def test_never_set_up(self):
        localai.WANTED.unlink()
        self.assertFalse(localai.there_is_something())
        localai.remove()
        self.assertEqual([call.args for call in self.systemctl.call_args_list], [("daemon-reload",)])


class TheRealFiles(unittest.TestCase):
    def test_the_checksums_written_down_are_the_right_length(self):
        for name, sha256, size in REAL_FILES.values():
            self.assertRegex(sha256, r"^[0-9a-f]{64}$", name)
            self.assertGreater(size, 100_000_000)
            self.assertTrue(name.endswith(".tar.zst"))
        self.assertTrue(localai.OLLAMA_URL.startswith("https://github.com/ollama/ollama/releases/download/v"))


if __name__ == "__main__":
    unittest.main()
