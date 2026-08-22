"""Start PatentsGrabber locally. One process, no build step.

    python run.py            -> http://127.0.0.1:8000
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("patentsgrabber.app:app", host="127.0.0.1", port=8000, log_level="info")
