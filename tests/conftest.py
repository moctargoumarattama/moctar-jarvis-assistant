"""Keep Ollama-dependent tests deterministic without a local model server.

Only the configured Ollama hosts on port 11434 are intercepted. Real HTTP
servers used by the VPS and gateway contract tests retain their own transport.
Tests can still override ``ai_brain.urlopen`` to exercise errors explicitly.
"""

from __future__ import annotations

from copy import deepcopy
from email.message import Message
from io import BytesIO
import json
import os
import socket
from unittest.mock import patch
from urllib.parse import urlsplit
import urllib.request

import pytest


OLLAMA_TEST_MODEL = "qwen2.5:1.5b"
OLLAMA_TEST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "192.168.11.103"})


def _is_ollama_address(host: str, port: int | None) -> bool:
    return str(host).strip("[]").lower() in OLLAMA_TEST_HOSTS and port == 11434


def _is_ollama_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and _is_ollama_address(parsed.hostname or "", parsed.port)


class _JSONResponse(BytesIO):
    """The small HTTP response surface consumed by urllib/Ollama callers."""

    def __init__(self, payload: dict, url: str) -> None:
        super().__init__(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.url = url
        self.status = self.code = 200
        self.reason = "OK"
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def info(self):
        return self.headers


class OfflineOllama:
    """Return native, non-streaming Qwen responses at the HTTP boundary."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        # Tool execution is opt-in for a test that also stubs its handler.
        self.tool_name: str | None = None

    @staticmethod
    def _final_response(messages: list[dict]) -> str:
        observations = [message for message in messages if message.get("role") == "tool"]
        if observations:
            detail = str(observations[-1].get("content", "Observation de test reçue."))
        else:
            detail = "Réponse simulée de test ; aucune mesure réelle n'a été collectée."
        return (
            "[État Opérationnel]\n" + detail + "\n"
            "[Recommandations M.O.C.T.A.R]\n"
            "Vérifier les observations avant toute action sur l'infrastructure."
        )

    def respond(self, request, data=None) -> _JSONResponse:
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        body = data if data is not None else getattr(request, "data", None)
        payload = json.loads(body.decode("utf-8")) if body else {}
        self.requests.append({"url": url, "payload": deepcopy(payload)})
        endpoint = urlsplit(url).path.rstrip("/")
        common = {"model": OLLAMA_TEST_MODEL, "done": True, "done_reason": "stop"}

        if endpoint == "/api/tags":
            response = {"models": [{"name": OLLAMA_TEST_MODEL, "model": OLLAMA_TEST_MODEL}]}
        elif endpoint == "/api/generate":
            response = {**common, "response": self._final_response([])}
        elif endpoint in {"/api/chat", "/v1/chat/completions"}:
            messages = payload.get("messages", [])
            tool_names = {
                tool.get("function", {}).get("name") for tool in payload.get("tools", [])
            }
            tool_name = None
            if not any(message.get("role") == "tool" for message in messages):
                if self.tool_name in tool_names:
                    tool_name = self.tool_name
            message = {"role": "assistant", "content": self._final_response(messages)}
            if tool_name:
                message = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_offline_ollama",
                        "type": "function",
                        "function": {"name": tool_name, "arguments": {}},
                    }],
                }
            if endpoint == "/v1/chat/completions":
                # OpenAI compatibility fallback must also stay offline.
                message = deepcopy(message)
                for call in message.get("tool_calls", []):
                    call["function"]["arguments"] = json.dumps(call["function"]["arguments"])
                response = {
                    "model": OLLAMA_TEST_MODEL,
                    "choices": [{"message": message, "finish_reason": "tool_calls" if tool_name else "stop"}],
                }
            else:
                response = {**common, "message": message}
        else:
            raise AssertionError(f"Unexpected Ollama endpoint in offline test: {endpoint}")
        return _JSONResponse(response, url)


@pytest.fixture(autouse=True)
def offline_ollama():
    """Mock inference HTTP, preserving production serialization and parsing."""
    import ai_brain

    backend = OfflineOllama()
    original_urlopen = urllib.request.urlopen
    original_open = urllib.request.OpenerDirector.open
    original_create_connection = socket.create_connection

    def urlopen(request, data=None, *args, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        if _is_ollama_url(url):
            return backend.respond(request, data)
        return original_urlopen(request, data, *args, **kwargs)

    def opener_open(opener, request, data=None, *args, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        if _is_ollama_url(url):
            return backend.respond(request, data)
        return original_open(opener, request, data, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        if _is_ollama_address(address[0], address[1]):
            raise AssertionError("A test attempted a real Ollama connection; mock its HTTP transport.")
        return original_create_connection(address, *args, **kwargs)

    with patch.dict(os.environ, {"AI_PROVIDER": "ollama", "GEMINI_API_KEY": ""}), \
            patch.object(ai_brain, "urlopen", side_effect=urlopen), \
            patch.object(urllib.request, "urlopen", side_effect=urlopen), \
            patch.object(urllib.request.OpenerDirector, "open", new=opener_open), \
            patch.object(socket, "create_connection", side_effect=create_connection):
        yield backend
