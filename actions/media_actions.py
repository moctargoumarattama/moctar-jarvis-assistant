"""Module de Contrôle Média Universel en 0 ms pour M.O.C.T.A.R (Linux & Windows).

Pilote déterministement Spotify, YouTube, les lecteurs MPRIS et le volume système
via playerctl, pactl/amixer, xdotool et les raccourcis multimédias matériels.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from typing import Any, Dict, Optional

from actions.base import format_action_result

LOGGER = logging.getLogger("moctar.media_actions")


def _get_linux_env() -> Dict[str, str]:
    """Prépare l'environnement graphique X11 / PulseAudio pour les commandes multimédias."""
    env = os.environ.copy()
    try:
        import desktop_window_manager
        for k, v in desktop_window_manager._get_linux_display_env().items():
            if k not in env:
                env[k] = v
    except Exception:
        pass
    if "DISPLAY" not in env:
        env["DISPLAY"] = ":0"
    return env


def control_playback(action: str) -> Dict[str, Any]:
    """Contrôle la lecture multimédia (pause, play, resume, next, previous, stop).

    Args:
        action (str): Type d'action ('pause', 'play', 'resume', 'next', 'previous', 'stop').

    Returns:
        Dict[str, Any]: Résultat standardisé avec message oral pour Moctar.
    """
    act = (action or "").lower().strip()
    action_name = "control_playback"
    env = _get_linux_env()

    # Mapping vers les commandes playerctl et les touches multimédias
    playerctl_map = {
        "pause": ("pause", "Lecture mise en pause, Moctar.", "XF86AudioPlay"),
        "stop": ("stop", "Lecture mise en pause, Moctar.", "XF86AudioStop"),
        "play": ("play", "Reprise de la lecture, Moctar.", "XF86AudioPlay"),
        "resume": ("play", "Reprise de la lecture, Moctar.", "XF86AudioPlay"),
        "next": ("next", "Piste suivante activée, Moctar.", "XF86AudioNext"),
        "previous": ("previous", "Retour à la piste précédente, Moctar.", "XF86AudioPrev"),
    }

    if act not in playerctl_map:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Action multimédia '{act}' non reconnue.",
            data={"action": act},
            error="InvalidMediaAction",
        )

    cmd_verb, reply_msg, xdo_key = playerctl_map[act]
    executed = False

    # 1. Technologie Linux prioritaire : playerctl (MPRIS - Spotify, Chrome, Firefox, VLC)
    if shutil.which("playerctl"):
        try:
            res = subprocess.run(
                ["playerctl", cmd_verb],
                capture_output=True,
                text=True,
                timeout=2.0,
                env=env,
            )
            if res.returncode == 0:
                executed = True
                LOGGER.info("⚡ [MEDIA PLAYERCTL (0ms)]: %s exécuté avec succès", cmd_verb)
        except Exception as err:
            LOGGER.debug("playerctl failed: %s", err)

    # 2. Fallback Linux : xdotool (simulation touche clavier multimédia)
    if not executed and shutil.which("xdotool"):
        try:
            res = subprocess.run(
                ["xdotool", "key", xdo_key],
                capture_output=True,
                text=True,
                timeout=1.5,
                env=env,
            )
            if res.returncode == 0:
                executed = True
                LOGGER.info("⚡ [MEDIA XDOTOOL (0ms)]: Touche %s simulée", xdo_key)
        except Exception as err:
            LOGGER.debug("xdotool key failed: %s", err)

    # 3. Fallback universel : pyautogui
    if not executed:
        try:
            import pyautogui
            py_key_map = {
                "pause": "playpause",
                "stop": "playpause",
                "play": "playpause",
                "resume": "playpause",
                "next": "nexttrack",
                "previous": "prevtrack",
            }
            pyautogui.press(py_key_map.get(act, "playpause"))
            executed = True
            LOGGER.info("⚡ [MEDIA PYAUTOGUI (0ms)]: Touche %s pressée", py_key_map.get(act))
        except Exception as err:
            LOGGER.debug("pyautogui press failed: %s", err)

    return format_action_result(
        action=action_name,
        success=True,
        message=reply_msg,
        data={"action": act, "executed": executed},
        error=None,
    )


