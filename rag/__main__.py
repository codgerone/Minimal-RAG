"""Windows-safe module entry point."""

import sys

from rag.cli import main


def _make_console_output_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


_make_console_output_safe()
raise SystemExit(main())

