"""Module d'Agencement Géométrique de l'Espace de Travail — « Mode Bureau » (Ubuntu X11 & Dell M4800).

Applique un découpage géométrique rationnel (2/3 - 1/3) :
- Haut-Gauche (1/3 L, 2/3 H) : Lecteur Média / Spotify
- Haut-Droite (2/3 L, 2/3 H) : Visual Studio Code / Terminal de supervision
- Bas (Plein L, 1/3 H)       : Navigateur Chrome / Dashboard NOC (http://localhost:5050)
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from actions.base import format_action_result

LOGGER = logging.getLogger("moctar.workspace_layout")

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080


def _get_linux_display_env() -> Dict[str, str]:
    """Prépare l'environnement graphique X11 pour communiquer avec le bureau sous Ubuntu."""
    env = os.environ.copy()
    target_user = "moctar" if os.path.exists("/home/moctar") else os.environ.get("USER", "root")
    try:
        import desktop_window_manager
        for k, v in desktop_window_manager._get_linux_display_env().items():
            if k not in env:
                env[k] = v
    except Exception:
        pass
    if "DISPLAY" not in env:
        env["DISPLAY"] = ":0"
    if "XAUTHORITY" not in env:
        for auth_candidate in (
            f"/home/{target_user}/.Xauthority",
            "/home/moctar/.Xauthority",
            os.path.expanduser("~/.Xauthority"),
            "/root/.Xauthority",
        ):
            if os.path.exists(auth_candidate):
                env["XAUTHORITY"] = auth_candidate
                break
    return env


def get_screen_resolution() -> Tuple[int, int]:
    """Détecte dynamiquement la résolution de l'écran principal via X11 (xdpyinfo, xrandr, wmctrl).

    Fallback gracieux sur (1920, 1080) sans jamais lever d'exception si le serveur X11 est indisponible.
    """
    if sys.platform.startswith("linux") or os.name == "posix":
        env = _get_linux_display_env()

        # Essai 1 : xdpyinfo
        if shutil.which("xdpyinfo"):
            try:
                proc = subprocess.run(["xdpyinfo"], capture_output=True, text=True, timeout=1.5, env=env)
                if proc.returncode == 0:
                    match = re.search(r"dimensions:\s+(\d+)x(\d+)\s+pixels", proc.stdout)
                    if match:
                        w, h = int(match.group(1)), int(match.group(2))
                        if w > 0 and h > 0:
                            return w, h
            except Exception as e:
                LOGGER.debug("xdpyinfo check failed: %s", e)

        # Essai 2 : xrandr
        if shutil.which("xrandr"):
            try:
                proc = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, timeout=1.5, env=env)
                if proc.returncode == 0:
                    match = re.search(r"\bconnected\s+(?:primary\s+)?(\d+)x(\d+)\+(\d+)\+(\d+)", proc.stdout)
                    if not match:
                        match = re.search(r"current\s+(\d+)\s*x\s*(\d+)", proc.stdout)
                    if match:
                        w, h = int(match.group(1)), int(match.group(2))
                        if w > 0 and h > 0:
                            return w, h
            except Exception as e:
                LOGGER.debug("xrandr check failed: %s", e)

        # Essai 3 : wmctrl -d
        if shutil.which("wmctrl"):
            try:
                proc = subprocess.run(["wmctrl", "-d"], capture_output=True, text=True, timeout=1.5, env=env)
                if proc.returncode == 0:
                    match = re.search(r"DG:\s*(\d+)x(\d+)", proc.stdout)
                    if match:
                        w, h = int(match.group(1)), int(match.group(2))
                        if w > 0 and h > 0:
                            return w, h
            except Exception as e:
                LOGGER.debug("wmctrl -d check failed: %s", e)

    # Windows fallback
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass

    return DEFAULT_WIDTH, DEFAULT_HEIGHT


