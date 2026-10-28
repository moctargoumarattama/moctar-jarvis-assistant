"""
SQLite-backed persistent store for scheduled tasks.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

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
    last_sent   TEXT    DEFAULT NULL,
    occurrence_key TEXT DEFAULT NULL,
    execution_id TEXT DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS scheduler_executions (
    execution_id TEXT PRIMARY KEY,
    task_id INTEGER NOT NULL,
    occurrence_key TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT DEFAULT NULL,
    detail TEXT NOT NULL DEFAULT '',
    UNIQUE(task_id, occurrence_key)
);
"""

REQUIRED_COLUMNS = {
    "media_path": "TEXT NOT NULL DEFAULT ''",
    "media_type": "TEXT NOT NULL DEFAULT ''",
    "media_paths_json": "TEXT NOT NULL DEFAULT '[]'",
    "media_types_json": "TEXT NOT NULL DEFAULT '[]'",
    "last_attempt_at": "TEXT DEFAULT NULL",
    "execution_note": "TEXT DEFAULT NULL",
    "occurrence_key": "TEXT DEFAULT NULL",
    "execution_id": "TEXT DEFAULT NULL",
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
            try:
                conn.execute(
                    f"ALTER TABLE scheduled_tasks ADD COLUMN {column_name} {column_sql}"
                )
            except sqlite3.OperationalError:
                # Two workers can open an old database simultaneously. A column
                # added by the other migration is safe; any other error isn't.
                current = {row[1] for row in conn.execute("PRAGMA table_info(scheduled_tasks)")}
                if column_name not in current:
                    raise


def _get_conn() -> sqlite3.Connection:
    config.ensure_runtime_directories()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
    except Exception:
        conn.close()
        raise
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
        "last_attempt_at",
        "execution_note",
        "occurrence_key",
        "execution_id",
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


def occurrence_key_for(task: dict, now: datetime | None = None) -> str:
    """Identify the scheduled local calendar slot, not the confirmation time.

    A late confirmation keeps the key assigned when the task was queued. Weekly
    tasks use the latest configured weekday; daily tasks use today's slot, even
    when explicitly confirmed before their configured hour.
    """
    reference = now or datetime.now()
    slot = reference
    if str(task.get("frequency") or "daily").lower() == "weekly":
        weekday = task.get("weekday")
        if weekday is not None:
            slot -= timedelta(days=(reference.weekday() - int(weekday)) % 7)
    send_time = str(task.get("send_time") or "09:00")
    return f"{int(task['id'])}:{slot.date().isoformat()}T{send_time}"


def _seed_previous_delivery(conn: sqlite3.Connection, task: dict) -> None:
    """Import a pre-migration last_sent without rewriting or losing task data."""
    if not task.get("last_sent") or task.get("execution_id") or task.get("occurrence_key"):
        return
    try:
        delivered_at = datetime.fromisoformat(task["last_sent"])
    except (TypeError, ValueError):
        return
    key = occurrence_key_for(task, delivered_at)
    conn.execute(
        """INSERT OR IGNORE INTO scheduler_executions
           (execution_id, task_id, occurrence_key, status, started_at, finished_at, detail)
           VALUES (?, ?, ?, 'completed', ?, ?, ?)""",
        (str(uuid.uuid4()), task["id"], key, task["last_sent"], task["last_sent"],
         "Livraison historique importee depuis last_sent."),
    )


def _seed_legacy_interruption(conn: sqlite3.Connection, task: dict, key: str) -> None:
    """Retain interrupted attempts recorded before occurrence tracking existed."""
    if task.get("execution_id") or not task.get("last_attempt_at"):
        return
    if task.get("status") not in ("awaiting_confirmation", "failed", "error", "interrupted_uncertain"):
        return
    execution_id = str(uuid.uuid4())
    conn.execute(
        """INSERT OR IGNORE INTO scheduler_executions
           (execution_id, task_id, occurrence_key, status, started_at, detail)
           VALUES (?, ?, ?, 'interrupted_uncertain', ?, ?)""",
        (execution_id, task["id"], key, task["last_attempt_at"],
         task.get("execution_note") or "Ancienne execution interrompue : verification requise."),
    )
    execution = conn.execute(
        "SELECT execution_id FROM scheduler_executions WHERE task_id=? AND occurrence_key=?",
        (task["id"], key),
    ).fetchone()
    conn.execute(
        "UPDATE scheduled_tasks SET occurrence_key=?, execution_id=? WHERE id=?",
        (key, execution["execution_id"], task["id"]),
    )
    task.update(occurrence_key=key, execution_id=execution["execution_id"])


def queue_task_occurrence(task_id: int, now: datetime | None = None) -> bool:
    """Queue one calendar slot atomically, never queue an already claimed slot."""
    with _conn_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None or row["status"] != "active":
            return False
        task = _row_to_dict(row)
        key = occurrence_key_for(task, now)
        _seed_previous_delivery(conn, task)
        exists = conn.execute(
            "SELECT 1 FROM scheduler_executions WHERE task_id=? AND occurrence_key=?",
            (task_id, key),
        ).fetchone()
        if exists:
            return False
        conn.execute(
            """UPDATE scheduled_tasks SET status='pending_confirmation',
               occurrence_key=?, execution_id=NULL, execution_note=NULL WHERE id=?""",
            (key, task_id),
        )
        return True


def claim_task_execution(task_id: int, now: datetime | None = None) -> tuple[bool, dict | None, str]:
    """Commit the occurrence and UUID before any external side effect.

    The unique constraint is the durable arbiter across workers and restarts.
    Database errors propagate; bypassing the database would permit a duplicate.
    """
    reference = now or datetime.now()
    now_iso = reference.isoformat(timespec="seconds")
    with _conn_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return False, None, f"Tache {task_id} introuvable."
        task = _row_to_dict(row)
        status = str(task.get("status") or "").lower()
        if status in ("processing", "awaiting_manual_action", "completed", "sent", "confirmed"):
            return False, task, f"Doublon ignore (idempotent) : tache {task_id} {status}."
        if status not in ("active", "pending", "pending_confirmation", "awaiting_confirmation",
                          "scheduled", "failed", "error", "interrupted_uncertain"):
            return False, task, f"Tache {task_id} non executable (statut={status})."

        _seed_previous_delivery(conn, task)
        key = task.get("occurrence_key") if status != "active" else None
        if not key and status != "active" and task.get("last_attempt_at"):
            try:
                key = occurrence_key_for(task, datetime.fromisoformat(task["last_attempt_at"]))
            except (TypeError, ValueError):
                pass
        key = key or occurrence_key_for(task, reference)
        _seed_legacy_interruption(conn, task, key)
        previous = conn.execute(
            "SELECT * FROM scheduler_executions WHERE task_id=? AND occurrence_key=?",
            (task_id, key),
        ).fetchone()
        if previous:
            if previous["status"] in ("completed", "awaiting_manual_action", "processing"):
                return False, task, (
                    f"Doublon ignore (idempotent) : occurrence {key} deja reservee "
                    f"({previous['status']})."
                )
            return False, task, (
                f"Verification requise : occurrence {key} deja tentee ({previous['status']}). "
                "Aucun renvoi automatique. Verifiez la livraison puis marquez-la envoyee ; "
                "une nouvelle tentative necessite une nouvelle tache explicite."
            )

        execution_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO scheduler_executions
               (execution_id, task_id, occurrence_key, status, started_at)
               VALUES (?, ?, ?, 'processing', ?)""",
            (execution_id, task_id, key, now_iso),
        )
        conn.execute(
            """UPDATE scheduled_tasks SET status='processing', last_attempt_at=?,
               occurrence_key=?, execution_id=?, execution_note='Reserve pour envoi' WHERE id=?""",
            (now_iso, key, execution_id, task_id),
        )
        task.update(status="processing", last_attempt_at=now_iso,
                    occurrence_key=key, execution_id=execution_id)
        return True, task, ""


def finish_task_execution(
    task_id: int, execution_id: str, status: str, detail: str = "", now: datetime | None = None,
) -> None:
    """Record the external result and task state in one committed transaction."""
    state_map = {
        "completed": "active", "awaiting_manual_action": "awaiting_manual_action",
        "failed": "failed", "interrupted_uncertain": "awaiting_confirmation",
    }
    if status not in state_map:
        raise ValueError(f"Etat d'execution inconnu : {status}")
    now_iso = (now or datetime.now()).isoformat(timespec="seconds")
    with _conn_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        result = conn.execute(
            """UPDATE scheduler_executions SET status=?, finished_at=?, detail=?
               WHERE task_id=? AND execution_id=? AND status='processing'""",
            (status, now_iso, detail, task_id, execution_id),
        )
        if result.rowcount != 1:
            raise RuntimeError("Reservation d'execution absente ou deja terminee.")
        conn.execute(
            """UPDATE scheduled_tasks SET status=?, execution_note=?,
               last_sent=CASE WHEN ?='completed' THEN ? ELSE last_sent END
               WHERE id=? AND execution_id=?""",
            (state_map[status], detail, status, now_iso, task_id, execution_id),
        )


def recover_stale_execution(
    task_id: int, max_age_seconds: float = 300.0, now: datetime | None = None,
) -> bool:
    """Freeze an abandoned lease without allowing its occurrence to run again."""
    reference = now or datetime.now()
    with _conn_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None or row["status"] != "processing":
            return False
        task = _row_to_dict(row)
        attempted_at = None
        try:
            attempted_at = datetime.fromisoformat(task.get("last_attempt_at") or "")
            if (reference - attempted_at).total_seconds() <= max_age_seconds:
                return False
        except (TypeError, ValueError):
            pass
        key = task.get("occurrence_key") or occurrence_key_for(task, attempted_at or reference)
        execution_id = task.get("execution_id") or str(uuid.uuid4())
        now_iso = reference.isoformat(timespec="seconds")
        note = (
            "Execution precedente interrompue (>300s). Verification de la livraison requise ; "
            "cette occurrence ne sera jamais renvoyee automatiquement."
        )
        conn.execute(
            """INSERT OR IGNORE INTO scheduler_executions
               (execution_id, task_id, occurrence_key, status, started_at, detail)
               VALUES (?, ?, ?, 'interrupted_uncertain', ?, ?)""",
            (execution_id, task_id, key, task.get("last_attempt_at") or now_iso, note),
        )
        conn.execute(
            """UPDATE scheduler_executions SET status='interrupted_uncertain',
               finished_at=?, detail=? WHERE task_id=? AND occurrence_key=? AND status='processing'""",
            (now_iso, note, task_id, key),
        )
        execution = conn.execute(
            "SELECT execution_id FROM scheduler_executions WHERE task_id=? AND occurrence_key=?",
            (task_id, key),
        ).fetchone()
        conn.execute(
            """UPDATE scheduled_tasks SET status='awaiting_confirmation', occurrence_key=?,
               execution_id=?, execution_note=? WHERE id=?""",
            (key, execution["execution_id"], note, task_id),
        )
        return True


def mark_execution_manually_sent(task_id: int, now: datetime | None = None) -> tuple[bool, str]:
    """Preserve the manual-finalization workflow, including uncertain deliveries."""
    reference = now or datetime.now()
    now_iso = reference.isoformat(timespec="seconds")
    with _conn_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return False, f"Tache {task_id} introuvable."
        task = _row_to_dict(row)
        if task["status"] == "processing":
            return False, "Tache en cours : finalisation manuelle indisponible."
        key = task.get("occurrence_key") or occurrence_key_for(task, reference)
        execution_id = task.get("execution_id") or str(uuid.uuid4())
        note = "Tache marquee comme envoyee apres verification manuelle."
        conn.execute(
            """INSERT OR IGNORE INTO scheduler_executions
               (execution_id, task_id, occurrence_key, status, started_at, finished_at, detail)
               VALUES (?, ?, ?, 'completed', ?, ?, ?)""",
            (execution_id, task_id, key, now_iso, now_iso, note),
        )
        conn.execute(
            """UPDATE scheduler_executions SET status='completed', finished_at=?, detail=?
               WHERE task_id=? AND occurrence_key=?""",
            (now_iso, note, task_id, key),
        )
        execution = conn.execute(
            "SELECT execution_id FROM scheduler_executions WHERE task_id=? AND occurrence_key=?",
            (task_id, key),
        ).fetchone()
        conn.execute(
            """UPDATE scheduled_tasks SET status='active', last_sent=?, execution_note=?,
               occurrence_key=?, execution_id=? WHERE id=?""",
            (now_iso, note, key, execution["execution_id"], task_id),
        )
    return True, "Tache marquee comme envoyee."


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
