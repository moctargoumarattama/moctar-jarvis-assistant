import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ai_brain


class AiBrainConfigTests(unittest.TestCase):
    def test_ollama_defaults_share_the_central_configuration(self):
        with patch("ai_brain.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            brain = ai_brain.AIBrain(api_type="ollama")

        self.assertEqual(ai_brain.config.OLLAMA_MODEL, ai_brain.DEFAULT_MODEL)
        self.assertEqual("qwen2.5:1.5b", brain.model)
        self.assertEqual(ai_brain.config.OLLAMA_BASE_URL, ai_brain.DEFAULT_BASE_URL)

    def test_ollama_settings_are_loaded_from_environment(self):
        with patch.dict(
            os.environ,
            {"OLLAMA_MODEL": "qwen2.5:1.5b", "OLLAMA_BASE_URL": "http://localhost:11434"},
            clear=True,
        ):
            model, base_url = ai_brain._ollama_settings()

        self.assertEqual("qwen2.5:1.5b", model)
        self.assertEqual("http://localhost:11434", base_url)

    def test_get_ai_status_reports_ollama_when_tags_are_available(self):
        response = Mock()
        response.read.return_value = b'{"models":[{"name":"qwen2.5:1.5b"}]}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with patch("ai_brain.urlopen", return_value=response):
            status = ai_brain.get_ai_status()

        self.assertEqual("ollama", status["mode"])
        self.assertEqual("qwen2.5:1.5b", status["model"])
        self.assertIn("Ollama", status["label"])

    def test_get_ai_status_rejects_a_missing_configured_model(self):
        response = Mock()
        response.read.return_value = b'{"models":[{"name":"other-model:latest"}]}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with patch("ai_brain._ollama_settings", return_value=("qwen2.5:1.5b", "http://localhost:11434")):
            with patch("ai_brain.urlopen", return_value=response):
                status = ai_brain.get_ai_status()

        self.assertFalse(status["online"])
        self.assertTrue(status["server_online"])
        self.assertFalse(status["model_available"])
        self.assertIn("Modele absent", status["label"])

    def test_get_ai_status_accepts_the_implicit_latest_tag(self):
        response = Mock()
        response.read.return_value = b'{"models":[{"name":"test-model:latest"}]}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with patch("ai_brain._ollama_settings", return_value=("test-model", "http://localhost:11434")):
            with patch("ai_brain.urlopen", return_value=response):
                status = ai_brain.get_ai_status()

        self.assertTrue(status["online"])
        self.assertTrue(status["model_available"])

    def test_ollama_uses_its_model_when_openai_is_also_configured(self):
        with patch("ai_brain.load_dotenv"), patch.dict(
            os.environ,
            {"OLLAMA_MODEL": "qwen2.5:1.5b", "OPENAI_MODEL": "gpt-4o-mini"},
            clear=True,
        ):
            brain = ai_brain.AIBrain()

        self.assertEqual("ollama", brain.api_type)
        self.assertEqual("qwen2.5:1.5b", brain.model)

    def test_explicit_ollama_ignores_openai_base_url(self):
        with patch("ai_brain.load_dotenv"), patch.dict(
            os.environ,
            {
                "OLLAMA_MODEL": "qwen2.5:1.5b",
                "OLLAMA_BASE_URL": "http://localhost:11434",
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_MODEL": "gpt-4o-mini",
            },
            clear=True,
        ):
            brain = ai_brain.AIBrain(api_type="ollama")

        self.assertEqual("http://localhost:11434", brain.base_url)
        self.assertEqual("qwen2.5:1.5b", brain.model)

    def test_openai_backend_preserves_its_model(self):
        with patch("ai_brain.load_dotenv"), patch.dict(
            os.environ,
            {
                "OLLAMA_MODEL": "qwen2.5:1.5b",
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_MODEL": "gpt-4o-mini",
            },
            clear=True,
        ):
            brain = ai_brain.AIBrain()

        self.assertEqual("openai", brain.api_type)
        self.assertEqual("gpt-4o-mini", brain.model)

    def test_explicit_model_overrides_backend_environment(self):
        with patch("ai_brain.load_dotenv"), patch.dict(
            os.environ,
            {"OLLAMA_MODEL": "other-model:latest", "OPENAI_MODEL": "gpt-4o-mini"},
            clear=True,
        ):
            brain = ai_brain.AIBrain(model="qwen2.5:1.5b")

        self.assertEqual("qwen2.5:1.5b", brain.model)

    def test_cloud_api_key_does_not_switch_loopback_ollama_backend(self):
        with patch("ai_brain.load_dotenv"), patch.dict(
            os.environ,
            {
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                "OLLAMA_MODEL": "qwen2.5:1.5b",
                "OPENAI_API_KEY": "test-key",
                "OPENAI_MODEL": "gpt-4o-mini",
            },
            clear=True,
        ):
            brain = ai_brain.AIBrain()

        self.assertEqual("ollama", brain.api_type)
        self.assertEqual("qwen2.5:1.5b", brain.model)

    def test_remote_dell_endpoint_remains_ollama_with_a_cloud_key(self):
        with patch("ai_brain.load_dotenv"), patch.dict(
            os.environ,
            {
                "OLLAMA_BASE_URL": "http://192.168.11.103:11434",
                "OLLAMA_MODEL": "qwen2.5:1.5b",
                "OPENAI_API_KEY": "test-key",
                "OPENAI_MODEL": "gpt-4o-mini",
            },
            clear=True,
        ):
            brain = ai_brain.AIBrain()

        self.assertEqual("ollama", brain.api_type)
        self.assertEqual("http://192.168.11.103:11434", brain.base_url)
        self.assertEqual("qwen2.5:1.5b", brain.model)

    def test_ask_ollama_posts_to_chat_endpoint(self):
        response = Mock()
        response.read.return_value = b'{"message":{"content":"bonjour"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with patch.dict(
            os.environ,
            {"OLLAMA_MODEL": "qwen2.5:1.5b", "OLLAMA_BASE_URL": "http://localhost:11434"},
            clear=True,
        ):
            with patch("ai_brain.urlopen", return_value=response) as open_mock:
                answer = ai_brain.ask_ollama("salut")

        self.assertEqual("bonjour", answer)
        open_mock.assert_called_once()
        request = open_mock.call_args.args[0]
        self.assertEqual("http://localhost:11434/api/chat", request.full_url)
        self.assertEqual("POST", request.method)
        self.assertEqual("application/json", request.headers["Content-type"])

    def test_chat_ollama_sends_tools_and_keeps_complete_response(self):
        raw_payload = {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "time", "arguments": {}}},
                ],
            }
        }
        response = Mock()
        response.read.return_value = json.dumps(raw_payload).encode("utf-8")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "time",
                    "description": "Retourne l'heure locale.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        with patch("ai_brain.urlopen", return_value=response) as open_mock:
            payload = ai_brain.chat_ollama(
                [{"role": "user", "content": "Quelle heure est-il ?"}],
                tools=tools,
                think=False,
            )

        self.assertEqual(raw_payload, payload)
        request_payload = json.loads(open_mock.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(tools, request_payload["tools"])
        self.assertFalse(request_payload["think"])
        self.assertEqual(512, request_payload["options"]["num_predict"])
        self.assertEqual(0.15, request_payload["options"]["temperature"])
        self.assertEqual(["Observation:", "<|im_end|>", "\n\nUser:"], request_payload["options"]["stop"])

    def test_universal_ollama_chat_keeps_enough_tokens_for_tool_arguments(self):
        response = Mock()
        response.read.return_value = b'{"message":{"content":"bonjour"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        brain = ai_brain.AIBrain(api_type="ollama", model="qwen2.5:1.5b")

        with patch("ai_brain.urlopen", return_value=response) as open_mock:
            brain.chat([{"role": "user", "content": "bonjour"}])

        payload = json.loads(open_mock.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(512, payload["options"]["num_predict"])
        self.assertEqual(0.15, payload["options"]["temperature"])
        self.assertEqual(["Observation:", "<|im_end|>", "\n\nUser:"], payload["options"]["stop"])

    def test_compact_ollama_options_bound_tokens_and_temperature(self):
        response = Mock()
        response.read.return_value = b'{"message":{"content":"bonjour"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        for requested_tokens, temperature, expected_tokens, expected_temperature in (
            (4096, 0.9, 512, 0.2),
            (96, 0.0, 384, 0.1),
            (384, 0.15, 384, 0.15),
        ):
            with self.subTest(tokens=requested_tokens), patch("ai_brain.urlopen", return_value=response) as open_mock:
                ai_brain.chat_ollama(
                    [{"role": "user", "content": "bonjour"}],
                    max_predict=requested_tokens, temperature=temperature,
                )
                payload = json.loads(open_mock.call_args.args[0].data.decode("utf-8"))
                self.assertEqual(expected_tokens, payload["options"]["num_predict"])
                self.assertEqual(expected_temperature, payload["options"]["temperature"])

    def test_qwen_ignores_unsupported_thinking_mode(self):
        response = Mock()
        response.read.return_value = b'{"message":{"content":"bonjour"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        with patch("ai_brain.urlopen", return_value=response) as open_mock:
            ai_brain.chat_ollama([{"role": "user", "content": "bonjour"}], model="qwen2.5:1.5b", think=True)
        payload = json.loads(open_mock.call_args.args[0].data.decode("utf-8"))
        self.assertFalse(payload["think"])

    def test_ollama_compatibility_endpoint_uses_the_same_inference_limits(self):
        response = Mock()
        response.read.return_value = b'{"choices":[{"message":{"content":"bonjour"},"finish_reason":"stop"}]}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        with patch("ai_brain._ollama_settings", return_value=("qwen2.5:1.5b", "http://localhost:11434/v1")), \
                patch("ai_brain.urlopen", return_value=response) as open_mock:
            ai_brain.chat_ollama([{"role": "user", "content": "bonjour"}], max_predict=4096, temperature=0.8)
        payload = json.loads(open_mock.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(512, payload["max_tokens"])
        self.assertEqual(0.2, payload["temperature"])
        self.assertEqual(["Observation:", "<|im_end|>", "\n\nUser:"], payload["stop"])

    def test_cloud_chat_preserves_openai_model_and_parameters(self):
        response = Mock()
        response.read.return_value = b'{"choices":[{"message":{"content":"bonjour"},"finish_reason":"stop"}]}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        brain = ai_brain.AIBrain(
            api_type="openai", base_url="https://api.openai.com/v1", model="gpt-4o-mini",
        )

        with patch("ai_brain.urlopen", return_value=response) as open_mock:
            result = brain.chat([{"role": "user", "content": "bonjour"}])

        payload = json.loads(open_mock.call_args.args[0].data.decode("utf-8"))
        self.assertEqual("gpt-4o-mini", payload["model"])
        self.assertEqual(0.2, payload["temperature"])
        self.assertEqual(512, payload["max_tokens"])
        self.assertNotIn("options", payload)
        self.assertEqual("bonjour", result["content"])

    def test_qwen_tool_arguments_are_normalized_from_native_and_text_json(self):
        arguments = {"target": "rapport solaire", "notes": "Panneaux orientés au sud"}
        for raw_payload in (
            {"message": {"content": "", "tool_calls": [{"function": {"name": "create_note", "arguments": arguments}}]}},
            {"message": {"content": "<tool_call>" + json.dumps({"name": "create_note", "arguments": arguments}) + "</tool_call>"}},
        ):
            response = Mock()
            response.read.return_value = json.dumps(raw_payload).encode("utf-8")
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)
            brain = ai_brain.AIBrain(api_type="ollama")

            with self.subTest(payload=raw_payload), patch("ai_brain.urlopen", return_value=response):
                result = brain.chat([{"role": "user", "content": "Crée ma note solaire"}])

            self.assertEqual("tool_calls", result["finish_reason"])
            self.assertEqual("create_note", result["tool_calls"][0]["name"])
            self.assertEqual(arguments, result["tool_calls"][0]["arguments"])

    def test_chat_ollama_reports_timeout_explicitly(self):
        with patch("ai_brain.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(ai_brain.OllamaTimeoutError):
                ai_brain.chat_ollama([{"role": "user", "content": "bonjour"}])

    def test_ask_ollama_can_return_complete_response(self):
        raw_payload = {"message": {"role": "assistant", "content": "reponse complete"}}
        with patch("ai_brain.chat_ollama", return_value=raw_payload):
            payload = ai_brain.ask_ollama("bonjour", return_full_response=True)

        self.assertEqual(raw_payload, payload)

    def test_ask_ollama_with_tools_keeps_complete_response_automatically(self):
        raw_payload = {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "time", "arguments": {}}}],
            }
        }
        with patch("ai_brain.chat_ollama", return_value=raw_payload):
            payload = ai_brain.ask_ollama(
                "quelle heure",
                tools=[{"type": "function", "function": {"name": "time"}}],
            )

        self.assertEqual(raw_payload, payload)

    def test_safe_ask_ollama_returns_fallback_on_failure(self):
        with patch("ai_brain.ask_ollama", side_effect=RuntimeError("boom")):
            answer = ai_brain.safe_ask_ollama("bonjour", fallback="fallback local")

        self.assertEqual("fallback local", answer)

    def test_ask_ollama_includes_images_when_provided(self):
        response = Mock()
        response.read.return_value = b'{"message":{"content":"analyse"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
            handle.write(b"fake-image")
            image_path = Path(handle.name)

        try:
            with patch("ai_brain.urlopen", return_value=response) as open_mock:
                ai_brain.ask_ollama("decris", images=[str(image_path)])
        finally:
            image_path.unlink(missing_ok=True)

        request = open_mock.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertIn("images", payload["messages"][1])
        self.assertEqual(1, len(payload["messages"][1]["images"]))

    def test_local_brain_chat_uses_ollama_path(self):
        with patch("ai_brain.can_answer_general_questions", return_value=True):
            with patch("ai_brain.safe_ask_ollama", return_value="message ameliore") as ask_mock:
                answer = ai_brain.LocalBrain().chat("Rends ce message plus pro", context="scheduler")

        self.assertEqual("message ameliore", answer)
        ask_mock.assert_called_once()
        self.assertIn("Contexte additionnel: scheduler", ask_mock.call_args.args[0])

