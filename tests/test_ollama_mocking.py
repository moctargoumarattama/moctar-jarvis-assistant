"""Exercise the real Ollama request/response code without a model server."""

import json
import socket
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, urlopen

import pytest

import ai_brain
from agent_core import AgentCore
from assistant_memory import AssistantMemory


MODEL = "qwen2.5:1.5b"
TELEMETRY_TOOL = {
    "type": "function",
    "function": {
        "name": "get_system_telemetry",
        "description": "Mesure CPU, mémoire et disque.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


@pytest.mark.parametrize("base_url", [
    "http://localhost:11434",
    "http://127.0.0.1:11434",
    "http://[::1]:11434",
    "http://192.168.11.103:11434",
])
def test_native_tool_call_and_observation_are_parsed_without_network(base_url, offline_ollama):
    offline_ollama.tool_name = "get_system_telemetry"
    brain = ai_brain.AIBrain(base_url=base_url, model=MODEL, api_type="ollama")
    messages = [{"role": "user", "content": "Mesure le CPU et la RAM du système."}]

    with patch.object(socket, "create_connection", side_effect=AssertionError("real network")):
        reply = brain.chat(messages, tools=[TELEMETRY_TOOL])
        assert reply["finish_reason"] == "tool_calls"
        call = reply["tool_calls"][0]
        assert call["name"] == "get_system_telemetry"
        assert call["arguments"] == {}

        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": call["id"], "type": "function",
                "function": {"name": call["name"], "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": call["id"], "name": call["name"],
             "content": json.dumps({"success": True, "data": {"cpu_percent": 12.5}})},
        ])
        final = brain.chat(messages, tools=[TELEMETRY_TOOL])

    assert final["finish_reason"] == "stop"
    assert final["tool_calls"] == []
    assert "[État Opérationnel]" in final["content"]
    assert "[Recommandations M.O.C.T.A.R]" in final["content"]
    assert "12.5" in final["content"]
    assert len(offline_ollama.requests) == 2
    assert offline_ollama.requests[0]["payload"]["model"] == MODEL
    assert offline_ollama.requests[0]["payload"]["tools"] == [TELEMETRY_TOOL]
    assert offline_ollama.requests[0]["url"] == base_url + "/api/chat"


def test_agent_react_executes_stubbed_tool_then_uses_its_observation(tmp_path, offline_ollama):
    offline_ollama.tool_name = "get_system_telemetry"
    observation = {"success": True, "data": {"cpu_percent": 18.75}}
    brain = ai_brain.AIBrain(base_url="http://localhost:11434", model=MODEL, api_type="ollama")
    agent = AgentCore(
        ai_brain_instance=brain, tools=[TELEMETRY_TOOL], security=object(),
        memory=AssistantMemory(db_path=tmp_path / "memory.db"),
    )
    with patch("agent_core.execute_tool", return_value=observation) as handler, \
            patch.object(socket, "create_connection", side_effect=AssertionError("real network")):
        result = agent.run("Mesure le CPU du système", tool_names=("get_system_telemetry",))

    handler.assert_called_once_with("get_system_telemetry", {})
    assert result.status == "final"
    assert "18.75" in result.response
    assert "[État Opérationnel]" in result.response
    assert "[Recommandations M.O.C.T.A.R]" in result.response
    assert len(offline_ollama.requests) == 2
    assert offline_ollama.requests[1]["payload"]["messages"][-1]["role"] == "tool"


def test_default_mock_never_selects_or_executes_tools(offline_ollama):
    brain = ai_brain.AIBrain(base_url="http://localhost:11434", model=MODEL, api_type="ollama")
    reply = brain.chat([{"role": "user", "content": "Mesure le CPU et redémarre le serveur."}], tools=[TELEMETRY_TOOL])
    assert reply["tool_calls"] == []
    assert reply["finish_reason"] == "stop"
    assert "aucune mesure réelle" in reply["content"]


def test_generate_tags_and_imported_urlopen_are_offline(offline_ollama):
    with patch.object(socket, "create_connection", side_effect=AssertionError("real network")):
        with urlopen("http://localhost:11434/api/tags") as response:
            tags = json.load(response)
        request = Request(
            "http://192.168.11.103:11434/api/generate",
            data=json.dumps({"model": MODEL, "prompt": "Bonjour", "stream": False}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with build_opener().open(request) as response:
            generated = json.load(response)

    assert tags["models"][0]["name"] == MODEL
    assert generated["model"] == MODEL
    assert generated["done"] is True
    assert "[État Opérationnel]" in generated["response"]
    assert len(offline_ollama.requests) == 2


@pytest.mark.parametrize("failure", [TimeoutError("simulated timeout"), HTTPError(
    "http://localhost:11434/api/chat", 400, "simulated bad JSON", {}, None,
)])
def test_individual_tests_can_override_offline_transport_for_error_paths(failure):
    brain = ai_brain.AIBrain(base_url="http://localhost:11434", model=MODEL, api_type="ollama")
    with patch("ai_brain.urlopen", side_effect=failure) as explicit_mock:
        reply = brain.chat([{"role": "user", "content": "test"}])
    explicit_mock.assert_called_once()
    assert reply["finish_reason"] == "error"


def test_non_ollama_http_transport_is_untouched(offline_ollama):
    # These URLs are used by the local VPS/gateway tests and must reach their
    # transport; the sentinel exception proves the offline mock delegated.
    for url in ("http://127.0.0.1:5050/api/status", "http://example.invalid:11434/api/chat"):
        with patch("urllib.request.OpenerDirector._open", side_effect=RuntimeError("delegated transport")):
            with pytest.raises(RuntimeError, match="delegated transport"):
                ai_brain.urlopen(url)
    assert offline_ollama.requests == []
