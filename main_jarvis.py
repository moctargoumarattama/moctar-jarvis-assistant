import logging
import time
import threading
from logging.handlers import RotatingFileHandler

import config
import ai_brain
import speech2text as s2t
import text2speech as t2s
from assistant_core import AssistantCore
from interface import run_ui
from intent_engine import detect_intent
from scheduler import scheduler_core as sched_core
try:
    import pyttsx3
except Exception:
    class _DummyEngine:
        def say(self, _text):
            return None

        def runAndWait(self):
            return None

    class _PyttsxFallback:
        @staticmethod
        def init():
            return _DummyEngine()

    pyttsx3 = _PyttsxFallback()
from wake_word_simple import (
    acquire_command_microphone,
    consume_voice_activation,
    pause_soft_wake_listener,
    release_command_microphone,
    should_activate_by_voice,
    start_soft_wake_listener,
    stop_soft_wake_listener,
)


IDLE_SLEEP_SECONDS = 0.15
logger = logging.getLogger("jarvis")
COMMAND_ONLY_ALLOWED_PREFIXES = {
    "active",
    "ajoute",
    "augmente",
    "baisse",
    "alarme",
    "cherche",
    "combien",
    "coupe",
    "cree",
    "donne",
    "joue",
    "lance",
    "met",
    "met-moi",
    "mets",
    "mets-moi",
    "planifie",
    "planifie-moi",
    "programme",
    "programme-moi",
    "open",
    "ouvre",
    "play",
    "quel",
    "quelle",
    "rappel",
    "rappel-moi",
    "rappelle",
    "rappelle-moi",
    "resume",
}
COMMAND_ONLY_SAFE_INTENTS = {
    "add_todo",
    "battery",
    "create_note",
    "date",
    "energy_audit_template",
    "energy_consumption",
    "energy_cost",
    "inverter_sizing",
    "iot_status",
    "iot_temperature",
    "launch_project_server",
    "list_todos",
    "mute",
    "open_app",
    "open_folder",
    "open_project",
    "open_site",
    "play_music",
    "playlist",
    "prioritize_tasks",
    "remind_me",
    "routine_evening",
    "routine_morning",
    "screenshot",
    "search_google",
    "search_personal",
    "search_youtube",
    "solar_sizing",
    "summarize_file",
    "time",
    "volume_down",
    "volume_up",
    "focus_mode",
}


def format_assistant_message(text):
    text = (text or "").strip()
    if not text:
        return "Je n'ai pas de reponse pour le moment."
    return text


