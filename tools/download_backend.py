"""Download the latest official CLI release to the patcher's user cache."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive_backend import ensure_latest_backend

if __name__ == "__main__":
    print(ensure_latest_backend())
