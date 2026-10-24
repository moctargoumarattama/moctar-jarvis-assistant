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

    repaired_eloigne, _ = stt_phonetic_repair.repair_stt_transcript("Tu m'éloignes de soi ? Tu m'éloignes de soi ?")
    assert "tu me reçois" in repaired_eloigne.lower()

    repaired_eloigne_toi, _ = stt_phonetic_repair.repair_stt_transcript("Tu m'éloignes de toi ?")
    assert "tu me reçois" in repaired_eloigne_toi.lower()

    repaired_erevois, _ = stt_phonetic_repair.repair_stt_transcript("Tu m'érevois ?")
    assert "tu me reçois" in repaired_erevois.lower()

    # Doit être accepté comme commande directe même hors session
    assert main.is_direct_voice_command("Tu me reçois ?") is True
    assert main.is_direct_voice_command("T'es réçois ?") is True
    assert main.is_direct_voice_command("Tu m'éloignes de soi ?") is True
    assert main.is_direct_voice_command("Tu m'éloignes de toi ?") is True
    assert main.is_direct_voice_command("Tu m'érevois ?") is True

    # Doit répondre "5 sur 5 Moctar" en 0ms
    rep = main.handle_instant_local_command("Tu m'éloignes de soi ?")
    assert rep is not None
    assert "5 sur 5" in rep

    rep_toi = main.handle_instant_local_command("Tu m'éloignes de toi ?")
    assert rep_toi is not None
    assert "5 sur 5" in rep_toi

    rep2 = main.handle_instant_local_command("Tu m'érevois ?")
    assert rep2 is not None
    assert "5 sur 5" in rep2


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


def test_extract_voice_interact_never_ignores_coherent_sentences():
    """Vérifie qu'aucune phrase cohérente prononcée par Moctar n'est ignorée, même sans mot-clé 'Moctar' préalable."""
    coherent_queries = [
        "Salut !",
        "Bonjour !",
        "tu es capable de faire quoi",
        "comment tu vas mon ami",
        "qui es-tu exactement",
        "cpu",
        "météo",
        "pourquoi tu ne me réponds pas",
        "qu'est-ce que tu peux faire pour moi",
        "affiche la terre",
        "arrête la musique",
    ]
    for q in coherent_queries:
        accepted, cmd, wake = main.extract_voice_interact_command(q, session_active=False)
        assert accepted is True, f"La phrase '{q}' a été rejetée à tort !"
        assert len(cmd) > 0

    # Les bruits parasites et hésitations vides doivent en revanche être ignorés
    fillers = ["euh", "ah", "hum", "..."]
    for f in fillers:
        accepted, cmd, wake = main.extract_voice_interact_command(f, session_active=False)
        assert accepted is False, f"Le bruit parasite '{f}' aurait dû être ignoré !"


def test_same_origin_allows_reverse_proxy_and_host_matching():
    """Vérifie que same_origin ne rejette pas les requêtes légitimes derrière Cloudflare Tunnel, Nginx ou Tailscale."""
    from starlette.requests import Request

    # 1. Requête où l'origin correspond au Host header (ex: https://jarvis.example.com vers port 5050 uvicorn)
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"host", b"jarvis.moctar.tech"),
            (b"origin", b"https://jarvis.moctar.tech"),
            (b"sec-fetch-site", b"same-origin"),
            (b"x-forwarded-proto", b"https"),
        ],
        "server": ("127.0.0.1", 5050),
        "client": ("127.0.0.1", 45678),
        "path": "/api/voice/interact",
        "query_string": b"",
    }
    req = Request(scope)
    # Ne doit pas lever d'exception HTTPException(403)
    main.same_origin(req)


def test_groq_voice_semantic_turn_integration():
    """Vérifie que le moteur sémantique vocal dispose de la fonction d'interrogation Groq rapide."""
    import voice_semantic_engine

    assert hasattr(voice_semantic_engine, "_call_groq_voice_semantic")
    # Sans clé API, doit retourner None immédiatement (0 ms)
    res = voice_semantic_engine._call_groq_voice_semantic([], groq_api_key="")
    assert res is None


def test_whisper_repetition_deduplication_preserves_speech():
    """Vérifie que les répétitions de phrases Whisper (boucles) sont dédupliquées sans détruire la phrase réelle."""
    import voice_transcribe

    repeated_input = "Salut, tu me reçois ? Salut, tu me reçois ? Tu me reçois ? Tu me reçois ?"
    res = voice_transcribe.filter_whisper_hallucinations(repeated_input)
    assert res != ""
    assert "reçois" in res.lower()
    assert "salut" in res.lower()


def test_noc_and_hud_synchronization_and_autonomous_execution():
    """Vérifie que /api/jarvis/query et /api/voice/interact partagent la même logique autonome (ex: mode HUD 3D) et synchronisent l'état HUD."""
    from fastapi.testclient import TestClient
    import hud_state_manager

    app = main.create_app()
    with TestClient(app) as client:
        # 1. Demande de passage au mode HUD 3D depuis le dashboard NOC (/api/jarvis/query)
        resp = client.post("/api/jarvis/query", json={"prompt": "Mets-toi en mode HUD 3D", "source": "web_text"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data.get("action") == "open_app"
        assert data.get("url") == "/hud"
        assert "HUD 3D" in data["reply"]

        # L'état HUD partagé doit avoir été mis à jour immédiatement
        hud_st = hud_state_manager.get_hud_state()
        assert hud_st.get("user_transcript") == "Mets-toi en mode HUD 3D"
        assert "HUD 3D" in hud_st.get("assistant_reply", "")


def test_mic_gain_normalized_to_prevent_clipping():
    """Vérifie que le gain micro est configuré à 1.0 (et non 2.0) dans les interfaces frontend pour éviter la distorsion audio."""
    with open("static/index.html", "r", encoding="utf-8") as f:
        noc_html = f.read()
    assert "micGain: 1.0" in noc_html
    assert "micGain: 2.0" not in noc_html

    with open("static/js/hud_3d.js", "r", encoding="utf-8") as f:
        hud_js = f.read()
    assert "micGain: 1.0" in hud_js
    assert "micGain: 2.0" not in hud_js


def test_groq_discovery_prioritizes_llama_models():
    """Vérifie que la découverte des modèles Groq priorise Llama 3.3 70B et Llama 3.1 8B pour éviter les quotas 429."""
    import ai_brain

    models = ai_brain.get_available_groq_chat_models(api_key="", force_refresh=True)
    assert len(models) >= 2
    assert models[0] == "llama-3.3-70b-versatile"
    assert models[1] == "llama-3.1-8b-instant"


