"""Package the standalone binary with its license and instructions."""

from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    windows = sys.platform == "win32"
    platform = "windows" if windows else "linux"
    executable = ROOT / "dist" / ("Vanilla BSAs Patcher.exe" if windows else "Vanilla BSAs Patcher")
    files = [executable, ROOT / "LICENSE", ROOT / "README.md"]
    destination = ROOT / "dist/release"
    destination.mkdir(parents=True, exist_ok=True)
    name = f"Vanilla-BSAs-Patcher-{platform}-x86_64"
    if windows:
        with zipfile.ZipFile(destination / (name + ".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, arcname=path.name)
    else:
        executable.chmod(0o755)
        with tarfile.open(destination / (name + ".tar.gz"), "w:gz") as archive:
            for path in files:
                archive.add(path, arcname=path.name)
    print(f"Packaged {name}")


if __name__ == "__main__":
    main()
