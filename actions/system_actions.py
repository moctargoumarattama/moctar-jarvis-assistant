import os
import subprocess
from datetime import datetime

import psutil
import pyautogui

import config


def get_time_response(now=None):
    current = now or datetime.now()
    return f"Il est {current.strftime('%H:%M')}."


def get_battery_response(battery_provider=psutil.sensors_battery):
    battery = battery_provider()
    if battery:
        return f"La batterie est a {battery.percent} pour cent."
    return "Je ne peux pas lire la batterie."


def capture_screenshot(pyautogui_module=pyautogui):
    config.ensure_runtime_directories()
    filename = config.SCREENSHOTS_DIR / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    pyautogui_module.screenshot().save(filename)
    return f"Capture enregistree : {filename.name}."


def change_volume(action, pyautogui_module=pyautogui):
    mapping = {
        "volume_up": ("volumeup", "Volume augmente."),
        "volume_down": ("volumedown", "Volume diminue."),
        "mute": ("volumemute", "Son coupe."),
    }
    key, message = mapping[action]
    pyautogui_module.press(key)
    return message


def open_app(target):
    command = config.APP_COMMANDS.get(target)
    if not command:
        return "Je ne connais pas encore cette application."

    try:
        subprocess.Popen(command, shell=True)
        return f"J'ouvre {target.replace('_', ' ')}."
    except Exception as exc:
        return f"Impossible d'ouvrir {target.replace('_', ' ')} : {exc}"


def close_app(target):
    process_name = config.APP_PROCESS_NAMES.get(target)
    if not process_name:
        return "Je ne sais pas encore fermer cette application."

    try:
        result = subprocess.run(
            ["taskkill", "/f", "/im", process_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return f"Impossible de fermer {target.replace('_', ' ')} : {exc}"

    if result.returncode == 0:
        return "Application fermee."
    return f"{target.replace('_', ' ')} n'est peut-etre pas lance."


def open_folder(target):
    folder_path = config.FOLDER_PATHS.get(target)
    if not folder_path:
        return "Je ne connais pas encore ce dossier."
    if not folder_path.exists():
        return f"Le dossier {folder_path} est introuvable."

    try:
        os.startfile(str(folder_path))
        return f"J'ouvre {folder_path.name}."
    except Exception as exc:
        return f"Impossible d'ouvrir {folder_path} : {exc}"
