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
_stop_event = threading.Event()
_thread: threading.Thread | None = None


def enhance_message_with_ai(
    message: str,
    platform: str,
    title: str = "",
    media_paths=None,
) -> str:
    """
    Use the project's LocalBrain / Ollama path to polish the message.
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
    """Mark due tasks as pending confirmation."""
    due_tasks = store.get_due_tasks(now=now)
    current_time = now or datetime.now()
    for task in due_tasks:
        last_sent = task.get("last_sent")
        if last_sent:
            try:
                last_sent_dt = datetime.fromisoformat(last_sent)
                if (current_time - last_sent_dt).total_seconds() < 60:
                    continue
            except Exception:
                pass
        logger.info("Task due: [%s] %s -> %s", task["id"], task["title"], task["platform"])
        store.update_task(task["id"], status="pending_confirmation")


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
    Try to execute a task immediately and update its status.
    """
    task = store.get_task(task_id)
    if task is None:
        return False, f"Tache {task_id} introuvable."

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
        store.update_task(task_id, status="active", last_sent=now_iso)
        return True, result.detail

    if result.requires_manual_action:
        store.update_task(task_id, status="awaiting_manual_action")
        return True, result.detail

    store.update_task(task_id, status="error")
    return False, result.detail


def mark_task_sent(task_id: int) -> tuple[bool, str]:
    """
    Mark a manually finalized task as sent.
    """
    task = store.get_task(task_id)
    if task is None:
        return False, f"Tache {task_id} introuvable."

    now_iso = datetime.now().isoformat(timespec="seconds")
    store.update_task(task_id, status="active", last_sent=now_iso)
    return True, "Tache marquee comme envoyee."


def get_pending_tasks() -> list[dict]:
    return store.list_tasks(status="pending_confirmation")


def get_manual_action_tasks() -> list[dict]:
    return store.list_tasks(status="awaiting_manual_action")
