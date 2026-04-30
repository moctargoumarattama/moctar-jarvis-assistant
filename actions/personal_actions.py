import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from dateutil import parser as date_parser

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

    def search(self, query):
        query_lower = query.lower().strip()
        todos = self._load_json(self.todos_file)
        todo_hits = [todo["content"] for todo in todos if query_lower in todo.get("content", "").lower()]
        note_hits = []
        for note_path in self.notes_dir.glob("*.txt"):
            content = note_path.read_text(encoding="utf-8", errors="ignore")
            if query_lower in content.lower() or query_lower in note_path.name.lower():
                note_hits.append(note_path.name)

        if not todo_hits and not note_hits:
            return "Aucun resultat dans notes et todos."

        lines = []
        if todo_hits:
            lines.append("Todos trouves :")
            lines.extend(f"- {item}" for item in todo_hits[:5])
        if note_hits:
            lines.append("Notes trouvees :")
            lines.extend(f"- {item}" for item in note_hits[:5])
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

        return date_parser.parse(cleaned, default=reference)

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

        return "\n".join(lines[:3])
