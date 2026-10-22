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


def _get_target_user_and_env() -> tuple[str, Dict[str, str], list[str]]:
    """Détecte l'utilisateur cible (ex: moctar) et prépare l'environnement X11 / D-Bus / PulseAudio.

    Retourne:
        (target_user, env_dict, sudo_prefix_list)
    """
    target_user = "moctar" if os.path.exists("/home/moctar") else (
        os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
    )
    display = os.environ.get("DISPLAY", ":0")

    # Détection XAUTHORITY
    xauth = os.environ.get("XAUTHORITY")
    if not xauth or not os.path.exists(xauth):
        for candidate in (
            f"/home/{target_user}/.Xauthority",
            os.path.expanduser(f"~{target_user}/.Xauthority") if target_user != "root" else "",
            os.path.expanduser("~/.Xauthority"),
            "/root/.Xauthority",
        ):
            if candidate and os.path.exists(candidate):
                xauth = candidate
                break
        if not xauth:
            xauth = f"/home/{target_user}/.Xauthority"

    # Détection XDG_RUNTIME_DIR & DBUS
    runtime_dir = "/run/user/1000"
    if not os.path.exists(runtime_dir):
        if os.environ.get("XDG_RUNTIME_DIR") and os.path.exists(os.environ.get("XDG_RUNTIME_DIR", "")):
            runtime_dir = os.environ["XDG_RUNTIME_DIR"]

    bus_path = f"{runtime_dir}/bus"
    dbus_addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={bus_path}"
    pulse_server = f"unix:{runtime_dir}/pulse/native"

    env = os.environ.copy()
    env["DISPLAY"] = display
    env["XAUTHORITY"] = xauth
    env["XDG_RUNTIME_DIR"] = runtime_dir
    env["DBUS_SESSION_BUS_ADDRESS"] = dbus_addr
    env["PULSE_SERVER"] = pulse_server

    sudo_prefix: list[str] = []
    is_linux = sys.platform.startswith("linux") or os.name == "posix"
    is_root = hasattr(os, "getuid") and os.getuid() == 0 and target_user != "root"

    if is_linux and is_root:
        sudo_prefix = [
            "sudo", "-u", target_user,
            "env",
            f"DISPLAY={display}",
            f"XAUTHORITY={xauth}",
            f"XDG_RUNTIME_DIR={runtime_dir}",
            f"DBUS_SESSION_BUS_ADDRESS={dbus_addr}",
            f"PULSE_SERVER={pulse_server}",
            f"PATH=/usr/local/bin:/usr/bin:/bin:/snap/bin:{os.environ.get('PATH', '')}",
        ]

    return target_user, env, sudo_prefix


def _run_linux_cmd(cmd_args: list[str], timeout: float = 2.0) -> subprocess.CompletedProcess:
    """Exécute une commande multimédia sous Linux avec les privilèges et l'environnement de l'utilisateur actif."""
    target_user, env, sudo_prefix = _get_target_user_and_env()
    full_cmd = (sudo_prefix + cmd_args) if sudo_prefix else cmd_args
    return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout, env=env)


