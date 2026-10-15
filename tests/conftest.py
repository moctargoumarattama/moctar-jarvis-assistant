"""Run inference through the real Gemini SDK with an offline HTTP transport.

Only Google's client is substituted; gateway/VPS HTTP tests retain their real
transport. The retired model server is deliberately never simulated.
"""

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import json
import os
import socket
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
import urllib.request

import httpx
import pytest


GEMINI_TEST_MODEL = "gemini-3.5-flash-lite"


class OfflineGemini:
    """Deterministic native Google responses; tool execution is opt-in."""

    def __init__(self):
        self.requests = []
        self.tool_name = None

    def respond(self, request):
        payload = json.loads(request.content)
        self.requests.append({"url": str(request.url), "payload": deepcopy(payload)})
        observations = [
            part["functionResponse"]
            for content in payload.get("contents", [])
            for part in content.get("parts", []) if "functionResponse" in part
        ]
        names = {
            declaration["name"] for tool in payload.get("tools", [])
            for declaration in tool.get("functionDeclarations", [])
        }
        if not observations and self.tool_name in names:
            part = {"functionCall": {
                "id": "call_offline_gemini", "name": self.tool_name, "args": {},
            }}
        else:
            detail = (
                json.dumps(observations[-1]["response"], ensure_ascii=False)
                if observations else
                "Réponse simulée de test ; aucune mesure réelle n'a été collectée."
            )
            part = {"text": (
                "[État Opérationnel]\n" + detail + "\n"
                "[Recommandations M.O.C.T.A.R]\n"
                "Vérifier les observations avant toute action sur l'infrastructure."
            )}
        return httpx.Response(200, json={"candidates": [{
            "content": {"role": "model", "parts": [part]}, "finishReason": "STOP",
        }]})


@pytest.fixture(autouse=True)
def offline_gemini():
    """Never consume a production key or contact a local/cloud model server."""
    import ai_brain
    from google import genai

    backend = OfflineGemini()
    real_client = genai.Client
    original_urlopen = urllib.request.urlopen
    original_open = urllib.request.OpenerDirector.open
    original_create_connection = socket.create_connection

    def reject_retired_server(url):
        if urlsplit(url).port == 11434:
            raise AssertionError("The retired Ollama server must never be contacted.")

    def urlopen(request, *args, **kwargs):
        reject_retired_server(getattr(request, "full_url", str(request)))
        return original_urlopen(request, *args, **kwargs)

    def opener_open(opener, request, *args, **kwargs):
        reject_retired_server(getattr(request, "full_url", str(request)))
        return original_open(opener, request, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        if address[1] == 11434:
            raise AssertionError("The retired Ollama server must never be contacted.")
        if "googleapis.com" in str(address[0]):
            raise AssertionError("Google inference tests require an offline transport.")
        return original_create_connection(address, *args, **kwargs)

    with ExitStack() as stack:
        def client_factory(*, api_key, http_options=None, **kwargs):
            http = stack.enter_context(httpx.Client(transport=httpx.MockTransport(backend.respond)))
            options = http_options.model_dump(exclude_none=True) if hasattr(http_options, "model_dump") else dict(http_options or {})
            options["httpx_client"] = http
            return stack.enter_context(real_client(api_key="offline-validation", http_options=options, **kwargs))

        stack.enter_context(patch.dict(os.environ, {
            "AI_PROVIDER": "gemini", "GEMINI_API_KEY": "offline-validation",
            "GEMINI_MODEL": GEMINI_TEST_MODEL, "GEMINI_TIMEOUT_SECONDS": "15.0",
        }))
        stack.enter_context(patch.object(ai_brain, "genai", SimpleNamespace(Client=client_factory)))
        stack.enter_context(patch.object(urllib.request, "urlopen", side_effect=urlopen))
        stack.enter_context(patch.object(urllib.request.OpenerDirector, "open", new=opener_open))
        stack.enter_context(patch.object(socket, "create_connection", side_effect=create_connection))
        yield backend
