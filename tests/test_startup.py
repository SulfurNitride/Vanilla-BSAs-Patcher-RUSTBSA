"""Check the first browser request against a deliberately delayed Eel server."""

import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def test_browser_first_request_succeeds_after_delayed_server_start(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix="patcher startup ") as temp:
            result = Path(temp) / "browser.json"
            # Run Eel in a separate process so its global routes/greenlets cannot
            # leak between tests. The browser callback makes exactly one request.
            script = f'''
import importlib.util
import json
from pathlib import Path
import time
import urllib.request
spec = importlib.util.spec_from_file_location("patcher", {str(ROOT / "Vanilla BSAs Patcher.py")!r})
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)
def browser(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            result = dict(status=response.status, body=response.read().decode())
    except Exception as error:
        result = dict(error=str(error))
    output = Path({str(result)!r})
    staging = output.with_suffix(".tmp")
    staging.write_text(json.dumps(result), encoding="utf-8")
    staging.replace(output)
ready = patcher.open_browser_when_ready
patcher.open_browser_when_ready = lambda *args: ready(*args, opener=browser)
start = patcher.eel.start
def delayed_start(*args, **kwargs):
    time.sleep(0.3)
    start(*args, **kwargs)
patcher.eel.start = delayed_start
patcher.launch_app(port={port})
'''
            with (Path(temp) / "app.log").open("w+") as log:
                process = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 15
                    while not result.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    log.seek(0)
                    self.assertTrue(result.exists(), log.read())
                    response = json.loads(result.read_text(encoding="utf-8"))
                    self.assertEqual(response.get("status"), 200, response)
                    self.assertIn("BSAs at once", response["body"])
                    self.assertIn("/eel.js", response["body"])
                finally:
                    # Windows virtualenv Python is a launcher with a child
                    # process; terminate the whole test-owned tree.
                    if sys.platform == "win32":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    unittest.main()
