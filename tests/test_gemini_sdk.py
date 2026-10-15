"""Exercise Gemini serialization with the real SDK and an offline HTTP transport."""

import base64
import json
import os
from unittest.mock import Mock, patch

import httpx
import pytest

import ai_brain
from actions import get_all_tools_schema
from agent_core import AgentCore
from assistant_plugins import PluginRegistry
from assistant_security import SecurityPolicy


def assert_fast_generation_config(generation):
    generation = dict(generation)
    thinking = generation.pop("thinkingConfig")
    assert generation == {"temperature": 0.2, "maxOutputTokens": 350, "topP": 0.8}
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


def test_real_sdk_and_agent_preserve_signed_hybrid_tool_turns_on_gemini():
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
            with patch.object(brain, "_chat_ollama", side_effect=TimeoutError("Ollama offline")) as local:
                result = agent.run("Consulte l'horloge de test.")

    assert result.status == "final"
    assert result.response == "Il est 12:00."
    handler.assert_called_once_with({"intent": "test_clock", "target": "", "slots": {}})
    local.assert_called_once()
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
