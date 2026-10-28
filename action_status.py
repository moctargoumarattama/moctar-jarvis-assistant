"""Truthful action metadata shared by voice, headless and WhatsApp responses."""
from __future__ import annotations

from typing import Any


def action_status_fields(result: dict[str, Any]) -> dict[str, Any]:
    """Keep transport success separate from execution and security outcomes."""
    status = str(result.get("status") or (
        "failed" if result.get("success") is False else "success"
    ))
    status = {"final": "success", "confirmation_required": "pending_confirmation",
              "degraded": "degraded_fallback", "DEGRADE": "degraded_fallback",
              "INDISPONIBLE": "error"}.get(status, status)
    security = str(result.get("security_status") or "allowed")
    if security == "blocked":
        status = "blocked"
    requires_confirmation = bool(result.get("requires_confirmation") or status in {
        "pending_confirmation", "awaiting_confirmation",
    })
    if requires_confirmation and status not in {"blocked", "error", "failed"}:
        status = "pending_confirmation"
    if requires_confirmation and security == "allowed":
        security = "pending_confirmation"
    if status == "blocked":
        security = "blocked"
    failed = status in {"error", "failed", "blocked", "degraded_fallback", "max_steps"}
    if status == "fallback" and result.get("model_offline"):
        status, failed = "degraded_fallback", True
    verification = result.get("verification_status")
    if not verification:
        verification = "pending_confirmation" if requires_confirmation else (
            "not_verified" if failed else "not_required"
        )
    return {
        "ok": not failed,
        "status": status,
        "security_status": security,
        "requires_confirmation": requires_confirmation,
        "pending_action_id": result.get("pending_action_id"),
        "verification_status": verification,
    }
