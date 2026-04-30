# Legacy Files

These files were moved out of the active phase 1/phase 2 runtime because they are not referenced by `main_jarvis.py`, `assistant_core.py`, `intent_engine.py`, `actions/`, or the current test suite.

Moved on 2026-04-28:
- root legacy voice, browser, utility, and experimental modules
- old desktop shortcut
- old PyQt GUI tree: `JarvisGUI/`
- old gesture control tree: `Gesture Control/`
- legacy helper scripts: `MAIN.py`, `gptIntegration.py`, `NewsApi.py`, `WeatherUpdates.py`
- legacy dataset used by the old GUI flow: `os_dataset.csv`

Reasoning:
- these files are preserved for reference and possible extraction later
- they are outside the active runtime driven by `main_jarvis.py`
- the active tests and phase 1/phase 2 architecture do not depend on them

Wake word note:
- the modern local wake-word runtime is `wake_word_simple.py`
- the root `wake_word.py` module is deprecated and kept only as a compatibility path for the older Picovoice-based backend
