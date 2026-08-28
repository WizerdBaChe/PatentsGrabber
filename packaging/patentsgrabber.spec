# -*- mode: python ; coding: utf-8 -*-
"""One folder, one executable.

**onedir, not onefile.** A onefile binary unpacks its whole payload into
`%TEMP%\\_MEIxxxxxx` on EVERY launch and removes it on a graceful exit — but this
program's off switch is the console window's X button, which is not a graceful
exit, and the orphaned directories then accumulate forever. Nothing can sweep
them afterwards either: `_MEI*` is the name every PyInstaller onefile program
uses, so deleting them would delete other programs' state. onedir does not
extract at all, so the leak has no source, and it starts faster for the same
reason. The cost is that the deliverable is a folder rather than a file — which
is why the release ships a zip with exactly one thing to double-click inside it.

Build from the project root:

    python -m PyInstaller --noconfirm --clean ^
        --distpath build\\dist --workpath build\\work ^
        packaging\\patentsgrabber.spec
"""

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
SRC = os.path.join(ROOT, "src")

# THE RULE FOR THIS LIST: only modules resolved BY STRING at runtime, which
# PyInstaller's static analysis cannot see. Anything reached by an ordinary
# import statement — including one inside a function body, which is how Pillow
# and pypdf are reached — is found on its own and must NOT be listed here.
#
# uvicorn picks all of these out of configuration strings during `Config.load()`,
# before a single request arrives, so their absence is a startup crash rather
# than a lost capability. `websockets.auto` is included even though this program
# serves no websockets: uvicorn's default `ws="auto"` imports it regardless.
HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

# The page is the product's entire interface, and `paths.web_dir()` reads exactly
# this location when `sys.frozen` is set.
DATAS = [
    (os.path.join(SRC, "patentsgrabber", "web"), os.path.join("patentsgrabber", "web")),
    (os.path.join(ROOT, "README.md"), "."),
    (os.path.join(ROOT, ".env.example"), "."),
]

# Test frameworks and the gates' Chrome driver have no business in a release.
# Everything here is verified absent from the runtime path by
# `tools/check_release.py`, which starts the packaged build and drives it —
# an over-eager exclusion shows up only at run time, never at build time.
EXCLUDES = ["pytest", "_pytest", "websockets", "tkinter", "matplotlib", "numpy", "IPython"]

analysis = Analysis(
    [os.path.join(SRC, "patentsgrabber", "__main__.py")],
    pathex=[SRC],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PatentsGrabber",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console on purpose: this window is the program's off switch, and it is
    # where a startup failure is readable. See launcher.py.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PatentsGrabber",
)
