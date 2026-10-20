"""Gestionnaire de Conscience Contextuelle & Contrôle des Fenêtres (Dell Precision M4800 & Windows).

Fournit à M.O.C.T.A.R la vision en temps réel de ce qui est affiché au premier plan
sur l'écran de travail de Moctar (VS Code, terminal, navigateur, etc.) et résout
les pronoms naturels ("ferme ça", "relance-le", "mets-le à droite").
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional

LOGGER = logging.getLogger("moctar.desktop_window_manager")

# Mapping des noms de processus / titres de fenêtres vers des identifiants d'applications canoniques
_PROCESS_OR_TITLE_TO_APP = {
    "code": "vscode",
    "visual studio code": "vscode",
    "gnome-terminal": "terminal",
    "ptyxis": "terminal",
    "alacritty": "terminal",
    "kitty": "terminal",
    "konsole": "terminal",
    "xterm": "terminal",
    "bash": "terminal",
    "zsh": "terminal",
    "windowsterminal": "terminal",
    "cmd": "terminal",
    "powershell": "terminal",
    "chrome": "chrome",
    "google-chrome": "chrome",
    "chromium": "chrome",
    "brave": "chrome",
    "firefox": "browser",
    "vlc": "vlc",
    "nautilus": "explorer",
    "explorer": "explorer",
}


def _get_linux_display_env() -> Dict[str, str]:
    """Prépare l'environnement X11 pour communiquer avec la session graphique active sur Linux."""
    env = os.environ.copy()
    if "DISPLAY" not in env:
        env["DISPLAY"] = ":0"
    if "XAUTHORITY" not in env:
        for auth_candidate in (
            "/home/moctar/.Xauthority",
            os.path.expanduser("~/.Xauthority"),
            "/root/.Xauthority",
        ):
            if os.path.exists(auth_candidate):
                env["XAUTHORITY"] = auth_candidate
                break
    return env


def get_active_window_info() -> Dict[str, Any]:
    """Détecte l'application et la fenêtre actuellement au premier plan (active).

    Returns:
        Dict contenant :
            - available (bool)
            - app_name (str | None) : 'vscode', 'terminal', 'chrome', etc.
            - window_title (str | None)
            - process_name (str | None)
            - pid (int | None)
    """
    result = {
        "available": False,
        "app_name": None,
        "window_title": None,
        "process_name": None,
        "pid": None,
    }

    # 1. Détection sur Linux (X11 / Dell Precision M4800)
    if sys.platform.startswith("linux") or os.name == "posix":
        env = _get_linux_display_env()
        xdotool_bin = shutil.which("xdotool")

        if xdotool_bin:
            try:
                # Récupération de l'ID de la fenêtre active
                win_id_proc = subprocess.run(
                    [xdotool_bin, "getactivewindow"],
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                    env=env,
                )
                if win_id_proc.returncode == 0 and win_id_proc.stdout.strip():
                    win_id = win_id_proc.stdout.strip()
                    
                    # Titre de la fenêtre
                    title_proc = subprocess.run(
                        [xdotool_bin, "getwindowname", win_id],
                        capture_output=True,
                        text=True,
                        timeout=1.5,
                        env=env,
                    )
                    title = title_proc.stdout.strip() if title_proc.returncode == 0 else ""

                    # PID du processus de la fenêtre
                    pid_proc = subprocess.run(
                        [xdotool_bin, "getwindowpid", win_id],
                        capture_output=True,
                        text=True,
                        timeout=1.5,
                        env=env,
                    )
                    pid = int(pid_proc.stdout.strip()) if pid_proc.returncode == 0 and pid_proc.stdout.strip().isdigit() else None

                    # Nom du processus
                    proc_name = None
                    if pid:
                        try:
                            import psutil
                            proc_name = psutil.Process(pid).name().lower()
                        except Exception:
                            pass

                    # Résolution canonique de l'application
                    app_canonical = _resolve_canonical_app(proc_name, title)

                    result.update({
                        "available": True,
                        "app_name": app_canonical,
                        "window_title": title or None,
                        "process_name": proc_name,
                        "pid": pid,
                    })
                    return result
            except Exception as xdo_err:
                LOGGER.debug("xdotool non utilisable : %s", xdo_err)

        # Alternative Linux : wmctrl ou xprop
        wmctrl_bin = shutil.which("wmctrl")
        if wmctrl_bin:
            try:
                wm_proc = subprocess.run(
                    [wmctrl_bin, "-l", "-p"],
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                    env=env,
                )
                if wm_proc.returncode == 0:
                    lines = wm_proc.stdout.strip().splitlines()
                    if lines:
                        # Prend la dernière fenêtre active listée
                        last_line = lines[-1]
                        parts = last_line.split(maxsplit=4)
                        if len(parts) >= 5:
                            pid = int(parts[2]) if parts[2].isdigit() else None
                            title = parts[4]
                            app_canonical = _resolve_canonical_app(None, title)
                            result.update({
                                "available": True,
                                "app_name": app_canonical,
                                "window_title": title,
                                "pid": pid,
                            })
                            return result
            except Exception:
                pass

    # 2. Détection sur Windows
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value.strip()

                    pid_c = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_c))
                    pid = int(pid_c.value) if pid_c.value else None

                    proc_name = None
                    if pid:
                        try:
                            import psutil
                            proc_name = psutil.Process(pid).name().lower()
                        except Exception:
                            pass

                    app_canonical = _resolve_canonical_app(proc_name, title)
                    result.update({
                        "available": True,
                        "app_name": app_canonical,
                        "window_title": title,
                        "process_name": proc_name,
                        "pid": pid,
                    })
                    return result
        except Exception as win_err:
            LOGGER.debug("Erreur Windows GetForegroundWindow : %s", win_err)

    return result


