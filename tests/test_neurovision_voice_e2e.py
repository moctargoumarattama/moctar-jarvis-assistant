"""Tests de bout en bout pour le contrôle vocal exhaustif de NeuroVision et du cockpit HUD/NOC."""

import pytest
from unittest.mock import patch, MagicMock

import main
import autonomous_library
from vision_engine import VisionEngine


def test_neurovision_direct_voice_command_recognition():
    """Vérifie que les ordres NeuroVision sous toutes leurs formes sont reconnus comme ordres directs sans mot de réveil."""
    phrases = [
        "mets le mode neurovision",
        "mets-toi en mode neurovision",
        "mode neurovision",
        "active neurovision",
        "active le mode neurovision",
        "ouvre neurovision",
        "ouvre le mode neurovision",
        "affiche neurovision",
        "lance neurovision",
        "lance le scanner neurovision",
        "scanner neurovision",
        "active la caméra",
        "active la vision",
        "désactive neurovision",
        "désactive le mode neurovision",
        "ferme neurovision",
        "ferme le mode neurovision",
        "quitte le mode neurovision",
        "coupe la caméra",
        "arrête la vision",
        "niveau de fatigue",
        "score de fatigue",
        "analyse mon visage",
    ]
    for p in phrases:
        assert main.is_direct_voice_command(p) is True, f"Doit être reconnu comme commande directe : '{p}'"


def test_neurovision_instant_local_commands():
    """Vérifie le dispatch instantané en 0 ms côté main.handle_instant_local_command."""
    # Activation
    for p in ["mets le mode neurovision", "active neurovision", "ouvre le mode neurovision", "lance le scanner neurovision"]:
        reply = main.handle_instant_local_command(p)
        assert reply is not None, f"Réponse attendue pour : '{p}'"
        assert "activées" in reply.lower() or "active" in reply.lower(), f"Mauvaise réponse : '{reply}'"

    # Désactivation
    for p in ["désactive le mode neurovision", "ferme neurovision", "quitte le mode neurovision", "coupe la caméra"]:
        reply = main.handle_instant_local_command(p)
        assert reply is not None, f"Réponse attendue pour : '{p}'"
        assert "désactivés" in reply.lower() or "desactive" in reply.lower(), f"Mauvaise réponse : '{reply}'"


def test_autonomous_library_neurovision_actions():
    """Vérifie que autonomous_library renvoie les métadonnées de modale pour le frontend."""
    # Start
    res_start = autonomous_library.execute_autonomous_task("mets le mode neurovision")
    assert res_start.get("handled") is True
    assert res_start.get("action") == "open_modal"
    assert res_start.get("target") == "neurovision-dialog"
    assert res_start.get("modal_id") == "neurovision-dialog"
    assert res_start.get("task_category") == "neurovision"

    # Stop
    res_stop = autonomous_library.execute_autonomous_task("désactive le mode neurovision")
    assert res_stop.get("handled") is True
    assert res_stop.get("action") == "close_modal"
    assert res_stop.get("target") == "neurovision-dialog"
    assert res_stop.get("modal_id") == "neurovision-dialog"
    assert res_stop.get("task_category") == "neurovision"

    # Face analysis
    res_face = autonomous_library.execute_autonomous_task("analyse mon visage")
    assert res_face.get("handled") is True
    assert res_face.get("task_category") == "neurovision"

