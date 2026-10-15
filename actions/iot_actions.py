"""Module d'actions IoT pour l'interaction domotique et capteurs locaux de JARVIS.

Fournit des outils standardisés pour le Function Calling (JSON Schema / Google GenAI) permettant
de consulter les capteurs domotiques (température, état global) et d'actionner les relais.
"""

from typing import Any, Dict, Optional, Tuple
import config
from actions.base import ActionResult, format_action_result


def _build_url(base_url: str, endpoint: str) -> str:
    """Construit une URL absolue propre à partir de l'URL de base et du endpoint.

    Args:
        base_url (str): URL racine de l'API IoT (ex: 'http://192.168.1.50:8000').
        endpoint (str): Chemin d'accès de l'endpoint (ex: '/api/v1/status').

    Returns:
        str: URL complète assemblée sans double slash.
    """
    return f"{base_url.rstrip('/')}{endpoint}"


def _safe_get_json(url: str, timeout: float) -> Tuple[Optional[Any], Optional[str]]:
    """Exécute un appel HTTP GET sécurisé avec gestion d'erreurs et retour JSON.

    Args:
        url (str): URL absolue à interroger.
        timeout (float): Délai maximal d'attente en secondes.

    Returns:
        Tuple[Optional[Any], Optional[str]]: Tuple (payload_json, message_erreur).
    """
    try:
        import requests
    except Exception as exc:
        return None, f"Module HTTP indisponible : {exc}"

    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json(), None
    except Exception as exc:
        return None, str(exc)


def get_iot_status() -> Dict[str, Any]:
    """Récupère l'état global et la télémétrie complète de l'ensemble des capteurs IoT connectés.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande un diagnostic
          général des capteurs, l'état global de la domotique, ou la liste des appareils
          connectés en ligne (ex: 'Quel est l'état de la maison ?', 'Vérifie les capteurs').
        - L'agent NE DOIT PAS appeler cet outil si l'utilisateur demande spécifiquement
          la température (utiliser `get_iot_temperature`) ou souhaite modifier l'état d'un
          appareil ou d'un relais électrique (utiliser `set_relay_state`).

    Args:
        Aucun argument requis.

    Returns:
        Dict[str, Any]: Dictionnaire standardisé contenant le statut d'exécution :
            - success (bool): True si les capteurs ont été joints avec succès, False sinon.
            - action (str): 'get_iot_status'.
            - message (str): Résumé textuel lisible pour l'agent et l'utilisateur.
            - data (dict | list | None): Données brutes de télémétrie renvoyées par l'API.
            - error (str | None): Message explicite de l'erreur en cas d'échec.
    """
    action_name = "get_iot_status"
    try:
        base_url = config.IOT_SETTINGS.get("base_url")
        if not base_url:
            return format_action_result(
                action=action_name,
                success=False,
                message="API IoT non configuree.",
                data=None,
                error="API IoT non configuree.",
            )

        payload, error = _safe_get_json(
            _build_url(base_url, config.IOT_SETTINGS["status_endpoint"]),
            config.IOT_SETTINGS["request_timeout"],
        )
        if error:
            return format_action_result(
                action=action_name,
                success=False,
                message=f"Etat capteurs indisponible : {error}",
                data=None,
                error=error,
            )

        return format_action_result(
            action=action_name,
            success=True,
            message=f"Dernier etat capteurs : {payload}",
            data=payload,
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Erreur inattendue lors de la récupération du statut IoT : {exc}",
            data=None,
            error=str(exc),
        )


