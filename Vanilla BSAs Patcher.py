import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from queue import Empty, SimpleQueue
import time
import urllib.request
import uuid
import webbrowser

import blake3
import bottle
import eel
import soundfile as sf
import pyxdelta as pxd
from patcher_platform import resource_dir, detect_game_data_path, find_child
from archive_backend import ensure_latest_backend

ALL_BSAS = {
    "Fallout - Meshes.bsa": ("f7f2179ed7666e282d9308dfca614b134344cba7a033d94deb13cf5cc6be43c4", True),
    "Fallout - Misc.bsa": ("5a86184084145fd46feaefb8a8fc1c02caf4f9f004019cbbc1055b137d32d8ad", True),
    "Fallout - Textures.bsa": ("aec435835519438e467c3a97ed60fa8b0990a8446dfb5ce1ece35eedbe2d81ff", True),
    "Fallout - Textures2.bsa": ("90befeabbbb4dbb2188b78415bd86a548707405d29a4ab00e4a6644a9a7548e1", True),
    "Fallout - Sound.bsa": ("671f1ff31bbdcc4e00f04a922b69744f346567d9262e641ea7de1aebbab4dda5", True),
    "DeadMoney - Sounds.bsa": ("3f49992b3cfd1d85b76fe13319e852efc62508b2007b6dc26e1aff3615298801", False),
    "HonestHearts - Sounds.bsa": ("4bb535ecbd25f89ea1732d6fd802dba7ed2d1ba510e10a790854e7e6e330b8f3", False),
    "LonesomeRoad - Sounds.bsa": ("f0887cd205d2f34c1f67487fd09e9bbfe84afde56a8b6e467673047f03302830", False),
    "OldWorldBlues - Sounds.bsa": ("21cfe6f24a7bd692b34b86a40d871a9334acf673fbd83a6300905594cdbf0a10", False)
}

ui_events = None

def emit_ui(name, *args):
    if ui_events is None:
        getattr(eel, name)(*args)
    else:
        ui_events.put((name, args))

def dispatch_ui_events():
    while True:
        try:
            while True:
                name, args = ui_events.get_nowait()
                getattr(eel, name)(*args)
        except Empty:
            pass
        eel.sleep(0.02)

def log_to_ui(msg=""):
    emit_ui("addLog", msg)

@eel.expose
def detect_data_path():
    try:
        return detect_game_data_path()
    except (OSError, ValueError) as e:
        log_to_ui(f"Failed to auto-detect game installation path: {e}")
    return ""

@eel.expose
def select_folder():
    root = None
    try:
        if sys.platform.startswith("linux"):
            for name, args in (
                ("kdialog", ["--getexistingdirectory", os.path.expanduser("~"), "--title", "Select folder"]),
                ("zenity", ["--file-selection", "--directory", "--title=Select folder"]),
            ):
                executable = shutil.which(name)
                if executable:
                    result = subprocess.run([executable, *args], capture_output=True, text=True)
                    if result.returncode == 0:
                        return result.stdout.rstrip("\r\n")
                    if result.returncode == 1:
                        return ""
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        if sys.platform == "win32":
            root.wm_attributes("-topmost", True)
        folder = filedialog.askdirectory()
        return folder or ""
    except Exception as e:
        log_to_ui(f"Error opening folder picker: {e}. You can enter the folder path manually.")
    finally:
        if root is not None:
            root.destroy()
    return ""

def convert_single_ogg(args):
    ogg_path, temp_dir = args

    # Skip same folders as FNV BSA Decompressor except for the Dog ones, which are already in WAV format
    skip_folders = ("sound/songs/", "sound/fx/mus/", "sound/fx/emt/raintoggle/")

    rel_path = os.path.relpath(ogg_path, temp_dir).replace("\\", "/").casefold()
    if rel_path.startswith(skip_folders):
        return

    wav_path = os.path.splitext(ogg_path)[0] + ".wav"
    try:
        data, samplerate = sf.read(ogg_path)
        sf.write(wav_path, data, samplerate, subtype='PCM_16')
        os.remove(ogg_path)
    except Exception as e:
        raise RuntimeError(f"Error converting '{ogg_path}': {e}") from e

