"""
Make stdout safe for the characters these scripts actually print.

Every script here prints box-drawing rules, arrows and a Δ or two. On Windows the
console defaults to cp1252, which cannot encode any of them, so the script dies
with a UnicodeEncodeError *after* doing all its work — the acceptance suite
passes and then crashes on the way to saying so.

Importing this module reconfigures stdout/stderr to UTF-8 with replacement, so a
console that genuinely cannot render a glyph prints a placeholder instead of
taking the run down with it.
"""
from __future__ import annotations

import sys


def init() -> None:
    for stream in (sys.stdout, sys.stderr):
        # Absent when output is piped through something that isn't a TextIOWrapper.
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:                       # noqa: BLE001
                pass


init()
