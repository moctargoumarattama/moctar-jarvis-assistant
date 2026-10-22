"""Tests unitaires pour le système de mémoire duale (AssistantMemory) et ses outils d'actions."""

import json
from pathlib import Path
import tempfile
import unittest

from actions import execute_tool, get_all_tools_schema
from actions.personal_actions import store_memory_fact, search_memory_facts
from assistant_memory import AssistantMemory, SessionMemory, UserMemoryStore
from agent_core import AgentCore


class AssistantMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_memory.db"
        self.memory = AssistantMemory(db_path=self.db_path)

    def tearDown(self):
        # En Windows, s'assurer que toutes les connexions sont fermées
        self.temp_dir.cleanup()

    def test_save_and_search_facts_exact_and_token(self):
        # Sauvegarde d'un fait réseau
        ok1 = self.memory.save_fact(
            category="network",
            key="vps_ip",
            value="192.168.1.100",
            importance=3,
        )
        self.assertTrue(ok1)

        # Sauvegarde d'un fait domotique
        ok2 = self.memory.save_fact(
            category="iot",
            key="salon_relay_port",
            value="8088",
            importance=2,
        )
        self.assertTrue(ok2)

        # Recherche exacte par clé
        results = self.memory.search_facts("vps_ip")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual("192.168.1.100", results[0]["content"])

        # Recherche sémantique / mots-clés
        results_keyword = self.memory.search_facts("adresse IP du VPS")
        self.assertGreaterEqual(len(results_keyword), 1)
        self.assertEqual("vps_ip", results_keyword[0]["key"])

        # Recherche par catégorie
        results_iot = self.memory.search_facts("relay", category="iot")
        self.assertEqual(1, len(results_iot))
        self.assertEqual("salon_relay_port", results_iot[0]["key"])

    def test_upsert_fact_updates_content(self):
        self.memory.save_fact(category="prefs", key="theme", value="dark", importance=1)
        self.memory.save_fact(category="prefs", key="theme", value="light", importance=2)

        results = self.memory.search_facts("theme", category="prefs")
        self.assertEqual(1, len(results))
        self.assertEqual("light", results[0]["content"])
        self.assertEqual(2, results[0]["importance"])

    def test_delete_fact(self):
        self.memory.save_fact(category="temp", key="test_key", value="123")
        self.assertTrue(self.memory.delete_fact(category="temp", key="test_key"))
        results = self.memory.search_facts("test_key", category="temp")
        self.assertEqual([], results)

    def test_get_system_context_snippet(self):
        self.memory.save_fact(category="network", key="nas_ip", value="10.0.0.50", importance=3)
        snippet = self.memory.get_system_context_snippet("Quelle est l'IP du NAS ?")
        self.assertIn("FAITS MÉMORISÉS ET PRÉFÉRENCES", snippet)
        self.assertIn("nas_ip = 10.0.0.50", snippet)

        empty_snippet = self.memory.get_system_context_snippet("Quelque chose d'introuvable")
        self.assertEqual("", empty_snippet)

    def test_sliding_window_context(self):
        session_id = "test_session_1"
        for i in range(10):
            self.memory.add_message(session_id, "user", f"Demande {i}")
            self.memory.add_message(session_id, "assistant", f"Réponse {i}")

        # Fenêtre glissante par défaut (max_turns=2 -> 4 messages)
        window = self.memory.get_context_window(session_id, max_turns=2)
        self.assertEqual(4, len(window))
        self.assertEqual("Demande 8", window[0]["content"])
        self.assertEqual("Réponse 9", window[-1]["content"])

    def test_sliding_window_prunes_orphaned_tool_calls(self):
        session_id = "test_session_tools"
        self.memory.add_message(session_id, "user", "Message 1")
        self.memory.add_message(session_id, "assistant", "Appel", tool_calls=[{"id": "call_1"}])
        self.memory.add_message(session_id, "tool", '{"ok": true}', tool_call_id="call_1")
        self.memory.add_message(session_id, "assistant", "Conclut")

        # Si on coupe la fenêtre à 2 éléments (tool et assistant sans l'appelant)
        window = self.memory.get_context_window(session_id, max_turns=2)
        # Le premier message ne doit pas être un message 'tool' orphelin
        if window:
            self.assertNotEqual("tool", window[0]["role"])

    def test_anti_doublon_add_message(self):
        """Vérifie que l'enregistrement consécutif identique (double mémoire) est neutralisé."""
        session_id = "test_dedup_session"
        # Ajout du même message utilisateur 2 fois de suite
        self.memory.add_message(session_id, "user", "Mets la musique.")
        self.memory.add_message(session_id, "user", "Mets la musique.")

        # Ajout de la réponse assistant 2 fois de suite
        self.memory.add_message(session_id, "assistant", "Musique lancée, Moctar.")
        self.memory.add_message(session_id, "assistant", "Musique lancée, Moctar.")

        window = self.memory.get_context_window(session_id, max_turns=10)
        self.assertEqual(2, len(window))
        self.assertEqual("user", window[0]["role"])
        self.assertEqual("Mets la musique.", window[0]["content"])
        self.assertEqual("assistant", window[1]["role"])
        self.assertEqual("Musique lancée, Moctar.", window[1]["content"])

    def test_get_last_assistant_message(self):
        """Vérifie la récupération précise du dernier propos de l'assistant pour la répétition."""
        session_id = "test_repeat_session"
        self.assertIsNone(self.memory.get_last_assistant_message(session_id))
        self.memory.add_message(session_id, "user", "Quelle heure est-il ?")
        self.memory.add_message(session_id, "assistant", "Il est 14 heures 20, Moctar.")
        last_msg = self.memory.get_last_assistant_message(session_id)
        self.assertEqual("Il est 14 heures 20, Moctar.", last_msg)

    def test_backward_compatibility_classes(self):
        session_mem = SessionMemory()
        session_mem.add("user", "Bonjour")
        history = session_mem.get_history()
        self.assertEqual(1, len(history))
        self.assertEqual("Bonjour", history[0]["content"])

        user_store = UserMemoryStore(self.temp_dir.name)
        ok = user_store.remember("ville", "Paris")
        self.assertTrue(ok)
        val = user_store.recall("ville")
        self.assertEqual("Paris", val)


class MemoryToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tools_memory.db"
        self.custom_memory = AssistantMemory(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_store_and_search_memory_fact_functions(self):
        # Test direct store_memory_fact
        res_store = store_memory_fact(
            key="server_port",
            value="8000",
            category="network",
            importance=2,
            memory_instance=self.custom_memory,
        )
        self.assertTrue(res_store["success"])
        self.assertEqual("store_memory_fact", res_store["action"])

        # Test direct search_memory_facts
        res_search = search_memory_facts(
            query="server port",
            category="network",
            limit=2,
            memory_instance=self.custom_memory,
        )
        self.assertTrue(res_search["success"])
        self.assertEqual("search_memory_facts", res_search["action"])
        self.assertGreaterEqual(len(res_search["data"]), 1)
        self.assertEqual("8000", res_search["data"][0]["content"])

    def test_tools_registered_in_dispatcher_and_schema(self):
        schemas = get_all_tools_schema()
        names = [s["function"]["name"] for s in schemas]
        self.assertIn("store_memory_fact", names)
        self.assertIn("search_memory_facts", names)

        # Exécution via execute_tool
        exec_res = execute_tool(
            "store_memory_fact",
            {"key": "test_agent_key", "value": "test_agent_value", "category": "test"},
        )
        self.assertTrue(exec_res["success"])

        search_res = execute_tool(
            "search_memory_facts",
            {"query": "test_agent_key"},
        )
        self.assertTrue(search_res["success"])
        self.assertGreaterEqual(len(search_res["data"]), 1)


class AgentCoreMemoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "agent_memory.db"
        self.memory = AssistantMemory(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_agent_core_injects_memory_snippet_and_persists_turns(self):
        # Enregistrer un fait en amont
        self.memory.save_fact(category="credentials", key="mqtt_broker", value="10.0.0.42", importance=3)

        captured_messages = []

        def mock_chat_client(messages, **kwargs):
            captured_messages.extend(messages)
            return {"content": "J'ai bien noté que le broker MQTT est à 10.0.0.42.", "tool_calls": [], "finish_reason": "stop"}

        agent = AgentCore(
            chat_client=mock_chat_client,
            memory=self.memory,
        )

        response = agent.process_query("Quel est le broker MQTT ?", session_id="session_alpha")

        self.assertIn("10.0.0.42", response)
        # Vérifier que le snippet de mémoire était dans le system prompt
        system_msg = next((m for m in captured_messages if m["role"] == "system"), None)
        self.assertIsNotNone(system_msg)
        self.assertIn("mqtt_broker = 10.0.0.42", system_msg["content"])

        # Vérifier que le dialogue a été persisté dans la session_alpha
        context = self.memory.get_context_window("session_alpha")
        self.assertEqual(2, len(context))
        self.assertEqual("user", context[0]["role"])
        self.assertEqual("Quel est le broker MQTT ?", context[0]["content"])
        self.assertEqual("assistant", context[1]["role"])
        self.assertIn("10.0.0.42", context[1]["content"])


if __name__ == "__main__":
    unittest.main()
