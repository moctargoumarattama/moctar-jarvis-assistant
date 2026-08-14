"""
SQLite-backed persistent store for scheduled tasks.
Each task has: id, title, platform, target (group/page/number),
message, frequency (daily/weekly), weekday (0=Mon..6=Sun),
send_time (HH:MM), status (pending/confirmed/sent/error),
ai_enhanced, created_at, last_sent.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger("jarvis.scheduler")

DB_PATH = config.DATA_DIR / "scheduler.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    platform    TEXT    NOT NULL,
    target      TEXT    NOT NULL DEFAULT '',
    message     TEXT    NOT NULL,
    frequency   TEXT    NOT NULL DEFAULT 'daily',
    weekday     INTEGER DEFAULT NULL,
    send_time   TEXT    NOT NULL DEFAULT '09:00',
    status      TEXT    NOT NULL DEFAULT 'active',
    ai_enhanced INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    last_sent   TEXT    DEFAULT NULL
);
"""


def _get_conn() -> sqlite3.Connection:
    config.ensure_runtime_directories()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["ai_enhanced"] = bool(d.get("ai_enhanced"))
    return d


# --------------------------------------------------------------------------- #
#  CRUD helpers                                                                #
# --------------------------------------------------------------------------- #

def create_task(
    title: str,
    platform: str,
    target: str,
    message: str,
    frequency: str = "daily",
    weekday: int | None = None,
    send_time: str = "09:00",
    ai_enhanced: bool = False,
) -> dict:
    """Insert a new scheduled task and return it."""
    now = datetime.now().isoformat(timespec="seconds")
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO scheduled_tasks
               (title, platform, target, message, frequency, weekday,
                send_time, status, ai_enhanced, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                title.strip(),
                platform.lower().strip(),
                target.strip(),
                message.strip(),
                frequency.lower().strip(),
                weekday,
                send_time.strip(),
                "active",
                1 if ai_enhanced else 0,
                now,
            ),
        )
        task_id = cur.lastrowid
    return get_task(task_id)


def get_task(task_id: int) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_tasks(status: str | None = None) -> list[dict]:
    with _get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE status=? ORDER BY send_time",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks ORDER BY send_time"
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_task(task_id: int, **kwargs) -> dict | None:
    """Update any subset of task fields."""
    allowed = {
        "title", "platform", "target", "message",
        "frequency", "weekday", "send_time", "status",
        "ai_enhanced", "last_sent",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return get_task(task_id)
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [task_id]
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE scheduled_tasks SET {set_clause} WHERE id=?", values
        )
    return get_task(task_id)


def delete_task(task_id: int) -> bool:
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM scheduled_tasks WHERE id=?", (task_id,)
        )
    return cur.rowcount > 0


def get_due_tasks(now: datetime | None = None) -> list[dict]:
    """Return active tasks whose send_time matches the current HH:MM."""
    if now is None:
        now = datetime.now()
    current_time = now.strftime("%H:%M")
    current_weekday = now.weekday()  # 0=Mon
    tasks = list_tasks(status="active")
    due = []
    for task in tasks:
        if task["send_time"] != current_time:
            continue
        if task["frequency"] == "weekly":
            if task["weekday"] is None:
                continue
            if int(task["weekday"]) != current_weekday:
                continue
        due.append(task)
    return due
