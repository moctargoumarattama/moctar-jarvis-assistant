"""
Background scheduler thread.
Checks every minute for due tasks and marks them 'pending_confirmation'
so the UI can display a confirmation button.
Also exposes confirm_and_send() for immediate send.
"""

import logging
import threading
import time
from datetime import datetime

from scheduler import scheduler_store as store
from scheduler import platforms

logger = logging.getLogger("jarvis.scheduler")

_lock = threading.Lock()
_stop_event = threading.Event()
_thread: threading.Thread | None = None


# --------------------------------------------------------------------------- #
#  AI message enhancement                                                      #
# --------------------------------------------------------------------------- #

def enhance_message_with_ai(message: str, platform: str, title: str = "") -> str:
    """
    Use the project's LocalBrain / OpenAI to polish the message.
    Falls back to the original message if AI is unavailable.
    """
    try:
        import ai_brain
        brain = ai_brain.LocalBrain()
        prompt = (
            f"Ameliore ce message pour une publication {platform} "
            f"(contexte: {title}). "
            f"Rends-le engageant, naturel, avec des emojis si approprie. "
            f"Reponds UNIQUEMENT avec le message ameliore, sans explication.\n\n"
            f"Message original:\n{message}"
        )
        result = brain.chat(prompt, context="scheduler")
        if result and result.strip():
            return result.strip()
    except Exception as exc:
        logger.warning("AI enhance failed: %s", exc)
    return message


# --------------------------------------------------------------------------- #
#  Scheduler logic                                                             #
# --------------------------------------------------------------------------- #

def _check_and_queue():
    """Called every minute — marks due tasks as 'pending_confirmation'."""
    due_tasks = store.get_due_tasks()
    for task in due_tasks:
        # Avoid double-triggering: check last_sent
        last_sent = task.get("last_sent")
        if last_sent:
            try:
                ls = datetime.fromisoformat(last_sent)
                if (datetime.now() - ls).total_seconds() < 60:
                    continue
            except Exception:
                pass
        logger.info(
            "Task due: [%s] %s → %s", task["id"], task["title"], task["platform"]
        )
        store.update_task(task["id"], status="pending_confirmation")


def _scheduler_loop():
    """Runs in background thread, ticks every 60 s."""
    logger.info("Scheduler thread started.")
    while not _stop_event.is_set():
        try:
            with _lock:
                _check_and_queue()
        except Exception as exc:
            logger.error("Scheduler loop error: %s", exc)
        # sleep in small increments so stop_event is responsive
        for _ in range(60):
            if _stop_event.is_set():
                break
            time.sleep(1)
    logger.info("Scheduler thread stopped.")


# --------------------------------------------------------------------------- #
#  Public API                                                                  #
# --------------------------------------------------------------------------- #

def start():
    """Start the background scheduler thread (idempotent)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_scheduler_loop, daemon=True, name="jarvis-scheduler")
    _thread.start()


def stop():
    """Stop the background scheduler thread."""
    _stop_event.set()


def confirm_and_send(task_id: int) -> tuple[bool, str]:
    """
    Immediately send a task and update its status.
    Returns (success, message).
    """
    task = store.get_task(task_id)
    if task is None:
        return False, f"Tache {task_id} introuvable."

    message = task["message"]
    # Optionally re-enhance with AI if flagged
    if task.get("ai_enhanced"):
        message = enhance_message_with_ai(message, task["platform"], task["title"])

    ok, detail = platforms.dispatch(task["platform"], task["target"], message)
    now_iso = datetime.now().isoformat(timespec="seconds")
    if ok:
        store.update_task(task_id, status="active", last_sent=now_iso)
    else:
        store.update_task(task_id, status="error", last_sent=now_iso)
    return ok, detail


def get_pending_tasks() -> list[dict]:
    """Return tasks waiting for user confirmation."""
    return store.list_tasks(status="pending_confirmation")