def get_iot_temperature() -> Dict[str, Any]:
    """Récupère la température ambiante actuelle relevée par les sondes IoT connectées.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur pose une question sur la
          température ambiante (ex: 'Quelle est la température ?', 'Fait-il froid dans le salon ?',
          'Donne-moi la température actuelle').
        - L'agent NE DOIT PAS appeler cet outil pour un bilan complet des capteurs (utiliser
          `get_iot_status`) ni pour allumer ou éteindre un système de chauffage ou ventilateur
          (utiliser `set_relay_state`).

    Args:
        Aucun argument requis.

    Returns:
        Dict[str, Any]: Dictionnaire standardisé contenant le relevé de température :
            - success (bool): True si la sonde a répondu correctement, False sinon.
            - action (str): 'get_iot_temperature'.
            - message (str): Message textuel lisible indiquant la température.
            - data (dict | float | int | None): Données brutes de température (en °C).
            - error (str | None): Détail de l'erreur si la mesure a échoué.
    """
    action_name = "get_iot_temperature"
    try:
        base_url = config.IOT_SETTINGS.get("base_url")
        if not base_url:
            return format_action_result(
                action=action_name,
                success=False,
                message="API IoT non configuree.",
                data=None,
                error="API IoT non configuree.",
            )

        payload, error = _safe_get_json(
            _build_url(base_url, config.IOT_SETTINGS["temperature_endpoint"]),
            config.IOT_SETTINGS["request_timeout"],
        )
        if error:
            return format_action_result(
                action=action_name,
                success=False,
                message=f"Temperature indisponible : {error}",
                data=None,
                error=error,
            )

        return format_action_result(
            action=action_name,
            success=True,
            message=f"Temperature actuelle : {payload}",
            data=payload,
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Erreur inattendue lors de la lecture de la température IoT : {exc}",
            data=None,
            error=str(exc),
        )


def set_relay_state(relay_id: int, enabled: bool) -> Dict[str, Any]:
    """Active ou désactive un relais domotique physique (marche / arrêt).

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil dès que l'utilisateur demande explicitement
          d'allumer, éteindre, commuter ou basculer un relais ou équipement électrique
          contrôlé (ex: 'Allume la lampe du salon', 'Coupe le relais 2', 'Active le relais 1').
        - L'agent NE DOIT PAS appeler cet outil pour une simple consultation d'état ou de mesure
          (utiliser `get_iot_status` ou `get_iot_temperature`).
        - L'agent ne doit pas l'appeler sans connaître l'identifiant du relais ou avec des arguments invalides.

    Args:
        relay_id (int): Identifiant numérique unique du relais à commander.
            Contraintes: Entier positif >= 1 (exemples valides: 1, 2, 3).
        enabled (bool): État binaire cible du relais.
            Valeurs valides: True pour activer / fermer le relais (ON), False pour couper / ouvrir le relais (OFF).

    Returns:
        Dict[str, Any]: Dictionnaire standardisé contenant le résultat de la commande :
            - success (bool): True si le relais a été commuté avec succès, False sinon.
            - action (str): 'set_relay_state'.
            - message (str): Message de confirmation ou d'échec de la commande.
            - data (dict | None): Données de confirmation {'relay_id': relay_id, 'enabled': enabled}.
            - error (str | None): Détail de l'erreur en cas de rejet ou défaillance matérielle.
    """
    action_name = "set_relay_state"
    try:
        base_url = config.IOT_SETTINGS.get("base_url")
        if not base_url:
            return format_action_result(
                action=action_name,
                success=False,
                message="API IoT non configuree.",
                data=None,
                error="API IoT non configuree.",
            )

        try:
            import requests
        except Exception as exc:
            return format_action_result(
                action=action_name,
                success=False,
                message=f"Module HTTP indisponible : {exc}",
                data=None,
                error=str(exc),
            )

        try:
            response = requests.post(
                _build_url(base_url, config.IOT_SETTINGS["relay_endpoint"]),
                json={"relay_id": int(relay_id), "enabled": bool(enabled)},
                timeout=config.IOT_SETTINGS["request_timeout"],
            )
            response.raise_for_status()
            return format_action_result(
                action=action_name,
                success=True,
                message=f"Commande relais {relay_id} envoyee.",
                data={"relay_id": int(relay_id), "enabled": bool(enabled)},
                error=None,
            )
        except Exception as exc:
            return format_action_result(
                action=action_name,
                success=False,
                message=f"Commande relais impossible : {exc}",
                data=None,
                error=str(exc),
            )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Erreur inattendue lors de la commande du relais : {exc}",
            data=None,
            error=str(exc),
        )
