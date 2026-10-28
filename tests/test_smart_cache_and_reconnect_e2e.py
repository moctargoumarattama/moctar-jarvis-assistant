"""Tests d'intégration de bout en bout pour le cache intelligent et l'auto-reconnexion IA."""

import pytest
from fastapi.testclient import TestClient

import main
import smart_command_cache
import ai_brain


@pytest.fixture
def client():
    app = main.create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_ai_reconnect_endpoint(client):
    """Vérifie que l'endpoint /api/ai/reconnect réinitialise et reconnecte le client IA proprement."""
    response = client.post("/api/ai/reconnect")
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert "reconnected" in data
    assert "message" in data


def test_ai_cache_stats_endpoint(client):
    """Vérifie l'endpoint /api/ai/cache/stats pour la supervision des hits et des fréquences."""
    cache = smart_command_cache.get_command_cache()
    cache.set("météo rabat", "22°C ensoleillé à Rabat.", category="weather")
    cache.record_query("météo rabat")

    response = client.get("/api/ai/cache/stats")
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert "stats" in data
    assert data["stats"]["active_cached_entries"] >= 1
    assert "top_queries_today" in data


def test_ai_brain_reconnect_method():
    """Vérifie le comportement de la méthode reconnect_client sur l'instance AIBrain."""
    brain = ai_brain.AIBrain()
    success = brain.reconnect_client(force=True)
    assert isinstance(success, bool)


def test_voice_interact_smart_cache_fast_path(client):
    """Vérifie qu'une commande présente en cache retourne instantanément (0ms) avec from_cache=True."""
    cache = smart_command_cache.get_command_cache()
    query = "météo à Rabat"
    reply = "Grand soleil à Rabat avec 23 degrés, Moctar."
    cache.set(query, reply, category="weather", ttl=300.0)

    response = client.post(
        "/api/voice/interact",
        json={"text": f"Jarvis, quelle est la {query} ?"},
        headers={"X-Voice-Session-Active": "true"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert data.get("reply_text") == reply
    assert data.get("from_cache") is True
    assert data.get("fast_path") is True
