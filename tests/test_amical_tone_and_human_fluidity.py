"""Tests pour le Pack Amical, Humanité Fluide & Résolution Définitive des Réponses Vocales.

Vérifie :
1. L'interception 0ms des contrôles de liaison radio ("Tu me reçois ?", "Tu me captes ?").
2. L'interception 0ms des requêtes de compétences ("Tu es capable de faire", "Qu'est-ce que tu es capable de faire ?").
3. La connaissance intime et fière du profil de Moctar ("Quelles sont les informations que tu connais sur moi ?").
4. L'enregistrement instantané et persistant de la nationalité nigérienne.
5. La robustesse de l'outil `store_memory_fact` qui accepte indifféremment `value`, `fact` ou `content`.
6. L'aliasing d'arguments dans `actions.execute_tool`.
7. Le fallback immédiat 0ms sur `/api/jarvis/query` pour les commandes locales (CPU, RAM, etc.).
"""

from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

import main
from actions.personal_actions import store_memory_fact
from actions import execute_tool
from assistant_memory import AssistantMemory


@pytest.fixture
def test_memory(tmp_path: Path):
    db_file = tmp_path / "test_memory.db"
    return AssistantMemory(db_path=db_file)


def test_radio_check_instant_reply():
    """Vérifie que 'Tu me reçois ?' ou 'Tu me captes ?' répond immédiatement en 5 sur 5 amical."""
    for phrase in ("Tu me reçois ?", "Tu me captes ?", "tu me recois", "tu m'entends", "tu es là"):
        reply = main.handle_instant_local_command(phrase)
        assert reply is not None
        assert "5 sur 5 Moctar" in reply
        assert "mon ami" in reply


def test_capabilities_breakdown_instant_reply():
    """Vérifie que 'Tu es capable de faire' ou 'Qu'est-ce que tu es capable de faire ?' répond en 0ms."""
    queries = [
        "Tu es capable de faire",
        "Qu'est-ce que tu es capable de faire ? Dis-moi.",
        "qu'est ce que tu es capable de faire",
        "tu peux faire quoi",
        "tu sais faire quoi",
    ]
    for q in queries:
        reply = main.handle_instant_local_command(q)
        assert reply is not None
        assert "Dell Precision M4800" in reply
        assert "VS Code" in reply or "piloter Git" in reply or "surveiller" in reply


def test_knows_moctar_profile_instant_reply(test_memory):
    """Vérifie que 'Quelles sont les informations que tu connais sur moi ?' retourne son profil avec fierté."""
    test_memory.update_moctar_profile({"nationality": "Nigérien", "country": "Niger"})
    queries = [
        "Quelles sont les informations que tu connais sur moi ?",
        "qu'est-ce que tu connais sur moi",
        "qu'est-ce que tu sais sur moi",
        "tu me connais",
    ]
    for q in queries:
        reply = main.handle_instant_local_command(q, memory=test_memory)
        assert reply is not None
        assert "Nigérien" in reply
        assert "Real Madrid" in reply
        assert "M4800" in reply


def test_add_nationality_instant_persistence(test_memory):
    """Vérifie que 'Ajoute que je suis Nigérien' persiste instantanément dans la base et le profil."""
    reply = main.handle_instant_local_command("Ajoute que je suis Nigérien.", memory=test_memory)
    assert reply is not None
    assert "nationalité nigérienne enregistrée" in reply

    prof = test_memory.get_moctar_profile()
    assert prof.get("nationality") == "Nigérien"
    assert prof.get("country") == "Niger"

    facts = test_memory.search_facts("Nigérien")
    assert any("Nigérien" in str(f.get("content", "")) for f in facts)


def test_store_memory_fact_accepts_fact_alias(test_memory):
    """Valide que store_memory_fact accepte 'fact' au lieu de 'value' sans crash."""
    res = store_memory_fact(
        key="nationalite_moctar",
        fact="Moctar est Nigérien",
        category="personal",
        memory_instance=test_memory,
    )
    assert res["success"] is True
    assert "Nigérien" in res["message"]
    assert res["data"]["value"] == "Moctar est Nigérien"


def test_execute_tool_aliases_fact_to_value(test_memory):
    """Valide que actions.execute_tool normalise l'argument 'fact' en 'value' pour store_memory_fact."""
    raw_call = {
        "key": "nationalite_moctar",
        "fact": "Moctar est Nigérien",
        "category": "personal",
    }
    with patch("assistant_memory.AssistantMemory", return_value=test_memory):
        res = execute_tool("store_memory_fact", raw_call)
        assert res["success"] is True
        assert res["data"]["value"] == "Moctar est Nigérien"


def test_frustration_reassurance_instant_reply():
    """Vérifie que 'Je te parle depuis mais tu ne me réponds pas' déclenche une réassurance immédiate et chaleureuse."""
    queries = [
        "Je te parle depuis mais tu ne me réponds pas.",
        "tu ne me réponds pas",
        "J'ai l'impression que tu ne me comprends pas.",
        "pourquoi tu me réponds pas ?",
        "Pourquoi tu ne me répondais pas depuis ?",
        "Pourquoi tu me récompenses depuis ?",
    ]
    for q in queries:
        reply = main.handle_instant_local_command(q)
        assert reply is not None
        assert "Pardonne-moi Moctar" in reply
        assert "100%" in reply
        assert "mon ami" in reply


def test_phonetic_repair_for_radio_and_identity():
    """Vérifie que les approximations Whisper (T'es réçois, Je suis qui toi) sont réparées et acceptées directement."""
    import stt_phonetic_repair

    repaired, rules = stt_phonetic_repair.repair_stt_transcript("T'es réçois ?")
    assert "tu me reçois" in repaired.lower()

    repaired_id, _ = stt_phonetic_repair.repair_stt_transcript("Je suis qui toi ?")
    assert "tu es qui toi" in repaired_id.lower()

    # Doit être accepté comme commande directe même hors session
    assert main.is_direct_voice_command("Tu me reçois ?") is True
    assert main.is_direct_voice_command("T'es réçois ?") is True


def test_jarvis_query_endpoint_intercepts_local_command_0ms(tmp_path: Path):
    """Vérifie que /api/jarvis/query intercepte les commandes locales en 0ms sans faire d'appel LLM."""
    from fastapi.testclient import TestClient

    app = main.create_app()
    with TestClient(app) as client:
        resp = client.post("/api/jarvis/query", json={"prompt": "cpu", "source": "hud_web"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Processeur" in data["reply"]
        assert "mémoire vive" in data["reply"]
