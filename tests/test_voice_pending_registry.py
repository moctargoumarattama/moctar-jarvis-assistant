"""Session ownership and single-consumption regressions for voice confirmations."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import voice_semantic_engine as voice


@pytest.fixture
def registry(monkeypatch):
    voice.clear_pending_voice_action()
    security = SimpleNamespace(
        is_domain_allowed=Mock(return_value=True),
        validate_arguments=Mock(return_value=(True, "")),
        get_risk_level=Mock(return_value=3),
        requires_confirmation=Mock(return_value=True),
        confirmation_prompt=Mock(return_value="Confirmez cette action."),
        audit_log=Mock(),
    )
    execute = Mock(return_value={"success": True, "message": "Effectue."})
    monkeypatch.setattr(voice, "_resolve_security_engine", lambda runtime=None: security)
    monkeypatch.setattr(voice, "execute_tool", execute)
    yield security, execute
    voice.clear_pending_voice_action()


def queue(session="alice", **arguments):
    return voice.execute_voice_tool_securely(
        "delete_file", arguments, runtime=SimpleNamespace(session_id=session),
    )


def test_unknown_session_cannot_read_confirm_or_cancel_another_action(registry):
    _, execute = registry
    result = queue()
    assert voice.get_pending_voice_action("bob") is None
    assert not voice.has_pending_voice_action("bob")
    assert not voice.confirm_pending_voice_action("bob")["success"]
    assert not voice.cancel_pending_voice_action("bob")["success"]
    assert not voice.confirm_pending_voice_action("alice", SimpleNamespace(session_id="bob"))["success"]
    assert not voice.cancel_pending_voice_action("alice", SimpleNamespace(session_id="bob"))["success"]
    assert voice.has_pending_voice_action("alice")
    assert not voice.has_pending_voice_action()
    assert not voice.confirm_pending_voice_action(
        result["pending_action_id"], SimpleNamespace(session_id="bob"),
    )["success"]
    execute.assert_not_called()
    assert voice.confirm_pending_voice_action(
        result["pending_action_id"], SimpleNamespace(session_id="alice"),
    )["success"]


def test_simultaneous_confirmations_consume_before_executing(registry):
    _, execute = registry
    queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: voice.confirm_pending_voice_action("alice"), range(2)))
    assert sum(bool(result["success"]) for result in results) == 1
    execute.assert_called_once()
    assert not voice.has_pending_voice_action("alice")


def test_cancel_consumes_only_the_matching_session(registry):
    _, execute = registry
    queue("alice")
    queue("bob")
    assert voice.cancel_pending_voice_action("alice")["success"]
    assert not voice.cancel_pending_voice_action("alice")["success"]
    assert voice.has_pending_voice_action("bob")
    execute.assert_not_called()


def test_actions_expire_at_the_ttl_boundary(registry, monkeypatch):
    _, execute = registry
    monkeypatch.setattr(voice.time, "time", lambda: 1000.0)
    queue()
    monkeypatch.setattr(voice.time, "time", lambda: 1180.0)
    assert not voice.confirm_pending_voice_action("alice")["success"]
    execute.assert_not_called()
    with voice._pending_voice_lock:
        assert not voice._pending_voice_actions


def test_same_second_actions_have_unique_ids_and_registry_is_bounded(registry, monkeypatch):
    monkeypatch.setattr(voice.time, "time", lambda: 1000.0)
    results = [queue(str(index)) for index in range(70)]
    assert len({result["pending_action_id"] for result in results}) == 70
    with voice._pending_voice_lock:
        assert len(voice._pending_voice_actions) == voice.MAX_PENDING_VOICE_ACTIONS == 64
    assert not voice.has_pending_voice_action("0")
    assert voice.has_pending_voice_action("69")


def test_reading_cannot_mutate_the_registered_arguments(registry):
    queue(path="original.txt")
    action = voice.get_pending_voice_action("alice")
    action["arguments"]["path"] = "other.txt"
    assert voice.get_pending_voice_action("alice")["arguments"]["path"] == "original.txt"


def test_confirmation_rechecks_permissions_and_reports_execution_failure(registry):
    security, execute = registry
    queue()
    security.is_domain_allowed.return_value = False
    assert voice.confirm_pending_voice_action("alice")["status"] == "blocked"
    execute.assert_not_called()
    security.is_domain_allowed.return_value = True
    queue()
    execute.return_value = {"success": False, "status": "failed", "message": "Refuse."}
    result = voice.confirm_pending_voice_action("alice")
    assert not result["success"]
    assert result["status"] == "error"
    assert not voice.has_pending_voice_action("alice")


def test_trusted_scope_overrides_llm_supplied_session_and_resumes_exact_session(registry):
    _, execute = registry
    with voice.voice_session_scope("alice"):
        queue("wrong-runtime", session_id="bob")
    assert voice.has_pending_voice_action("alice")
    assert not voice.has_pending_voice_action("bob")
    assert not voice.resolve_pending_voice_command("Confirme l'action", "bob")["success"]
    execute.assert_not_called()
    assert voice.resolve_pending_voice_command("Confirme l’action", "alice")["success"]
    execute.assert_called_once()


def test_semantic_turn_binds_pending_action_to_its_actual_conversation(registry, monkeypatch):
    brain = SimpleNamespace(chat=Mock(return_value={
        "content": "", "tool_calls": [{"name": "delete_file", "arguments": {"session_id": "forged"}}],
    }))
    monkeypatch.setattr(voice, "build_live_system_context", lambda runtime=None: "")
    result = voice.execute_voice_semantic_turn(
        "Execute cette operation de maintenance", runtime=SimpleNamespace(ai_brain=brain), session_id="alice",
    )
    assert result["status"] == "pending_confirmation"
    assert result["pending_action_id"]
    assert result["tools_used"][0]["pending_action_id"] == result["pending_action_id"]
    assert voice.has_pending_voice_action("alice")
    assert not voice.has_pending_voice_action("forged")


def test_semantic_turn_exposes_a_disabled_domain_as_blocked(registry, monkeypatch):
    security, execute = registry
    security.is_domain_allowed.return_value = False
    brain = SimpleNamespace(chat=Mock(return_value={
        "content": "", "tool_calls": [{"name": "open_app", "arguments": {"target": "terminal"}}],
    }))
    monkeypatch.setattr(voice, "build_live_system_context", lambda runtime=None: "")
    result = voice.execute_voice_semantic_turn(
        "Lance une application de travail", runtime=SimpleNamespace(ai_brain=brain), session_id="alice",
    )
    assert result["status"] == result["security_status"] == "blocked"
    execute.assert_not_called()


def test_bridge_confirmation_revalidates_its_own_security_policy(monkeypatch, tmp_path):
    """A permissive process default must not override the headless bridge policy."""
    voice.clear_pending_voice_action()
    bridge_security = voice.SecurityPolicyEngine(
        settings={"domain_permissions": {"system": False}}, log_path=tmp_path / "bridge-audit.log",
    )
    default_security = voice.SecurityPolicyEngine(
        settings={"domain_permissions": {"system": True}}, log_path=tmp_path / "default-audit.log",
    )
    monkeypatch.setattr(voice, "_DEFAULT_SECURITY_ENGINE", default_security)
    execute = Mock(return_value={"success": True, "message": "Effectue."})
    monkeypatch.setattr(voice, "execute_tool", execute)
    bridge = SimpleNamespace(agent=SimpleNamespace(security=bridge_security), active_session_id="alice")
    assert voice._resolve_security_engine(bridge) is bridge_security
    assert voice._resolve_security_engine(SimpleNamespace(jarvis=bridge)) is bridge_security
    voice._register_pending_voice_action("alice", "execute_system_command", {"command": "echo audit"}, 3, "Confirmez.")
    try:
        result = voice.resolve_pending_voice_command("Confirme l'action", "alice", bridge)
        assert result["status"] == result["security_status"] == "blocked"
        execute.assert_not_called()
        assert not voice.has_pending_voice_action("alice")
    finally:
        voice.clear_pending_voice_action()


def test_service_diagnosis_obeys_system_domain_permission(monkeypatch, tmp_path):
    security = voice.SecurityPolicyEngine(
        settings={"domain_permissions": {"system": False, "services": True}},
        log_path=tmp_path / "diagnosis-audit.log",
    )
    bridge = SimpleNamespace(agent=SimpleNamespace(security=security))
    execute = Mock()
    monkeypatch.setattr(voice, "execute_tool", execute)
    result = voice.execute_voice_tool_securely("diagnose_linux_service", {"service_name": "jarvis.service"}, bridge)
    assert result["status"] == "blocked"
    execute.assert_not_called()
