"""Module d'actions de contrôle pour l'interface HUD 3D de M.O.C.T.A.R."""

from __future__ import annotations

from typing import Any, Dict

from actions.base import format_action_result
import hud_state_manager


def switch_hud_view(view: str, locate: bool = False) -> Dict[str, Any]:
    """Bascule dynamiquement la vue HUD (orbe ou globe terrestre).

    Args:
        view (str): 'orb' ou 'globe'.
        locate (bool): Si True, active le focus GPS sur la position de Moctar.
    """
    state = hud_state_manager.set_hud_view(view=view, locate=locate)
    return format_action_result(
        action="switch_hud_view",
        success=True,
        message=f"Vue HUD basculée sur {state['view']}.",
        data=state,
        error=None,
    )
