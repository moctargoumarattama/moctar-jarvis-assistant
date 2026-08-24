"""
SQLite-backed persistent store for scheduled tasks.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import sqlite3
from contextlib import contextmanager
from datetime import datetime

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
    media_path  TEXT    NOT NULL DEFAULT '',
    media_type  TEXT    NOT NULL DEFAULT '',
    media_paths_json TEXT NOT NULL DEFAULT '[]',
    media_types_json TEXT NOT NULL DEFAULT '[]',
    frequency   TEXT    NOT NULL DEFAULT 'daily',
    weekday     INTEGER DEFAULT NULL,
    send_time   TEXT    NOT NULL DEFAULT '09:00',
    status      TEXT    NOT NULL DEFAULT 'active',
    ai_enhanced INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    last_sent   TEXT    DEFAULT NULL
);
"""

REQUIRED_COLUMNS = {
    "media_path": "TEXT NOT NULL DEFAULT ''",
    "media_type": "TEXT NOT NULL DEFAULT ''",
    "media_paths_json": "TEXT NOT NULL DEFAULT '[]'",
    "media_types_json": "TEXT NOT NULL DEFAULT '[]'",
}


def infer_media_type(media_path: str) -> str:
    media_path = (media_path or "").strip()
    if not media_path:
        return ""

    guessed_type, _ = mimetypes.guess_type(media_path)
    if guessed_type:
        if guessed_type.startswith("image/"):
            return "image"
        if guessed_type.startswith("video/"):
            return "video"

    lowered = media_path.lower()
    if lowered.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")):
        return "image"
    if lowered.endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")):
        return "video"
    return "file"


