"""Tests de validation pour la sensibilité d'écoute, l'accueil démo sans latence et la robustesse vocale."""

import pytest
import main
import dynamic_attention_engine
import voice_transcribe
from actions import hud_actions


def test_hud_actions_optional_import():
    """Vérifie que les fonctions de hud_actions s'exécutent sans erreur d'import Optional."""
    res_mood = hud_actions.set_hud_mood("friendly")
    assert res_mood["success"] is True
    assert res_mood["data"]["mood"] == "friendly"

    res_view = hud_actions.switch_hud_view("orb", locate=False, mood="success")
    assert res_view["success"] is True
    assert res_view["data"]["view"] == "orb"


def test_is_direct_voice_command_for_demos_and_friends():
    """Vérifie que toutes les phrases types de démonstration devant amis sont reconnues directement."""
    demo_phrases = [
        "dis bonjour à mes amis",
        "salue tout le monde",
        "accueille nos invités",
        "je vous présente mon assistant",
        "voici mes amis",
        "fais-nous une démo",
        "montre ce que tu sais faire",
        "montre tes capacités",
        "tu nous entends ?",
        "tu peux parler un peu ?",
        "fais une blague",
        "raconte une blague",
        "tu sais faire quoi ?",
        "explique-moi ce projet",
        "donne-moi l'heure",
    ]
    for phrase in demo_phrases:
        assert main.is_direct_voice_command(phrase) is True, f"Phrase non reconnue: {phrase}"


def test_dynamic_attention_does_not_ignore_conversational_openers():
    """Vérifie que 'alors...', 'bon alors...', 'attends...' suivis d'un ordre ne sont JAMAIS classés en soliloque."""
    openers = [
        "alors présente-toi",
        "bon alors dis bonjour",
        "alors montre ce que tu sais faire",
        "attends donne l'heure",
        "alors fais une démo",
    ]
    for text in openers:
        att = dynamic_attention_engine.classify_utterance_attention(text, is_session_active=False)
        assert att["is_thinking_out_loud"] is False, f"Ne doit pas être classé en soliloque: {text}"
        assert att["is_directed"] is True, f"Doit être adressé au système: {text}"


def test_polite_merci_does_not_abruptly_disengage():
    """Un simple 'merci' ou 'merci mon ami' ne doit pas couper brutalement la session."""
    att = dynamic_attention_engine.classify_utterance_attention("merci", is_session_active=True)
    assert att.get("should_disengage") is not True

    att_friend = dynamic_attention_engine.classify_utterance_attention("merci mon ami", is_session_active=True)
    assert att_friend.get("should_disengage") is not True

    # Seule une clôture explicite coupe la session
    att_close = dynamic_attention_engine.classify_utterance_attention("merci c'est tout pour aujourd'hui", is_session_active=True)
    assert att_close.get("should_disengage") is True


def test_voice_session_duration_default_is_generous():
    """La durée de session par défaut doit être d'au moins 45s pour éviter d'ignorer les questions enchaînées."""
    assert main.VOICE_SESSION_DURATION_SECONDS >= 45.0


def test_voice_transcribe_candidates_include_opt_jarvis():
    """Vérifie que le répertoire /opt/jarvis est bien inclus dans les chemins de recherche Whisper."""
    candidates = [str(c[0]) for c in voice_transcribe._candidates("base")]
    assert any("/opt/jarvis" in c or "\\opt\\jarvis" in c for c in candidates)