def configure_logging():
    if logger.handlers:
        return

    config.ensure_runtime_directories()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    file_handler = RotatingFileHandler(
        config.LOGS_DIR / "jarvis.log",
        maxBytes=512_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


def setup_runtime():
    app, ui = run_ui()
    engine = pyttsx3.init()
    assistant = AssistantCore()
    status = ai_brain.get_ai_status()
    ui.set_ai_status(status["model"], status["base_url"], status["online"])
    startup_message = start_soft_wake_listener()
    logger.info(startup_message)
    state = {
        "should_listen": False,
        "activation_source": "ui",
    }

    def activate_listen(source="ui"):
        if state["should_listen"]:
            return
        state["activation_source"] = source
        state["should_listen"] = True
        ui.set_mode("listening")
        if source == "soft_wake":
            ui.update_text("🎤 Activation vocale détectée")
        else:
            ui.update_text("Activation...")
        app.processEvents()
        logger.info("Listen requested from %s", source)

    def activate_from_ui():
        activate_listen("ui")

    ui.listen_requested.connect(activate_from_ui)

    def show_ai_status():
        ui.update_text(ui.ai_status_message)
        app.processEvents()

    ui.ai_status_requested.connect(show_ai_status)

    def open_scheduler_from_ui():
        try:
            from scheduler.scheduler_ui import open_scheduler
            open_scheduler(parent=None)
        except Exception as exc:
            logger.exception("Failed to open scheduler UI from button")

    ui.scheduler_requested.connect(open_scheduler_from_ui)
    state["activate_listen"] = activate_listen
    ui.set_mode("idle")
    if config.ENABLE_SOFT_WAKE_WORD:
        ui.update_text("Clique sur le coeur, ou dis Hey Moctar / Yo Moctar / Yo")
    else:
        ui.update_text("Clique sur le coeur pour parler")
    return app, ui, engine, assistant, state


def speak(engine, ui, app, text):
    text = format_assistant_message(text)
    logger.info("Assistant response: %s", text)
    print("M.O.C.T.A.R:", text)
    ui.set_mode("speaking")
    ui.update_text(text)
    app.processEvents()

    try:
        t2s.text2speech(engine, text)
    finally:
        ui.set_mode("idle")
        app.processEvents()


def listen_for_command(_app=None, _ui=None):
    acquire_command_microphone()
    try:
        return s2t.voice2text("fr")
    finally:
        release_command_microphone()


def handle_command(command, assistant):
    logger.info("User command: %s", command)
    intent_data = detect_intent(command)
    return assistant.handle_intent(intent_data)


def start_command_job(command, assistant, music_was_paused, state):
    def _worker():
        try:
            result = handle_command(command, assistant)
        except Exception as exc:
            logger.exception("Intent handling failed")
            result = f"Erreur interne : {exc}"
        state["command_job"]["result"] = result
        state["command_job"]["done"] = True

    job = {
        "command": command,
        "music_was_paused": music_was_paused,
        "result": None,
        "done": False,
    }
    thread = threading.Thread(target=_worker, daemon=True)
    job["thread"] = thread
    state["command_job"] = job
    thread.start()


def get_finished_command_job(state):
    job = state.get("command_job")
    if not job:
        return None
    thread = job.get("thread")
    if thread and thread.is_alive():
        return None
    state["command_job"] = None
    return job


def start_listen_job(app, ui, state):
    def _worker():
        try:
            command = listen_for_command()
        except Exception as exc:
            logger.exception("Voice capture failed")
            command = ""
            state["listen_job_error"] = str(exc)
        state["listen_job"]["result"] = command
        state["listen_job"]["done"] = True

    job = {
        "result": None,
        "done": False,
    }
    thread = threading.Thread(target=_worker, daemon=True)
    job["thread"] = thread
    state["listen_job"] = job
    thread.start()


def get_finished_listen_job(state):
    job = state.get("listen_job")
    if not job:
        return None
    thread = job.get("thread")
    if thread and thread.is_alive():
        return None
    state["listen_job"] = None
    return job


def _set_listening_popup(ui, visible):
    method = getattr(ui, "show_listening_popup" if visible else "hide_listening_popup", None)
    if method:
        method()


def is_safe_command_only_activation(command):
    normalized = (command or "").strip().lower()
    if not normalized:
        return False

    first_word = normalized.split()[0]
    if first_word not in COMMAND_ONLY_ALLOWED_PREFIXES:
        return False

    intent_data = detect_intent(normalized)
    return intent_data["intent"] in COMMAND_ONLY_SAFE_INTENTS


def resolve_next_command(app, ui, state):
    if state["should_listen"]:
        # The button owns the microphone session. A wake transcript captured
        # during that session must not be replayed after the UI capture fails.
        consume_voice_activation()
        state["should_listen"] = False
        return listen_for_command(app, ui)

    activation = consume_voice_activation()
    inline_command = ""

    if activation:
        state["activate_listen"]("soft_wake")
        inline_command = (activation.get("command") or "").strip()
        if inline_command:
            if activation.get("command_only") and not is_safe_command_only_activation(inline_command):
                state["should_listen"] = False
                state["feedback"] = (
                    f"J'ai entendu « {inline_command} », mais je ne l'exécute pas sans activation claire. "
                    "Dis « Hey Moctar » puis répète la commande."
                )
                logger.info("Rejected ambiguous command-only wake payload: %s", inline_command)
                return ""
            state["should_listen"] = False
            logger.info("Using inline voice command from wake word: %s", inline_command)
            return inline_command

    return ""


def run_loop(app, ui, engine, assistant, state):
    while True:
        app.processEvents()

        listen_job = get_finished_listen_job(state)
        if listen_job is not None:
            command = (listen_job.get("result") or "").strip()
            if not command:
                error_message = state.pop("listen_job_error", "")
                _set_listening_popup(ui, False)
                ui.set_mode("idle")
                if state.pop("listen_job_music_was_paused", False) and not assistant.pending_music_request:
                    assistant.resume_music_after_command()
                feedback = format_assistant_message(
                    state.pop(
                        "feedback",
                        "Je n'ai pas compris. Essaie encore ou utilise le bouton d'écoute.",
                    )
                )
                if error_message:
                    feedback = format_assistant_message("Je n'ai pas pu écouter correctement. Réessaie.")
                ui.update_text(feedback)
                app.processEvents()
                logger.info("No command understood: %s", feedback)
                continue

            music_was_paused = state.pop("listen_job_music_was_paused", False)
            ui.update_text("Je reflechis...")
            app.processEvents()
            _set_listening_popup(ui, False)
            start_command_job(command, assistant, music_was_paused, state)
            continue

        job = get_finished_command_job(state)
        if job:
            result = job.get("result") or "Erreur interne : aucune reponse."
            music_was_paused = job.get("music_was_paused", False)

            if result == "__STOP__":
                speak(engine, ui, app, "D'accord. J'arrete.")
                _set_listening_popup(ui, False)
                break

            if result == "__OPEN_SCHEDULER__":
                speak(engine, ui, app, "J'ouvre le planificateur de tâches.")
                try:
                    from scheduler.scheduler_ui import open_scheduler
                    open_scheduler(parent=None)
                except Exception as exc:
                    logger.exception("Failed to open scheduler UI")
                    speak(engine, ui, app, f"Impossible d'ouvrir le planificateur : {exc}")
                ui.set_mode("idle")
                app.processEvents()
                _set_listening_popup(ui, False)
                if music_was_paused:
                    assistant.resume_music_after_command()
                continue

            spoken_result = format_assistant_message(result)
            speak(engine, ui, app, spoken_result)
            _set_listening_popup(ui, False)
            if music_was_paused and not assistant.pending_music_request:
                assistant.resume_music_after_command()
            ui.update_text(spoken_result)
            ui.set_mode("idle")
            if assistant.pending_music_request:
                state["should_listen"] = True
            app.processEvents()
            continue

        if state.get("command_job"):
            time.sleep(IDLE_SLEEP_SECONDS)
            continue

        if state.get("listen_job"):
            time.sleep(IDLE_SLEEP_SECONDS)
            continue

        try:
            pending_messages = assistant.poll_background_messages()
        except Exception as exc:
            logger.exception("Background polling failed")
            pending_messages = [f"Erreur de rappel : {exc}"]

        for message in pending_messages:
            speak(engine, ui, app, message)

        if not state["should_listen"] and not should_activate_by_voice():
            time.sleep(IDLE_SLEEP_SECONDS)
            continue

        _set_listening_popup(ui, True)
        music_was_paused = assistant.pause_music_for_command()
        if state["should_listen"]:
            state["listen_job_music_was_paused"] = music_was_paused
            consume_voice_activation()
            state["should_listen"] = False
            ui.set_mode("listening")
            ui.update_text("Je t'ecoute...")
            app.processEvents()
            start_listen_job(app, ui, state)
            continue

        command = resolve_next_command(app, ui, state)
        if not command:
            _set_listening_popup(ui, False)
            if music_was_paused and not assistant.pending_music_request:
                assistant.resume_music_after_command()
            if assistant.pending_music_request:
                state["should_listen"] = True
            ui.set_mode("idle")
            feedback = format_assistant_message(
                state.pop("feedback", "Je n'ai pas compris. Essaie encore ou utilise le bouton d'écoute.")
            )
            ui.update_text(feedback)
            app.processEvents()
            logger.info("No command understood: %s", feedback)
            continue

        ui.update_text("Je reflechis...")
        app.processEvents()
        _set_listening_popup(ui, False)
        state["should_listen"] = False
        start_command_job(command, assistant, music_was_paused, state)


def shutdown_runtime(state):
    pause_soft_wake_listener()
    stop_soft_wake_listener()
    sched_core.stop()


def main():
    configure_logging()
    logger.info("Jarvis starting")
    app, ui, engine, assistant, state = setup_runtime()
    sched_core.start()
    try:
        run_loop(app, ui, engine, assistant, state)
    finally:
        shutdown_runtime(state)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Jarvis stopped by keyboard interrupt")