class LocalBrainTests(unittest.TestCase):
    def setUp(self):
        self.brain = ai_brain.LocalBrain()

    def test_generates_daily_brief_from_local_context(self):
        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.brain.generate_daily_brief(
                todos=[
                    {"content": "appeler client"},
                    {"content": "verifier les panneaux"},
                ],
                reminders=[
                    {"content": "reunion chantier", "due_at": "2026-08-14T09:30:00"},
                ],
                history=[
                    {"intent": "open_project", "target": "audit energetique"},
                    {"intent": "open_project", "target": "audit energetique"},
                ],
                preferences={"favorite_music": "afrobeat"},
            )

        self.assertIn("Brief local", response)
        self.assertIn("reunion chantier", response)
        self.assertIn("appeler client", response)
        self.assertIn("afrobeat", response)

    def test_suggests_next_action_from_first_reminder(self):
        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.brain.suggest_next_action(
                todos=[{"content": "preparer devis"}],
                reminders=[{"content": "envoyer rapport", "due_at": "2026-08-14T08:00:00"}],
                history=[],
            )

        self.assertIn("envoyer rapport", response)

    def test_prioritization_pushes_urgent_task_first(self):
        ranked = self.brain.prioritize_tasks(
            todos=[
                {"content": "verifier archive"},
                {"content": "urgent appeler client"},
            ]
        )

        self.assertEqual("urgent appeler client", ranked[0]["content"])

    def test_project_focus_mode_uses_active_project(self):
        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.brain.project_focus_mode(
                todos=[
                    {"content": "noor_express corriger endpoint"},
                    {"content": "faire menage boite mail"},
                ],
                insights={"active_project": "noor_express"},
                history=[],
            )

        self.assertIn("noor_express", response)
        self.assertIn("corriger endpoint", response)

    def test_build_auto_routine_evening_label(self):
        with patch("ai_brain.can_answer_general_questions", return_value=False):
            response = self.brain.build_auto_routine(
                todos=[{"content": "urgent finir rapport"}],
                reminders=[],
                now=ai_brain.datetime(2026, 8, 14, 20, 0, 0),
                mode="auto",
            )

        self.assertIn("Routine soir auto", response)

    def test_answers_greeting_without_external_ai(self):
        response = self.brain.answer("bonjour")

        self.assertIn("Bonjour", response)
        self.assertIn("pret", response)

    def test_answers_simple_math_locally(self):
        response = self.brain.answer("combien font 0 x 0")

        self.assertEqual("0", response)

    def test_open_fact_question_prefers_ollama_with_short_prompt(self):
        with patch("ai_brain.can_answer_general_questions", return_value=True):
            with patch("ai_brain.safe_ask_ollama", return_value="Niamey est la capitale du Niger.") as ask_mock:
                response = self.brain.answer("ou est la capitale du niger")

        self.assertEqual("Niamey est la capitale du Niger.", response)
        self.assertIn("une ou deux phrases maximum", ask_mock.call_args.kwargs["system_prompt"])

    def test_activity_request_uses_ollama_with_personal_context(self):
        with patch("ai_brain.can_answer_general_questions", return_value=True):
            with patch(
                "ai_brain.safe_ask_ollama",
                return_value="Demain, commence par appeler les clients puis prepare l'offre.",
            ) as ask_mock:
                response = self.brain.answer(
                    "donne moi un plan pour demain pour baba market",
                    todos=[{"content": "appeler trois clients chauds"}],
                    reminders=[{"content": "envoyer offre", "due_at": "2026-08-24T09:00:00"}],
                    history=[{"intent": "open_project", "target": "baba_market", "response": "Projet ouvert."}],
                    insights={"active_project": "baba_market"},
                )

        self.assertIn("Demain", response)
        self.assertIn("Projet actif: baba_market", ask_mock.call_args.args[0])
        self.assertIn("Todos ouverts:", ask_mock.call_args.args[0])
        self.assertIn("activites quotidiennes", ask_mock.call_args.kwargs["system_prompt"])

    def test_enhance_social_post_uses_local_fallback_when_ai_is_disabled(self):
        with patch("ai_brain.can_answer_general_questions", return_value=False):
            result = self.brain.enhance_social_post(
                "",
                platform="facebook",
                title="Promotion Baba Market",
                media_paths=["C:/demo/photo.jpg", "C:/demo/video.mp4"],
            )

        self.assertIn("Promotion Baba Market", result)
        self.assertIn("commentaire", result.lower())


if __name__ == "__main__":
    unittest.main()
