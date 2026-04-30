# Smart Web Opening and Hybrid Wake Word Design

## Goal

Improve browser handling so site commands reuse an active browser tab when possible, and add an optional wake-word activation path that coexists with the existing click-based activation.

## Scope

- Add `open_url_smart(...)` in `actions/web_actions.py`
- Reuse active Chrome, Brave, or Edge sessions when possible
- Fall back cleanly to `webbrowser.open_new_tab(...)`
- Avoid repeated duplicate opens in a short window when possible
- Refactor `wake_word.py` into an optional runtime service
- Keep click activation working even when wake word is unavailable
- Integrate wake-word polling in `main_jarvis.py` without changing `assistant_core.py` or `interface.py`

## Architecture

`actions/web_actions.py` remains the browser-facing module. It gains a single smart helper responsible for browser selection, active-process detection, duplicate suppression, and clear user-facing messages. Existing functions (`open_site`, `search_google`, `search_youtube`) become thin wrappers.

`wake_word.py` becomes an optional service object with no side effects at import time. It loads environment configuration lazily, starts a background listening thread only when enabled, and exposes a non-blocking trigger-consumption API to the main loop.

`main_jarvis.py` remains the only entrypoint. It owns runtime setup and now wires two activation sources into the same listening state:
- UI click
- optional wake-word trigger

## Data Flow

1. A site intent reaches `web_actions.open_site(...)` or a search helper.
2. The helper calls `open_url_smart(...)`.
3. `open_url_smart(...)` checks active browsers with `psutil`, chooses the best controller, suppresses recent duplicates if needed, opens a new tab, and returns a short string.

For wake word:

1. `main_jarvis.setup_runtime()` creates the wake-word service.
2. If configuration is valid, the service starts a background listener.
3. The main loop polls a lightweight trigger flag.
4. Clicks and wake-word detections both set the same `should_listen` state.

## Error Handling

- Missing or invalid `PICOVOICE_ACCESS_KEY` disables wake word only.
- Missing Porcupine or PyAudio disables wake word only.
- Browser-controller failures fall back to the default browser new-tab path.
- Every path returns or logs a clean string; no crash should escape into the UI loop.

## Testing

- Unit tests for smart browser selection and duplicate suppression
- Unit tests for wake-word service disabled mode
- Lightweight runtime test that click + optional wake-word wiring does not break import/setup assumptions

