"""Module d'actions système pour le contrôle de l'OS et le monitoring de JARVIS.

Fournit des outils standardisés pour le Function Calling (OpenAI / Ollama) permettant
d'interagir avec le système d'exploitation Windows (heure, date, batterie, capture d'écran,
contrôle du volume sonore, lancement et fermeture d'applications, ouverture de dossiers).
"""

from datetime import datetime
import locale
import os
import subprocess
from typing import Any, Dict, Optional

try:
    import psutil
except Exception:
    class _PsutilFallback:
        @staticmethod
        def sensors_battery():
            return None

    psutil = _PsutilFallback()

try:
    import pyautogui
except Exception:
    class _PyAutoGuiFallback:
        @staticmethod
        def screenshot():
            raise RuntimeError("pyautogui indisponible")

        @staticmethod
        def press(_key):
            raise RuntimeError("pyautogui indisponible")

    pyautogui = _PyAutoGuiFallback()

import config
from actions.base import ActionResult, format_action_result


def get_time_response(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Donne l'heure locale actuelle du système hôte.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil dès que l'utilisateur demande l'heure actuelle
          (ex: 'Quelle heure est-il ?', 'Donne-moi l'heure', 'Il est quelle heure ?').
        - L'agent NE DOIT PAS appeler cet outil si l'utilisateur demande la date du jour
          (utiliser `get_date_response`).

    Args:
        now (Optional[datetime]): Horodatage personnalisé pour tests unitaires (par défaut datetime.now()).

    Returns:
        Dict[str, Any]: Résultat standardisé contenant l'heure formatée :
            - success (bool): True si l'heure a été lue.
            - action (str): 'get_time_response'.
            - message (str): Message lisible (ex: 'Il est 14:30.').
            - data (dict): Horodatage structuré {'time': '14:30', 'iso': '...'}.
            - error (None): Aucune erreur.
    """
    action_name = "get_time_response"
    try:
        current = now or datetime.now()
        time_str = current.strftime("%H:%M")
        return format_action_result(
            action=action_name,
            success=True,
            message=f"Il est {time_str}.",
            data={"time": time_str, "iso": current.isoformat()},
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Erreur lors de la lecture de l'heure : {exc}",
            data=None,
            error=str(exc),
        )


def get_date_response(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Donne la date actuelle du jour au format complet francophone.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande quel jour on est,
          la date courante, ou le calendrier du jour (ex: 'Quel jour sommes-nous ?', 'Donne-moi la date').
        - L'agent NE DOIT PAS appeler cet outil pour une simple vérification d'heure
          (utiliser `get_time_response`).

    Args:
        now (Optional[datetime]): Horodatage personnalisé pour tests unitaires (par défaut datetime.now()).

    Returns:
        Dict[str, Any]: Résultat standardisé contenant la date complète du jour :
            - success (bool): True si la date a été générée.
            - action (str): 'get_date_response'.
            - message (str): Message lisible (ex: 'Nous sommes le mardi 14 octobre 2026.').
            - data (dict): Date structurée {'date': '...', 'iso': '...'}.
            - error (None): Aucune erreur.
    """
    action_name = "get_date_response"
    try:
        current = now or datetime.now()
        try:
            locale.setlocale(locale.LC_TIME, "")
        except Exception:
            pass
        formatted = current.strftime("%A %d %B %Y")
        return format_action_result(
            action=action_name,
            success=True,
            message=f"Nous sommes le {formatted}.",
            data={"date": formatted, "iso": current.isoformat()},
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Erreur lors de la lecture de la date : {exc}",
            data=None,
            error=str(exc),
        )


def get_battery_response(battery_provider: Any = psutil.sensors_battery) -> Dict[str, Any]:
    """Récupère le pourcentage et l'état de charge de la batterie du système hôte.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil dès que l'utilisateur s'enquiert du niveau d'énergie,
          de l'autonomie restante ou de la charge de la batterie de l'ordinateur portable
          (ex: 'Quel est le niveau de la batterie ?', 'Combien de batterie reste-t-il ?').
        - L'agent NE DOIT PAS appeler cet outil sur des serveurs fixes dépourvus de batterie
          ou pour les calculs de dimensionnement solaire (utiliser `calculate_battery_sizing`).

    Args:
        battery_provider (Any): Fonction fournissant l'état psutil de la batterie (injection de dépendance).

    Returns:
        Dict[str, Any]: Résultat standardisé avec l'état de la batterie :
            - success (bool): True si le capteur de batterie est accessible, False sinon.
            - action (str): 'get_battery_response'.
            - message (str): Message textuel lisible (ex: 'La batterie est a 85 pour cent.').
            - data (dict | None): Détails techniques {'percent': 85, 'power_plugged': True}.
            - error (str | None): Détail de l'erreur si la batterie n'est pas détectable.
    """
    action_name = "get_battery_response"
    try:
        battery = battery_provider()
        if battery:
            return format_action_result(
                action=action_name,
                success=True,
                message=f"La batterie est a {battery.percent} pour cent.",
                data={
                    "percent": battery.percent,
                    "power_plugged": getattr(battery, "power_plugged", None),
                },
                error=None,
            )
        return format_action_result(
            action=action_name,
            success=False,
            message="Je ne peux pas lire la batterie.",
            data=None,
            error="Capteur de batterie indisponible ou machine sur secteur sans batterie.",
        )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Erreur lors de la lecture de la batterie : {exc}",
            data=None,
            error=str(exc),
        )


def capture_screenshot(pyautogui_module: Any = pyautogui) -> Dict[str, Any]:
    """Capture une capture d'écran de l'affichage principal et l'enregistre sur le disque local.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande explicitement de faire
          une capture d'écran, un screenshot ou d'immortaliser l'affichage actuel
          (ex: 'Prends une capture d'écran', 'Fais un screenshot', 'Capture l'écran').
        - L'agent NE DOIT PAS appeler cet outil sans demande visuelle explicite de l'utilisateur.

    Args:
        pyautogui_module (Any): Module d'automatisation GUI (injection de dépendance).

    Returns:
        Dict[str, Any]: Résultat standardisé contenant les métadonnées de l'image enregistrée :
            - success (bool): True si le screenshot a été sauvegardé avec succès.
            - action (str): 'capture_screenshot'.
            - message (str): Confirmation avec le nom du fichier créé.
            - data (dict): Chemin complet et nom de la capture {'file_path': '...', 'name': '...'}.
            - error (str | None): Détail de l'erreur en cas d'échec de capture.
    """
    action_name = "capture_screenshot"
    try:
        config.ensure_runtime_directories()
        filename = (
            config.SCREENSHOTS_DIR
            / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        )
        pyautogui_module.screenshot().save(filename)
        return format_action_result(
            action=action_name,
            success=True,
            message=f"Capture enregistree : {filename.name}.",
            data={"file_path": str(filename), "name": filename.name},
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Impossible de prendre la capture d'écran : {exc}",
            data=None,
            error=str(exc),
        )


def change_volume(action: str, pyautogui_module: Any = pyautogui) -> Dict[str, Any]:
    """Modifie le volume sonore du système hôte (augmenter, diminuer ou couper le son).

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande de monter le son,
          de baisser le volume ou de mettre en sourdine (mute).
        - L'agent NE DOIT PAS appeler cet outil pour contrôler la lecture musicale (pause, lecture)
          (utiliser `control_music`).

    Args:
        action (str): Type de modification du volume.
            Valeurs obligatoires: 'volume_up' (monter le son), 'volume_down' (baisser le son),
            'mute' (couper / rétablir le son).
        pyautogui_module (Any): Module d'automatisation (injection de dépendance).

    Returns:
        Dict[str, Any]: Résultat standardisé de l'ajustement du volume.
    """
    action_name = "change_volume"
    mapping = {
        "volume_up": ("volumeup", "Volume augmente."),
        "volume_down": ("volumedown", "Volume diminue."),
        "mute": ("volumemute", "Son coupe."),
    }

    if action not in mapping:
        valid_actions = list(mapping.keys())
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Action de volume '{action}' invalide. Choix possibles : {valid_actions}.",
            data={"valid_actions": valid_actions},
            error=f"InvalidVolumeAction: {action}",
        )

    key, message = mapping[action]
    try:
        pyautogui_module.press(key)
        return format_action_result(
            action=action_name,
            success=True,
            message=message,
            data={"action": action, "key": key},
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Impossible de modifier le volume : {exc}",
            data=None,
            error=str(exc),
        )


