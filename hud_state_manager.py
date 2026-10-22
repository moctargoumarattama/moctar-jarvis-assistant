"""Gestionnaire d'état en temps réel pour l'interface HUD 3D de M.O.C.T.A.R.

Maintient la synchronisation 0 ms entre les ordres vocaux autonomes,
le streaming SSE, les métriques matérielles Dell Precision M4800,
et les vues 3D du navigateur (Globe réaliste NASA, Réacteur Plasma & Cœur Quantique).
"""

from __future__ import annotations

import socket
import time
from typing import Any, Dict, Optional

try:
    import psutil
except ImportError:
    psutil = None

# État singleton partagé en mémoire
_HUD_STATE: Dict[str, Any] = {
    "view": "orb",            # 'orb' (orbe / réacteur à arc plasma) | 'quantum' (noyau neuronal) | 'globe' (Terre NASA 3D)
    "locate": False,          # True pour activer la géolocalisation et le zoom radar
    "status": "idle",         # 'idle' | 'listening' | 'thinking' | 'speaking'
    "user_transcript": "",    # Dernière phrase prononcée par Moctar
    "assistant_reply": "",    # Dernière réponse vocale formulée par M.O.C.T.A.R
    "target_coords": None,    # {'lat': float, 'lon': float} si position fixée
    "last_command": None,
    "last_updated": time.time(),
}


def get_hud_state() -> Dict[str, Any]:
    """Retourne l'état HUD enrichi des métriques matérielles en temps réel."""
    state = dict(_HUD_STATE)
    state["cpu_percent"] = 12.0
    state["ram_percent"] = 45.0
    state["ram_used_gb"] = 7.2
    state["ram_total_gb"] = 16.0
    state["hostname"] = "moctar-Precision-M4800"

    if psutil is not None:
        try:
            state["cpu_percent"] = round(float(psutil.cpu_percent(interval=None)), 1)
            mem = psutil.virtual_memory()
            state["ram_percent"] = round(float(mem.percent), 1)
            state["ram_used_gb"] = round(float(mem.used) / (1024**3), 1)
            state["ram_total_gb"] = round(float(mem.total) / (1024**3), 1)
            state["hostname"] = socket.gethostname()
        except Exception:
            pass

    return state


def set_hud_view(
    view: str,
    locate: bool = False,
    coords: Optional[Dict[str, float]] = None,
    command: Optional[str] = None,
) -> Dict[str, Any]:
    """Bascule la vue 3D ('globe', 'plasma', 'quantum' ou 'orb') et gère la géolocalisation."""
    v = (view or "").lower().strip()
    if v in ("globe", "earth", "terre"):
        view_clean = "globe"
    elif v in ("quantum", "core", "neural", "quantique"):
        view_clean = "quantum"
    elif v in ("plasma", "reactor", "arc", "reacteur"):
        view_clean = "plasma"
    elif v in ("orb", "orbe"):
        view_clean = "orb"
    else:
        view_clean = "orb"

    _HUD_STATE["view"] = view_clean
    _HUD_STATE["locate"] = bool(locate)
    if coords is not None:
        _HUD_STATE["target_coords"] = coords
    if command:
        _HUD_STATE["last_command"] = command
    _HUD_STATE["last_updated"] = time.time()
    return get_hud_state()


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
    return get_hud_state()


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
    return get_hud_state()
