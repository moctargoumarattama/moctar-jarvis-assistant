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
    # Activation
    resp_on = main.handle_instant_local_command("active la vision")
    assert resp_on is not None
    assert "activées" in resp_on.lower() or "active" in resp_on.lower()

    # Coupure
    resp_off = main.handle_instant_local_command("coupe la caméra")
    assert resp_off is not None
    assert "désactivés" in resp_off.lower() or "desactive" in resp_off.lower()

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

