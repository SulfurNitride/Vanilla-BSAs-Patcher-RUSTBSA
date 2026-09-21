"""Check the frozen app's first browser request and first backend download."""

import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def send_message(connection, message):
    payload = json.dumps(message).encode()
    size = len(payload)
    header = bytes([0x81, 0x80 | size]) if size < 126 else bytes([0x81, 0xFE]) + struct.pack("!H", size)
    mask = os.urandom(4)
    connection.sendall(header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))


def receive_message(connection):
    def read(size):
        data = b""
        while len(data) < size:
            chunk = connection.recv(size - len(data))
            if not chunk:
                raise RuntimeError("The packaged app closed the connection during its backend download")
            data += chunk
        return data

    header = read(2)
    size = header[1] & 0x7F
    if size == 126:
        size = struct.unpack("!H", read(2))[0]
    elif size == 127:
        size = struct.unpack("!Q", read(8))[0]
    return json.loads(read(size))


def check_backend_download(temp):
    with socket.create_connection(("127.0.0.1", 8000), timeout=120) as connection:
        connection.sendall(
            b"GET /eel?page=index.html HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        headers = b""
        while not headers.endswith(b"\r\n\r\n"):
            chunk = connection.recv(1)
            if not chunk:
                raise RuntimeError("WebSocket handshake failed")
            headers += chunk
        assert b"101" in headers, headers
        # Installation happens before Data-folder validation. An absent folder
        # exercises the real frozen downloader without requiring game assets.
        send_message(connection, {"call": 1, "name": "start_processing", "args": [str(temp / "missing Data"), "", {}]})
        logs = []
        while True:
            message = receive_message(connection)
            if "call" in message:
                name, args = message["name"], message["args"]
                if name == "addLog":
                    logs.extend(args)
                send_message(connection, {"return": message["call"], "status": "ok", "value": None})
                if name == "processFinished":
                    assert args == [False], args
                    break
            else:
                assert message.get("status") == "ok", message
        assert any("downloaded and verified" in line for line in logs), "\n".join(logs)
        assert any("Game Data folder not found" in line for line in logs), "\n".join(logs)
    cache = temp / "cache/vanilla-bsas-patcher/backend"
    record = json.loads((cache / "current.json").read_text())
    name = "bsa-ba2-tool.exe" if sys.platform == "win32" else "bsa-ba2-tool"
    assert (cache / record["tag"] / name).is_file()
    print(f"Packaged app downloaded and verified backend {record['tag']} from an empty cache.")


def main():
    with socket.socket() as reservation:
        if sys.platform != "win32":
            reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
        environment = dict(os.environ,
                           BROWSER=f'"{Path(sys.executable).as_posix()}" "{browser.as_posix()}" %s',
                           XDG_CACHE_HOME=str(temp / "cache"), LOCALAPPDATA=str(temp / "cache"),
                           # Exercise the bundled CA roots even on the build
                           # host, where OpenSSL's default paths normally work.
                           SSL_CERT_FILE=str(temp / "missing-ca.pem"),
                           SSL_CERT_DIR=str(temp / "missing-ca-directory"))
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
                check_backend_download(temp)
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
