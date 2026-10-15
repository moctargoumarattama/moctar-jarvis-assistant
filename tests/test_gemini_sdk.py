"""Exercise Gemini serialization with the real SDK and an offline HTTP transport."""

import json
import os
from unittest.mock import patch

import httpx
import pytest

import ai_brain
from actions import get_all_tools_schema


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
            with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
                brain = ai_brain.AIBrain(api_type="gemini")
            brain.gemini_client = client
            tools = get_all_tools_schema()
            messages = [{"role": "system", "content": "Assistant de test"},
                        {"role": "user", "content": "Quelle heure est-il ?"}]
            first = brain.chat(messages, tools=tools)
            assert first["finish_reason"] == "tool_calls"
            assert first["tool_calls"][0]["name"] == "get_time_response"
            assert len(requests[0]["tools"][0]["functionDeclarations"]) == len(tools)
            assert requests[0]["generationConfig"] == {
                "temperature": 0.2, "maxOutputTokens": 350, "topP": 0.8,
            }

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
