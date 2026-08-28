"""Entry point for both `python -m patentsgrabber` and the packaged build.

One entry point, so the thing that gets tested and the thing that gets shipped
are the same code path — a launcher that only exists inside the executable is a
launcher nobody runs until it is too late to fix.

The import below is ABSOLUTE for a reason that cost a build: PyInstaller runs
its entry script as `__main__` with no package context, so `from .launcher`
raises `ImportError: attempted relative import with no known parent package` —
at run time, from a build that reported success. `tools/check_release.py` is
what caught it, and is what keeps catching it.
"""

from __future__ import annotations

import sys

from patentsgrabber.launcher import run

if __name__ == "__main__":
    sys.exit(run())
