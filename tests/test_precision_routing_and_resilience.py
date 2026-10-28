# -*- coding: utf-8 -*-
"""Tests de non-régression et de haute précision pour le routage des commandes vocales,
la résilience des modèles d'IA (Gemini/Groq) et les règles d'exclusion du Smart Command Cache.
"""

import pytest
import autonomous_library
from smart_command_cache import SmartCommandCache, UNCACHEABLE_PATTERNS
import ai_brain
import voice_semantic_engine


def test_media_pause_precision_routing():
    """Vérifie que toutes les variations de pause média sont strictement routées
    vers control_playback(pause) et ne déclenchent JAMAIS launch_playlist.
    """
    pause_phrases = [
        "mets pause",
        "mets en pause",
        "mets sur pause",
        "mettre en pause",
        "mets la musique en pause",
        "met la musique en pause",
        "mets la video en pause",
        "mets la vidéo en pause",
        "pause la musique",
        "stop la musique",
        "coupe la musique",
        "arrete la musique",
        "arrête la musique",
        "mets youtube en pause",
    ]
    for phrase in pause_phrases:
        res = autonomous_library.execute_autonomous_task(phrase)
        assert res.get("handled") is True, f"Doit être géré: '{phrase}'"
        assert res.get("task_category") == "media", f"Catégorie media attendue pour '{phrase}'"
        tools = [t.get("tool") for t in res.get("tools_used", [])]
        assert "control_playback" in tools, f"control_playback attendu pour '{phrase}' (reçu: {tools})"
        assert "launch_playlist" not in tools, f"launch_playlist NE DOIT PAS être appelé pour '{phrase}'"
        args = [t.get("arguments", {}).get("action") for t in res.get("tools_used", []) if t.get("tool") == "control_playback"]
        assert "pause" in args, f"Action 'pause' attendue pour '{phrase}'"


def test_media_play_and_playlist_routing():
    """Vérifie que les commandes explicites de lecture ou de playlist fonctionnent normalement."""
    res_play = autonomous_library.execute_autonomous_task("lance ma playlist")
    assert res_play.get("handled") is True
    assert res_play.get("task_category") == "media"
    tools_play = [t.get("tool") for t in res_play.get("tools_used", [])]
    assert "launch_playlist" in tools_play


def test_neurovision_strict_routing_no_false_positive():
    """Vérifie que la phrase 'Quelle playlist tu vois bien, active Youtube'
    ne déclenche JAMAIS la télémétrie faciale NeuroVision.
    """
    res = autonomous_library.execute_autonomous_task("Quelle playlist tu vois bien, active Youtube.")
    # Ne doit absolument PAS être routé vers NeuroVision
    assert res.get("task_category") != "neurovision", (
        "La phrase contient 'vois' dans un contexte musical, elle ne doit pas déclencher NeuroVision"
    )

    # En revanche, les requêtes faciales explicites doivent déclencher NeuroVision
    res_face1 = autonomous_library.execute_autonomous_task("analyse mon visage")
    assert res_face1.get("handled") is True
    assert res_face1.get("task_category") == "neurovision"

    res_face2 = autonomous_library.execute_autonomous_task("est-ce que tu me vois")
    assert res_face2.get("handled") is True
    assert res_face2.get("task_category") == "neurovision"


def test_window_snap_priority_over_music():
    """Vérifie qu'une commande contenant un snap de fenêtre et le mot 'musique'
    donne la priorité au placement de la fenêtre.
    """
    res = autonomous_library.execute_autonomous_task("Mets-le à gauche, snap, musique")
    assert res.get("handled") is True
    assert res.get("task_category") == "system_tools"
    tools = [t.get("tool") for t in res.get("tools_used", [])]
    assert "snap_active_window" in tools


def test_smart_cache_excludes_clarification_and_actions(tmp_path):
    """Vérifie que les clarifications et les actions mutantes ne sont jamais mises en cache."""
    cache = SmartCommandCache(db_path=tmp_path / "test_cache.db")

    # 1. Phrases de clarification / incompréhension
    clarif_reply = "Pardon Moctar, je n'ai pas bien capté ta phrase. Tu peux me redire ?"
    saved = cache.set("encore_une_fois_sais_pas", clarif_reply, category="general")
    assert saved is False, "Les phrases de clarification ne doivent jamais être mises en cache"
    assert cache.get("encore_une_fois_sais_pas") is None

    # 2. Actions mutantes multimédia
    action_reply = "Initialisation du flux audio. Votre playlist est lancée, Moctar."
    saved_action = cache.set(
        "mets la musique",
        action_reply,
        category="media",
        agent_result={"handled": True, "action": "play", "tools_used": [{"tool": "launch_playlist"}]},
    )
    assert saved_action is False, "Les actions média ne doivent jamais être mises en cache"
    assert cache.get("mets la musique") is None


def test_gemini_backup_models_configuration():
    """Vérifie que le modèle de secours Gemini est valide et non décommissionné."""
    assert ai_brain.GEMINI_BACKUP_MODEL == "gemini-2.0-flash"
    assert "gemini-2.5-flash" not in ai_brain.GEMINI_FALLBACK_MODELS
    assert "gemini-2.0-flash" in ai_brain.GEMINI_FALLBACK_MODELS
    assert "gemini-1.5-flash" in ai_brain.GEMINI_FALLBACK_MODELS


def test_groq_fallback_models_include_qwen_and_llama():
    """Vérifie que les priorités Groq contiennent les modèles de secours en cas de 429."""
    assert "llama-3.3-70b-versatile" in ai_brain.GROQ_MODELS_PRIORITY
    assert "llama-3.1-8b-instant" in ai_brain.GROQ_MODELS_PRIORITY
    assert "qwen/qwen3.8-27b" in ai_brain.GROQ_MODELS_PRIORITY