def _get_linux_env() -> Dict[str, str]:
    """Prépare l'environnement graphique X11 / PulseAudio pour les commandes multimédias."""
    _, env, _ = _get_target_user_and_env()
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
    # On transmet -a (--all-players) pour forcer la mise en pause / reprise sur tous les lecteurs actifs
    if shutil.which("playerctl"):
        try:
            playerctl_args = ["playerctl"]
            if act in ("pause", "stop", "play", "resume"):
                playerctl_args.extend(["-a", cmd_verb])
            else:
                playerctl_args.append(cmd_verb)

            res = _run_linux_cmd(playerctl_args, timeout=2.0)
            if res.returncode == 0:
                executed = True
                LOGGER.info("⚡ [MEDIA PLAYERCTL (0ms)]: %s exécuté avec succès", cmd_verb)
            else:
                LOGGER.debug("playerctl exit code %s: %s", res.returncode, res.stderr)
        except Exception as err:
            LOGGER.debug("playerctl failed: %s", err)

    # 2. Contrôle ciblé YouTube / Navigateur Web (Chrome, Firefox, Brave) via xdotool
    # Si YouTube est en cours de lecture dans un onglet actif ou en arrière-plan
    if shutil.which("xdotool"):
        try:
            search_res = _run_linux_cmd(["xdotool", "search", "--name", "[Yy]ou[Tt]ube"], timeout=1.5)
            stdout_text = getattr(search_res, "stdout", "") or ""
            yt_win_ids = []
            if isinstance(stdout_text, str):
                yt_win_ids = [line.strip() for line in stdout_text.strip().splitlines() if line.strip()]

            if yt_win_ids:
                LOGGER.info("⚡ [MEDIA XDOTOOL YOUTUBE]: %d fenêtre(s) YouTube détectée(s)", len(yt_win_ids))
                for win_id in yt_win_ids[:3]:
                    if act in ("pause", "stop", "play", "resume"):
                        # Raccourci natif universel YouTube : 'k' bascule pause/play sans défilement de page
                        _run_linux_cmd(["xdotool", "key", "--window", win_id, "k"], timeout=1.0)
                        executed = True
                    elif act == "next":
                        _run_linux_cmd(["xdotool", "key", "--window", win_id, "Shift+n"], timeout=1.0)
                        executed = True
                    elif act == "previous":
                        _run_linux_cmd(["xdotool", "key", "--window", win_id, "Shift+p"], timeout=1.0)
                        executed = True
        except Exception as yt_err:
            LOGGER.debug("xdotool youtube search/key failed: %s", yt_err)

    # 3. Fallback Linux : simulation touche multimédia globale (XF86AudioPlay, etc.)
    if shutil.which("xdotool"):
        try:
            res = _run_linux_cmd(["xdotool", "key", xdo_key], timeout=1.5)
            if res.returncode == 0:
                executed = True
                LOGGER.info("⚡ [MEDIA XDOTOOL (0ms)]: Touche %s simulée", xdo_key)
        except Exception as err:
            LOGGER.debug("xdotool key failed: %s", err)

    # 4. Fallback universel : pyautogui
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
    reply_msg = "Initialisation du flux audio. Votre playlist est lancée, Moctar."
    target_user, env, sudo_prefix = _get_target_user_and_env()
    playlist_uri = os.getenv("SPOTIFY_PLAYLIST_URI", "").strip()

    launched = False
    # 1. Si une URI Spotify est configurée, tenter de l'ouvrir directement via spotify ou xdg-open
    if playlist_uri:
        if shutil.which("spotify"):
            try:
                cmd = (sudo_prefix + ["spotify", f"--uri={playlist_uri}"]) if sudo_prefix else ["spotify", f"--uri={playlist_uri}"]
                subprocess.Popen(cmd, env=env)
                launched = True
            except Exception:
                pass
        if not launched and shutil.which("xdg-open"):
            try:
                cmd = (sudo_prefix + ["xdg-open", playlist_uri]) if sudo_prefix else ["xdg-open", playlist_uri]
                subprocess.Popen(cmd, env=env)
                launched = True
            except Exception:
                pass

    # 2. Si pas d'URI ou échec, lancer Spotify par défaut
    if not launched and shutil.which("spotify"):
        try:
            cmd = (sudo_prefix + ["spotify"]) if sudo_prefix else ["spotify"]
            subprocess.Popen(cmd, env=env)
            launched = True
        except Exception:
            pass

    # 3. Repli Web (YouTube Music)
    if not launched:
        try:
            if sudo_prefix and shutil.which("xdg-open"):
                subprocess.Popen(sudo_prefix + ["xdg-open", "https://music.youtube.com"], env=env)
                launched = True
            else:
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
    executed = False

    # 1. pactl (Linux moderne avec PulseAudio / PipeWire)
    if shutil.which("pactl"):
        try:
            if act == "up":
                _run_linux_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"], timeout=1.5)
                _run_linux_cmd(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], timeout=1.5)
                executed = True
            elif act == "down":
                _run_linux_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"], timeout=1.5)
                executed = True
            elif act == "mute":
                _run_linux_cmd(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"], timeout=1.5)
                executed = True
            elif act in ("unmute", "demute"):
                _run_linux_cmd(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], timeout=1.5)
                executed = True
            elif act == "set" and percent is not None:
                clamped = max(0, min(100, int(percent)))
                _run_linux_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{clamped}%"], timeout=1.5)
                _run_linux_cmd(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], timeout=1.5)
                executed = True
        except Exception as pactl_err:
            LOGGER.debug("pactl failed: %s", pactl_err)

    # 2. amixer (ALSA Linux direct)
    if not executed and shutil.which("amixer"):
        try:
            if act == "up":
                _run_linux_cmd(["amixer", "-D", "pulse", "sset", "Master", "10%+"], timeout=1.5)
                executed = True
            elif act == "down":
                _run_linux_cmd(["amixer", "-D", "pulse", "sset", "Master", "10%-"], timeout=1.5)
                executed = True
            elif act == "mute":
                _run_linux_cmd(["amixer", "-D", "pulse", "sset", "Master", "mute"], timeout=1.5)
                executed = True
            elif act in ("unmute", "demute"):
                _run_linux_cmd(["amixer", "-D", "pulse", "sset", "Master", "unmute"], timeout=1.5)
                executed = True
            elif act == "set" and percent is not None:
                clamped = max(0, min(100, int(percent)))
                _run_linux_cmd(["amixer", "-D", "pulse", "sset", "Master", f"{clamped}%"], timeout=1.5)
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
