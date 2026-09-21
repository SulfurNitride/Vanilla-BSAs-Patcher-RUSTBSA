"""Build a single executable containing assets; the CLI updates from GitHub."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    assets = [ROOT / name for name in (
        "Fallout - Misc.vcdiff", "libvorbis.dll", "libvorbisfile.dll", "ogg.dll",
    )]
    for asset in assets:
        if not asset.is_file():
            raise FileNotFoundError(f"Required packaging asset is missing: {asset}")
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
        "--icon=web/icon.ico", "--add-data", "web:web",
    ]
    for asset in assets:
        command.extend(["--add-data", f"{asset}:assets"])
    command.append("Vanilla BSAs Patcher.py")
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
