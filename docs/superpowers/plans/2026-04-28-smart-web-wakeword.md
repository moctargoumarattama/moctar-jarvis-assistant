# Smart Web Opening and Hybrid Wake Word Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add smart browser tab reuse and optional wake-word activation while preserving the current local-first runtime architecture.

**Architecture:** Keep browser behavior inside `actions/web_actions.py`, keep wake-word logic inside `wake_word.py`, and let `main_jarvis.py` consume an optional trigger without changing the assistant dispatch layer.

**Tech Stack:** Python, `unittest`, `psutil`, `webbrowser`, `pvporcupine`, `pyaudio`, existing PyQt runtime

---

### Task 1: Lock behavior with failing tests

**Files:**
- Create: `tests/test_web_actions.py`
- Create: `tests/test_wake_word.py`
- Modify: `tests/test_entrypoint.py`

- [ ] Step 1: Add failing tests for smart browser reuse and duplicate suppression.
- [ ] Step 2: Run the targeted tests and confirm they fail for the right reasons.
- [ ] Step 3: Add failing tests for wake-word disabled mode and runtime import safety.
- [ ] Step 4: Run the targeted tests and confirm the new failures are correct.

### Task 2: Implement smart browser opening

**Files:**
- Modify: `actions/web_actions.py`
- Modify: `config.py`
- Modify: `.env.example`

- [ ] Step 1: Add browser settings and browser executable metadata.
- [ ] Step 2: Implement `open_url_smart(...)` with active-browser detection and duplicate suppression.
- [ ] Step 3: Route site and search helpers through the smart helper.
- [ ] Step 4: Keep every branch returning a short user-facing string.

### Task 3: Implement optional wake-word service

**Files:**
- Modify: `wake_word.py`
- Modify: `main_jarvis.py`

- [ ] Step 1: Replace the blocking script with a service object that has no import-time side effects.
- [ ] Step 2: Load `PICOVOICE_ACCESS_KEY` lazily from `.env` or the environment.
- [ ] Step 3: Integrate trigger polling into the main loop while keeping click activation intact.
- [ ] Step 4: Add cleanup so the background listener stops cleanly on exit.

### Task 4: Verify full runtime safety

**Files:**
- Modify: any affected tests if behavior wording changes

- [ ] Step 1: Run `venv\Scripts\python.exe -m unittest discover -s tests -v`.
- [ ] Step 2: Run `venv\Scripts\python.exe -m py_compile main_jarvis.py assistant_core.py intent_engine.py config.py wake_word.py actions/web_actions.py`.
- [ ] Step 3: Report exact verification results and any residual constraints.