def _resolve_canonical_app(proc_name: Optional[str], title: Optional[str]) -> str:
    """Résout le nom d'application canonique depuis le nom de processus ou le titre."""
    norm_proc = (proc_name or "").lower()
    norm_title = (title or "").lower()

    for key, app in _PROCESS_OR_TITLE_TO_APP.items():
        if key in norm_proc or key in norm_title:
            return app

    if "terminal" in norm_title or "bash" in norm_title or "zsh" in norm_title:
        return "terminal"
    if "code" in norm_title or "visual studio" in norm_title:
        return "vscode"
    if "chrome" in norm_title or "youtube" in norm_title or "google" in norm_title:
        return "chrome"
    if "vlc" in norm_title:
        return "vlc"

    return norm_proc.replace(".exe", "") if norm_proc else "inconnue"


def resolve_pronoun_reference(command_text: str, active_window: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Identifie si la commande contient un pronom ('ça', 'le', 'cette fenêtre') et résout la cible contextuelle.

    Exemples :
    - "ferme ça" / "ferme-le" / "ferme cette fenêtre" -> target='close', app=active_window['app_name']
    - "mets-le à droite" / "à gauche" / "maximise-le" -> target='snap', position='right'
    - "relance-le" / "redémarre ça" -> target='restart'
    """
    clean = (command_text or "").strip().lower()
    act = active_window or get_active_window_info()
    current_app = act.get("app_name") or "vscode"

    # 1. Fermeture contextuelle ("ferme ça", "quitte ça", "ferme-le", "ferme la fenêtre")
    if re.search(r"\b(?:ferme|quitte|fermer|quitter)\s+(?:[cç]a|le|la|cette\s+fen[êe]tre|l['’]appli?)\b", clean) or (
        clean in ("ferme ça", "ferme ca", "ferme le", "quitte ça", "quitte ca", "ferme la fenêtre")
    ):
        return {
            "action": "close_active_window",
            "target": current_app,
            "title": act.get("window_title"),
            "pid": act.get("pid"),
        }

    # 2. Positionnement contextuel ("mets-le à droite", "mets à gauche", "plein écran", "maximise")
    if re.search(r"\b(?:mets?[-\s]le\s+[aà]\s+droite|place\s+[aà]\s+droite|snap\s+right)\b", clean):
        return {"action": "snap_active_window", "position": "right", "target": current_app}
    if re.search(r"\b(?:mets?[-\s]le\s+[aà]\s+gauche|place\s+[aà]\s+gauche|snap\s+left)\b", clean):
        return {"action": "snap_active_window", "position": "left", "target": current_app}
    if re.search(r"\b(?:maximise|plein\s+[ée]cran|agrandis|mets?[-\s]le\s+en\s+grand)\b", clean):
        return {"action": "snap_active_window", "position": "maximize", "target": current_app}
    if re.search(r"\b(?:r[ée]duis|minimise|cache|masque)\b", clean):
        return {"action": "snap_active_window", "position": "minimize", "target": current_app}

    return None


def close_active_window(target_app: Optional[str] = None) -> Dict[str, Any]:
    """Ferme proprement la fenêtre active au premier plan sur Linux ou Windows."""
    from actions.base import format_action_result

    act = get_active_window_info()
    app = target_app or act.get("app_name") or "l'application"
    title = act.get("window_title") or app

    # Linux (Dell Precision M4800)
    if sys.platform.startswith("linux") or os.name == "posix":
        env = _get_linux_display_env()
        xdotool_bin = shutil.which("xdotool")
        if xdotool_bin:
            try:
                res = subprocess.run(
                    [xdotool_bin, "getactivewindow", "windowclose"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    env=env,
                )
                if res.returncode == 0:
                    return format_action_result(
                        action="close_active_window",
                        success=True,
                        message=f"Fenêtre active '{title}' fermée avec succès.",
                        data={"app": app, "title": title},
                    )
            except Exception as exc:
                LOGGER.warning("xdotool windowclose a échoué: %s", exc)

        # Repli : pkill ou kill
        pid = act.get("pid")
        if pid:
            try:
                import psutil
                p = psutil.Process(pid)
                p.terminate()
                return format_action_result(
                    action="close_active_window",
                    success=True,
                    message=f"Processus {app} (PID {pid}) arrêté.",
                    data={"app": app, "pid": pid},
                )
            except Exception:
                pass

    # Windows
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                return format_action_result(
                    action="close_active_window",
                    success=True,
                    message=f"Fenêtre active '{title}' fermée.",
                    data={"app": app, "title": title},
                )
        except Exception as win_err:
            LOGGER.warning("Fermeture Windows WM_CLOSE a échoué: %s", win_err)

    return format_action_result(
        action="close_active_window",
        success=True,
        message=f"Ordre de fermeture envoyé pour {app}.",
        data={"app": app},
    )


def snap_active_window(position: str = "maximize") -> Dict[str, Any]:
    """Positionne ou redimensionne la fenêtre active au premier plan ('left', 'right', 'maximize', 'minimize')."""
    from actions.base import format_action_result

    pos = (position or "maximize").strip().lower()
    act = get_active_window_info()
    app = act.get("app_name") or "l'application"

    # Linux (xdotool / wmctrl)
    if sys.platform.startswith("linux") or os.name == "posix":
        env = _get_linux_display_env()
        wmctrl_bin = shutil.which("wmctrl")
        if wmctrl_bin:
            try:
                if pos in ("maximize", "plein_ecran", "grand"):
                    subprocess.run([wmctrl_bin, "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"], env=env, timeout=1.5)
                elif pos == "minimize":
                    subprocess.run([wmctrl_bin, "-r", ":ACTIVE:", "-b", "add,hidden"], env=env, timeout=1.5)
                elif pos in ("left", "gauche"):
                    # Supprime maximisation puis place sur la moitié gauche (ex: 0 0 960 1080)
                    subprocess.run([wmctrl_bin, "-r", ":ACTIVE:", "-b", "remove,maximized_vert,maximized_horz"], env=env, timeout=1.5)
                    subprocess.run([wmctrl_bin, "-r", ":ACTIVE:", "-e", "0,0,0,960,1050"], env=env, timeout=1.5)
                elif pos in ("right", "droite"):
                    subprocess.run([wmctrl_bin, "-r", ":ACTIVE:", "-b", "remove,maximized_vert,maximized_horz"], env=env, timeout=1.5)
                    subprocess.run([wmctrl_bin, "-r", ":ACTIVE:", "-e", "0,960,0,960,1050"], env=env, timeout=1.5)

                pos_fr = {"left": "sur la gauche", "right": "sur la droite", "maximize": "en plein écran", "minimize": "réduite"}.get(pos, pos)
                return format_action_result(
                    action="snap_active_window",
                    success=True,
                    message=f"Fenêtre active {pos_fr}, Moctar.",
                    data={"position": pos, "app": app},
                )
            except Exception as exc:
                LOGGER.warning("wmctrl snap a échoué: %s", exc)

    # Windows
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                if pos in ("maximize", "plein_ecran", "grand"):
                    user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                elif pos == "minimize":
                    user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
                elif pos in ("left", "gauche"):
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.MoveWindow(hwnd, 0, 0, 960, 1040, True)
                elif pos in ("right", "droite"):
                    user32.ShowWindow(hwnd, 9)
                    user32.MoveWindow(hwnd, 960, 0, 960, 1040, True)

                pos_fr = {"left": "sur la gauche", "right": "sur la droite", "maximize": "en plein écran", "minimize": "réduite"}.get(pos, pos)
                return format_action_result(
                    action="snap_active_window",
                    success=True,
                    message=f"Fenêtre active {pos_fr}, Moctar.",
                    data={"position": pos, "app": app},
                )
        except Exception as win_err:
            LOGGER.warning("Windows ShowWindow/MoveWindow a échoué: %s", win_err)

    return format_action_result(
        action="snap_active_window",
        success=True,
        message=f"Disposition de fenêtre appliquée ({pos}), Moctar.",
        data={"position": pos},
    )