def convert_audio(temp_dir, workers, log=log_to_ui):
    ogg_files = [(os.path.join(r, f), temp_dir) for r, _, files in os.walk(temp_dir) for f in files if f.lower().endswith(".ogg")]

    if not ogg_files:
        return

    log("Converting OGG files to WAV...")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(convert_single_ogg, ogg_files))

def extract_mp3s(temp_dir, mp3_output_dir):
    for root, _, files in os.walk(temp_dir):
        for file in files:
            if file.lower().endswith(".mp3"):
                src, dest = os.path.join(root, file), os.path.join(mp3_output_dir, os.path.relpath(root, temp_dir), file)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(src, dest)

# Using BLAKE3 because it's fastest, even against SHA1
def calculate_blake3(file_path):
    hasher = blake3.blake3()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def run_archive_tool(tool, *args, threads=None):
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    if threads is not None:
        kwargs["env"] = dict(os.environ, RAYON_NUM_THREADS=str(threads))
    result = subprocess.run(
        [tool, *map(str, args)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", **kwargs,
    )
    if result.returncode:
        raise RuntimeError(f"Archive {args[0]} failed (exit {result.returncode}):\n{result.stdout.strip()}")


def process_bsas_thread(data_path, custom_path, options):
    try:
        process_bsas(data_path, custom_path, options)
    except Exception as e:
        log_to_ui(f"Error during execution: {e}")
        emit_ui("processFinished", False)


def process_bsas(data_path, custom_path, options):
    current_dir = resource_dir()
    data_path = os.path.realpath(os.path.expanduser(data_path))
    custom_path = os.path.realpath(os.path.expanduser(custom_path)) if custom_path else ""
    output_dir = custom_path or data_path
    is_game_folder = (os.path.normcase(output_dir) == os.path.normcase(data_path))
    root_path = os.path.dirname(os.path.normpath(data_path))

    archive_tool = ensure_latest_backend(log_to_ui)
    vcdiff = os.path.join(current_dir, "Fallout - Misc.vcdiff")
    vorbis_dlls = ["libvorbis.dll", "libvorbisfile.dll", "ogg.dll"]

    log_to_ui("Running prechecks...")
    if not os.path.isdir(data_path):
        raise NotADirectoryError(f"Game Data folder not found: {data_path}")
    required_precheck_files = [(vcdiff, "Fallout - Misc.vcdiff")]
    if options.get("upgrade_vorbis"):
        for dll in vorbis_dlls:
            required_precheck_files.append((os.path.join(current_dir, dll), dll))

    for path, name in required_precheck_files:
        if not os.path.exists(path):
            log_to_ui(f"Error: {name} not found. Make sure to extract everything from the downloaded archive.")
            return emit_ui("processFinished", False)

    if find_child(data_path, "Fallout - Meshes2.bsa").exists():
        log_to_ui("Error: 'Fallout - Meshes2.bsa' found in Data - it is a remnant from older FNV BSA Decompressor versions. Remove it and verify game files.")
        return emit_ui("processFinished", False)

    try:
        os.makedirs(output_dir, exist_ok=True)
        if not is_game_folder and any(f.lower() != "meta.ini" for f in os.listdir(output_dir)):
            log_to_ui("Error: Custom output directory must be empty (ignoring meta.ini).")
            return emit_ui("processFinished", False)
    except OSError as e:
        log_to_ui(f"Error handling output directory: {e}")
        return emit_ui("processFinished", False)

    try:
        total, _, free = shutil.disk_usage(output_dir)
        if free < 8 * 1024**3:
            log_to_ui(f"Error: Insufficient free disk space (required: 8GB, total: {total / (1024**3):.2f}GB, available: {free / (1024**3):.2f}GB).")
            return emit_ui("processFinished", False)
    except OSError as e:
        log_to_ui(f"Warning: Could not check disk space: {e}")

    if sys.platform == "win32" and os.path.normpath(output_dir).lower().startswith(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)").lower()):
        log_to_ui("Warning: The game is installed in 'Program Files (x86)'. UAC may restrict permissions.")

    log_to_ui("All prechecks passed.")
    log_to_ui()

    backup_dir = os.path.join(data_path, "Vanilla BSAs backup") if (is_game_folder and options.get("backup_bsas", True)) else None
    if backup_dir:
        try: os.makedirs(backup_dir, exist_ok=True)
        except OSError as e:
            log_to_ui(f"Error: Could not create backup directory: {e}")
            return emit_ui("processFinished", False)

    try:
        active_bsas = []
        for bsa_name, (expected_hash, is_required) in ALL_BSAS.items():
            backup_path = find_child(backup_dir, bsa_name) if backup_dir else ""
            game_path = find_child(data_path, bsa_name)
            target = backup_path if (backup_dir and os.path.exists(backup_path)) else (game_path if os.path.exists(game_path) else "")

            if not target:
                if is_required:
                    log_to_ui(f"Error: Required file '{bsa_name}' not found!")
                    return emit_ui("processFinished", False)
                log_to_ui(f"Warning: '{bsa_name}' not found, skipping.")
                continue

            if options.get("verify_hashes"):
                target_hash = calculate_blake3(target)
                if target_hash != expected_hash:
                    log_to_ui(f"Error: Hash mismatch for '{bsa_name}'! Expected: {expected_hash}, got: {target_hash}")
                    log_to_ui("Hash verification requires clean, English game files. Verify your game files, or uncheck the option if using a non-English version.")
                    return emit_ui("processFinished", False)

            active_bsas.append(bsa_name)

        if options.get("verify_hashes"):
            log_to_ui("Hashes verified.")
        else:
            log_to_ui("Hash verification skipped.")
        log_to_ui()

        if options.get("upgrade_vorbis"):
            log_to_ui("Upgrading Vorbis libraries...")
            try:
                for dll in vorbis_dlls:
                    src_dll = os.path.join(current_dir, dll)
                    dest_dll = find_child(root_path, dll)
                    shutil.copy2(src_dll, dest_dll)
                log_to_ui("Vorbis libraries upgraded.")
                log_to_ui()
            except OSError as e:
                log_to_ui(f"Error upgrading Vorbis libraries: {e}")
                return emit_ui("processFinished", False)

        workers = int(options.get("archive_workers", 2))
        if not 1 <= workers <= 4:
            raise ValueError("Choose between 1 and 4 BSAs at once")
        workers = min(workers, len(active_bsas))
        threads = max(1, ((os.cpu_count() or 1) - 1) // workers)
        cancelled = threading.Event()
        completed = 0
        failure = None
        loose_owners = {}
        emit_ui("updateProgress", 0)
        log_to_ui(f"Processing up to {workers} BSAs at once...")
        with tempfile.TemporaryDirectory(prefix=".vanilla-bsa-run-", dir=output_dir) as staging:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        prepare_archive, name, data_path, output_dir, backup_dir,
                        archive_tool, vcdiff, options, os.path.join(staging, str(index)),
                        threads, cancelled,
                    ): (index, name)
                    for index, name in enumerate(active_bsas)
                }
                for future in as_completed(futures):
                    index, name = futures[future]
                    try:
                        workspace = future.result()
                        if cancelled.is_set():
                            continue
                        commit_archive(workspace, name, index, output_dir, loose_owners)
                        shutil.rmtree(workspace)
                        completed += 1
                        emit_ui("updateProgress", int(completed / len(active_bsas) * 100))
                        log_to_ui(f"[{name}] Done.")
                    except CancelledError:
                        pass
                    except Exception as error:
                        if failure is None:
                            failure = RuntimeError(f"{name}: {error}")
                        cancelled.set()
                        for pending in futures:
                            pending.cancel()
            if failure is not None:
                raise failure

        log_to_ui("Patching successful!")
        emit_ui("processFinished", True)

    except Exception as e:
        log_to_ui(f"Error during execution: {e}")
        emit_ui("processFinished", False)


