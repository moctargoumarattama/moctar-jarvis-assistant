import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from actions.personal_actions import PersonalAssistant


class PersonalAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.notes_dir = self.base_path / "notes"
        self.data_dir = self.base_path / "data"
        self.assistant = PersonalAssistant(
            notes_dir=self.notes_dir,
            data_dir=self.data_dir,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_note_writes_local_file(self):
        response = self.assistant.create_note("idee audit solaire")

        note_files = list(self.notes_dir.glob("*.txt"))
        self.assertEqual(1, len(note_files))
        self.assertIn("idee audit solaire", note_files[0].read_text(encoding="utf-8"))
        self.assertIn("idee-audit-solaire", response)

    def test_add_and_list_todos(self):
        self.assistant.add_todo("appeler client demain")
        self.assistant.add_todo("verifier pompe solaire")

        listing = self.assistant.list_todos()

        self.assertIn("appeler client demain", listing)
        self.assertIn("verifier pompe solaire", listing)

    def test_get_open_todos_returns_only_pending_items(self):
        self.assistant.add_todo("appeler client demain")

        todos = self.assistant.get_open_todos()

        self.assertEqual(1, len(todos))
        self.assertEqual("appeler client demain", todos[0]["content"])

    def test_schedule_and_poll_reminder(self):
        now = datetime(2026, 4, 28, 10, 0, 0)
        self.assistant.schedule_reminder(
            "appeler le client",
            "2026-04-28 10:05",
            now=now,
        )

        nothing_due = self.assistant.poll_due_reminders(now=now + timedelta(minutes=4))
        due = self.assistant.poll_due_reminders(now=now + timedelta(minutes=6))

        self.assertEqual([], nothing_due)
        self.assertEqual(1, len(due))
        self.assertIn("appeler le client", due[0])

    def test_get_upcoming_reminders_returns_future_only(self):
        now = datetime(2026, 4, 28, 10, 0, 0)
        self.assistant.schedule_reminder("appel client", "2026-04-28 10:05", now=now)

        reminders = self.assistant.get_upcoming_reminders(now=now)

        self.assertEqual(1, len(reminders))
        self.assertEqual("appel client", reminders[0]["content"])

    def test_summarize_file_returns_short_summary(self):
        source = self.notes_dir / "audit.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "Audit solaire site A\n"
            "Les lampes restent allumees toute la nuit.\n"
            "Priorite: changer vers LED et minuterie.\n"
            "Suivi prevu la semaine prochaine.\n",
            encoding="utf-8",
        )

        summary = self.assistant.summarize_file(str(source))

        self.assertIn("Resume intelligent local", summary)
        self.assertIn("Priorite: changer vers LED", summary)

    def test_search_returns_semantic_results_when_exact_match_absent(self):
        self.assistant.add_todo("verifier audit lampes entrepot")

        response = self.assistant.search("audit entrepot energie")
        self.assertIn("Todos", response)


if __name__ == "__main__":
    unittest.main()
