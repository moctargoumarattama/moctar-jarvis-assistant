import json
from datetime import datetime
from pathlib import Path

import config


class SessionMemory:
    def __init__(self, max_turns=20):
        self.max_turns = max_turns
        self.turns = []

    def add_turn(self, intent, target, response):
        self.turns.append(
            {
                "intent": intent,
                "target": target,
                "response": response,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns :]

    def last_turn(self):
        if not self.turns:
            return None
        return self.turns[-1]


class UserMemoryStore:
    def __init__(self, path=None):
        self.path = Path(path or config.USER_MEMORY_FILE)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache = None
        if not self.path.exists():
            self.path.write_text(
                json.dumps({"preferences": {}, "history": []}, indent=2),
                encoding="utf-8",
            )

    def _load(self):
        if self._cache is not None:
            return self._cache
        try:
            self._cache = json.loads(self.path.read_text(encoding="utf-8"))
            return self._cache
        except Exception:
            self._cache = {"preferences": {}, "history": []}
            return self._cache

    def _save(self, payload):
        self._cache = payload
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def set_preference(self, key, value):
        payload = self._load()
        payload.setdefault("preferences", {})[key] = value
        self._save(payload)

    def get_preference(self, key, default=None):
        payload = self._load()
        return payload.get("preferences", {}).get(key, default)

    def record_interaction(self, intent, target, response):
        payload = self._load()
        history = payload.setdefault("history", [])
        history.append(
            {
                "intent": intent,
                "target": target,
                "response": response,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        payload["history"] = history[-100:]
        self._save(payload)