def prepare_archive(bsa_name, data_path, output_dir, backup_dir, tool, vcdiff,
                    options, workspace, threads, cancelled):
    def check_cancelled():
        if cancelled.is_set():
            raise CancelledError()

    def log(message):
        log_to_ui(f"[{bsa_name}] {message}")

    try:
        check_cancelled()
        os.makedirs(workspace)
        bsa_path = find_child(data_path, bsa_name)
        if backup_dir:
            backup = find_child(backup_dir, bsa_name)
            if not backup.exists():
                log("Backing up...")
                shutil.copy2(bsa_path, backup)
            bsa_path = backup
        check_cancelled()
        if bsa_name.lower() == "fallout - misc.bsa":
            log("Applying delta patch...")
            patched = os.path.join(workspace, "patched.bsa")
            if not pxd.decode(str(bsa_path), vcdiff, patched):
                raise RuntimeError("xdelta failed: invalid delta or source archive")
            bsa_path = patched

        extracted = os.path.join(workspace, "extracted")
        log("Unpacking...")
        run_archive_tool(tool, "extract", bsa_path, extracted, threads=threads)
        check_cancelled()
        if bsa_name.lower() == "fallout - misc.bsa":
            bad_file = find_child(find_child(extracted, "menus"), "s.txt")
            if bad_file.exists():
                bad_file.unlink()
            meshes2 = find_child(output_dir, "Fallout - Meshes2.bsa")
            if meshes2.exists():
                log("Merging Meshes2 with Misc...")
                run_archive_tool(tool, "extract", meshes2, extracted,
                                 "--overwrite", "overwrite", threads=threads)
        if options.get("split_mp3"):
            extract_mp3s(extracted, os.path.join(workspace, "mp3"))
        if options.get("ogg_to_wav"):
            convert_audio(extracted, threads, log)
        check_cancelled()
        log("Repacking...")
        packed = os.path.join(workspace, "packed.bsa")
        compression = "none" if options.get("decompress") else "zlib"
        run_archive_tool(tool, "pack", extracted, "fonv", "--compression", compression,
                         "--output", packed, threads=threads)
        check_cancelled()
        run_archive_tool(tool, "verify", packed, threads=threads)
        check_cancelled()
        return workspace
    except Exception:
        cancelled.set()
        raise


