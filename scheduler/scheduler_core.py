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
_start_lock = threading.Lock()
_in_flight_tasks: set[int] = set()
# Compatibility for older integrations; this cache is no longer authoritative
# or populated. SQLite occurrence reservations survive its loss on restart.
_recent_dispatches: dict[int, tuple[float, str]] = {}
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
    """Assign and persist the due slot before asking for confirmation."""
    due_tasks = store.get_due_tasks(now=now)
    current_time = now or datetime.now()
    for task in due_tasks:
        tid = int(task["id"])
        with _dispatch_lock:
            if tid in _in_flight_tasks:
                continue
        if store.queue_task_occurrence(tid, now=current_time):
            logger.info("Task due: [%s] %s -> %s", tid, task["title"], task["platform"])


def is_task_in_flight(task_id: int) -> bool:
    """Return True if task_id is currently being confirmed or dispatched."""
    with _dispatch_lock:
        return int(task_id) in _in_flight_tasks


def _reserve_task_atomically(task_id: int) -> tuple[bool, dict | None, str]:
    """Claim durably; never fall back to an unprotected in-memory reservation."""
    tid = int(task_id)
    with _dispatch_lock:
        if tid in _in_flight_tasks:
            return False, store.get_task(tid), f"Doublon ignore (idempotent) : tache {tid} en cours."
        try:
            store.recover_stale_execution(tid)
            claimed, task, detail = store.claim_task_execution(tid)
        except Exception as exc:
            logger.error("Reservation SQLite refusee [%s]: %s", tid, exc)
            return False, None, f"Reservation persistante impossible : {exc}"
        if claimed:
            _in_flight_tasks.add(tid)
        return claimed, task, detail


def _reclaim_stale_processing_tasks(max_age_seconds: float = 300.0) -> None:
    """Freeze abandoned leases and their persistent occurrences for verification."""
    try:
        processing_tasks = store.list_tasks(status="processing")
        for t in processing_tasks:
            tid = int(t["id"])
            with _dispatch_lock:
                if tid in _in_flight_tasks:
                    continue
                if store.recover_stale_execution(tid, max_age_seconds=max_age_seconds):
                    logger.warning("Execution interrompue [%d] : verification requise, aucun renvoi.", tid)
    except Exception as exc:
        logger.error("Erreur lors de la récupération des tâches stale : %s", exc)


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
    _reclaim_stale_processing_tasks()
    while not _stop_event.is_set():
        try:
            _reclaim_stale_processing_tasks()
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
    """Start the background scheduler thread with concurrency lock."""
    global _thread
    with _start_lock:
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
        return reservation_msg.startswith("Doublon ignore (idempotent)"), reservation_msg

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
        if result.is_final:
            store.finish_task_execution(tid, task["execution_id"], "completed", result.detail)
            return True, result.detail

        if result.requires_manual_action:
            store.finish_task_execution(tid, task["execution_id"], "awaiting_manual_action", result.detail)
            return True, result.detail

        fail_reason = str(result.detail or "Erreur d'envoi inconnue").strip()
        logger.error("Echec d'envoi de la tache planifiee [%s]: %s", tid, fail_reason)
        store.finish_task_execution(tid, task["execution_id"], "failed", fail_reason)
        return False, fail_reason
    except Exception as exc:
        fail_reason = f"Erreur d'execution de la tache {tid}: {exc}"
        logger.error("Exception dans confirm_and_send(%s): %s", tid, exc, exc_info=True)
        try:
            store.finish_task_execution(
                tid, task["execution_id"], "interrupted_uncertain",
                fail_reason + " Verification de la livraison requise ; aucun renvoi automatique.",
            )
        except Exception:
            # A committed processing marker still blocks replay when result
            # persistence itself failed (disk full, crash, database unavailable).
            logger.exception("Resultat non persiste [%s] ; reservation conservee.", tid)
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
        if tid in _in_flight_tasks:
            return False, "Tache en cours : attendez la fin de l'execution."
        return store.mark_execution_manually_sent(tid)


def get_pending_tasks() -> list[dict]:
    return store.list_tasks(status="pending_confirmation")


def get_manual_action_tasks() -> list[dict]:
    return store.list_tasks(status="awaiting_manual_action")
