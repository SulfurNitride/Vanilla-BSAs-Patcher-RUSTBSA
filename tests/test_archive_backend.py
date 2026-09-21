import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import archive_backend as backend


class BackendDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        guard = patch.object(backend, "cache_directory", return_value=self.root)
        guard.start()
        self.addCleanup(guard.stop)

    def release(self, asset, executable, tag="0.0.4"):
        files = {executable: f"binary for {tag}".encode(), "LICENSE": b"license", "THIRD_PARTY_NOTICES.md": b"notices"}
        stream = io.BytesIO()
        if asset.endswith(".zip"):
            with zipfile.ZipFile(stream, "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
        else:
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for name, data in files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
        data = stream.getvalue()
        archive_url = backend.DOWNLOAD_PREFIX + tag + "/" + asset
        checksum_url = backend.DOWNLOAD_PREFIX + tag + "/SHA256SUMS"
        metadata = dict(tag_name=tag, assets=[
            dict(name=asset, browser_download_url=archive_url),
            dict(name="SHA256SUMS", browser_download_url=checksum_url),
        ])
        responses = {
            backend.LATEST_RELEASE: json.dumps(metadata).encode(),
            archive_url: data,
            checksum_url: f"{hashlib.sha256(data).hexdigest()}  {asset}\n".encode(),
        }
        return responses, archive_url

    def test_verified_download_for_linux_and_windows(self):
        for asset, executable in (
            ("bsa-ba2-tool-cli-linux-x86_64.tar.gz", "bsa-ba2-tool"),
            ("bsa-ba2-tool-cli-windows-x86_64.zip", "bsa-ba2-tool.exe"),
        ):
            with self.subTest(asset=asset), patch.object(backend, "platform_asset", return_value=(asset, executable)):
                responses, _ = self.release(asset, executable)
                with patch.object(backend, "_request", side_effect=lambda url: io.BytesIO(responses[url])):
                    binary = Path(backend.ensure_latest_backend(lambda _: None))
                    self.assertEqual(binary.read_bytes(), b"binary for 0.0.4")
                    self.assertEqual((binary.parent / "LICENSE").read_bytes(), b"license")
                    self.assertEqual(backend.cached_backend(), str(binary))
                with patch.object(backend, "_request", return_value=io.BytesIO(responses[backend.LATEST_RELEASE])) as request:
                    self.assertEqual(backend.ensure_latest_backend(lambda _: None), str(binary))
                    request.assert_called_once_with(backend.LATEST_RELEASE)

    def test_download_corruption_is_rejected(self):
        asset, executable = backend.platform_asset()
        responses, url = self.release(asset, executable)
        responses[url] += b"corrupt download"
        with patch.object(backend, "_request", side_effect=lambda url: io.BytesIO(responses[url])):
            with self.assertRaisesRegex(RuntimeError, "SHA256"):
                backend.ensure_latest_backend(lambda _: None)
        self.assertIsNone(backend.cached_backend())
        self.assertFalse((self.root / "current.json").exists())

    def test_latest_upgrade_and_offline_fallback(self):
        asset, executable = backend.platform_asset()
        for tag in ("0.0.4", "0.0.5"):
            responses, _ = self.release(asset, executable, tag)
            with patch.object(backend, "_request", side_effect=lambda url: io.BytesIO(responses[url])):
                binary = Path(backend.ensure_latest_backend(lambda _: None))
                self.assertEqual(binary.parent.name, tag)
                self.assertEqual(binary.read_bytes(), f"binary for {tag}".encode())
        with patch.object(backend, "_request", side_effect=urllib.error.URLError("offline")):
            self.assertEqual(backend.ensure_latest_backend(lambda _: None), str(binary))
            binary.write_bytes(b"damaged cache")
            with self.assertRaisesRegex(RuntimeError, "offline"):
                backend.ensure_latest_backend(lambda _: None)

    def test_old_buggy_release_cannot_be_installed(self):
        asset, executable = backend.platform_asset()
        responses, _ = self.release(asset, executable, "0.0.3")
        with patch.object(backend, "_request", side_effect=lambda url: io.BytesIO(responses[url])):
            with self.assertRaisesRegex(RuntimeError, "0.0.4 or newer"):
                backend.ensure_latest_backend(lambda _: None)
        self.assertIsNone(backend.cached_backend())


if __name__ == "__main__":
    unittest.main()
