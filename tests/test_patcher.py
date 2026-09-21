"""Run with python -m unittest discover -s tests -v.

Integration tests use the real Rust CLI and small, generated New Vegas BSAs.
Run tools/download_backend.py first, or set BSA_TOOL to its executable.
"""

import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import patcher_platform as platform
from archive_backend import cached_backend

spec = importlib.util.spec_from_file_location("patcher", ROOT / "Vanilla BSAs Patcher.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class PlatformTests(unittest.TestCase):
    def test_frozen_app_uses_embedded_assets_after_moving(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            extracted = root / "extracted"
            assets = extracted / "assets"
            assets.mkdir(parents=True)
            with patch.object(platform.sys, "frozen", True, create=True), patch.object(
                platform.sys, "_MEIPASS", str(extracted), create=True
            ), patch.object(platform.sys, "executable", str(root / "elsewhere/patcher")):
                self.assertEqual(platform.resource_dir(), assets)

    def test_secondary_steam_library_and_lowercase_data(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            steamapps = home / ".local/share/Steam/steamapps"
            steamapps.mkdir(parents=True)
            library = home / "Other Drive/Steam Library"
            data = library / "steamapps/common/Custom FNV/data"
            data.mkdir(parents=True)
            (steamapps / "libraryfolders.vdf").write_text(
                f'"libraryfolders" {{ "1" {{ "path" "{library.as_posix()}" }} }}'
            )
            (library / "steamapps/appmanifest_22380.acf").write_text(
                '"AppState" { "appid" "22380" "installdir" "Custom FNV" }'
            )
            with patch.object(platform.Path, "home", return_value=home), patch.dict(
                os.environ, {"XDG_DATA_HOME": str(home / ".local/share")}
            ):
                self.assertEqual(platform.steam_data_path(), str(data))

    def test_flatpak_steam(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            data = home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common/Fallout New Vegas/Data"
            data.mkdir(parents=True)
            with patch.object(platform.Path, "home", return_value=home), patch.dict(
                os.environ, {"XDG_DATA_HOME": str(home / ".local/share")}
            ):
                self.assertEqual(platform.steam_data_path(), str(data))

    def test_windows_registry_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "Data"
            data.mkdir()
            registry = Mock()
            registry.OpenKey.return_value.__enter__ = Mock(return_value="key")
            registry.OpenKey.return_value.__exit__ = Mock(return_value=False)
            registry.QueryValueEx.return_value = (temp, 1)
            with patch.object(platform.sys, "platform", "win32"), patch.dict(sys.modules, {"winreg": registry}):
                self.assertEqual(platform.detect_game_data_path(), str(data))

    def test_subprocess_platform_options_and_error_details(self):
        for system in ("linux", "win32"):
            with self.subTest(system=system), patch.object(patcher.sys, "platform", system), patch.object(
                subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True
            ), patch.object(patcher.subprocess, "run", return_value=Mock(returncode=1, stdout="bad archive")) as run:
                with self.assertRaisesRegex(RuntimeError, "bad archive"):
                    patcher.run_archive_tool("tool with spaces", "verify", "archive with spaces.bsa")
                self.assertEqual(run.call_args.args[0], ["tool with spaces", "verify", "archive with spaces.bsa"])
                self.assertEqual("creationflags" in run.call_args.kwargs, system == "win32")


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = os.environ.get("BSA_TOOL") or cached_backend()
        if not cls.tool:
            raise unittest.SkipTest("Run tools/download_backend.py or set BSA_TOOL for archive integration tests")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="patcher test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "Game with spaces/data"
        self.data.mkdir(parents=True)
        self.assets = self.root / "patcher"
        self.assets.mkdir()
        self.logs = []
        self.finished = Mock()
        for target, value in (
            ("resource_dir", lambda: self.assets),
            ("ensure_latest_backend", lambda _: self.tool),
            ("log_to_ui", lambda msg="": self.logs.append(msg)),
        ):
            guard = patch.object(patcher, target, value)
            guard.start()
            self.addCleanup(guard.stop)
        for name, value in (("processFinished", self.finished), ("updateProgress", Mock())):
            guard = patch.object(patcher.eel, name, value, create=True)
            guard.start()
            self.addCleanup(guard.stop)
        # Exercise the pipeline without depending on the test runner's free space.
        guard = patch.object(patcher.shutil, "disk_usage", return_value=(100 * 1024**3, 0, 100 * 1024**3))
        guard.start()
        self.addCleanup(guard.stop)

    def tool_run(self, *args):
        return subprocess.run([str(self.tool), *map(str, args)], check=True, capture_output=True, text=True)

    def source(self, name, files):
        root = self.root / name
        root.mkdir()
        for relative, data in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return root

    def pack(self, source, destination):
        self.tool_run("pack", source, "fonv", "--compression", "zlib", "--output", destination)

    def read_archive(self, path, name):
        output = self.root / name
        self.tool_run("verify", path)
        self.tool_run("extract", path, output)
        return {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()}

    def run_pipeline(self, archives, output="", **overrides):
        options = dict(backup_bsas=True, verify_hashes=True, decompress=True, ogg_to_wav=False, split_mp3=False, upgrade_vorbis=False)
        options.update(overrides)
        with patch.object(patcher, "ALL_BSAS", archives):
            patcher.process_bsas_thread(str(self.data), str(output), options)

    def test_full_pipeline_delta_audio_mp3_and_dlls(self):
        mesh = self.source("mesh source", {"meshes/test.nif": b"mesh fixture" * 100})
        self.pack(mesh, self.data / "Fallout - Meshes.bsa")
        misc = self.source("misc source", {"menus/s.txt": b"remove me", "menus/test.xml": b"before patch"})
        original = self.data / "Fallout - Misc.bsa"
        self.pack(misc, original)
        (misc / "menus/test.xml").write_bytes(b"after patch")
        modified = self.root / "modified.bsa"
        self.pack(misc, modified)
        self.assertTrue(patcher.pxd.run(str(original), str(modified), str(self.assets / "Fallout - Misc.vcdiff")))

        sound = self.source("sound source", {"sound/voice/test.mp3": b"mp3 fixture"})
        for relative in ("sound/fx/effect.ogg", "sound/songs/music.ogg", "sound/fx/musics/test.ogg"):
            path = sound / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            patcher.sf.write(path, np.zeros(200, dtype=np.float32), 22050, format="OGG", subtype="VORBIS")
        self.pack(sound, self.data / "Fallout - Sound.bsa")
        originals = {p.name: p.read_bytes() for p in self.data.iterdir()}
        archives = {name: (patcher.blake3.blake3(content).hexdigest(), True) for name, content in originals.items()}
        archives["DeadMoney - Sounds.bsa"] = ("unused", False)
        for dll in ("libvorbis.dll", "libvorbisfile.dll", "ogg.dll"):
            (self.assets / dll).write_bytes(dll.encode())
        output = self.root / "Mod output"
        self.run_pipeline(archives, output, ogg_to_wav=True, split_mp3=True, upgrade_vorbis=True)
        self.assertEqual(self.finished.call_args.args, (True,), "\n".join(self.logs))
        self.finished.assert_called_once_with(True)
        self.assertEqual({p.name: p.read_bytes() for p in self.data.iterdir()}, originals)
        self.assertEqual(self.read_archive(output / "Fallout - Misc.bsa", "read misc"), {"menus/test.xml": b"after patch"})
        audio = self.read_archive(output / "Fallout - Sound.bsa", "read sound")
        self.assertEqual(set(audio), {"sound/fx/effect.wav", "sound/songs/music.ogg", "sound/fx/musics/test.wav"})
        info = patcher.sf.info(self.root / "read sound/sound/fx/effect.wav")
        self.assertEqual((info.samplerate, info.subtype), (22050, "PCM_16"))
        self.assertEqual((output / "sound/voice/test.mp3").read_bytes(), b"mp3 fixture")
        self.assertEqual((self.data.parent / "libvorbis.dll").read_bytes(), b"libvorbis.dll")
        for path in output.glob("*.bsa"):
            header = struct.unpack("<4s8I", path.read_bytes()[:36])
            self.assertEqual(header[:2], (b"BSA\x00", 104))
            self.assertFalse(header[3] & 4, "Decompressed archive must not set the compressed flag")
        self.assertFalse(list(output.glob(".vanilla-bsa-*")))

    def test_in_place_backup_case_and_repeated_run(self):
        source = self.source("source", {"meshes/test.nif": b"fixture" * 100})
        archive = self.data / "fallout - meshes.bsa"
        self.pack(source, archive)
        original = archive.read_bytes()
        (self.assets / "Fallout - Misc.vcdiff").touch()
        archives = {"Fallout - Meshes.bsa": (patcher.blake3.blake3(original).hexdigest(), True)}
        self.run_pipeline(archives, self.data / ".")
        self.finished.assert_called_once_with(True)
        self.assertEqual((self.data / "Vanilla BSAs backup/Fallout - Meshes.bsa").read_bytes(), original)
        self.assertEqual([p.name for p in self.data.glob("*.bsa")], [archive.name])
        self.finished.reset_mock()
        self.run_pipeline(archives, decompress=False)
        self.finished.assert_called_once_with(True)
        self.assertEqual(self.read_archive(archive, "reread"), {"meshes/test.nif": b"fixture" * 100})
        self.assertTrue(struct.unpack_from("<I", archive.read_bytes(), 12)[0] & 4)

    def test_failed_pack_or_verify_keeps_original_without_backup(self):
        source = self.source("source", {"meshes/test.nif": b"fixture"})
        archive = self.data / "Fallout - Meshes.bsa"
        self.pack(source, archive)
        original = archive.read_bytes()
        (self.assets / "Fallout - Misc.vcdiff").touch()
        archives = {archive.name: (patcher.blake3.blake3(original).hexdigest(), True)}
        real_run = patcher.run_archive_tool
        for failed_command in ("pack", "verify"):
            self.finished.reset_mock()
            def fail(tool, command, *args, **kwargs):
                if command == failed_command:
                    raise RuntimeError("simulated failure")
                return real_run(tool, command, *args, **kwargs)
            with self.subTest(command=failed_command), patch.object(patcher, "run_archive_tool", side_effect=fail):
                self.run_pipeline(archives, backup_bsas=False)
                self.finished.assert_called_once_with(False)
                self.assertEqual(archive.read_bytes(), original)
                self.assertFalse(list(self.data.glob(".vanilla-bsa-*")))

    def test_archives_overlap_and_respect_worker_limit(self):
        names = ["Fallout - Meshes.bsa", "Fallout - Textures.bsa", "Fallout - Textures2.bsa"]
        archives = {}
        for index, name in enumerate(names):
            source = self.source(f"source {index}", {f"meshes/test{index}.nif": b"fixture"})
            self.pack(source, self.data / name)
            archives[name] = (patcher.calculate_blake3(self.data / name), True)
        (self.assets / "Fallout - Misc.vcdiff").touch()
        barrier = threading.Barrier(2, timeout=5)
        lock = threading.Lock()
        active = peak = started = 0
        prepare = patcher.prepare_archive

        def observe(*args):
            nonlocal active, peak, started
            with lock:
                active += 1
                started += 1
                first_pair = started <= 2
                peak = max(peak, active)
            try:
                if first_pair:
                    barrier.wait()
                return prepare(*args)
            finally:
                with lock:
                    active -= 1

        output = self.root / "parallel output"
        with patch.object(patcher, "prepare_archive", side_effect=observe):
            self.run_pipeline(archives, output, archive_workers=2)
        self.finished.assert_called_once_with(True)
        self.assertEqual((peak, started, active), (2, 3, 0))
        for index, name in enumerate(names):
            self.assertEqual(self.read_archive(output / name, f"parallel read {index}"), {f"meshes/test{index}.nif": b"fixture"})
        progress = [call.args[0] for call in patcher.eel.updateProgress.call_args_list]
        self.assertEqual(progress, [0, 33, 66, 100])

    def test_out_of_order_completion_preserves_mp3_overlay_order(self):
        names = ["Fallout - Sound.bsa", "DeadMoney - Sounds.bsa"]
        archives = {}
        for index, name in enumerate(names):
            source = self.source(f"mp3 source {index}", {
                "sound/voice/shared.mp3": bytes([index]),
                f"sound/voice/keep{index}.wav": b"remaining fixture",
            })
            self.pack(source, self.data / name)
            archives[name] = (patcher.calculate_blake3(self.data / name), True)
        (self.assets / "Fallout - Misc.vcdiff").touch()
        second_committed = threading.Event()
        prepare, commit = patcher.prepare_archive, patcher.commit_archive

        def delayed_first(name, *args):
            workspace = prepare(name, *args)
            if name == names[0]:
                self.assertTrue(second_committed.wait(5))
            return workspace

        def observe_commit(workspace, name, *args):
            commit(workspace, name, *args)
            if name == names[1]:
                second_committed.set()

        output = self.root / "mp3 output"
        with patch.object(patcher, "prepare_archive", side_effect=delayed_first), patch.object(
            patcher, "commit_archive", side_effect=observe_commit
        ):
            self.run_pipeline(archives, output, split_mp3=True, archive_workers=2)
        self.finished.assert_called_once_with(True)
        self.assertEqual((output / "sound/voice/shared.mp3").read_bytes(), b"\x01")

    def test_failure_waits_for_other_worker_before_finishing(self):
        names = ["Fallout - Meshes.bsa", "Fallout - Textures.bsa", "Fallout - Textures2.bsa"]
        archives = {}
        originals = {}
        for index, name in enumerate(names):
            source = self.source(f"failure source {index}", {f"meshes/test{index}.nif": b"fixture"})
            self.pack(source, self.data / name)
            originals[name] = (self.data / name).read_bytes()
            archives[name] = (patcher.calculate_blake3(self.data / name), True)
        (self.assets / "Fallout - Misc.vcdiff").touch()
        second_started, release_second, failed = (threading.Event() for _ in range(3))
        prepare = patcher.prepare_archive

        def controlled(name, *args):
            if name == names[0]:
                self.assertTrue(second_started.wait(5))
                args[-1].set()
                failed.set()
                raise RuntimeError("intentional parallel failure")
            if name == names[1]:
                second_started.set()
                self.assertTrue(release_second.wait(5))
            return prepare(name, *args)

        with patch.object(patcher, "prepare_archive", side_effect=controlled):
            worker = threading.Thread(target=self.run_pipeline, args=(archives,), kwargs={"archive_workers": 2})
            worker.start()
            try:
                self.assertTrue(failed.wait(5))
                self.finished.assert_not_called()
            finally:
                release_second.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.finished.assert_called_once_with(False)
        for name, original in originals.items():
            self.assertEqual((self.data / name).read_bytes(), original)
        self.assertFalse(list(self.data.glob(".vanilla-bsa-*")))

    def test_failed_delta_keeps_original(self):
        source = self.source("source", {"menus/test.xml": b"fixture"})
        archive = self.data / "Fallout - Misc.bsa"
        self.pack(source, archive)
        original = archive.read_bytes()
        (self.assets / "Fallout - Misc.vcdiff").write_bytes(b"invalid patch")
        archives = {archive.name: (patcher.blake3.blake3(original).hexdigest(), True)}
        self.run_pipeline(archives, backup_bsas=False)
        self.finished.assert_called_once_with(False)
        self.assertEqual(archive.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
