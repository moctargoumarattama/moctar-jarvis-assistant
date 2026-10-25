"""
Background scheduler logic.

The scheduler promotes due tasks to `pending_confirmation`.
Confirmed tasks can either be fully sent or moved to
`awaiting_manual_action` when the platform still needs a final human click.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

import config
from scheduler import platforms
from scheduler import scheduler_store as store

logger = logging.getLogger("jarvis.scheduler")

_lock = threading.Lock()
_dispatch_lock = threading.Lock()
_in_flight_tasks: set[int] = set()
_recent_dispatches: dict[int, tuple[float, str]] = {}
_IDEMPOTENCY_TTL_SECONDS = 30.0
_stop_event = threading.Event()
_thread: threading.Thread | None = None


def enhance_message_with_ai(
    message: str,
    platform: str,
    title: str = "",
    media_paths=None,
) -> str:
    """
    Use the project's LocalBrain / Gemini Cloud path to polish the message.
    Falls back to the original message if AI is unavailable.
    """
    try:
        import ai_brain

        brain = ai_brain.LocalBrain()
        result = brain.enhance_social_post(
            message,
            platform=platform,
            title=title,
            media_paths=media_paths,
        )
        if result and result.strip():
            return result.strip()
    except Exception as exc:
        logger.warning("AI enhance failed: %s", exc)
    return message


def _configured_poll_seconds() -> int:
    return max(int(config.SCHEDULER_SETTINGS.get("poll_seconds", 60)), 5)


def _check_and_queue(now: datetime | None = None) -> None:
    """Mark due tasks as pending confirmation with atomic reservation."""
    due_tasks = store.get_due_tasks(now=now)
    current_time = now or datetime.now()
    for task in due_tasks:
        tid = int(task["id"])
        with _dispatch_lock:
            if tid in _in_flight_tasks:
                continue
        last_sent = task.get("last_sent")
        if last_sent:
            try:
                last_sent_dt = datetime.fromisoformat(last_sent)
                if (current_time - last_sent_dt).total_seconds() < 60:
                    continue
            except Exception:
                pass
        logger.info("Task due: [%s] %s -> %s", tid, task["title"], task["platform"])
        store.update_task(tid, status="pending_confirmation")


def is_task_in_flight(task_id: int) -> bool:
    """Return True if task_id is currently being confirmed or dispatched."""
    with _dispatch_lock:
        return int(task_id) in _in_flight_tasks


def _reserve_task_atomically(task_id: int) -> tuple[bool, dict | None, str]:
    """ Atomically claim a task for execution via Lock + SQLite conditional UPDATE.

    Returns:
        (claimed, task_dict, idempotent_or_error_message)
    """
    tid = int(task_id)
    now_mono = time.monotonic()

    with _dispatch_lock:
        # 1. Vérification du verrou en vol (in-flight)
        if tid in _in_flight_tasks:
            return False, store.get_task(tid), f"Tache {tid} deja en cours de traitement (processing)."

        # 2. Vérification du registre d'idempotence récent (anti double-clic / rejeu)
        recent = _recent_dispatches.get(tid)
        if recent is not None:
            sent_mono, prev_detail = recent
            if (now_mono - sent_mono) < _IDEMPOTENCY_TTL_SECONDS:
                current_t = store.get_task(tid)
                if current_t and str(current_t.get("status") or "").lower() not in (
                    "pending_confirmation",
                    "awaiting_confirmation",
                    "pending",
                    "scheduled",
                ):
                    return False, current_t, f"Doublon ignore (idempotent) : {prev_detail}"

        task = store.get_task(tid)
        if task is None:
            return False, None, f"Tache {tid} introuvable."

        current_status = str(task.get("status") or "").strip().lower()
        if current_status == "processing":
            return False, task, f"Tache {tid} deja en cours de traitement (processing)."
        if current_status in ("completed", "sent", "confirmed"):
            return False, task, f"Tache {tid} deja executee ({current_status})."

        # 3. Transition atomique SQLite : UPDATE ... SET status = 'processing' WHERE id = ? AND status IN (...)
        if hasattr(store, "_conn_scope"):
            try:
                with store._conn_scope() as conn:
                    cur = conn.execute(
                        """UPDATE scheduled_tasks
                           SET status = 'processing'
                           WHERE id = ?
                             AND status IN (
                                 'pending',
                                 'awaiting_confirmation',
                                 'pending_confirmation',
                                 'scheduled',
                                 'active',
                                 'failed',
                                 'error'
                             )""",
                        (tid,),
                    )
                    if cur.rowcount == 0:
                        existing_row = conn.execute(
                            "SELECT status FROM scheduled_tasks WHERE id = ?",
                            (tid,),
                        ).fetchone()
                        if existing_row is not None:
                            db_status = str(existing_row[0] or "").strip().lower()
                            return (
                                False,
                                task,
                                f"Tache {tid} deja reservee ou traitee (statut={db_status}).",
                            )
            except Exception as db_exc:
                logger.debug("Transition SQLite directe ignoree (fallback store.update_task): %s", db_exc)

        store.update_task(tid, status="processing")
        _in_flight_tasks.add(tid)
        return True, task, ""


def run_once(now: datetime | None = None) -> None:
    with _lock:
        _check_and_queue(now=now)


def _sleep_with_stop(total_seconds: float) -> None:
    deadline = time.monotonic() + max(total_seconds, 0)
    while not _stop_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


def _scheduler_loop(poll_seconds: int) -> None:
    logger.info("Scheduler thread started.")
    while not _stop_event.is_set():
        try:
            run_once()
        except Exception as exc:
            logger.error("Scheduler loop error: %s", exc)
        _sleep_with_stop(poll_seconds)
    logger.info("Scheduler thread stopped.")


def serve_forever(poll_seconds: int | None = None) -> None:
    poll_seconds = max(int(poll_seconds or _configured_poll_seconds()), 5)
    _stop_event.clear()
    _scheduler_loop(poll_seconds)


def start(poll_seconds: int | None = None) -> None:
    """Start the background scheduler thread."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    resolved_poll_seconds = max(int(poll_seconds or _configured_poll_seconds()), 5)
    _thread = threading.Thread(
        target=_scheduler_loop,
        args=(resolved_poll_seconds,),
        daemon=True,
        name="jarvis-scheduler",
    )
    _thread.start()


