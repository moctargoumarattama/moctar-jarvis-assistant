"""Direct Gemini configuration, SDK contracts and deterministic local utilities."""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import urllib.request

import ai_brain


class AiBrainConfigTests(unittest.TestCase):
    def test_defaults_share_central_gemini_configuration(self):
        with patch("ai_brain.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            brain = ai_brain.AIBrain()
        self.assertEqual("gemini", brain.ai_provider)
        self.assertEqual("gemini", brain.api_type)
        self.assertEqual("gemini-3.5-flash-lite", brain.model)
        self.assertEqual(ai_brain.config.gemini_model, ai_brain.DEFAULT_MODEL)
        self.assertEqual({"temperature": 0.6, "max_tokens": 350, "top_p": 0.8}, ai_brain.GENERATION_CONFIG)
        self.assertEqual(15.0, brain.gemini_timeout)

    def test_legacy_environment_providers_always_resolve_to_cloud(self):
        for provider in ("ollama", "hybrid", "openai", "auto", "invalid", "gemini"):
            with self.subTest(provider=provider), patch.dict(os.environ, {
                "AI_PROVIDER": provider, "OLLAMA_BASE_URL": "http://localhost:11434",
                "OLLAMA_MODEL": "retired-model", "OPENAI_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_MODEL": "gpt-retired", "GEMINI_MODEL": "gemini-test",
            }), patch.object(urllib.request, "urlopen", side_effect=AssertionError("local probe")) as probe:
                brain = ai_brain.AIBrain()
                status = ai_brain.get_ai_status()
                self.assertEqual("gemini", brain.ai_provider)
                self.assertEqual("gemini", brain.api_type)
                self.assertEqual("gemini-test", brain.model)
                self.assertIn("googleapis.com", brain.base_url)
                self.assertEqual("gemini", status["provider"])
                self.assertNotIn("ollama_online", status)
                probe.assert_not_called()

    def test_legacy_constructor_providers_always_use_gemini(self):
        for provider in ("ollama", "hybrid", "openai", "auto", "gemini"):
            with self.subTest(provider=provider):
                brain = ai_brain.AIBrain(api_type=provider, base_url="http://localhost:11434", timeout=0.1)
                self.assertEqual("gemini", brain.api_type)
                self.assertIn("googleapis.com", brain.base_url)
                self.assertEqual(15.0, brain.gemini_timeout)

    def test_explicit_cloud_model_overrides_environment(self):
        brain = ai_brain.AIBrain(model="gemini-custom")
        self.assertEqual("gemini-custom", brain.model)
        self.assertEqual("gemini-custom", brain.gemini_model)

    def test_cloud_health_never_probes_or_consumes_inference_quota(self):
        with patch.object(urllib.request, "urlopen", side_effect=AssertionError("local probe")) as probe:
            status = ai_brain.get_ai_status()
            brain = ai_brain.AIBrain()
            for request_probe in (False, True):
                health = brain.get_health_status(probe=request_probe)
                self.assertTrue(health["online"])
                self.assertEqual("gemini", health["active_provider"])
                self.assertNotIn("ollama_online", health)
        self.assertTrue(status["online"])
        self.assertEqual("gemini", status["mode"])
        self.assertIn("Gemini Cloud", status["label"])
        probe.assert_not_called()

    def test_cloud_health_without_credentials_is_offline(self):
        with patch("ai_brain.load_dotenv"), patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            status = ai_brain.get_ai_status()
            brain = ai_brain.AIBrain()
        self.assertFalse(status["online"])
        self.assertFalse(brain.get_health_status(probe=False)["online"])

    def test_missing_sdk_is_reported_without_local_server_probe(self):
        with patch("ai_brain.genai", None):
            brain = ai_brain.AIBrain()
            status = brain.get_health_status(probe=False)
        self.assertIsNone(brain.gemini_client)
        self.assertFalse(status["online"])

    def test_chat_ai_preserves_native_plugin_response(self):
        with patch.object(ai_brain.AIBrain, "chat", return_value={
            "content": "Cloud direct", "tool_calls": [], "finish_reason": "stop",
        }) as chat:
            result = ai_brain.chat_ai([{"role": "user", "content": "Bonjour"}])
        self.assertEqual("Cloud direct", result["message"]["content"])
        self.assertEqual("stop", result["done_reason"])
        chat.assert_called_once()

    def test_ask_ai_returns_text_without_tools(self):
        with patch("ai_brain.chat_ai", return_value={
            "message": {"role": "assistant", "content": "Réponse Cloud"},
        }):
            self.assertEqual("Réponse Cloud", ai_brain.ask_ai("Bonjour"))

    def test_ask_ai_can_return_complete_response(self):
        payload = {"message": {"role": "assistant", "content": "Réponse complète"}}
        with patch("ai_brain.chat_ai", return_value=payload):
            self.assertEqual(payload, ai_brain.ask_ai("Bonjour", return_full_response=True))

    def test_ask_ai_with_tools_keeps_complete_response(self):
        payload = {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "function": {"name": "time", "arguments": {}},
        }]}}
        with patch("ai_brain.chat_ai", return_value=payload):
            self.assertEqual(payload, ai_brain.ask_ai("Heure", tools=[{
                "type": "function", "function": {"name": "time"},
            }]))

    def test_safe_ask_ai_returns_fallback_and_logs_failure(self):
        failure = RuntimeError("cloud down")
        with patch("ai_brain.ask_ai", side_effect=failure), self.assertLogs(ai_brain.logger, level="WARNING") as captured:
            result = ai_brain.safe_ask_ai("Bonjour", fallback="Réponse déterministe")
        self.assertEqual("Réponse déterministe", result)
        self.assertTrue(any(record.exc_info for record in captured.records))

    def test_deprecated_helpers_only_forward_to_cloud(self):
        for old_name, new_name in (("ask_ollama", "ask_ai"), ("chat_ollama", "chat_ai"), ("safe_ask_ollama", "safe_ask_ai")):
            with self.subTest(helper=old_name), patch.object(ai_brain, new_name, return_value="Cloud") as cloud:
                self.assertEqual("Cloud", getattr(ai_brain, old_name)("Bonjour"))
                cloud.assert_called_once()

    def test_image_helper_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "capture.jpg"
            image.write_bytes(b"fake-image")
            with patch.object(ai_brain.AIBrain, "chat", return_value={
                "content": "Analyse", "tool_calls": [], "finish_reason": "stop",
            }) as chat:
                result = ai_brain.ask_ai("Décris", images=[str(image)])
            self.assertEqual("Analyse", result)
            messages = chat.call_args.args[0]
            self.assertEqual(1, len(messages[-1]["images"]))
            self.assertEqual(b"fake-image", image.read_bytes())

    def test_legacy_payload_normalizers_preserve_plugin_arguments(self):
        brain = ai_brain.AIBrain()
        arguments = {"target": "rapport solaire", "notes": "Panneaux orientés au sud"}
        for payload in (
            {"message": {"content": "", "tool_calls": [{"function": {"name": "create_note", "arguments": arguments}}]}},
            {"message": {"content": "<tool_call>" + json.dumps({"name": "create_note", "arguments": arguments}) + "</tool_call>"}},
        ):
            with self.subTest(payload=payload):
                result = brain._normalize_ollama_payload(payload)
                self.assertEqual("tool_calls", result["finish_reason"])
                self.assertEqual(arguments, result["tool_calls"][0]["arguments"])

    def test_local_assistant_chat_uses_gemini(self):
        with patch("ai_brain.can_answer_general_questions", return_value=True), patch("ai_brain.safe_ask_ai", return_value="Message amélioré") as ask:
            result = ai_brain.LocalBrain().chat("Améliore ce message", context="scheduler")
        self.assertEqual("Message amélioré", result)
        self.assertIn("Contexte additionnel: scheduler", ask.call_args.args[0])
        self.assertEqual(ai_brain.GENERATION_CONFIG["temperature"], ask.call_args.kwargs["temperature"])


class GeminiBrainTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AI_PROVIDER": "gemini",
            "GEMINI_API_KEY": "unit-test-gemini-key",
            "GEMINI_MODEL": "gemini-test-model",
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

    def test_gemini_accepts_a_plain_text_prompt(self):
        brain = ai_brain.AIBrain(api_type="gemini")

        result = brain.chat("Explique le solaire.")

        self.assertEqual("Reponse Gemini.", result["content"])
        contents = self.client.models.generate_content.call_args.kwargs["contents"]
        self.assertEqual("user", contents[0].role)
        self.assertEqual("Explique le solaire.", contents[0].parts[0].text)

    def test_ask_returns_text_for_plain_prompts_and_message_history(self):
        brain = ai_brain.AIBrain(api_type="gemini")
        for prompt in ("Explique le solaire.", [{"role": "user", "content": "Explique le solaire."}]):
            with self.subTest(prompt=prompt):
                self.client.models.generate_content.reset_mock()

                result = brain.ask(prompt)

                self.assertEqual("Reponse Gemini.", result)
                self.client.models.generate_content.assert_called_once()

    def test_gemini_generation_error_logs_original_exception_and_traceback(self):
        brain = ai_brain.AIBrain(api_type="gemini")
        failure = RuntimeError("Google 400: unsupported generation configuration")
        self.client.models.generate_content.side_effect = failure

        with self.assertLogs(ai_brain.logger, level="WARNING") as captured:
            result = brain.chat("Explique le solaire.")

        self.assertEqual("error", result["finish_reason"])
        self.assertNotIn(str(failure), result["content"])
        record = next(record for record in captured.records if str(failure) in record.getMessage())
        self.assertIs(failure, record.exc_info[1])
        self.assertIsNotNone(record.exc_info[2])
        self.assertIn("Traceback (most recent call last)", "\n".join(captured.output))

    def test_gemini_initialization_error_logs_original_exception_and_traceback(self):
        failure = RuntimeError("Google SDK client initialization failed")
        self.genai.Client.side_effect = failure

        with self.assertLogs(ai_brain.logger, level="WARNING") as captured:
            brain = ai_brain.AIBrain(api_type="gemini")

        self.assertIsNone(brain.gemini_client)
        record = next(record for record in captured.records if str(failure) in record.getMessage())
        self.assertIs(failure, record.exc_info[1])
        self.assertIsNotNone(record.exc_info[2])

    def test_outer_cloud_request_error_logs_original_exception_and_traceback(self):
        brain = ai_brain.AIBrain(api_type="gemini")
        failure = RuntimeError("Unexpected cloud worker failure")

        with patch.object(brain, "_chat_gemini", side_effect=failure), \
                self.assertLogs(ai_brain.logger, level="WARNING") as captured:
            result = brain.chat("Explique le solaire.")

        self.assertEqual("error", result["finish_reason"])
        record = next(record for record in captured.records if str(failure) in record.getMessage())
        self.assertIs(failure, record.exc_info[1])
        self.assertIsNotNone(record.exc_info[2])

    def test_missing_gemini_client_logs_configuration_failure_with_traceback(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            brain = ai_brain.AIBrain(api_type="gemini")

        with self.assertLogs(ai_brain.logger, level="WARNING") as captured:
            result = brain.chat("Explique le solaire.")

        self.assertEqual("error", result["finish_reason"])
        self.client.models.generate_content.assert_not_called()
        self.assertTrue(any(record.exc_info and record.exc_info[2] is not None
                            for record in captured.records))

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
        self.assertIsInstance(request["config"], ai_brain.genai_types.GenerateContentConfig)
        generation_config = request["config"].model_dump(mode="json", exclude_none=True)
        self.assertEqual("Tu es Jarvis.", generation_config["system_instruction"])
        self.assertEqual(ai_brain.GENERATION_CONFIG["temperature"], generation_config["temperature"])
        self.assertEqual(350, generation_config["max_output_tokens"])
        self.assertEqual(0.8, generation_config["top_p"])
        declaration = generation_config["tools"][0]["function_declarations"][0]
        self.assertEqual("create_note", declaration["name"])
        self.assertEqual(tools[0]["function"]["parameters"], declaration["parameters_json_schema"])
        self.assertTrue(generation_config["automatic_function_calling"]["disable"])
        contents = [content.model_dump(mode="json", exclude_none=True) for content in request["contents"]]
        self.assertEqual([{"role": "user", "parts": [{"text": "Cree la note."}]}], contents)

        observation = {"success": True, "message": "Note creee."}
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": "gemini-call-1", "type": "function", "function": {"name": "create_note", "arguments": '{"target": "rapport solaire"}'}}]},
            {"role": "tool", "name": "create_note", "tool_call_id": "gemini-call-1", "content": json.dumps(observation)},
        ])
        second = brain.chat(messages, tools=tools, temperature=0.15)

        self.assertEqual("Note creee.", second["content"])
        self.assertEqual(0.15, self.client.models.generate_content.call_args.kwargs["config"].temperature)
        contents = [content.model_dump(mode="json", exclude_none=True)
                    for content in self.client.models.generate_content.call_args.kwargs["contents"]]
        self.assertEqual("model", contents[1]["role"])
        self.assertEqual("create_note", contents[1]["parts"][0]["function_call"]["name"])
        response = contents[2]["parts"][0]["function_response"]
        self.assertEqual("create_note", response["name"])
        self.assertEqual(observation, response["response"])


    def test_provider_model_and_client_use_google_only(self):
        brain = ai_brain.AIBrain()
        self.assertEqual("gemini", brain.ai_provider)
        self.assertEqual("gemini", brain.api_type)
        self.assertEqual("gemini-test-model", brain.model)
        self.assertEqual(brain.model, brain.gemini_model)
        self.genai.Client.assert_called_once()
        self.assertEqual("unit-test-gemini-key", self.genai.Client.call_args.kwargs["api_key"])

    def test_all_legacy_provider_modes_dispatch_directly_to_gemini(self):
        for provider in ("ollama", "hybrid", "gemini", "openai", "auto"):
            with self.subTest(provider=provider):
                self.client.models.generate_content.reset_mock()
                brain = ai_brain.AIBrain(api_type=provider)
                with patch.object(urllib.request, "urlopen", side_effect=AssertionError("Ollama probe")) as local:
                    result = brain.chat("Explique le solaire.")
                self.assertEqual("Reponse Gemini.", result["content"])
                self.client.models.generate_content.assert_called_once()
                local.assert_not_called()
                status = brain.get_health_status(probe=True)
                self.assertEqual("gemini", status["active_provider"])
                self.assertEqual("gemini-test-model", status["model"])
                self.assertTrue(status["online"])

    def test_gemini_deadline_is_independent_of_legacy_local_budget(self):
        for requested, milliseconds in (("1", 10000), ("8", 10000), ("9.99", 10000), ("10", 10000), ("15", 15000), ("30", 30000)):
            with self.subTest(requested=requested), patch.dict(os.environ, {"GEMINI_TIMEOUT_SECONDS": requested, "LLM_TIMEOUT_SECONDS": "0.1"}):
                brain = ai_brain.AIBrain(timeout=0.1)
                result = brain.chat("Explique le solaire.")
                self.assertEqual("Reponse Gemini.", result["content"])
                self.assertEqual(milliseconds / 1000, brain.gemini_timeout)
                self.assertEqual(milliseconds, self.genai.Client.call_args.kwargs["http_options"].timeout)
                self.assertEqual(milliseconds, self.client.models.generate_content.call_args.kwargs["config"].http_options.timeout)

    def test_default_cloud_deadline_is_fifteen_seconds(self):
        brain = ai_brain.AIBrain(timeout=8)
        brain.chat("Analyse ces mesures.")
        self.assertEqual(15.0, brain.gemini_timeout)
        self.assertEqual(15000, self.client.models.generate_content.call_args.kwargs["config"].http_options.timeout)

    def test_cloud_failure_changes_status_without_local_recovery_probe(self):
        brain = ai_brain.AIBrain()
        self.client.models.generate_content.side_effect = RuntimeError("Cloud offline")
        result = brain.chat("Explique le solaire.")
        for probe in (False, True):
            status = brain.get_health_status(probe=probe)
            self.assertFalse(status["online"])
            self.assertFalse(status["gemini_online"])
            self.assertTrue(status["all_providers_offline"])
            self.assertTrue(status["model_offline"])
            self.assertNotIn("ollama_online", status)
        self.assertEqual("error", result["finish_reason"])
        self.assertNotIn("Cloud offline", result["content"])

    def test_cloud_recovery_restores_online_status(self):
        brain = ai_brain.AIBrain()
        self.client.models.generate_content.side_effect = RuntimeError("Cloud offline")
        failed = brain.chat("Explique le solaire.")
        self.client.models.generate_content.side_effect = None
        recovered = brain.chat("Explique le solaire.")
        self.assertEqual("error", failed["finish_reason"])
        self.assertEqual("Reponse Gemini.", recovered["content"])
        status = brain.get_health_status(probe=False)
        self.assertTrue(status["online"])
        self.assertTrue(status["gemini_online"])
        self.assertEqual("gemini", status["active_provider"])
        self.assertFalse(status["all_providers_offline"])

    def test_cloud_stall_is_bounded_even_if_client_ignores_http_timeout(self):
        brain = ai_brain.AIBrain()
        release = threading.Event()
        def stalled(*args, **kwargs):
            release.wait(2)
            return self.gemini_response("Trop tard.")
        self.client.models.generate_content.side_effect = stalled
        real_deadline = ai_brain._with_inference_deadline
        try:
            with patch("ai_brain._with_inference_deadline", side_effect=lambda callback, timeout: real_deadline(callback, 0.03)):
                started = time.monotonic()
                result = brain.chat("Explique le solaire.")
                elapsed = time.monotonic() - started
        finally:
            release.set()
        self.assertTrue(result["fallback_courtesy"])
        self.assertEqual("DEGRADE", result["status"])
        self.assertLess(elapsed, 0.75)
        self.assertTrue(brain.get_health_status(probe=False)["all_providers_offline"])

    def test_cloud_without_credentials_or_sdk_never_uses_retired_backend(self):
        for missing_sdk in (False, True):
            context = patch("ai_brain.genai", None) if missing_sdk else patch.dict(os.environ, {"GEMINI_API_KEY": ""})
            with self.subTest(missing_sdk=missing_sdk), context, patch.object(urllib.request, "urlopen") as local:
                brain = ai_brain.AIBrain(api_type="hybrid")
                result = brain.chat("Explique le solaire.")
                self.assertEqual("error", result["finish_reason"])
                self.assertFalse(brain.get_health_status(probe=False)["online"])
                local.assert_not_called()

    def test_native_sdk_content_and_message_dict_are_accepted(self):
        for prompt in ({"role": "user", "content": "Solaire"}, [
            ai_brain.genai_types.Content(role="user", parts=[ai_brain.genai_types.Part(text="Solaire")]),
        ]):
            with self.subTest(prompt=prompt):
                brain = ai_brain.AIBrain()
                result = brain.chat(prompt)
                self.assertEqual("Reponse Gemini.", result["content"])
                self.assertEqual("Solaire", self.client.models.generate_content.call_args.kwargs["contents"][0].parts[0].text)

    def test_empty_cloud_candidate_reports_safe_error_with_trace(self):
        brain = ai_brain.AIBrain()
        self.client.models.generate_content.return_value = SimpleNamespace(candidates=[], prompt_feedback="blocked")
        with self.assertLogs(ai_brain.logger, level="WARNING") as captured:
            result = brain.chat("Analyse")
        self.assertEqual("error", result["finish_reason"])
        self.assertTrue(any(record.exc_info for record in captured.records))

    def test_thought_only_cloud_response_is_not_shown_as_final_text(self):
        brain = ai_brain.AIBrain()
        response = self.gemini_response("Texte interne")
        response.candidates[0].content.parts[0].thought = True
        self.client.models.generate_content.return_value = response
        result = brain.chat("Analyse")
        self.assertEqual("error", result["finish_reason"])
        self.assertNotIn("Texte interne", result["content"])

    def test_legacy_tool_helper_receives_native_response_and_closes_client(self):
        call = SimpleNamespace(id="gemini-time", name="time", args={})
        self.client.models.generate_content.return_value = self.gemini_response(function_call=call)
        tools = [{"type": "function", "function": {"name": "time", "parameters": {"type": "object", "properties": {}}}}]
        result = ai_brain.ask_ollama("Quelle heure ?", tools=tools)
        self.assertEqual("tool_calls", result["done_reason"])
        self.assertEqual("time", result["message"]["tool_calls"][0]["function"]["name"])
        self.client.close.assert_called_once()


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

    def test_open_fact_question_prefers_gemini_with_short_prompt(self):
        with patch("ai_brain.can_answer_general_questions", return_value=True):
            with patch("ai_brain.safe_ask_ai", return_value="Niamey est la capitale du Niger.") as ask_mock:
                response = self.brain.answer("ou est la capitale du niger")

        self.assertEqual("Niamey est la capitale du Niger.", response)
        self.assertIn("une ou deux phrases maximum", ask_mock.call_args.kwargs["system_prompt"])
        self.assertEqual(96, ask_mock.call_args.kwargs["max_predict"])
        self.assertEqual(ai_brain.GENERATION_CONFIG["temperature"], ask_mock.call_args.kwargs["temperature"])

    def test_activity_request_uses_gemini_with_personal_context(self):
        with patch("ai_brain.can_answer_general_questions", return_value=True):
            with patch(
                "ai_brain.safe_ask_ai",
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
        self.assertIn("priorites", ask_mock.call_args.kwargs["system_prompt"])
        self.assertEqual(ai_brain.GENERATION_CONFIG["temperature"], ask_mock.call_args.kwargs["temperature"])

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
