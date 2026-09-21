"""Move the frozen app alone and check its very first browser request."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 8000))
    name = "Vanilla BSAs Patcher.exe" if sys.platform == "win32" else "Vanilla BSAs Patcher"
    with tempfile.TemporaryDirectory(prefix="standalone patcher ") as temp:
        temp = Path(temp)
        executable = temp / name
        shutil.copy2(ROOT / "dist" / name, executable)
        browser = temp / "browser.py"
        result = temp / "browser.json"
        browser.write_text('''
import json
from pathlib import Path
import sys
import urllib.request
result = {}
try:
    for path in ("index.html", "eel.js", "style.css", "icon.ico"):
        url = sys.argv[1].rsplit("/", 1)[0] + "/" + path
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read()
            assert response.status == 200 and body, path
            if path == "index.html":
                assert b"BSAs at once" in body
            if path == "eel.js":
                assert b"start_processing" in body
            result[path] = response.status
except Exception as error:
    result["error"] = str(error)
output = Path(__file__).with_name("browser.json")
staging = output.with_suffix(".tmp")
staging.write_text(json.dumps(result))
staging.replace(output)
''', encoding="utf-8")
        environment = dict(os.environ, BROWSER=f'"{Path(sys.executable).as_posix()}" "{browser.as_posix()}" %s')
        with (temp / "app.log").open("w+") as log:
            process = subprocess.Popen([str(executable)], cwd=temp, env=environment, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 60
                while not result.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                log.seek(0)
                if not result.exists():
                    raise RuntimeError(f"The packaged app did not open its browser: {log.read()}")
                response = json.loads(result.read_text())
                assert response == dict.fromkeys(("index.html", "eel.js", "style.css", "icon.ico"), 200), response
                print("Packaged app: first browser request and all local UI assets returned HTTP 200.")
            finally:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if sys.platform == "win32":
                    # taskkill returns before Windows always releases the
                    # bootloader child's mapped executable. Wait for that
                    # handle to close before TemporaryDirectory removes it.
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            executable.unlink()
                            break
                        except PermissionError:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(0.1)


if __name__ == "__main__":
    main()
