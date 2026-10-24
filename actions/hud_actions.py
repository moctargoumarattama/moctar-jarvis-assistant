"""Module d'actions de contrôle pour l'interface HUD 3D de M.O.C.T.A.R."""

from __future__ import annotations

from typing import Any, Dict

from actions.base import format_action_result
import hud_state_manager


def switch_hud_view(view: str, locate: bool = False, mood: Optional[str] = None) -> Dict[str, Any]:
    """Bascule dynamiquement la vue HUD (orbe, plasma, quantum ou globe terrestre).

    Args:
        view (str): 'orb', 'plasma', 'quantum' ou 'globe'.
        locate (bool): Si True, active le focus GPS sur la position de Moctar.
        mood (str, optional): Ambiance émotionnelle ('calm', 'friendly', 'success', 'quantum', 'night', 'alert').
    """
    if mood:
        hud_state_manager.set_hud_mood(mood=mood)
    state = hud_state_manager.set_hud_view(view=view, locate=locate)
    return format_action_result(
        action="switch_hud_view",
        success=True,
        message=f"Vue HUD basculée sur {state['view']}.",
        data=state,
        error=None,
    )


def set_hud_mood(mood: str, color: Optional[str] = None, pulse_speed: Optional[float] = None) -> Dict[str, Any]:
    """Définit l'ambiance émotionnelle et la couleur de l'orbe HUD 3D."""
    state = hud_state_manager.set_hud_mood(mood=mood, color=color, pulse_speed=pulse_speed)
    return format_action_result(
        action="set_hud_mood",
        success=True,
        message=f"Ambiance de l'orbe 3D configurée sur {state['mood']} ({state['mood_color']}).",
        data=state,
        error=None,
    )

