# Soft Wake Word Design

## Goal

Add a robust hybrid activation flow to M.O.C.T.A.R with three modes:
- UI click on the Iron Man heart
- voice wake word `yo`
- voice wake phrase `hey moctar`

The click path stays the primary reliable activation path. Voice wake is a convenience layer that must never become a failure point.

## Architecture

- `wake_word_simple.py` owns soft wake detection.
- `main_jarvis.py` remains the only entrypoint and keeps command processing centralized.
- `assistant_core.py` remains unchanged as the intent dispatcher.
- `interface.py` remains untouched.

## Soft Wake Strategy

The soft wake module uses short microphone captures on a background thread through `speech_recognition`, normalizes short transcripts, and matches them against configurable wake words. It exposes:

- `should_activate_by_voice() -> bool`
- startup and shutdown helpers
- microphone pause/resume helpers to avoid competing with the real command capture

## Runtime Flow

1. UI click or soft wake sets the shared `should_listen` flag.
2. `main_jarvis.py` shows activation feedback in the current UI.
3. The real command is captured once with `speech2text.voice2text("fr")`.
4. `handle_command(command)` runs intent detection and assistant dispatch.

## Robustness

- soft wake can be disabled in `config.py`
- microphone errors are logged and throttled
- the UI loop keeps calling `app.processEvents()`
- the main loop sleeps between iterations to avoid aggressive CPU usage
- if soft wake fails, click activation still works