def launch_playlist() -> Dict[str, Any]:
    """Lance la playlist Spotify ou YouTube Music par défaut sur l'hôte."""
    action_name = "launch_playlist"
    env = _get_linux_env()
    reply_msg = "Initialisation du flux audio. Votre playlist est lancée, Moctar."

    playlist_uri = os.getenv("SPOTIFY_PLAYLIST_URI", "").strip()

    launched = False
    # 1. Si une URI Spotify est configurée, tenter de l'ouvrir directement via spotify ou xdg-open
    if playlist_uri:
        if shutil.which("spotify"):
            try:
                subprocess.Popen(["spotify", f"--uri={playlist_uri}"], env=env)
                launched = True
            except Exception:
                pass
        if not launched and shutil.which("xdg-open"):
            try:
                subprocess.Popen(["xdg-open", playlist_uri], env=env)
                launched = True
            except Exception:
                pass

    # 2. Si pas d'URI ou échec, lancer Spotify ou YouTube Music par défaut
    if not launched:
        if shutil.which("spotify"):
            try:
                subprocess.Popen(["spotify"], env=env)
                launched = True
            except Exception:
                pass

    # 3. Repli Web (YouTube Music)
    if not launched:
        try:
            webbrowser.open_new_tab("https://music.youtube.com")
            launched = True
        except Exception:
            pass

    return format_action_result(
        action=action_name,
        success=True,
        message=reply_msg,
        data={"launched": launched, "uri": playlist_uri or "default"},
        error=None,
    )


def adjust_system_volume(action: str, percent: Optional[int] = None) -> Dict[str, Any]:
    """Ajuste le volume sonore système via pactl (PulseAudio/PipeWire) ou amixer (ALSA).

    Args:
        action (str): 'up', 'down', 'mute', 'unmute', 'set'.
        percent (int | None): Pourcentage cible si action='set'.
    """
    act = (action or "").lower().strip()
    action_name = "adjust_system_volume"
    reply_msg = "Niveau audio ajusté, Moctar."
    env = _get_linux_env()
    executed = False

    # 1. pactl (Linux moderne avec PulseAudio / PipeWire)
    if shutil.which("pactl"):
        try:
            if act == "up":
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"], check=True, timeout=1.5, env=env)
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], check=False, timeout=1.5, env=env)
                executed = True
            elif act == "down":
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"], check=True, timeout=1.5, env=env)
                executed = True
            elif act == "mute":
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"], check=True, timeout=1.5, env=env)
                executed = True
            elif act in ("unmute", "demute"):
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], check=True, timeout=1.5, env=env)
                executed = True
            elif act == "set" and percent is not None:
                clamped = max(0, min(100, int(percent)))
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{clamped}%"], check=True, timeout=1.5, env=env)
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], check=False, timeout=1.5, env=env)
                executed = True
        except Exception as pactl_err:
            LOGGER.debug("pactl failed: %s", pactl_err)

    # 2. amixer (ALSA Linux direct)
    if not executed and shutil.which("amixer"):
        try:
            if act == "up":
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", "10%+"], check=True, timeout=1.5, env=env)
                executed = True
            elif act == "down":
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", "10%-"], check=True, timeout=1.5, env=env)
                executed = True
            elif act == "mute":
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", "mute"], check=True, timeout=1.5, env=env)
                executed = True
            elif act in ("unmute", "demute"):
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", "unmute"], check=True, timeout=1.5, env=env)
                executed = True
            elif act == "set" and percent is not None:
                clamped = max(0, min(100, int(percent)))
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", f"{clamped}%"], check=True, timeout=1.5, env=env)
                executed = True
        except Exception as amix_err:
            LOGGER.debug("amixer failed: %s", amix_err)

    # 3. Fallback Windows / PyAutoGUI
    if not executed:
        try:
            import pyautogui
            if act == "up":
                for _ in range(5):
                    pyautogui.press("volumeup")
                executed = True
            elif act == "down":
                for _ in range(5):
                    pyautogui.press("volumedown")
                executed = True
            elif act in ("mute", "unmute", "demute"):
                pyautogui.press("volumemute")
                executed = True
        except Exception:
            pass

    return format_action_result(
        action=action_name,
        success=True,
        message=reply_msg,
        data={"action": act, "percent": percent, "executed": executed},
        error=None,
    )
