"""Module de base pour la standardisation des actions et outils JARVIS.

Définit le conteneur de retour unifié pour le Function Calling (JSON Schema / Google GenAI)
et les fonctions d'aide pour garantir un format de réponse prédictible et robuste.
"""

from typing import Any, Dict, List, Optional, Union


class ActionResult(dict):
    """Conteneur standardisé et sérialisable JSON pour les retours d'outils JARVIS.

    Garantit le respect strict de la structure Function Calling demandée :
    {
        "success": bool,
        "action": str,
        "message": str,
        "data": dict | list | str | int | float | None,
        "error": str | None
    }

    Assure également une rétrocompatibilité transparente avec les composants
    historiques qui attendent une chaîne de caractères ou effectuent des tests d'égalité.
    """

    def __init__(
        self,
        action: str,
        success: bool,
        message: str,
        data: Optional[Union[Dict[str, Any], List[Any], str, int, float]] = None,
        error: Optional[str] = None,
    ) -> None:
        super().__init__(
            success=bool(success),
            action=str(action),
            message=str(message),
            data=data,
            error=str(error) if error is not None else None,
        )

    @property
    def success(self) -> bool:
        """Indique si l'action a réussi."""
        return self["success"]

    @property
    def action(self) -> str:
        """Nom de la fonction exécutée."""
        return self["action"]

    @property
    def message(self) -> str:
        """Message lisible pour l'agent ou l'utilisateur."""
        return self["message"]

    @property
    def data(self) -> Optional[Union[Dict[str, Any], List[Any], str, int, float]]:
        """Données brutes retournées."""
        return self["data"]

    @property
    def error(self) -> Optional[str]:
        """Description de l'erreur en cas d'échec."""
        return self["error"]

    def strip(self, *args: Any, **kwargs: Any) -> str:
        """Compatibilité ascendante pour les couches attendant une chaîne."""
        return self["message"].strip(*args, **kwargs)

    def lower(self) -> str:
        """Compatibilité ascendante pour les vérifications textuelles."""
        return self["message"].lower()

    def __str__(self) -> str:
        """Représentation textuelle de l'action."""
        return self["message"]

    def __eq__(self, other: Any) -> bool:
        """Permet la comparaison avec une chaîne (tests) ou un dictionnaire."""
        if isinstance(other, str):
            return self["message"] == other
        return super().__eq__(other)

    def __contains__(self, item: Any) -> bool:
        """Permet la recherche par clé (dict) ou par sous-chaîne dans message."""
        if super().__contains__(item):
            return True
        if isinstance(item, str):
            return item in self["message"]
        return False


def format_action_result(
    action: str,
    success: bool,
    message: str,
    data: Optional[Union[Dict[str, Any], List[Any], str, int, float]] = None,
    error: Optional[str] = None,
) -> ActionResult:
    """Crée un résultat d'action standardisé et validé.

    Args:
        action (str): Nom de l'action ou de l'outil exécuté.
        success (bool): Indicateur de réussite de l'action.
        message (str): Résumé textuel lisible pour l'agent et l'utilisateur.
        data (Optional[Union[Dict[str, Any], List[Any], str, int, float]]): Données brutes renvoyées.
        error (Optional[str]): Message d'erreur explicite en cas d'échec.

    Returns:
        ActionResult: Dictionnaire typé conforme au contrat d'interface.
    """
    return ActionResult(
        action=action,
        success=success,
        message=message,
        data=data,
        error=error,
    )

