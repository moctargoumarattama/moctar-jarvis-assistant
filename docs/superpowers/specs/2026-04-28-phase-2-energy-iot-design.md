# Phase 2 Energy and IoT Design

## Goal

Extend the local voice assistant so energy and IoT commands are handled by deterministic intents and action modules, while GPT remains a fallback only for generic chat and music recommendation.

## Scope

- Add energy intents in `intent_engine.py`:
  - `energy_consumption`
  - `energy_cost`
  - `solar_sizing`
  - `battery_sizing`
  - `inverter_sizing`
  - `energy_audit_template`
- Add IoT intents in `intent_engine.py`:
  - `iot_status`
  - `iot_temperature`
  - `iot_relay_on`
  - `iot_relay_off`
- Dispatch those intents from `assistant_core.py` to `actions/energy_actions.py` and `actions/iot_actions.py`.
- Add unit coverage for intent detection, energy calculations, and offline IoT responses.
- Leave the UI untouched unless a code path demands it.

## Architecture

The implementation stays inside the phase 1 architecture. `intent_engine.py` remains responsible for local normalization, keyword detection, and slot extraction. `assistant_core.py` remains the single dispatcher that converts intent payloads into user-facing responses by calling action modules.

Energy calculations stay deterministic in `actions/energy_actions.py`. The assistant only formats the returned values into short spoken responses. IoT actions stay tolerant to missing configuration or HTTP failures by always returning a safe string.

## Data Flow

1. `main_jarvis.py` receives voice text and calls `detect_intent`.
2. `intent_engine.py` matches a local energy or IoT command and extracts numeric slots.
3. `assistant_core.py` dispatches to the relevant action function.
4. The action returns structured data for energy or a safe status string for IoT.
5. `assistant_core.py` returns a final response string to the existing UI and speech layers.

## Error Handling

- Missing energy parameters return a formula-oriented guidance response instead of falling back to GPT.
- IoT requests return a readable offline or configuration message when the API is unavailable.
- Unknown commands still go to `chat_fallback`, which keeps GPT as the last resort only.

## Testing

- Intent tests verify local detection and slot extraction for representative French commands.
- Energy tests verify battery, inverter, and audit helpers in addition to existing consumption, cost, and solar coverage.
- Assistant and IoT tests verify safe responses when the IoT API is missing or failing.

