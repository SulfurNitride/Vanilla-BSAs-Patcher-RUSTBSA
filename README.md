Nexus Mods page [here](https://www.nexusmods.com/newvegas/mods/98738).

Runs natively on Windows and Linux, using
[Rust BSA/BA2 Handler](https://github.com/SulfurNitride/Rust-BSA-BA2-Handler)
for archive operations. New Vegas itself still runs through Wine/Proton on Linux.

**Downloads**

Get the Windows or Linux x86_64 application from this fork's
[latest release](https://github.com/SulfurNitride/Vanilla-BSAs-Patcher-RUSTBSA/releases/latest).
Extract the archive and run **Vanilla BSAs Patcher** (`.exe` on Windows).
Linux builds require glibc 2.39 or newer (Ubuntu 24.04 or equivalent).
Python and Rust are not required for these downloads.

**Running from source**

Install Python 3.12 or newer and [uv](https://docs.astral.sh/uv/), then:

```sh
uv sync
uv run python "Vanilla BSAs Patcher.py"
```

The patcher automatically downloads the latest official CLI release when you
start processing. No separate archive executable or Rust installation is needed.

The working directory does not matter. A browser opens the existing web UI.
Linux auto-detection checks native and Flatpak Steam installations, including
additional libraries listed in `libraryfolders.vdf`. Windows retains registry
detection. For other installations, select or enter the game's **Data folder**.
Paths containing spaces and differently capitalized BSA filenames are supported.

Linux folder selection uses `kdialog` or `zenity` when installed, then falls
back to Tkinter (typically `python3-tk` on Debian/Ubuntu, or `tk` on Arch).
You can also enter paths manually.

**Archive backend**

Each processing run checks the handler's latest stable GitHub release. It selects
the Windows or Linux x86_64 CLI asset, verifies it against the release's
`SHA256SUMS`, and caches the executable and license notices. Release 0.0.4 or newer
is required for the parallel-extraction fix.

The cache lives under `$XDG_CACHE_HOME/vanilla-bsas-patcher/backend` (normally
`~/.cache/vanilla-bsas-patcher/backend`) on Linux, or
`%LOCALAPPDATA%/vanilla-bsas-patcher/backend` on Windows. If GitHub is unavailable,
the patcher uses its previously verified download. The first run needs internet
access. To download the tool in advance:

```sh
uv run python tools/download_backend.py
```

**Required assets**

These files are included in the standalone application. When running from source,
keep them beside the Python script:

- `Fallout - Misc.vcdiff`
- `libvorbis.dll`, `libvorbisfile.dll`, and `ogg.dll` when **Upgrade Vorbis libraries** is enabled.

Those DLLs belong to the Windows game, including when played through Proton.
The patcher copies them to the game folder; it does not load them itself.

The original options remain available: decompression, OGG-to-WAV conversion,
loose MP3 output, Vorbis upgrades, clean English archive checks, and backups.
**BSAs at once** controls how many archives run in parallel (1–4, default 2).
Jobs share the CPU budget; higher settings can help on fast storage but may
be slower on a hard drive.
Temporary work stays on the output filesystem. Each rebuilt BSA is verified
before replacing its destination; the intermediate delta-patched BSA is kept
outside the content being repacked.

**Building a standalone application**

Build on the target operating system after installing Python dependencies:

```sh
uv run python tools/build_app.py
```

The executable in `dist` includes the UI, delta patch, and game DLLs. It can be
moved on its own and downloads the archive tool into the user cache automatically.

**Tests**

```sh
uv run python -m unittest discover -s tests -v
```

Run `tools/download_backend.py` first, or set `BSA_TOOL` to a test executable.
Integration tests
create small BSA v104 fixtures and exercise delta patching, audio conversion,
MP3 extraction, DLL copying, both compression settings, backup reuse, filename
case handling, and preservation of the original after a failed rebuild.
They never use an installed game's files. CI runs the suite and builds the
application on both Windows and Linux.
