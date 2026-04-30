# Soft Wake Word Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add local soft wake words `yo` and `hey moctar` while preserving the current click-based activation path.

**Architecture:** Use a dedicated `wake_word_simple.py` helper module, keep `main_jarvis.py` as the only runtime entrypoint, and route all activation sources through one shared command-handling flow.

**Tech Stack:** Python, `speech_recognition`, `unittest`, existing PyQt runtime

---

### Task 1: Lock the behavior with tests

**Files:**
- Create: `tests/test_wake_word_simple.py`
- Modify: `tests/test_main_jarvis_runtime.py`

- [ ] Add failing tests for wake-word normalization and textual detection.
- [ ] Add a runtime test proving setup still supports UI click and soft wake startup.

### Task 2: Add the soft wake helper

**Files:**
- Create: `wake_word_simple.py`
- Modify: `config.py`

- [ ] Add configurable wake-word settings to `config.py`.
- [ ] Implement background soft wake detection in `wake_word_simple.py`.
- [ ] Add microphone coordination helpers so soft wake does not fight with the main command capture.

### Task 3: Refactor the runtime cleanly

**Files:**
- Modify: `main_jarvis.py`
- Modify: `README.md`

- [ ] Replace the external-key wake-word integration with the new local soft wake helper.
- [ ] Refactor command execution into a shared `handle_command(command)` path.
- [ ] Update the README for click, `yo`, and `hey moctar` activation modes.

