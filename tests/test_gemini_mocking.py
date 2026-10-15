"""Exercise direct Gemini inference with native SDK parsing and no network."""

import json
import socket
from unittest.mock import patch
from urllib.request import Request, build_opener, urlopen

import pytest

import ai_brain
from agent_core import AgentCore
from assistant_memory import AssistantMemory


MODEL = "gemini-3.5-flash-lite"
TELEMETRY_TOOL = {
    "type": "function",
    "function": {
        "name": "get_system_telemetry",
        "description": "Mesure CPU, mémoire et disque.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


@pytest.mark.parametrize("legacy_provider", ["gemini", "hybrid", "ollama", "auto"])
def test_native_tool_call_and_observation_are_parsed_directly_on_cloud(legacy_provider, offline_gemini):
    offline_gemini.tool_name = "get_system_telemetry"
    brain = ai_brain.AIBrain(api_type=legacy_provider)
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
    assert len(offline_gemini.requests) == 2
    first = offline_gemini.requests[0]
    assert "/models/" + MODEL + ":generateContent" in first["url"]
    assert "googleapis.com" in first["url"]
    declaration = first["payload"]["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "get_system_telemetry"
    assert first["payload"]["contents"][0]["parts"][0]["text"] == messages[0]["content"]


def test_agent_react_executes_stubbed_tool_then_uses_cloud_observation(tmp_path, offline_gemini):
    offline_gemini.tool_name = "get_system_telemetry"
    observation = {"success": True, "data": {"cpu_percent": 18.75}}
    brain = ai_brain.AIBrain()
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
    assert len(offline_gemini.requests) == 2
    assert "functionResponse" in offline_gemini.requests[1]["payload"]["contents"][-1]["parts"][0]


def test_default_mock_never_selects_or_executes_tools(offline_gemini):
    brain = ai_brain.AIBrain()
    reply = brain.chat([{"role": "user", "content": "Mesure le CPU et redémarre le serveur."}], tools=[TELEMETRY_TOOL])
    assert reply["tool_calls"] == []
    assert reply["finish_reason"] == "stop"
    assert "aucune mesure réelle" in reply["content"]


@pytest.mark.parametrize("entrypoint", [urlopen, build_opener().open])
def test_test_infrastructure_rejects_every_retired_local_server_call(entrypoint):
    with pytest.raises(AssertionError, match="retired Ollama"):
        entrypoint(Request("http://localhost:11434/api/tags"))


@pytest.mark.parametrize("failure", [TimeoutError("simulated timeout"), RuntimeError("simulated invalid SDK response")])
def test_individual_tests_can_override_cloud_transport_for_error_paths(failure):
    brain = ai_brain.AIBrain()
    with patch.object(brain.gemini_client.models, "generate_content", side_effect=failure) as explicit:
        reply = brain.chat("test")
    explicit.assert_called_once()
    assert reply["finish_reason"] == "error"
    assert reply["tool_calls"] == []


def test_non_inference_http_transport_is_untouched(offline_gemini):
    for url in ("http://127.0.0.1:5050/api/status", "http://example.invalid:5050/api/status"):
        with patch("urllib.request.OpenerDirector._open", side_effect=RuntimeError("delegated transport")):
            with pytest.raises(RuntimeError, match="delegated transport"):
                urlopen(url)
    assert offline_gemini.requests == []
