from __future__ import annotations

import logging
from pathlib import Path

from typerx.persistence.store import AppStore


def _configure_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(data_dir / "typerx.log", encoding="utf-8")],
    )
    # Network libraries must not log chat text or authentication details.
    logging.getLogger("telethon").setLevel(logging.WARNING)


def run() -> int:
    store = AppStore.default()
    _configure_logging(store.data_dir)
    logging.info("TyperX backend ready")
    return 0
