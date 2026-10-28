"""Exercise Gemini serialization with the real SDK and an offline HTTP transport."""

import base64
from contextlib import ExitStack
import json
import logging
import os
from unittest.mock import Mock, patch

import httpx
import pytest

import ai_brain
from actions import get_all_tools_schema
from agent_core import AgentCore
from assistant_plugins import PluginRegistry
from assistant_security import SecurityPolicy


SDK_TEST_TOOLS = [{
    "type": "function",
    "function": {
        "name": "test_clock",
        "description": "Read the test clock",
        "parameters": {
            "type": "object", "properties": {}, "additionalProperties": False,
        },
    },
}]


def sdk_text_response(text="Gemini operationnel."):
    return httpx.Response(200, json={"candidates": [{
        "content": {"role": "model", "parts": [{"text": text}]},
        "finishReason": "STOP",
    }]})


@pytest.fixture
def sdk_brain_factory():
    """Keep the real client/serializer; replace only the network transport."""
    genai = pytest.importorskip("google.genai")
    with ExitStack() as stack:
        def create(respond, provider="gemini"):
            http = stack.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
            client = stack.enter_context(genai.Client(
                api_key="offline-validation", http_options={"httpx_client": http},
            ))
            with patch.dict(os.environ, {
                "GEMINI_API_KEY": "", "GEMINI_MODEL": "gemini-3.5-flash-lite",
            }):
                brain = ai_brain.AIBrain(api_type=provider)
            brain.gemini_client = client
            generate = stack.enter_context(patch.object(
                client.models, "generate_content", wraps=client.models.generate_content,
            ))
            return brain, generate

        yield create


def assert_fast_generation_config(generation):
    generation = dict(generation)
    thinking = generation.pop("thinkingConfig")
    assert generation == {"temperature": ai_brain.GENERATION_CONFIG["temperature"], "maxOutputTokens": 350, "topP": 0.8}
    assert set(thinking) in ({"thinkingLevel"}, {"thinking_level"})
    assert next(iter(thinking.values())).lower() == "minimal"


def test_real_sdk_serializes_the_tool_catalog_and_observations():
    genai = pytest.importorskip("google.genai")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        part = (
            {"functionCall": {"id": "sdk-call", "name": "get_time_response", "args": {}}}
            if len(requests) == 1 else {"text": "Observation recue."}
        )
        return httpx.Response(200, json={"candidates": [{
            "content": {"role": "model", "parts": [part]}, "finishReason": "STOP",
        }]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with genai.Client(api_key="offline-validation", http_options={"httpx_client": http}) as client:
            with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GEMINI_MODEL": "gemini-3.5-flash-lite"}):
                brain = ai_brain.AIBrain(api_type="gemini")
            brain.gemini_client = client
            tools = get_all_tools_schema()
            messages = [{"role": "system", "content": "Assistant de test"},
                        {"role": "user", "content": "Quelle heure est-il ?"}]
            first = brain.chat(messages, tools=tools)
            assert first["finish_reason"] == "tool_calls"
            assert first["tool_calls"][0]["name"] == "get_time_response"
            assert len(requests[0]["tools"][0]["functionDeclarations"]) == len(tools)
            assert_fast_generation_config(requests[0]["generationConfig"])

            messages.extend([
                {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "sdk-call", "function": {"name": "get_time_response", "arguments": "{}"},
                }]},
                {"role": "tool", "name": "get_time_response", "tool_call_id": "sdk-call",
                 "content": json.dumps({"success": True, "message": "12:00"})},
            ])
            second = brain.chat(messages, tools=tools)

    assert second["content"] == "Observation recue."
    assert requests[1]["generationConfig"] == requests[0]["generationConfig"]
    observation = requests[1]["contents"][-1]["parts"][0]["functionResponse"]
    assert observation["name"] == "get_time_response"
    assert observation["response"] == {"success": True, "message": "12:00"}


