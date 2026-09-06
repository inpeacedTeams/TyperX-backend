"""CLI entry point. No frontend or HTTP server is started."""
from __future__ import annotations


def run() -> int:
    from typerx.headless import main
    return main()