def commit_archive(workspace, bsa_name, index, output_dir, loose_owners):
    mp3_root = os.path.join(workspace, "mp3")
    for root, _, files in os.walk(mp3_root):
        for name in files:
            source = os.path.join(root, name)
            relative = os.path.relpath(source, mp3_root)
            key = relative.replace("\\", "/").casefold()
            # Retain the original archive order for overlapping loose files,
            # regardless of which worker finishes first.
            if loose_owners.get(key, -1) > index:
                continue
            destination = os.path.join(output_dir, relative)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.move(source, destination)
            loose_owners[key] = index
    os.replace(os.path.join(workspace, "packed.bsa"), find_child(output_dir, bsa_name))
    if bsa_name.lower() == "fallout - misc.bsa":
        meshes2 = find_child(output_dir, "Fallout - Meshes2.bsa")
        if meshes2.exists():
            meshes2.unlink()


def open_browser_when_ready(url, probe_url, timeout=15, opener=webbrowser.open):
    deadline = time.monotonic() + timeout
    local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with local_http.open(probe_url, timeout=0.5) as response:
                if response.status == 200:
                    opener(url)
                    return
        except OSError:
            pass
        time.sleep(0.05)
    raise RuntimeError("The patcher's local server did not become ready")


def launch_app(host="127.0.0.1", port=8000):
    global ui_events
    ui_events = SimpleQueue()
    eel.init(os.path.join(os.path.dirname(__file__), "web"))
    app = bottle.Bottle()
    probe_path = f"/_ready/{uuid.uuid4().hex}"
    app.get(probe_path, callback=lambda: "ready")
    base = f"http://{host}:{port}"
    threading.Thread(target=open_browser_when_ready,
                     args=(base + "/index.html", base + probe_path), daemon=True).start()
    eel.spawn(dispatch_ui_events)
    eel.start("index.html", host=host, port=port, size=(720, 680), mode=None, app=app)

@eel.expose
def start_processing(path, custom, options):
    threading.Thread(target=process_bsas_thread, args=(path, custom, options), daemon=True).start()

if __name__ == "__main__":
    launch_app()
