"""Download and cache the latest official BSA/BA2 CLI release."""

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import ssl
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

import certifi

REPOSITORY = "SulfurNitride/Rust-BSA-BA2-Handler"
LATEST_RELEASE = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPOSITORY}/releases/download/"
MINIMUM_VERSION = (0, 0, 4)


def release_version(tag):
    if not isinstance(tag, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", tag):
        raise ValueError(f"Unsupported archive tool release: {tag}")
    version = tuple(map(int, tag.lstrip("v").split(".")))
    if version < MINIMUM_VERSION:
        raise ValueError("Archive tool release 0.0.4 or newer is required")
    return version


def platform_asset():
    if platform.machine().lower() not in ("x86_64", "amd64"):
        raise RuntimeError("The archive tool currently provides x86_64 releases only")
    if sys.platform == "win32":
        return "bsa-ba2-tool-cli-windows-x86_64.zip", "bsa-ba2-tool.exe"
    if sys.platform.startswith("linux"):
        return "bsa-ba2-tool-cli-linux-x86_64.tar.gz", "bsa-ba2-tool"
    raise RuntimeError("The archive tool currently provides Windows and Linux releases only")


def cache_directory():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "vanilla-bsas-patcher/backend"


def _sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _cached(root, asset, executable):
    try:
        record = json.loads((root / "current.json").read_text(encoding="utf-8"))
        release_version(record["tag"])
        binary = root / record["tag"] / executable
        if (record["asset"] == asset and binary.is_file() and os.access(binary, os.X_OK)
                and _sha256(binary) == record["binary_sha256"]):
            return binary, record
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def cached_backend():
    asset, executable = platform_asset()
    cached = _cached(cache_directory(), asset, executable)
    return str(cached[0]) if cached else None


def _request(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Vanilla-BSAs-Patcher",
        "Accept": "application/vnd.github+json" if url == LATEST_RELEASE else "application/octet-stream",
    })
    # A frozen Linux interpreter may look for its build machine's CA paths.
    # Include portable roots while retaining any certificates trusted locally.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return urllib.request.urlopen(request, timeout=20, context=context)


def _asset_url(assets, name):
    url = assets[name]["browser_download_url"]
    if not url.startswith(DOWNLOAD_PREFIX):
        raise ValueError(f"Unexpected GitHub release asset URL for {name}")
    return url


def _unpack(archive, destination, executable):
    # Copy only named regular files, never archive paths or symlinks.
    names = (executable, "LICENSE", "THIRD_PARTY_NOTICES.md")
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as package:
            for name in names:
                with package.open(name) as source, (destination / name).open("wb") as output:
                    shutil.copyfileobj(source, output)
    else:
        with tarfile.open(archive, "r:gz") as package:
            for name in names:
                member = package.getmember(name)
                if not member.isfile():
                    raise ValueError(f"Release asset {name} is not a regular file")
                with package.extractfile(member) as source, (destination / name).open("wb") as output:
                    shutil.copyfileobj(source, output)
    (destination / executable).chmod(0o755)


def ensure_latest_backend(log=print):
    asset, executable = platform_asset()
    root = cache_directory()
    cached = _cached(root, asset, executable)
    log("Checking GitHub for the latest archive tool...")
    try:
        with _request(LATEST_RELEASE) as response:
            release = json.load(response)
        tag = release["tag_name"]
        release_version(tag)
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("The archive tool release is not a stable published release")
        if cached and cached[1]["tag"] == tag:
            log(f"Archive tool {tag} is up to date.")
            return str(cached[0])

        assets = {entry["name"]: entry for entry in release["assets"]}
        download_url = _asset_url(assets, asset)
        with _request(_asset_url(assets, "SHA256SUMS")) as response:
            checksums = response.read().decode("utf-8")
        expected = None
        for line in checksums.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1].lstrip("*") == asset:
                expected = fields[0].lower()
        if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"No valid SHA256 checksum published for {asset}")

        root.mkdir(parents=True, exist_ok=True)
        log(f"Downloading archive tool {tag}...")
        with tempfile.TemporaryDirectory(prefix="download-", dir=root) as temp:
            staging = Path(temp)
            archive = staging / asset
            with _request(download_url) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            if _sha256(archive) != expected:
                raise ValueError("Archive tool download failed SHA256 verification")
            _unpack(archive, staging, executable)
            record = dict(tag=tag, asset=asset, binary_sha256=_sha256(staging / executable))
            version_dir = root / tag
            version_dir.mkdir(exist_ok=True)
            for name in (executable, "LICENSE", "THIRD_PARTY_NOTICES.md"):
                os.replace(staging / name, version_dir / name)
            state = staging / "current.json"
            state.write_text(json.dumps(record), encoding="utf-8")
            os.replace(state, root / "current.json")
        log(f"Archive tool {tag} downloaded and verified.")
        return str(version_dir / executable)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, zipfile.BadZipFile) as error:
        if cached:
            log(f"Could not update the archive tool: {error}. Using verified cached {cached[1]['tag']}.")
            return str(cached[0])
        raise RuntimeError(f"Unable to download the archive tool from GitHub: {error}. Connect to the internet and retry.") from error
