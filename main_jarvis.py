import logging
import time
from logging.handlers import RotatingFileHandler

import config
import speech2text as s2t
import text2speech as t2s
from assistant_core import AssistantCore
from interface import run_ui
from intent_engine import detect_intent
import pyttsx3
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
    "cherche",
    "combien",
    "coupe",
    "cree",
    "donne",
    "joue",
    "lance",
    "met",
    "mets",
    "open",
    "ouvre",
    "play",
    "quel",
    "quelle",
    "rappelle",
    "resume",
}
COMMAND_ONLY_SAFE_INTENTS = {
    "add_todo",
    "battery",
    "create_note",
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
    "remind_me",
    "screenshot",
    "search_google",
    "search_personal",
    "search_youtube",
    "solar_sizing",
    "summarize_file",
    "time",
    "volume_down",
    "volume_up",
}


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
    state["activate_listen"] = activate_listen
    ui.set_mode("idle")
    if config.ENABLE_SOFT_WAKE_WORD:
        ui.update_text("Clique sur le coeur, ou dis Hey Moctar / Yo Moctar / Yo")
    else:
        ui.update_text("Clique sur le coeur pour parler")
    return app, ui, engine, assistant, state


def speak(engine, ui, app, text):
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


def listen_for_command(app, ui):
    acquire_command_microphone(app=app)
    try:
        ui.set_mode("listening")
        ui.update_text("🎤 J'écoute...")
        app.processEvents()
        return s2t.voice2text("fr")
    finally:
        release_command_microphone()


def handle_command(command, assistant):
    logger.info("User command: %s", command)
    intent_data = detect_intent(command)
    return assistant.handle_intent(intent_data)


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
    activation = consume_voice_activation()
    inline_command = ""

    if activation:
        state["activate_listen"]("soft_wake")
        inline_command = (activation.get("command") or "").strip()
        if inline_command:
            if activation.get("command_only") and not is_safe_command_only_activation(inline_command):
                state["should_listen"] = False
                logger.info("Rejected ambiguous command-only wake payload: %s", inline_command)
                return ""
            state["should_listen"] = False
            logger.info("Using inline voice command from wake word: %s", inline_command)
            return inline_command

    if not state["should_listen"]:
        return ""

    state["should_listen"] = False
    return listen_for_command(app, ui)


def run_loop(app, ui, engine, assistant, state):
    while True:
        app.processEvents()

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

        command = resolve_next_command(app, ui, state)
        if not command:
            ui.set_mode("idle")
            ui.update_text("Je n'ai pas compris.")
            app.processEvents()
            logger.info("No command understood")
            continue

        ui.update_text(command)
        app.processEvents()

        try:
            result = handle_command(command, assistant)
        except Exception as exc:
            logger.exception("Intent handling failed")
            result = f"Erreur interne : {exc}"

        if result == "__STOP__":
            speak(engine, ui, app, "D'accord. J'arrete.")
            break

        speak(engine, ui, app, result)
        ui.update_text(result)
        ui.set_mode("idle")
        app.processEvents()


def shutdown_runtime(state):
    pause_soft_wake_listener()
    stop_soft_wake_listener()


def main():
    configure_logging()
    logger.info("Jarvis starting")
    app, ui, engine, assistant, state = setup_runtime()
    try:
        run_loop(app, ui, engine, assistant, state)
    finally:
        shutdown_runtime(state)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Jarvis stopped by keyboard interrupt")