def stop(join_timeout: float = 2) -> None:
    """Stop the background scheduler thread."""
    global _thread
    _stop_event.set()
    if _thread and _thread.is_alive() and _thread is not threading.current_thread():
        _thread.join(timeout=join_timeout)


def confirm_and_send(task_id: int) -> tuple[bool, str]:
    """
    Atomically reserve and execute a scheduled task once, preventing duplicate dispatches.
    """
    tid = int(task_id)
    claimed, task, reservation_msg = _reserve_task_atomically(tid)
    if not claimed:
        if task is None:
            return False, reservation_msg
        logger.info("confirm_and_send(%s) idempotent skip: %s", tid, reservation_msg)
        return True, reservation_msg

    try:
        message = task["message"]
        if task.get("ai_enhanced"):
            message = enhance_message_with_ai(
                message,
                task["platform"],
                task["title"],
                task.get("media_paths", []),
            )

        result = platforms.dispatch(
            task["platform"],
            task["target"],
            message,
            task.get("media_paths", []),
        )
        now_iso = datetime.now().isoformat(timespec="seconds")
        if result.is_final:
            store.update_task(tid, status="active", last_sent=now_iso)
            with _dispatch_lock:
                _recent_dispatches[tid] = (time.monotonic(), result.detail)
            return True, result.detail

        if result.requires_manual_action:
            store.update_task(tid, status="awaiting_manual_action")
            with _dispatch_lock:
                _recent_dispatches[tid] = (time.monotonic(), result.detail)
            return True, result.detail

        fail_reason = str(result.detail or "Erreur d'envoi inconnue").strip()
        logger.error("Echec d'envoi de la tache planifiee [%s]: %s", tid, fail_reason)
        store.update_task(tid, status="failed")
        return False, fail_reason
    except Exception as exc:
        fail_reason = f"Erreur d'execution de la tache {tid}: {exc}"
        logger.error("Exception dans confirm_and_send(%s): %s", tid, exc, exc_info=True)
        store.update_task(tid, status="failed")
        return False, fail_reason
    finally:
        with _dispatch_lock:
            _in_flight_tasks.discard(tid)


def mark_task_sent(task_id: int) -> tuple[bool, str]:
    """
    Mark a manually finalized task as sent with atomic lock protection.
    """
    tid = int(task_id)
    with _dispatch_lock:
        task = store.get_task(tid)
        if task is None:
            return False, f"Tache {tid} introuvable."
        now_iso = datetime.now().isoformat(timespec="seconds")
        store.update_task(tid, status="active", last_sent=now_iso)
        _recent_dispatches[tid] = (time.monotonic(), "Tache marquee comme envoyee.")
    return True, "Tache marquee comme envoyee."


def get_pending_tasks() -> list[dict]:
    return store.list_tasks(status="pending_confirmation")


def get_manual_action_tasks() -> list[dict]:
    return store.list_tasks(status="awaiting_manual_action")
