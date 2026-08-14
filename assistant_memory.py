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
    MAX_RESPONSE_CHARS = 220

    def __init__(self, path=None):
        self.path = Path(path or config.USER_MEMORY_FILE)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache = None
        if not self.path.exists():
            self.path.write_text(
                json.dumps(
                    {
                        "preferences": {},
                        "history": [],
                        "insights": {
                            "top_intents": {},
                            "active_project": "",
                            "recent_files": [],
                            "last_focus_topic": "",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    def _load(self):
        if self._cache is not None:
            return self._cache
        try:
            self._cache = json.loads(self.path.read_text(encoding="utf-8"))
            return self._cache
        except Exception:
            self._cache = {
                "preferences": {},
                "history": [],
                "insights": {
                    "top_intents": {},
                    "active_project": "",
                    "recent_files": [],
                    "last_focus_topic": "",
                },
            }
            return self._cache

    def _save(self, payload):
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        self.path.write_text(serialized, encoding="utf-8")
        self._cache = payload

    def set_preference(self, key, value):
        payload = self._load()
        payload.setdefault("preferences", {})[key] = value
        self._save(payload)

    def get_preference(self, key, default=None):
        payload = self._load()
        return payload.get("preferences", {}).get(key, default)

    def get_preferences(self):
        payload = self._load()
        return dict(payload.get("preferences", {}))

    def record_interaction(self, intent, target, response):
        payload = self._load()
        history = payload.setdefault("history", [])
        insights = payload.setdefault("insights", {})
        top_intents = insights.setdefault("top_intents", {})
        response_text = str(response or "")
        response_preview = response_text[: self.MAX_RESPONSE_CHARS]
        if len(response_text) > self.MAX_RESPONSE_CHARS:
            response_preview += "..."
        history.append(
            {
                "intent": intent,
                "target": target,
                "response": response_preview,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        payload["history"] = history[-100:]

        if intent not in {"confirm_yes", "confirm_no", "empty"}:
            top_intents[intent] = int(top_intents.get(intent, 0)) + 1
        normalized_target = (target or "").strip()
        if normalized_target:
            if intent in {"open_project", "launch_project_server"}:
                insights["active_project"] = normalized_target
            if intent in {"summarize_file", "search_personal", "create_note"}:
                recent_files = insights.setdefault("recent_files", [])
                recent_files.append(normalized_target)
                insights["recent_files"] = recent_files[-6:]
            if intent in {"next_action", "daily_brief", "search_personal", "summarize_file"}:
                insights["last_focus_topic"] = normalized_target

        self._save(payload)

    def get_history(self, limit=None):
        payload = self._load()
        history = list(payload.get("history", []))
        if limit is None:
            return history
        return history[-limit:]

    def get_insights(self):
        payload = self._load()
        insights = payload.get("insights", {})
        return {
            "top_intents": dict(insights.get("top_intents", {})),
            "active_project": insights.get("active_project", ""),
            "recent_files": list(insights.get("recent_files", [])),
            "last_focus_topic": insights.get("last_focus_topic", ""),
        }