def _normalize_media_paths(media_paths=None, media_path: str = "") -> list[str]:
    values = []
    seen = set()
    raw_items = []
    if media_paths:
        if isinstance(media_paths, str):
            raw_items = [media_paths]
        else:
            raw_items = list(media_paths)
    elif media_path:
        raw_items = [media_path]

    for item in raw_items:
        path = str(item or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        values.append(path)
    return values


def infer_media_types(media_paths) -> list[str]:
    return [infer_media_type(path) for path in _normalize_media_paths(media_paths)]


def _ensure_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(scheduled_tasks)").fetchall()
    existing_columns = {row[1] for row in rows}
    for column_name, column_sql in REQUIRED_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(
                f"ALTER TABLE scheduled_tasks ADD COLUMN {column_name} {column_sql}"
            )


def _get_conn() -> sqlite3.Connection:
    config.ensure_runtime_directories()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
    return conn


@contextmanager
def _conn_scope():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["ai_enhanced"] = bool(data.get("ai_enhanced"))
    data["media_path"] = data.get("media_path", "") or ""
    data["media_type"] = data.get("media_type", "") or ""
    try:
        media_paths = json.loads(data.get("media_paths_json", "[]") or "[]")
    except (TypeError, ValueError):
        media_paths = []
    try:
        media_types = json.loads(data.get("media_types_json", "[]") or "[]")
    except (TypeError, ValueError):
        media_types = []
    media_paths = _normalize_media_paths(media_paths, data["media_path"])
    if not media_types:
        media_types = infer_media_types(media_paths)
    data["media_paths"] = media_paths
    data["media_types"] = media_types
    return data


def create_task(
    title: str,
    platform: str,
    target: str,
    message: str,
    media_paths=None,
    media_path: str = "",
    media_type: str = "",
    frequency: str = "daily",
    weekday: int | None = None,
    send_time: str = "09:00",
    ai_enhanced: bool = False,
) -> dict:
    """Insert a new scheduled task and return it."""
    now = datetime.now().isoformat(timespec="seconds")
    normalized_media_paths = _normalize_media_paths(media_paths, media_path)
    normalized_media_path = normalized_media_paths[0] if normalized_media_paths else ""
    resolved_media_types = infer_media_types(normalized_media_paths)
    resolved_media_type = (
        (media_type or "").strip().lower()
        or (resolved_media_types[0] if resolved_media_types else "")
    )
    with _conn_scope() as conn:
        cur = conn.execute(
            """INSERT INTO scheduled_tasks
               (title, platform, target, message, media_path, media_type,
                media_paths_json, media_types_json, frequency, weekday,
                send_time, status, ai_enhanced, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                title.strip(),
                platform.lower().strip(),
                target.strip(),
                message.strip(),
                normalized_media_path,
                resolved_media_type,
                json.dumps(normalized_media_paths, ensure_ascii=True),
                json.dumps(resolved_media_types, ensure_ascii=True),
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
    with _conn_scope() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_tasks(status: str | None = None) -> list[dict]:
    with _conn_scope() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE status=? ORDER BY send_time",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks ORDER BY send_time"
            ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_task(task_id: int, **kwargs) -> dict | None:
    """Update any subset of task fields."""
    allowed = {
        "title",
        "platform",
        "target",
        "message",
        "media_paths",
        "media_path",
        "media_types",
        "media_type",
        "frequency",
        "weekday",
        "send_time",
        "status",
        "ai_enhanced",
        "last_sent",
    }
    fields = {key: value for key, value in kwargs.items() if key in allowed}
    if "media_paths" in fields:
        normalized_media_paths = _normalize_media_paths(fields["media_paths"])
        fields["media_paths_json"] = json.dumps(normalized_media_paths, ensure_ascii=True)
        media_types = infer_media_types(normalized_media_paths)
        fields["media_types_json"] = json.dumps(media_types, ensure_ascii=True)
        fields["media_path"] = normalized_media_paths[0] if normalized_media_paths else ""
        fields["media_type"] = media_types[0] if media_types else ""
        fields.pop("media_paths", None)
        fields.pop("media_types", None)
    if "media_path" in fields and "media_type" not in fields:
        normalized_media_paths = _normalize_media_paths(media_path=str(fields["media_path"]))
        media_types = infer_media_types(normalized_media_paths)
        fields["media_type"] = media_types[0] if media_types else ""
        fields["media_paths_json"] = json.dumps(normalized_media_paths, ensure_ascii=True)
        fields["media_types_json"] = json.dumps(media_types, ensure_ascii=True)
    if "media_type" in fields and "media_path" not in fields and "media_paths_json" not in fields:
        normalized_media_paths = _normalize_media_paths(media_path=get_task(task_id).get("media_path", ""))
        fields["media_paths_json"] = json.dumps(normalized_media_paths, ensure_ascii=True)
        fields["media_types_json"] = json.dumps(infer_media_types(normalized_media_paths), ensure_ascii=True)
    if not fields:
        return get_task(task_id)

    set_clause = ", ".join(f"{key}=?" for key in fields)
    values = list(fields.values()) + [task_id]
    with _conn_scope() as conn:
        conn.execute(
            f"UPDATE scheduled_tasks SET {set_clause} WHERE id=?",
            values,
        )
    return get_task(task_id)


def delete_task(task_id: int) -> bool:
    with _conn_scope() as conn:
        cur = conn.execute(
            "DELETE FROM scheduled_tasks WHERE id=?", (task_id,)
        )
    return cur.rowcount > 0


def get_due_tasks(now: datetime | None = None) -> list[dict]:
    """Return active tasks whose send_time matches the current HH:MM."""
    if now is None:
        now = datetime.now()
    current_time = now.strftime("%H:%M")
    current_weekday = now.weekday()
    tasks = list_tasks(status="active")
    due_tasks = []
    for task in tasks:
        if task["send_time"] != current_time:
            continue
        if task["frequency"] == "weekly":
            if task["weekday"] is None:
                continue
            if int(task["weekday"]) != current_weekday:
                continue
        due_tasks.append(task)
    return due_tasks
