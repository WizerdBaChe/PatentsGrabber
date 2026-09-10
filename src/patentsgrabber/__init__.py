"""PatentsGrabber — integrated patent reading tool (Stage 0: US, zero-credential)."""

# The source of truth is `app.py`'s VERSION line — `packaging/build.ps1` reads
# that literal to name the release, so it cannot move. This is the copy the CLI
# can read without importing FastAPI (importing `app` would also open the
# library, which a command-line process must not do twice). The two are pinned
# together by `tools/check_cli.py`; they were already 0.1.0 vs 1.0.0 apart when
# that check was written, which is the whole argument for having it.
__version__ = "1.0.0"
