import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import ai_brain


class AiBrainConfigTests(unittest.TestCase):
    def test_ollama_defaults_share_the_central_configuration(self):
        with patch("ai_brain.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            brain = ai_brain.AIBrain(api_type="ollama")

        self.assertEqual(ai_brain.config.OLLAMA_MODEL, ai_brain.DEFAULT_MODEL)
        self.assertEqual("qwen2.5:1.5b", brain.model)
        self.assertEqual(ai_brain.config.OLLAMA_BASE_URL, ai_brain.DEFAULT_BASE_URL)
        self.assertEqual({"temperature": 0.2, "max_tokens": 350, "top_p": 0.8}, ai_brain.GENERATION_CONFIG)
        self.assertEqual(ai_brain.GENERATION_CONFIG["max_tokens"], ai_brain.OLLAMA_MAX_PREDICT)
        self.assertEqual(ai_brain.GENERATION_CONFIG["temperature"], ai_brain.OLLAMA_TEMPERATURE)

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
            brain = ai_brain.AIBrain(api_type="auto")

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
            brain = ai_brain.AIBrain(api_type="auto")

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
            brain = ai_brain.AIBrain(api_type="auto")

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
            brain = ai_brain.AIBrain(api_type="auto")

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
        self.assertEqual(350, request_payload["options"]["num_predict"])
        self.assertEqual(0.2, request_payload["options"]["temperature"])
        self.assertEqual(0.8, request_payload["options"]["top_p"])
        self.assertEqual(["Observation:", "<|im_end|>", "\n\nUser:"], request_payload["options"]["stop"])

    def test_universal_ollama_chat_uses_the_generation_limits(self):
        response = Mock()
        response.read.return_value = b'{"message":{"content":"bonjour"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        brain = ai_brain.AIBrain(api_type="ollama", model="qwen2.5:1.5b")

        with patch("ai_brain.urlopen", return_value=response) as open_mock:
            brain.chat([{"role": "user", "content": "bonjour"}])

        payload = json.loads(open_mock.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(350, payload["options"]["num_predict"])
        self.assertEqual(0.2, payload["options"]["temperature"])
        self.assertEqual(0.8, payload["options"]["top_p"])
        self.assertEqual(["Observation:", "<|im_end|>", "\n\nUser:"], payload["options"]["stop"])

    def test_compact_ollama_options_bound_tokens_and_temperature(self):
        response = Mock()
        response.read.return_value = b'{"message":{"content":"bonjour"}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        for requested_tokens, temperature, expected_tokens, expected_temperature in (
            (4096, 0.9, 350, 0.2),
            (96, 0.0, 96, 0.1),
            (180, 0.15, 180, 0.15),
            (350, 0.2, 350, 0.2),
        ):
            with self.subTest(tokens=requested_tokens), patch("ai_brain.urlopen", return_value=response) as open_mock:
                ai_brain.chat_ollama(
                    [{"role": "user", "content": "bonjour"}],
                    max_predict=requested_tokens, temperature=temperature,
                )
                payload = json.loads(open_mock.call_args.args[0].data.decode("utf-8"))
                self.assertEqual(expected_tokens, payload["options"]["num_predict"])
                self.assertEqual(expected_temperature, payload["options"]["temperature"])
                self.assertEqual(0.8, payload["options"]["top_p"])

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
        for requested_tokens, expected_tokens in ((4096, 350), (96, 96), (180, 180)):
            with self.subTest(tokens=requested_tokens), \
                    patch("ai_brain._ollama_settings", return_value=("qwen2.5:1.5b", "http://localhost:11434/v1")), \
                    patch("ai_brain.urlopen", return_value=response) as open_mock:
                ai_brain.chat_ollama([{"role": "user", "content": "bonjour"}], max_predict=requested_tokens, temperature=0.8)
            payload = json.loads(open_mock.call_args.args[0].data.decode("utf-8"))
            self.assertEqual(expected_tokens, payload["max_tokens"])
            self.assertEqual(0.2, payload["temperature"])
            self.assertEqual(0.8, payload["top_p"])
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
        self.assertEqual(350, payload["max_tokens"])
        self.assertEqual(0.8, payload["top_p"])
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
        self.assertEqual(0.2, ask_mock.call_args.kwargs["temperature"])

class GeminiBrainTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AI_PROVIDER": "hybrid",
            "GEMINI_API_KEY": "unit-test-gemini-key",
            "GEMINI_MODEL": "gemini-test-model",
            "OLLAMA_MODEL": "qwen2.5:1.5b",
            "OLLAMA_BASE_URL": "http://localhost:11434",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.dotenv = patch("ai_brain.load_dotenv")
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)
        self.genai_patch = patch("ai_brain.genai")
        self.genai = self.genai_patch.start()
        self.addCleanup(self.genai_patch.stop)
        self.client = self.genai.Client.return_value
        self.client.models.generate_content.return_value = self.gemini_response("Reponse Gemini.")

    @staticmethod
    def gemini_response(text=None, function_call=None):
        part = SimpleNamespace(text=text, function_call=function_call, thought=False)
        candidate = SimpleNamespace(content=SimpleNamespace(parts=[part]), finish_reason="STOP")
        return SimpleNamespace(candidates=[candidate])

    @staticmethod
    def ollama_response(raw_payload):
        response = Mock()
        response.read.return_value = json.dumps(raw_payload).encode("utf-8")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        return response

    def test_provider_and_separate_models_are_loaded_from_environment(self):
        brain = ai_brain.AIBrain()

        self.assertEqual("hybrid", brain.ai_provider)
        self.assertEqual("qwen2.5:1.5b", brain.model)
        self.assertEqual("gemini-test-model", brain.gemini_model)
        self.genai.Client.assert_called_once()
        self.assertEqual("unit-test-gemini-key", self.genai.Client.call_args.kwargs["api_key"])

    def test_hybrid_prefers_successful_ollama_without_cloud_inference(self):
        brain = ai_brain.AIBrain(api_type="hybrid")
        response = self.ollama_response({"message": {"content": "Reponse locale."}})
        with patch("ai_brain.urlopen", return_value=response) as request:
            result = brain.chat([{"role": "user", "content": "Explique le solaire."}])

        self.assertEqual("Reponse locale.", result["content"])
        self.assertEqual("stop", result["finish_reason"])
        self.assertLessEqual(request.call_args.kwargs["timeout"], 15.0)
        self.client.models.generate_content.assert_not_called()

    def test_hybrid_uses_gemini_for_local_connection_timeout_http_and_json_failures(self):
        invalid_json = self.ollama_response({})
        invalid_json.read.return_value = b"not-json"
        failures = (
            URLError("Ollama offline"),
            TimeoutError("timed out"),
            HTTPError("http://localhost:11434/api/chat", 500, "failed", {}, None),
            invalid_json,
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                brain = ai_brain.AIBrain(api_type="hybrid")
                self.client.models.generate_content.reset_mock()
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch("ai_brain.urlopen", **kwargs):
                    result = brain.chat([{"role": "user", "content": "Explique le solaire."}])

                self.assertEqual("Reponse Gemini.", result["content"])
                self.assertEqual("stop", result["finish_reason"])
                self.assertNotIn("Ollama", result["content"])
                self.client.models.generate_content.assert_called_once()

    def test_hybrid_uses_gemini_for_invalid_local_payload(self):
        brain = ai_brain.AIBrain(api_type="hybrid")
        with patch("ai_brain.urlopen", return_value=self.ollama_response({"unexpected": True})):
            result = brain.chat([{"role": "user", "content": "Explique le solaire."}])

        self.assertEqual("Reponse Gemini.", result["content"])
        self.client.models.generate_content.assert_called_once()

    def test_hybrid_uses_gemini_when_local_backend_returns_an_error_or_crashes(self):
        for kwargs in (
            {"return_value": {"content": "Ollama inaccessible", "tool_calls": [], "finish_reason": "error"}},
            {"side_effect": RuntimeError("local crash")},
        ):
            with self.subTest(backend=kwargs):
                brain = ai_brain.AIBrain(api_type="hybrid")
                self.client.models.generate_content.reset_mock()
                with patch.object(brain, "_chat_ollama", **kwargs):
                    result = brain.chat([{"role": "user", "content": "Explique le solaire."}])

                self.assertEqual("Reponse Gemini.", result["content"])
                self.client.models.generate_content.assert_called_once()

    def test_hybrid_preserves_a_shorter_caller_timeout(self):
        brain = ai_brain.AIBrain(api_type="hybrid", timeout=0.25)
        with patch("ai_brain.urlopen", side_effect=TimeoutError("local timeout")) as request:
            result = brain.chat([{"role": "user", "content": "Explique le solaire."}])

        self.assertEqual("Reponse Gemini.", result["content"])
        self.assertLessEqual(request.call_args.kwargs["timeout"], 0.25)
        self.assertEqual(0.25, brain.timeout)

    def test_hybrid_bounds_real_wait_even_if_local_call_ignores_socket_timeout(self):
        brain = ai_brain.AIBrain(api_type="hybrid", timeout=60)
        release = threading.Event()

        def stalled_local(*args, **kwargs):
            release.wait(2.0)
            return {"content": "Trop tard.", "tool_calls": [], "finish_reason": "stop"}

        try:
            with patch("ai_brain.HYBRID_OLLAMA_TIMEOUT_SECONDS", 0.03), \
                    patch.object(brain, "_chat_ollama", side_effect=stalled_local):
                started = time.monotonic()
                result = brain.chat([{"role": "user", "content": "Explique le solaire."}])
                duration = time.monotonic() - started
        finally:
            release.set()

        self.assertEqual("Reponse Gemini.", result["content"])
        self.assertLess(duration, 0.75)
        self.assertEqual(60.0, brain.timeout)

    def test_gemini_only_never_calls_ollama(self):
        brain = ai_brain.AIBrain(api_type="gemini")
        with patch("ai_brain.urlopen") as local:
            result = brain.chat([{"role": "user", "content": "Explique le solaire."}])

        self.assertEqual("Reponse Gemini.", result["content"])
        self.client.models.generate_content.assert_called_once()
        local.assert_not_called()

    def test_explicit_ollama_never_falls_back_to_gemini(self):
        brain = ai_brain.AIBrain(api_type="ollama")
        with patch("ai_brain.urlopen", side_effect=URLError("Ollama offline")):
            result = brain.chat([{"role": "user", "content": "Explique le solaire."}])

        self.assertEqual("error", result["finish_reason"])
        self.client.models.generate_content.assert_not_called()

    def test_missing_key_or_sdk_preserves_the_local_backend(self):
        for missing_sdk in (False, True):
            with self.subTest(missing_sdk=missing_sdk):
                context = patch("ai_brain.genai", None) if missing_sdk else patch.dict(os.environ, {"GEMINI_API_KEY": ""})
                with context:
                    brain = ai_brain.AIBrain(api_type="hybrid")
                    response = self.ollama_response({"message": {"content": "Local disponible."}})
                    with patch("ai_brain.urlopen", return_value=response):
                        result = brain.chat([{"role": "user", "content": "Explique le solaire."}])

                self.assertEqual("Local disponible.", result["content"])
                self.client.models.generate_content.assert_not_called()

    def test_gemini_preserves_tool_schemas_calls_and_observations(self):
        brain = ai_brain.AIBrain(api_type="gemini")
        call = SimpleNamespace(id="gemini-call-1", name="create_note", args={"target": "rapport solaire"})
        self.client.models.generate_content.side_effect = [
            self.gemini_response(function_call=call),
            self.gemini_response("Note creee."),
        ]
        tools = [{"type": "function", "function": {
            "name": "create_note", "description": "Cree une note locale.",
            "parameters": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
        }}]
        messages = [{"role": "system", "content": "Tu es Jarvis."}, {"role": "user", "content": "Cree la note."}]
        first = brain.chat(messages, tools=tools)

        self.assertEqual("tool_calls", first["finish_reason"])
        self.assertEqual([{"id": "gemini-call-1", "name": "create_note", "arguments": {"target": "rapport solaire"}}], first["tool_calls"])
        request = self.client.models.generate_content.call_args.kwargs
        self.assertEqual("gemini-test-model", request["model"])
        self.assertEqual("Tu es Jarvis.", request["config"]["system_instruction"])
        self.assertEqual(0.2, request["config"]["temperature"])
        self.assertEqual(350, request["config"]["max_output_tokens"])
        self.assertEqual(0.8, request["config"]["top_p"])
        declaration = request["config"]["tools"][0]["function_declarations"][0]
        self.assertEqual("create_note", declaration["name"])
        self.assertEqual(tools[0]["function"]["parameters"], declaration["parameters_json_schema"])
        self.assertTrue(request["config"]["automatic_function_calling"]["disable"])
        self.assertEqual([{"role": "user", "parts": [{"text": "Cree la note."}]}], request["contents"])

        observation = {"success": True, "message": "Note creee."}
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": "gemini-call-1", "type": "function", "function": {"name": "create_note", "arguments": '{"target": "rapport solaire"}'}}]},
            {"role": "tool", "name": "create_note", "tool_call_id": "gemini-call-1", "content": json.dumps(observation)},
        ])
        second = brain.chat(messages, tools=tools, temperature=0.15)

        self.assertEqual("Note creee.", second["content"])
        self.assertEqual(0.15, self.client.models.generate_content.call_args.kwargs["config"]["temperature"])
        contents = self.client.models.generate_content.call_args.kwargs["contents"]
        self.assertEqual("model", contents[1]["role"])
        self.assertEqual("create_note", contents[1]["parts"][0]["function_call"]["name"])
        response = contents[2]["parts"][0]["function_response"]
        self.assertEqual("create_note", response["name"])
        self.assertEqual(observation, response["response"])

    def test_legacy_ask_helper_keeps_raw_tool_response_with_gemini_fallback(self):
        call = SimpleNamespace(id="gemini-time", name="time", args={})
        self.client.models.generate_content.return_value = self.gemini_response(function_call=call)
        tools = [{"type": "function", "function": {"name": "time", "parameters": {"type": "object", "properties": {}}}}]
        with patch("ai_brain.urlopen", side_effect=URLError("Ollama offline")):
            result = ai_brain.ask_ollama("Quelle heure est-il ?", tools=tools)

        self.assertEqual("tool_calls", result["done_reason"])
        self.assertEqual("time", result["message"]["tool_calls"][0]["function"]["name"])
        self.assertEqual({}, result["message"]["tool_calls"][0]["function"]["arguments"])
        self.client.close.assert_called_once()

    def test_legacy_ask_falls_back_when_local_message_has_no_content_or_tools(self):
        for content in (None, ""):
            with self.subTest(content=content):
                self.client.models.generate_content.reset_mock()
                response = self.ollama_response({"message": {"content": content}})
                with patch("ai_brain.urlopen", return_value=response):
                    result = ai_brain.ask_ollama("Explique le fonctionnement du solaire.")

                self.assertEqual("Reponse Gemini.", result)
                self.client.models.generate_content.assert_called_once()

    def test_explicit_environment_provider_wins_over_cloud_settings_for_remote_ollama(self):
        for provider in ("hybrid", "ollama"):
            with self.subTest(provider=provider), patch.dict(os.environ, {
                "AI_PROVIDER": provider,
                "OLLAMA_BASE_URL": "http://192.168.11.103:8080",
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_KEY": "test-openai-key",
                "OPENAI_MODEL": "gpt-4o-mini",
            }):
                brain = ai_brain.AIBrain()

                self.assertEqual(provider, brain.ai_provider)
                self.assertEqual("ollama", brain.api_type)
                self.assertEqual("http://192.168.11.103:8080", brain.base_url)
                self.assertEqual("qwen2.5:1.5b", brain.model)

    def test_default_hybrid_provider_takes_precedence_over_legacy_openai_environment(self):
        with patch.dict(os.environ, {
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_API_KEY": "test-openai-key",
            "OPENAI_MODEL": "gpt-4o-mini",
            "OLLAMA_MODEL": "qwen2.5:1.5b",
        }, clear=True), patch.object(ai_brain.config, "ai_provider", "hybrid"):
            brain = ai_brain.AIBrain()

        self.assertEqual("hybrid", brain.ai_provider)
        self.assertEqual("ollama", brain.api_type)
        self.assertEqual("qwen2.5:1.5b", brain.model)
        self.assertNotEqual("https://api.openai.com/v1", brain.base_url)

    def test_local_brain_general_answers_use_gemini_when_ollama_is_offline(self):
        with patch("ai_brain.can_answer_general_questions", return_value=True), \
                patch("ai_brain.urlopen", side_effect=URLError("Ollama offline")):
            result = ai_brain.LocalBrain().answer("Explique le fonctionnement d'un panneau solaire")

        self.assertEqual("Reponse Gemini.", result)
        self.client.models.generate_content.assert_called_once()

    def test_hybrid_status_reports_cloud_readiness_when_ollama_is_offline(self):
        with patch("ai_brain.urlopen", side_effect=URLError("Ollama offline")):
            status = ai_brain.get_ai_status()

        self.assertTrue(status["online"])
        self.assertFalse(status["ollama_online"])
        self.assertEqual("gemini", status["mode"])
        self.assertEqual("gemini-test-model", status["model"])
        self.client.models.generate_content.assert_not_called()

    def test_gemini_status_does_not_probe_ollama_or_spend_api_quota(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "gemini"}), patch("ai_brain.urlopen") as local:
            status = ai_brain.get_ai_status()

        self.assertTrue(status["online"])
        self.assertEqual("gemini", status["mode"])
        local.assert_not_called()
        self.client.models.generate_content.assert_not_called()

    def test_hybrid_status_stays_offline_without_cloud_credentials(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), \
                patch("ai_brain.urlopen", side_effect=URLError("Ollama offline")):
            status = ai_brain.get_ai_status()

        self.assertFalse(status["online"])
        self.client.models.generate_content.assert_not_called()


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
        self.assertEqual(96, ask_mock.call_args.kwargs["max_predict"])
        self.assertEqual(0.2, ask_mock.call_args.kwargs["temperature"])

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
        self.assertEqual(0.2, ask_mock.call_args.kwargs["temperature"])

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
