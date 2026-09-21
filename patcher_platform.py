"""Platform-specific paths for the otherwise shared patching workflow."""

import os
from pathlib import Path
import re
import sys


def find_child(directory, name):
    """Resolve Bethesda/Steam names on case-sensitive filesystems."""
    directory = Path(directory)
    exact = directory / name
    if exact.exists():
        return exact
    if directory.is_dir():
        matches = [p for p in directory.iterdir() if p.name.casefold() == name.casefold()]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous filename '{name}' in {directory}")
        if matches:
            return matches[0]
    return exact


def application_dir():
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def resource_dir():
    """PyInstaller extracts embedded assets independently of the executable's location."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets"
    return application_dir()


def steam_data_path():
    home = Path.home()
    data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    roots = [
        home / ".steam/steam",
        home / ".steam/root",
        data_home / "Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    libraries = list(roots)
    for root in roots:
        try:
            config = (root / "steamapps/libraryfolders.vdf").read_text(encoding="utf-8")
        except OSError:
            continue
        # Modern library entries use "path"; older files use numbered pairs.
        for value in re.findall(r'"(?:path|\d+)"\s+"([^"\r\n]+)"', config):
            path = Path(value.replace("\\\\", "\\"))
            if path.is_absolute():
                libraries.append(path)

    for library in dict.fromkeys(libraries):
        steamapps = library / "steamapps"
        install_name = "Fallout New Vegas"
        try:
            manifest = (steamapps / "appmanifest_22380.acf").read_text(encoding="utf-8")
            match = re.search(r'"installdir"\s+"([^"\r\n]+)"', manifest)
            if match:
                install_name = match.group(1)
        except OSError:
            pass
        data = find_child(steamapps / "common" / install_name, "Data")
        if data.is_dir():
            return str(data)
    return ""


def detect_game_data_path():
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Bethesda Softworks\FalloutNV") as key:
                data = find_child(winreg.QueryValueEx(key, "Installed Path")[0], "Data")
                if data.is_dir():
                    return str(data)
        except OSError:
            pass
        return ""
    return steam_data_path()
