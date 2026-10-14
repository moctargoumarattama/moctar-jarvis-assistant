"""
Tests unitaires pour l'historique des conversations, la gestion des sessions et la relecture/reprise.
"""

from pathlib import Path
import tempfile
import unittest

from assistant_core import AssistantCore
from assistant_memory import AssistantMemory
from intent_engine import detect_intent
import conversation_history_ui


class ConversationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_history.db"
        self.memory = AssistantMemory(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_auto_creation_and_message_tracking(self):
        session_id = "session_test_1"
        self.memory.add_message(session_id, "user", "Bonjour Jarvis, quel est l'état du serveur ?")
        self.memory.add_message(session_id, "assistant", "Le serveur fonctionne normalement.")

        sessions = self.memory.list_sessions()
        self.assertEqual(1, len(sessions))
        s = sessions[0]
        self.assertEqual(session_id, s["session_id"])
        self.assertIn("quel est l'état du serveur", s["title"])
        self.assertEqual(2, s["message_count"])
        self.assertEqual("Le serveur fonctionne normalement.", s["last_message"])

    def test_list_sessions_search_filtering(self):
        self.memory.add_message("session_iot", "user", "Vérifie les capteurs de température salon")
        self.memory.add_message("session_iot", "assistant", "Température salon : 21.5°C")

        self.memory.add_message("session_git", "user", "Fais un git status sur le projet Jarvis")
        self.memory.add_message("session_git", "assistant", "Branche main à jour.")

        # Recherche par titre ou contenu
        iot_results = self.memory.list_sessions(search_query="capteurs")
        self.assertEqual(1, len(iot_results))
        self.assertEqual("session_iot", iot_results[0]["session_id"])

        git_results = self.memory.list_sessions(search_query="Jarvis")
        self.assertEqual(1, len(git_results))
        self.assertEqual("session_git", git_results[0]["session_id"])

        all_results = self.memory.list_sessions()
        self.assertEqual(2, len(all_results))

    def test_get_session_messages_chronological_order(self):
        session_id = "session_ordered"
        self.memory.add_message(session_id, "user", "Étape 1")
        self.memory.add_message(
            session_id,
            "assistant",
            "Appel outil",
            tool_calls=[{"id": "call_abc", "name": "get_iot_status", "arguments": {}}],
        )
        self.memory.add_message(session_id, "tool", '{"success": true}', tool_call_id="call_abc")
        self.memory.add_message(session_id, "assistant", "Étape 2 terminée.")

        messages = self.memory.get_session_messages(session_id)
        self.assertEqual(4, len(messages))
        self.assertEqual("user", messages[0]["role"])
        self.assertEqual("Étape 1", messages[0]["content"])
        self.assertEqual("assistant", messages[1]["role"])
        self.assertIsNotNone(messages[1]["tool_calls"])
        self.assertEqual("tool", messages[2]["role"])
        self.assertEqual("assistant", messages[3]["role"])
        self.assertEqual("Étape 2 terminée.", messages[3]["content"])

    def test_update_session_title(self):
        session_id = "session_rename"
        self.memory.add_message(session_id, "user", "Coucou")
        self.memory.update_session_title(session_id, "Diagnostic Domotique et Énergie")

        sessions = self.memory.list_sessions()
        self.assertEqual(1, len(sessions))
        self.assertEqual("Diagnostic Domotique et Énergie", sessions[0]["title"])

    def test_delete_session(self):
        session_id = "session_to_delete"
        self.memory.add_message(session_id, "user", "Message temporaire")
        self.memory.add_message(session_id, "assistant", "Réponse temporaire")

        self.assertTrue(self.memory.delete_session(session_id))
        self.assertEqual([], self.memory.list_sessions())
        self.assertEqual([], self.memory.get_session_messages(session_id))

    def test_search_conversation_history_snippets(self):
        self.memory.add_message("session_solar", "user", "Dimensionnement des panneaux solaires 3000W")
        self.memory.add_message("session_solar", "assistant", "Il faut environ 8 panneaux de 400W.")

        matches = self.memory.search_conversation_history("panneaux")
        self.assertGreaterEqual(len(matches), 1)
        self.assertIn("panneaux", matches[0]["content"].lower())

    def test_assistant_core_session_lifecycle_and_resume(self):
        assistant = AssistantCore()
        initial_session = assistant.get_active_session_id()
        self.assertTrue(initial_session.startswith("session_"))

        # Simulation d'interaction enregistrée
        assistant._remember("time", "quelle heure est il", "Il est 14h30.")

        # Démarrage d'une nouvelle session
        new_session = assistant.start_new_session("Nouvelle discussion test")
        self.assertNotEqual(initial_session, new_session)
        self.assertEqual(new_session, assistant.get_active_session_id())

        # Reprise de l'ancienne session
        resumed = assistant.set_active_session(initial_session)
        self.assertTrue(resumed)
        self.assertEqual(initial_session, assistant.get_active_session_id())
        self.assertGreaterEqual(len(assistant.session_memory.get_history()), 1)

    def test_intent_engine_recognizes_open_history(self):
        for phrase in [
            "historique",
            "historique des conversations",
            "ouvre l historique",
            "ouvre historique",
            "voir l historique",
            "anciennes discussions",
        ]:
            intent_data = detect_intent(phrase)
            self.assertEqual("open_history", intent_data["intent"], f"Failed for phrase: '{phrase}'")

    def test_assistant_open_history_handler(self):
        assistant = AssistantCore()
        res = assistant._handle_open_history({})
        self.assertEqual("__OPEN_HISTORY__", res)

    def test_conversation_history_ui_entrypoint(self):
        # En mode headless/mock, open_conversation_history ne doit pas planter
        try:
            conversation_history_ui.open_conversation_history(memory=self.memory)
        except Exception as exc:
            self.fail(f"open_conversation_history raised an unexpected exception: {exc}")


if __name__ == "__main__":
    unittest.main()

