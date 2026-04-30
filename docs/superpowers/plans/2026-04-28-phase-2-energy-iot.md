# Phase 2 Energy and IoT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic energy and IoT voice intents without breaking the phase 1 core or GPT fallback behavior.

**Architecture:** Extend local intent parsing in `intent_engine.py`, keep `assistant_core.py` as the single dispatcher, and reuse `actions/energy_actions.py` plus `actions/iot_actions.py` for business logic and safe responses.

**Tech Stack:** Python, `unittest`, local rule-based intent detection, existing action modules

---

### Task 1: Lock intent behavior with failing tests

**Files:**
- Modify: `tests/test_intent_engine.py`
- Create: `tests/test_assistant_core.py`
- Create: `tests/test_iot_actions.py`

- [ ] Step 1: Add failing tests for energy and IoT intent detection.
- [ ] Step 2: Run the targeted tests and confirm they fail because intents are not wired yet.
- [ ] Step 3: Add failing dispatcher and offline IoT tests.
- [ ] Step 4: Run the targeted tests and confirm the new failures are correct.

### Task 2: Implement local parsing and dispatch

**Files:**
- Modify: `intent_engine.py`
- Modify: `assistant_core.py`
- Modify: `actions/energy_actions.py`

- [ ] Step 1: Add parsing helpers for energy slots and relay identifiers.
- [ ] Step 2: Add deterministic energy and IoT intent detection before chat fallback.
- [ ] Step 3: Wire energy intents in `assistant_core.py` and format spoken responses.
- [ ] Step 4: Keep GPT only on `chat_fallback` and existing music recommendation path.

### Task 3: Complete energy and IoT verification

**Files:**
- Modify: `tests/test_energy_actions.py`

- [ ] Step 1: Add coverage for battery sizing, inverter sizing, and audit template generation.
- [ ] Step 2: Run the energy, intent, assistant, and IoT tests until all are green.

### Task 4: Preserve the new core and isolate legacy files

**Files:**
- Create: `legacy/`
- Move: old root files and legacy GUI/browser helpers that are outside the new runtime path

- [ ] Step 1: Identify files not imported by `main_jarvis.py`, `assistant_core.py`, `intent_engine.py`, `actions/`, or `tests/`.
- [ ] Step 2: Move only those legacy files into `legacy/`, keeping folder structure readable.
- [ ] Step 3: Run the full test suite and compile checks after the move.

