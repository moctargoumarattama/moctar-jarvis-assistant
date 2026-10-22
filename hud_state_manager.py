"""Gestionnaire d'état en temps réel pour l'interface HUD 3D de M.O.C.T.A.R.

Maintient la synchronisation 0 ms entre les ordres vocaux autonomes,
le streaming SSE et les vues 3D du navigateur (Orbe de particules & Globe terrestre interactif).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

# État singleton partagé en mémoire
_HUD_STATE: Dict[str, Any] = {
    "view": "orb",            # 'orb' (orbe de particules) ou 'globe' (globe terrestre 3D)
    "locate": False,          # True pour activer la géolocalisation et le zoom radar
    "status": "idle",         # 'idle' | 'listening' | 'thinking' | 'speaking'
    "user_transcript": "",    # Dernière phrase prononcée par Moctar
    "assistant_reply": "",    # Dernière réponse vocale formulée par M.O.C.T.A.R
    "target_coords": None,    # {'lat': float, 'lon': float} si position fixée
    "last_command": None,
    "last_updated": time.time(),
}


def get_hud_state() -> Dict[str, Any]:
    """Retourne une copie de l'état HUD actuel."""
    return dict(_HUD_STATE)


def set_hud_view(
    view: str,
    locate: bool = False,
    coords: Optional[Dict[str, float]] = None,
    command: Optional[str] = None,
) -> Dict[str, Any]:
    """Bascule la vue 3D ('orb' ou 'globe') et gère la demande de géolocalisation."""
    view_clean = "globe" if (view or "").lower().strip() in ("globe", "earth", "terre") else "orb"
    _HUD_STATE["view"] = view_clean
    _HUD_STATE["locate"] = bool(locate)
    if coords is not None:
        _HUD_STATE["target_coords"] = coords
    if command:
        _HUD_STATE["last_command"] = command
    _HUD_STATE["last_updated"] = time.time()
    return dict(_HUD_STATE)


def set_hud_voice_status(
    status: str,
    user_transcript: Optional[str] = None,
    assistant_reply: Optional[str] = None,
) -> Dict[str, Any]:
    """Met à jour l'état de l'orbe ('idle', 'listening', 'thinking', 'speaking') et les textes affichés."""
    if status in ("idle", "listening", "thinking", "speaking"):
        _HUD_STATE["status"] = status
    if user_transcript is not None:
        _HUD_STATE["user_transcript"] = user_transcript
    if assistant_reply is not None:
        _HUD_STATE["assistant_reply"] = assistant_reply
    _HUD_STATE["last_updated"] = time.time()
    return dict(_HUD_STATE)


def reset_hud_state() -> Dict[str, Any]:
    """Réinitialise l'état HUD vers l'orbe neutre."""
    _HUD_STATE.update({
        "view": "orb",
        "locate": False,
        "status": "idle",
        "user_transcript": "",
        "assistant_reply": "",
        "target_coords": None,
        "last_command": None,
        "last_updated": time.time(),
    })
    return dict(_HUD_STATE)