def calculate_workspace_layout(
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Dict[str, Tuple[int, int, int, int]]:
    """Calcule le découpage géométrique de l'écran en 3 zones (2/3 - 1/3).

    Returns:
        Dict des coordonnées (X, Y, W, H) pour chaque zone :
            - 'top_left'  : (0, 0, W/3, 2H/3)
            - 'top_right' : (W/3, 0, 2W/3, 2H/3)
            - 'bottom'    : (0, 2H/3, W, H/3)
    """
    w = max(640, int(width))
    h = max(480, int(height))

    # Largeur : 1/3 pour la zone gauche, reste 2/3 pour la zone droite
    w_left = w // 3
    w_right = w - w_left

    # Hauteur : 2/3 pour la zone haute, reste 1/3 pour la zone basse
    h_top = (2 * h) // 3
    h_bottom = h - h_top

    return {
        "top_left": (0, 0, w_left, h_top),
        "top_right": (w_left, 0, w_right, h_top),
        "bottom": (0, h_top, w, h_bottom),
    }


def _list_wmctrl_windows(env: Dict[str, str]) -> List[Dict[str, Any]]:
    """Liste les fenêtres actives sous X11 via wmctrl."""
    windows: List[Dict[str, Any]] = []
    if not shutil.which("wmctrl"):
        return windows

    try:
        proc = subprocess.run(["wmctrl", "-l", "-p", "-x"], capture_output=True, text=True, timeout=1.5, env=env)
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines():
                parts = line.split(maxsplit=4)
                if len(parts) >= 5:
                    win_id = parts[0]
                    wm_class = parts[2].lower()
                    title = parts[4]
                    windows.append({
                        "id": win_id,
                        "class": wm_class,
                        "title": title,
                    })
    except Exception as e:
        LOGGER.debug("wmctrl -l failed: %s", e)
    return windows


def apply_window_geometry(win_id: str, x: int, y: int, w: int, h: int, env: Dict[str, str]) -> bool:
    """Retire les états maximisés et positionne une fenêtre au pixel près."""
    if not shutil.which("wmctrl"):
        return False
    try:
        # 1. Enlever l'état plein écran / maximisé
        subprocess.run(
            ["wmctrl", "-i", "-r", win_id, "-b", "remove,maximized_vert,maximized_horz"],
            check=False,
            timeout=1.0,
            env=env,
        )
        # 2. Positionner et dimensionner : 0,X,Y,W,H
        res = subprocess.run(
            ["wmctrl", "-i", "-r", win_id, "-e", f"0,{x},{y},{w},{h}"],
            check=False,
            timeout=1.0,
            env=env,
        )
        return res.returncode == 0
    except Exception as e:
        LOGGER.debug("wmctrl geometry failed for %s: %s", win_id, e)
        return False


def apply_workspace_layout() -> Dict[str, Any]:
    """Initialise le mode bureau géométrique 2/3 - 1/3 sur l'hôte.

    Lance si besoin les applications (VS Code, Chrome NOC, Spotify) et les agence
    selon la grille géométrique d'ingénierie.
    """
    action_name = "apply_workspace_layout"
    env = _get_linux_display_env()

    width, height = get_screen_resolution()
    layout = calculate_workspace_layout(width, height)
    reply_msg = "Agencement géométrique du poste de travail appliqué, Moctar."

    positioned_windows = []

    if sys.platform.startswith("linux") or os.name == "posix":
        # 1. Lancement non bloquant des applications indispensables si absentes
        open_wins = _list_wmctrl_windows(env)
        classes_str = " ".join(w["class"] for w in open_wins)

        # VS Code
        if "code" not in classes_str:
            try:
                from actions.system_actions import open_app
                open_app(target="vscode")
            except Exception:
                if shutil.which("code"):
                    try:
                        subprocess.Popen(["code", "/opt/jarvis"], env=env)
                    except Exception:
                        pass

        # Chrome Dashboard NOC
        if not any(c in classes_str for c in ("chrome", "chromium", "brave")):
            browser_bin = shutil.which("google-chrome") or shutil.which("chromium-browser") or shutil.which("brave-browser")
            if browser_bin:
                try:
                    if hasattr(os, "getuid") and os.getuid() == 0 and os.path.exists("/home/moctar"):
                        display = env.get("DISPLAY", ":0")
                        xauth = env.get("XAUTHORITY", "/home/moctar/.Xauthority")
                        bus = env.get("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
                        subprocess.Popen(
                            f"sudo -u moctar env DISPLAY={display} XAUTHORITY={xauth} XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS={bus} {browser_bin} --app=http://localhost:5050 >/dev/null 2>&1 &",
                            shell=True,
                            env=env,
                        )
                    else:
                        subprocess.Popen([browser_bin, "--app=http://localhost:5050"], env=env)
                except Exception:
                    pass

        # 2. Petite pause pour permettre l'apparition des fenêtres
        time.sleep(0.5)
        open_wins = _list_wmctrl_windows(env)

        # 3. Positionnement géométrique
        for win in open_wins:
            cls = win["class"]
            title = win["title"].lower()

            # Top-Right (2/3 L, 2/3 H) : VS Code ou Terminal de supervision
            if any(k in cls or k in title for k in ("code", "visual studio", "terminal", "kitty", "alacritty", "bash")):
                x, y, w, h = layout["top_right"]
                if apply_window_geometry(win["id"], x, y, w, h, env):
                    positioned_windows.append({"app": "vscode/terminal", "geometry": (x, y, w, h)})

            # Bottom (Plein L, 1/3 H) : Navigateur / Dashboard NOC
            elif any(k in cls or k in title for k in ("chrome", "chromium", "brave", "firefox", "localhost:5050", "m.o.c.t.a.r")):
                x, y, w, h = layout["bottom"]
                if apply_window_geometry(win["id"], x, y, w, h, env):
                    positioned_windows.append({"app": "browser/noc", "geometry": (x, y, w, h)})

            # Top-Left (1/3 L, 2/3 H) : Spotify / Musique / Autre
            elif any(k in cls or k in title for k in ("spotify", "vlc", "music")):
                x, y, w, h = layout["top_left"]
                if apply_window_geometry(win["id"], x, y, w, h, env):
                    positioned_windows.append({"app": "media/spotify", "geometry": (x, y, w, h)})

    return format_action_result(
        action=action_name,
        success=True,
        message=reply_msg,
        data={
            "screen": {"width": width, "height": height},
            "layout": layout,
            "positioned_count": len(positioned_windows),
        },
        error=None,
    )