def open_app(target: str) -> Dict[str, Any]:
    """Lance et ouvre une application installée sur le système d'exploitation Windows.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil dès que l'utilisateur demande d'ouvrir une application
          (ex: 'Ouvre le bloc-notes', 'Lance la calculatrice', 'Ouvre Chrome', 'Démarre VS Code').
        - L'agent NE DOIT PAS appeler cet outil pour naviguer vers une URL web (utiliser `open_site`
          ou `open_url_smart`) ni pour ouvrir un dossier du système de fichiers (utiliser `open_folder`).

    Args:
        target (str): Nom canonique de l'application configurée dans Jarvis (ex: 'notepad', 'calc', 'chrome').

    Returns:
        Dict[str, Any]: Résultat standardisé du lancement de l'application.
    """
    action_name = "open_app"
    normalized_target = target.strip().lower()
    command = config.APP_COMMANDS.get(normalized_target)

    if not command:
        return format_action_result(
            action=action_name,
            success=False,
            message="Je ne connais pas encore cette application.",
            data={"target": target, "available_apps": list(config.APP_COMMANDS.keys())},
            error=f"AppNotFound: '{target}'",
        )

    try:
        subprocess.Popen(command, shell=True)
        display_name = target.replace("_", " ")
        return format_action_result(
            action=action_name,
            success=True,
            message=f"J'ouvre {display_name}.",
            data={"target": target, "command": command},
            error=None,
        )
    except Exception as exc:
        display_name = target.replace("_", " ")
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Impossible d'ouvrir {display_name} : {exc}",
            data=None,
            error=str(exc),
        )


