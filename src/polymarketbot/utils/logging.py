"""Logging helpers."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler


def setup_logging(log_dir: str | Path = "logs", level: int = logging.INFO) -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("polymarketbot")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    console = RichHandler(rich_tracebacks=True, markup=True, show_path=False)
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        Path(log_dir) / "bot.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.addHandler(file_handler)
    return logger


def get_logger(name: str = "polymarketbot") -> logging.Logger:
    return logging.getLogger(name)

