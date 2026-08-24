import json
import re
from datetime import datetime, timedelta
from pathlib import Path

try:
    from dateutil import parser as date_parser
except Exception:
    date_parser = None

import config


class PersonalAssistant:
    def __init__(self, notes_dir=None, data_dir=None):
        self.notes_dir = Path(notes_dir or config.NOTES_DIR)
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.todos_file = self.data_dir / "todos.json"
        self.reminders_file = self.data_dir / "reminders.json"
        self._ensure_storage()

    def _ensure_storage(self):
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.todos_file.exists():
            self.todos_file.write_text("[]", encoding="utf-8")
        if not self.reminders_file.exists():
            self.reminders_file.write_text("[]", encoding="utf-8")

    def _load_json(self, path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_json(self, path, data):
        path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")

    def _slugify(self, text):
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
        return slug or "note"

    def create_note(self, content):
        if not content.strip():
            return "Dis-moi le contenu de la note."
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = self._slugify(content[:40])
        note_path = self.notes_dir / f"{timestamp}_{slug}.txt"
        note_path.write_text(content.strip() + "\n", encoding="utf-8")
        return f"Note creee : {note_path.name}"

    def add_todo(self, content):
        if not content.strip():
            return "Dis-moi le contenu du todo."
        todos = self._load_json(self.todos_file)
        todos.append(
            {
                "id": len(todos) + 1,
                "content": content.strip(),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "done": False,
            }
        )
        self._save_json(self.todos_file, todos)
        return f"Todo ajoute : {content.strip()}"

    def list_todos(self):
        todos = self._load_json(self.todos_file)
        if not todos:
            return "Tu n'as aucun todo."

        lines = ["Todos en cours :"]
        for todo in todos[:10]:
            status = "fait" if todo.get("done") else "a faire"
            lines.append(f"- [{status}] {todo.get('content', '')}")
        return "\n".join(lines)

    def get_open_todos(self, limit=10):
        todos = self._load_json(self.todos_file)
        open_todos = [todo for todo in todos if not todo.get("done")]
        return open_todos[:limit]

    def search(self, query):
        query_lower = query.lower().strip()
        query_tokens = {token for token in re.findall(r"\w+", query_lower) if token}
        todos = self._load_json(self.todos_file)
        todo_hits = []
        scored_todo_hits = []
        for todo in todos:
            content = todo.get("content", "")
            content_lower = content.lower()
            if query_lower in content_lower:
                todo_hits.append(content)
                continue
            overlap = len(query_tokens.intersection(set(re.findall(r"\w+", content_lower))))
            if overlap > 0:
                scored_todo_hits.append((overlap, content))
        note_hits = []
        scored_note_hits = []
        for note_path in self.notes_dir.glob("*.txt"):
            content = note_path.read_text(encoding="utf-8", errors="ignore")
            if query_lower in content.lower() or query_lower in note_path.name.lower():
                note_hits.append(note_path.name)
                continue
            overlap = len(query_tokens.intersection(set(re.findall(r"\w+", content.lower()))))
            if overlap > 0:
                scored_note_hits.append((overlap, note_path.name))

        if not todo_hits and not note_hits and not scored_todo_hits and not scored_note_hits:
            return "Aucun resultat dans notes et todos."

        lines = []
        if todo_hits:
            lines.append("Todos trouves :")
            lines.extend(f"- {item}" for item in todo_hits[:5])
        elif scored_todo_hits:
            lines.append("Todos pertinents :")
            scored_todo_hits.sort(key=lambda item: item[0], reverse=True)
            lines.extend(f"- {item}" for _score, item in scored_todo_hits[:5])
        if note_hits:
            lines.append("Notes trouvees :")
            lines.extend(f"- {item}" for item in note_hits[:5])
        elif scored_note_hits:
            lines.append("Notes pertinentes :")
            scored_note_hits.sort(key=lambda item: item[0], reverse=True)
            lines.extend(f"- {item}" for _score, item in scored_note_hits[:5])
        return "\n".join(lines)

    def _parse_reminder_time(self, when_text, now=None):
        reference = now or datetime.now()
        cleaned = when_text.strip().lower()
        cleaned = cleaned.replace(" a ", " ")
        cleaned = cleaned.replace(" a", " ")
        cleaned = cleaned.replace("heure", "h")

        if "demain" in cleaned:
            cleaned = cleaned.replace("demain", "").strip()
            reference = reference + timedelta(days=1)

        cleaned = cleaned.replace("h", ":")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if re.fullmatch(r"\d{1,2}", cleaned):
            cleaned = f"{cleaned}:00"

        if date_parser is not None:
            return date_parser.parse(cleaned, default=reference)

        if re.fullmatch(r"\d{1,2}:\d{2}", cleaned):
            hour, minute = map(int, cleaned.split(":"))
            return reference.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2})?", cleaned):
            normalized = cleaned.replace(" ", "T")
            try:
                parsed = datetime.fromisoformat(normalized)
                return parsed
            except ValueError:
                pass

        raise ValueError(f"Impossible de parser l'heure du rappel: {when_text!r}")

    def schedule_reminder(self, content, when_text, now=None):
        if not content.strip():
            return "Dis-moi le message du rappel."
        if not when_text:
            return "Dis-moi aussi l'heure du rappel."

        due_at = self._parse_reminder_time(when_text, now=now)
        reminders = self._load_json(self.reminders_file)
        reminders.append(
            {
                "content": content.strip(),
                "due_at": due_at.isoformat(timespec="seconds"),
                "notified": False,
            }
        )
        self._save_json(self.reminders_file, reminders)
        return f"Rappel programme pour {due_at.strftime('%Y-%m-%d %H:%M')} : {content.strip()}"

    def poll_due_reminders(self, now=None):
        current = now or datetime.now()
        reminders = self._load_json(self.reminders_file)
        due_messages = []

        for reminder in reminders:
            due_at = datetime.fromisoformat(reminder["due_at"])
            if not reminder.get("notified") and due_at <= current:
                reminder["notified"] = True
                due_messages.append(f"Rappel : {reminder['content']}")

        if due_messages:
            self._save_json(self.reminders_file, reminders)
        return due_messages

    def get_upcoming_reminders(self, limit=5, now=None):
        current = now or datetime.now()
        reminders = self._load_json(self.reminders_file)
        upcoming = []
        for reminder in reminders:
            due_at_raw = reminder.get("due_at")
            if not due_at_raw:
                continue
            try:
                due_at = datetime.fromisoformat(due_at_raw)
            except ValueError:
                continue
            if reminder.get("notified"):
                continue
            if due_at >= current:
                item = dict(reminder)
                item["due_at"] = due_at.isoformat(timespec="seconds")
                upcoming.append(item)

        upcoming.sort(key=lambda item: item["due_at"])
        return upcoming[:limit]

    def summarize_file(self, file_path):
        path = Path(file_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            return f"Le fichier {path} est introuvable."

        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return "Le fichier est vide."
        highlights = self._build_smart_highlights(lines)
        preview = "\n".join(f"- {line}" for line in highlights[:4])
        return (
            f"Resume intelligent local ({path.name}) :\n"
            f"- Lignes utiles analysees : {len(lines)}\n"
            f"{preview}"
        )

    def _build_smart_highlights(self, lines):
        if not lines:
            return []

        keywords = {
            "priorite": 5,
            "urgent": 5,
            "todo": 4,
            "action": 4,
            "deadline": 4,
            "prochaine": 3,
            "bloquant": 4,
            "decision": 3,
            "risque": 4,
            "important": 3,
            "focus": 3,
            "projet": 2,
        }
        scored = []
        for index, line in enumerate(lines):
            lower = line.lower()
            score = 1
            score += sum(weight for token, weight in keywords.items() if token in lower)
            if re.search(r"\d{1,2}[:h]\d{2}", lower):
                score += 2
            if len(line) > 130:
                score -= 1
            score += max(0, 3 - min(index, 3))
            scored.append((score, index, line))

        scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        selected = sorted(scored[:4], key=lambda item: item[1])
        return [line for _score, _index, line in selected]
