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
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
import urllib.request

import httpx
import pytest

# Test collection imports config/main before fixtures run. Never use the user's
# databases, notes or memories for implicit default paths during that import.
_TEST_RUNTIME_ROOT = tempfile.TemporaryDirectory(prefix="jarvis-unit-runtime-")
os.environ.setdefault("JARVIS_APP_ROOT", _TEST_RUNTIME_ROOT.name)

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
    import headless_bridge
    import assistant_memory
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
        real_bridge_init = headless_bridge.JarvisHeadlessBridge.__init__
        state_path = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="jarvis-unit-state-")))
        stack.enter_context(patch.object(assistant_memory, "_DEFAULT_DB_PATH", state_path / "memory.db"))

        def isolated_bridge_init(self, base_dir=None, db_path=None, model=None, base_url=None):
            return real_bridge_init(self, base_dir, db_path if db_path is not None else state_path / "memory.db", model, base_url)

        stack.enter_context(patch.object(headless_bridge.JarvisHeadlessBridge, "__init__", isolated_bridge_init))
        def client_factory(*, api_key=None, http_options=None, **kwargs):
            options = http_options.model_dump(exclude_none=True) if hasattr(http_options, "model_dump") else dict(http_options or {})
            if "httpx_client" not in options:
                http = stack.enter_context(httpx.Client(transport=httpx.MockTransport(backend.respond), trust_env=False))
                options["httpx_client"] = http
            # No TLS connection exists with MockTransport. Avoid constructing a
            # second Windows certificate store for the SDK's unused async client.
            options.setdefault("async_client_args", {"verify": False, "trust_env": False})
            return stack.enter_context(real_client(api_key=api_key or "offline-validation", http_options=options, **kwargs))

        stack.enter_context(patch.dict(os.environ, {
            "AI_PROVIDER": "gemini", "GEMINI_API_KEY": "offline-validation",
            "GEMINI_MODEL": GEMINI_TEST_MODEL, "GEMINI_TIMEOUT_SECONDS": "15.0",
            # A developer's real secondary key must never leak into fallback tests.
            "GROQ_API_KEY": "", "NOC_GROQ_API_KEY": "",
        }))
        stack.enter_context(patch.object(ai_brain, "genai", SimpleNamespace(Client=client_factory)))
        # Voice/vision sometimes import google.genai directly instead of going
        # through ai_brain; these clients need the same offline transport.
        stack.enter_context(patch.object(genai, "Client", side_effect=client_factory))
        stack.enter_context(patch.object(urllib.request, "urlopen", side_effect=urlopen))
        stack.enter_context(patch.object(urllib.request.OpenerDirector, "open", new=opener_open))
        stack.enter_context(patch.object(socket, "create_connection", side_effect=create_connection))
        yield backend
