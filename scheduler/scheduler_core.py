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
            # Si la tâche n'est plus en vol et que son bail de 300s a expiré, on la réclame proprement
            last_att = task.get("last_attempt_at")
            is_stale = False
            if last_att:
                try:
                    if (datetime.now() - datetime.fromisoformat(last_att)).total_seconds() > 300.0:
                        is_stale = True
                except Exception:
                    is_stale = True
            else:
                is_stale = True
            if is_stale and tid not in _in_flight_tasks:
                note = f"Bail expiré (>300s). Rebasculement en attente de confirmation ({datetime.now().isoformat()})."
                store.update_task(tid, status="awaiting_confirmation", execution_note=note)
                return False, store.get_task(tid), f"Tâche {tid} expirée et réinitialisée en attente de confirmation."
            return False, task, f"Tâche {tid} déjà en cours de traitement (processing)."
        if current_status in ("completed", "sent", "confirmed"):
            return False, task, f"Tâche {tid} déjà exécutée ({current_status})."

        now_iso = datetime.now().isoformat(timespec="seconds")
        # 3. Transition atomique SQLite : UPDATE ... SET status = 'processing', last_attempt_at = ?
        if hasattr(store, "_conn_scope"):
            try:
                with store._conn_scope() as conn:
                    cur = conn.execute(
                        """UPDATE scheduled_tasks
                           SET status = 'processing',
                               last_attempt_at = ?,
                               execution_note = 'Traitement en cours'
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
                        (now_iso, tid),
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
                                f"Tâche {tid} déjà réservée ou traitée (statut={db_status}).",
                            )
            except Exception as db_exc:
                logger.debug("Transition SQLite directe ignorée (fallback store.update_task): %s", db_exc)

        store.update_task(tid, status="processing", last_attempt_at=now_iso, execution_note="Réservé pour envoi")
        _in_flight_tasks.add(tid)
        return True, task, ""


def _reclaim_stale_processing_tasks(max_age_seconds: float = 300.0) -> None:
    """Identifie les tâches bloquées en 'processing' depuis plus de 300 secondes sans activité en vol.
    Ne les renvoie pas automatiquement à l'aveugle : passe leur statut à awaiting_confirmation avec une note explicite."""
    try:
        processing_tasks = store.list_tasks(status="processing")
        now_dt = datetime.now()
        for t in processing_tasks:
            tid = int(t["id"])
            with _dispatch_lock:
                if tid in _in_flight_tasks:
                    continue
            last_att = t.get("last_attempt_at")
            is_stale = False
            if not last_att:
                is_stale = True
            else:
                try:
                    diff = (now_dt - datetime.fromisoformat(last_att)).total_seconds()
                    if diff > max_age_seconds:
                        is_stale = True
                except Exception:
                    is_stale = True

            if is_stale:
                note = f"Exécution précédente interrompue (>300s). Vérification requise avant renvoi ({now_dt.isoformat()})."
                logger.warning("Récupération de la tâche stale [%d] -> awaiting_confirmation: %s", tid, note)
                store.update_task(tid, status="awaiting_confirmation", execution_note=note)
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
