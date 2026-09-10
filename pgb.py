"""Run PatentsGrabber from the command line. Same program, no browser.

    python pgb.py doctor
    python pgb.py lookup US20250383260A1 --json
    python pgb.py figures US20250383260A1 --pages 1-3 --json

This mirrors `run.py`: a two-line shim so ONE absolute path works from any
working directory, which is what a caller outside this repository needs. The
CLI itself is `src/patentsgrabber/cli.py` — this file must stay thin enough
that nothing can be true of it and false of `python -m patentsgrabber.cli`.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from patentsgrabber.cli import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
