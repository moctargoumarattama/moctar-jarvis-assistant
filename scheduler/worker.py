"""
Standalone scheduler worker.

Run with:
    python -m scheduler.worker
"""

from __future__ import annotations

import logging

import config
from scheduler import scheduler_core


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    config.ensure_runtime_directories()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(config.LOGS_DIR / "scheduler-worker.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    configure_logging()
    logging.getLogger("jarvis.scheduler.worker").info(
        "Scheduler worker starting with poll interval %ss",
        config.SCHEDULER_SETTINGS["poll_seconds"],
    )
    try:
        scheduler_core.serve_forever(config.SCHEDULER_SETTINGS["poll_seconds"])
    except KeyboardInterrupt:
        logging.getLogger("jarvis.scheduler.worker").info("Scheduler worker stopped")


if __name__ == "__main__":
    main()