def test_real_sdk_and_agent_preserve_signed_tool_turns_directly_on_gemini():
    genai = pytest.importorskip("google.genai")
    signature = base64.b64encode(b"opaque-signed-gemini-tool-turn\x00\xff").decode("ascii")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        part = (
            {"thoughtSignature": signature,
             "functionCall": {"id": "signed-call", "name": "test_clock", "args": {}}}
            if len(requests) == 1 else {"text": "Il est 12:00."}
        )
        return httpx.Response(200, json={"candidates": [{
            "content": {"role": "model", "parts": [part]}, "finishReason": "STOP",
        }]})

    registry = PluginRegistry()
    handler = Mock(return_value="12:00")
    registry.register(
        "test_clock", "system", handler, description="Horloge de test",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ai_enabled=True,
    )
    security = SecurityPolicy(settings={"domain_permissions": {"system": True}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with genai.Client(api_key="offline-validation", http_options={"httpx_client": http}) as client:
            with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GEMINI_MODEL": "gemini-3.5-flash-lite"}):
                brain = ai_brain.AIBrain(api_type="hybrid")
            brain.gemini_client = client
            agent = AgentCore(registry, security, ai_brain_instance=brain)
            with patch("urllib.request.urlopen", side_effect=AssertionError("retired local provider")) as local:
                result = agent.run("Consulte l'horloge de test.")

    assert result.status == "final"
    assert result.response == "Il est 12:00."
    handler.assert_called_once_with({"intent": "test_clock", "target": "", "slots": {}})
    local.assert_not_called()
    assert len(requests) == 2
    assert brain.last_provider == "gemini"
    signed_parts = [
        part for content in requests[1]["contents"] if content["role"] == "model"
        for part in content["parts"] if "functionCall" in part
    ]
    assert len(signed_parts) == 1
    assert signed_parts[0]["thoughtSignature"] == signature
    assert signed_parts[0]["functionCall"] == {
        "id": "signed-call", "name": "test_clock", "args": {},
    }
    observation = requests[1]["contents"][-1]["parts"][0]["functionResponse"]
    assert observation["id"] == "signed-call"
    assert observation["name"] == "test_clock"
    assert observation["response"]["result"] == "12:00"
    assert requests[0]["generationConfig"] == requests[1]["generationConfig"]
    assert_fast_generation_config(requests[0]["generationConfig"])


@pytest.mark.parametrize("entrypoint", ["ask", "chat"])
def test_real_sdk_accepts_simple_text_and_typed_config(sdk_brain_factory, entrypoint):
    from google.genai import types

    requests = []

    def respond(request):
        requests.append(request)
        return sdk_text_response()

    brain, generate = sdk_brain_factory(respond)
    result = getattr(brain, entrypoint)("Verifie le relais Gemini.")

    if entrypoint == "ask":
        assert result == "Gemini operationnel."
    else:
        assert result["content"] == "Gemini operationnel."
        assert result["tool_calls"] == []
        assert result["finish_reason"] == "stop"
    assert generate.call_count == len(requests) == 1
    kwargs = generate.call_args.kwargs
    assert kwargs["model"] == "gemini-3.5-flash-lite"
    assert isinstance(kwargs["config"], types.GenerateContentConfig)
    assert all(isinstance(content, types.Content) for content in kwargs["contents"])
    assert all(isinstance(part, types.Part) for content in kwargs["contents"] for part in content.parts)
    payload = json.loads(requests[0].content)
    assert payload["contents"][-1] == {
        "role": "user", "parts": [{"text": "Verifie le relais Gemini."}],
    }
    assert_fast_generation_config(payload["generationConfig"])
    assert requests[0].url.path.endswith("/models/gemini-3.5-flash-lite:generateContent")


def test_real_sdk_serializes_system_messages_and_converted_tools(sdk_brain_factory):
    from google.genai import types

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return sdk_text_response()

    brain, generate = sdk_brain_factory(respond)
    messages = [
        {"role": "system", "content": "Reponds en francais."},
        {"role": "user", "content": "Question precedente"},
        {"role": "assistant", "content": "Reponse precedente"},
        {"role": "user", "content": "Consulte l'horloge."},
    ]
    result = brain.chat(messages, tools=SDK_TEST_TOOLS)

    assert result["content"] == "Gemini operationnel."
    assert generate.call_count == len(requests) == 1
    config = generate.call_args.kwargs["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert isinstance(config.tools[0], types.Tool)
    assert isinstance(config.tools[0].function_declarations[0], types.FunctionDeclaration)
    assert config.automatic_function_calling.disable is True
    assert requests[0]["systemInstruction"]["parts"] == [{"text": "Reponds en francais."}]
    assert [content["role"] for content in requests[0]["contents"]] == ["user", "model", "user"]
    declaration = requests[0]["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "test_clock"
    # google-genai 2.31 serializes the JSON schema extension in snake_case.
    schema = declaration.get("parameters_json_schema", declaration.get("parametersJsonSchema"))
    assert schema == SDK_TEST_TOOLS[0]["function"]["parameters"]
    assert_fast_generation_config(requests[0]["generationConfig"])


def test_real_sdk_gemini_deadline_remains_at_least_ten_seconds(sdk_brain_factory, monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "8")
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "1")
    requests = []

    def respond(request):
        requests.append(request)
        return sdk_text_response()

    brain, generate = sdk_brain_factory(respond)
    result = brain.ask("Verifie le delai Google.")

    assert result == "Gemini operationnel."
    assert brain.gemini_timeout >= 10.0
    assert generate.call_args.kwargs["config"].http_options.timeout >= 10000
    assert generate.call_count == len(requests) == 1
    assert float(requests[0].headers["x-server-timeout"]) >= 10.0


@pytest.mark.parametrize("image_data, expected_mime", [
    (b"\x89PNG\r\n\x1a\nimage-content", "image/png"),
    (b"\xff\xd8\xffimage-content", "image/jpeg"),
])
def test_real_sdk_preserves_vision_image_payload(sdk_brain_factory, image_data, expected_mime):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return sdk_text_response("Capture analysee.")

    brain, generate = sdk_brain_factory(respond)
    result = brain.chat([{
        "role": "user", "content": "Decris cette capture.",
        "images": [base64.b64encode(image_data).decode("ascii")],
    }])

    assert result["content"] == "Capture analysee."
    assert generate.call_count == len(requests) == 1
    parts = requests[0]["contents"][0]["parts"]
    assert parts[0]["text"] == "Decris cette capture."
    image = parts[1]["inlineData"]
    assert image.get("mimeType", image.get("mime_type")) == expected_mime
    assert generate.call_args.kwargs["contents"][0].parts[1].inline_data.mime_type == expected_mime
    assert base64.urlsafe_b64decode(image["data"]) == image_data


def test_real_sdk_retries_schema_rejection_once_without_tools(sdk_brain_factory, caplog):
    requests = []
    error_message = (
        "Invalid JSON schema in GenerateContentRequest.tools[0].function_declarations[0]."
        "parameters.properties: unsupported schema."
    )

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {
                "code": 400, "status": "INVALID_ARGUMENT", "message": error_message,
            }})
        return sdk_text_response("Reponse texte de secours.")

    brain, generate = sdk_brain_factory(respond)
    with caplog.at_level(logging.WARNING, logger="jarvis.ai"):
        result = brain.chat("Explique la situation.", tools=SDK_TEST_TOOLS)

    assert result["content"] == "Reponse texte de secours."
    assert result["tool_calls"] == []
    assert result["finish_reason"] == "stop"
    assert generate.call_count == len(requests) == 2
    assert "tools" in requests[0]
    assert not requests[1].get("tools")
    assert requests[0]["contents"] == requests[1]["contents"]
    assert brain.last_provider == "gemini"
    assert brain.get_health_status(probe=False)["online"] is True
    assert any(record.exc_info for record in caplog.records if error_message in record.getMessage())


@pytest.mark.parametrize("status, error_status, message", [
    (400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."),
    (400, "INVALID_ARGUMENT", "The request deadline is 8.0s, but must be at least 10.0s."),
    (403, "PERMISSION_DENIED", "Permission denied for this API key."),
    (404, "NOT_FOUND", "models/gemini-3.5-flash-lite is not found for generateContent."),
    (429, "RESOURCE_EXHAUSTED", "Quota exhausted for this API key."),
    (500, "INTERNAL", "Internal server error."),
])
def test_real_sdk_failure_logs_traceback_without_text_retry(
    sdk_brain_factory, caplog, status, error_status, message,
):
    from google.genai import errors

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(status, json={"error": {
            "code": status, "status": error_status, "message": message,
        }})

    brain, generate = sdk_brain_factory(respond)
    with caplog.at_level(logging.WARNING, logger="jarvis.ai"):
        result = brain.chat("Verifie l'IA.", tools=SDK_TEST_TOOLS)

    if status == 500:
        assert result["finish_reason"] in ("error", "stop")
        assert result["tool_calls"] == []
        assert message not in (result["content"] or "")
    else:
        assert result["finish_reason"] == "error"
        assert result["tool_calls"] == []
        assert message not in (result["content"] or "")
        assert generate.call_count == len(requests) == 1
        failures = [record for record in caplog.records if record.msg == "Échec appel Gemini: %s"]
        assert failures
        assert message in failures[-1].getMessage()
        assert failures[-1].exc_info is not None
        assert isinstance(failures[-1].exc_info[1], errors.APIError)
        assert failures[-1].exc_info[2] is not None


def test_real_sdk_does_not_retry_without_tools_during_function_history(sdk_brain_factory, caplog):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {
            "code": 400, "status": "INVALID_ARGUMENT",
            "message": "GenerateContentRequest.tools[0].function_declarations: invalid JSON schema.",
        }})

    brain, generate = sdk_brain_factory(respond)
    messages = [
        {"role": "user", "content": "Quelle heure est-il ?"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "clock-call", "function": {"name": "test_clock", "arguments": "{}"},
        }]},
        {"role": "tool", "name": "test_clock", "tool_call_id": "clock-call", "content": "12:00"},
    ]
    with caplog.at_level(logging.WARNING, logger="jarvis.ai"):
        result = brain.chat(messages, tools=SDK_TEST_TOOLS)

    assert result["finish_reason"] == "error"
    assert generate.call_count == len(requests) == 1
    assert requests[0]["contents"][-1]["parts"][0]["functionResponse"]["name"] == "test_clock"
    assert any(record.exc_info for record in caplog.records if record.msg == "Échec appel Gemini: %s")
