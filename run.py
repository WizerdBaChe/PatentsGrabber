"""Start PatentsGrabber locally. One process, no build step.

    python run.py            -> http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    # 8000 by default; PORT lets a second copy run alongside one you already
    # started (handy when checking a change without stopping your own server).
    port = int(os.environ.get("PORT") or 8000)
    uvicorn.run("patentsgrabber.app:app", host="127.0.0.1", port=port, log_level="info")