def close_app(target: str) -> Dict[str, Any]:
    """Ferme et met fin au processus d'une application Windows en cours d'exécution.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande explicitement de fermer,
          quitter ou terminer une application (ex: 'Ferme le bloc-notes', 'Quitte la calculatrice').
        - L'agent NE DOIT PAS appeler cet outil pour fermer un onglet de navigateur spécifique.

    Args:
        target (str): Nom canonique de l'application à fermer (ex: 'notepad', 'calc', 'chrome').

    Returns:
        Dict[str, Any]: Résultat standardisé de la fermeture de l'application.
    """
    action_name = "close_app"
    normalized_target = target.strip().lower()
    process_name = config.APP_PROCESS_NAMES.get(normalized_target)

    if not process_name:
        return format_action_result(
            action=action_name,
            success=False,
            message="Je ne sais pas encore fermer cette application.",
            data={"target": target, "closable_apps": list(config.APP_PROCESS_NAMES.keys())},
            error=f"ProcessNotConfigured: '{target}'",
        )

    try:
        result = subprocess.run(
            ["taskkill", "/f", "/im", process_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        display_name = target.replace("_", " ")
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Impossible de fermer {display_name} : {exc}",
            data=None,
            error=str(exc),
        )

    if result.returncode == 0:
        return format_action_result(
            action=action_name,
            success=True,
            message="Application fermee.",
            data={"target": target, "process_name": process_name},
            error=None,
        )

    display_name = target.replace("_", " ")
    return format_action_result(
        action=action_name,
        success=False,
        message=f"{display_name} n'est peut-etre pas lance.",
        data={"target": target, "process_name": process_name, "returncode": result.returncode},
        error=f"ProcessNotRunning: {process_name}",
    )


def open_folder(target: str) -> Dict[str, Any]:
    """Ouvre un dossier local préconfiguré dans l'Explorateur de fichiers Windows.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande d'ouvrir un dossier
          connu du système (ex: 'Ouvre le dossier Téléchargements', 'Ouvre mes Documents', 'Ouvre le dossier Projets').
        - L'agent NE DOIT PAS appeler cet outil pour lancer une application (.exe) (utiliser `open_app`).

    Args:
        target (str): Clé canonique du dossier configuré dans config.FOLDER_PATHS (ex: 'downloads', 'documents').

    Returns:
        Dict[str, Any]: Résultat standardisé de l'ouverture du dossier.
    """
    action_name = "open_folder"
    normalized_target = target.strip().lower()
    folder_path = config.FOLDER_PATHS.get(normalized_target)

    if not folder_path:
        return format_action_result(
            action=action_name,
            success=False,
            message="Je ne connais pas encore ce dossier.",
            data={"target": target, "known_folders": list(config.FOLDER_PATHS.keys())},
            error=f"FolderNotConfigured: '{target}'",
        )

    if not folder_path.exists():
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Le dossier {folder_path} est introuvable.",
            data={"target": target, "path": str(folder_path)},
            error=f"FolderNotFound: {folder_path}",
        )

    try:
        os.startfile(str(folder_path))
        return format_action_result(
            action=action_name,
            success=True,
            message=f"J'ouvre {folder_path.name}.",
            data={"target": target, "path": str(folder_path)},
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Impossible d'ouvrir {folder_path} : {exc}",
            data=None,
            error=str(exc),
        )
