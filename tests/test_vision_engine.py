"""Tests unitaires pour le moteur de vision cognitive unifié NeuroVision (vision_engine.py)."""

import pytest
import numpy as np
from unittest.mock import MagicMock, patch

from vision_engine import VisionEngine
import main
import hud_state_manager
import actions


def test_vision_engine_singleton_and_defaults():
    engine = VisionEngine.get_instance()
    assert engine is not None
    metrics = engine.get_metrics()
    assert "active" in metrics
    assert "user_present" in metrics
    assert "fatigue_score" in metrics
    assert "fatigue_level" in metrics
    assert "blink_count" in metrics
    assert "yawn_count" in metrics


def test_vision_engine_head_pose_calculation():
    engine = VisionEngine.get_instance()
    # Test pose centrale
    tilt, roll = engine._detect_head_pose_real(face_x=190, face_y=130, face_w=100, face_h=100)
    assert abs(tilt) <= 5.0
    assert abs(roll) <= 5.0


def test_vision_engine_fatigue_score_calculation():
    engine = VisionEngine.get_instance()
    # Sans bâillement ni clignement excessif
    engine.blink_count = 5
    engine.yawn_count = 0
    engine.head_tilt = 0.0
    engine.head_roll = 0.0
    score = engine._calculate_real_fatigue_score()
    assert 0 <= score <= 100

    # Avec 3 bâillements
    engine.yawn_count = 3
    fatigued_score = engine._calculate_real_fatigue_score()
    assert fatigued_score >= score + 20


def test_hud_state_contains_vision():
    state = hud_state_manager.get_hud_state()
    assert "vision" in state
    assert isinstance(state["vision"], dict)


def test_voice_instant_commands_vision():
    # Activation variations
    for phrase in [
        "active la vision",
        "mets le mode neurovision",
        "mets-toi en mode neurovision",
        "mode neurovision",
        "active neurovision",
        "active le mode neurovision",
        "ouvre neurovision",
        "affiche neurovision",
        "lance neurovision",
        "allume la caméra",
    ]:
        resp_on = main.handle_instant_local_command(phrase)
        assert resp_on is not None, f"Failed on: {phrase}"
        assert "activées" in resp_on.lower() or "active" in resp_on.lower(), f"Unexpected reply: {resp_on}"

    # Coupure variations
    for phrase in [
        "coupe la caméra",
        "désactive neurovision",
        "désactive le mode neurovision",
        "ferme neurovision",
        "ferme le mode neurovision",
        "quitte le mode neurovision",
        "arrête la vision",
        "éteins la caméra",
    ]:
        resp_off = main.handle_instant_local_command(phrase)
        assert resp_off is not None, f"Failed on: {phrase}"
        assert "désactivés" in resp_off.lower() or "desactive" in resp_off.lower(), f"Unexpected reply: {resp_off}"

    # État de fatigue
    resp_fatigue = main.handle_instant_local_command("niveau de fatigue")
    assert resp_fatigue is not None
    assert "fatigue" in resp_fatigue.lower() or "veille" in resp_fatigue.lower()


def test_actions_tool_dispatcher_vision():
    res_telemetry = actions.execute_tool("get_vision_telemetry", {})
    assert res_telemetry["success"] is True
    assert "fatigue" in res_telemetry["message"].lower()

    res_control = actions.execute_tool("control_vision", {"action": "stop"})
    assert res_control["success"] is True
    assert res_control["data"]["active"] is False


def test_fastapi_vision_routes_registered():
    app = main.create_app()
    routes = [r.path for r in app.routes]
    assert "/api/vision/metrics" in routes
    assert "/api/vision/stream" in routes
    assert "/api/vision/toggle" in routes
    assert "/api/vision/reset" in routes
    assert "/api/vision/frame" in routes
    assert "/api/vision/telemetry" in routes


def test_update_client_telemetry():
    """Vérifie la synchronisation directe de télémétrie sans solliciter la caméra du serveur."""
    from vision_engine import VisionEngine
    engine = VisionEngine.get_instance()
    res = engine.update_client_telemetry({
        "blink_count": 14,
        "yawn_count": 2,
        "head_tilt_yaw": 5.5,
        "head_roll": -2.0,
        "fatigue_score": 35,
        "fatigue_level": "Alerte",
    })
    assert res["blink_count"] == 14
    assert res["yawn_count"] == 2
    assert res["head_tilt_yaw"] == 5.5
    assert res["source"] == "client_webcam"


def test_analyze_client_frame_processing():
    """Vérifie le traitement d'une frame transmise par un client distant (PC de Moctar)."""
    from vision_engine import VisionEngine, get_standby_jpeg_bytes
    engine = VisionEngine.get_instance()
    jpeg_bytes = get_standby_jpeg_bytes()
    metrics = engine.analyze_client_frame(jpeg_bytes)
    assert metrics["active"] is True
    assert metrics.get("source") == "client_webcam"
    assert "fatigue_score" in metrics
    assert "blink_count" in metrics


def test_standby_jpeg_bytes_is_valid_jpeg():
    """Vérifie que la mire de secours renvoie toujours un JPEG binaire valide."""
    from vision_engine import get_standby_jpeg_bytes
    data = get_standby_jpeg_bytes()
    assert isinstance(data, bytes)
    assert len(data) > 0
    # Vérification signature JPEG (Start Of Image 0xFFD8 et End Of Image 0xFFD9)
    assert data[:2] == b"\xff\xd8"
    assert data[-2:] == b"\xff\xd9"


def test_vision_engine_graceful_degradation_without_cv2():
    """Simule un hôte sans OpenCV (cv2 absent) pour vérifier la tolérance aux pannes."""
    import vision_engine
    with patch.object(vision_engine, "CV2_AVAILABLE", False), patch.object(vision_engine, "cv2", None):
        engine = VisionEngine(camera_index=99)
        engine._open_camera()
        assert engine.simulation is True
        metrics = engine.get_metrics()
        assert metrics["cv2_available"] is False
        assert metrics["camera_connected"] is False
        assert metrics["status"] == "cv2_missing"
        assert "opencv-python" in metrics["message"].lower()

