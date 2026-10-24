"""Gestionnaire d'état en temps réel pour l'interface HUD 3D de M.O.C.T.A.R.

Maintient la synchronisation 0 ms entre les ordres vocaux autonomes,
le streaming SSE, les métriques matérielles Dell Precision M4800,
et les vues 3D du navigateur (Globe réaliste NASA, Réacteur Plasma & Cœur Quantique).
"""

from __future__ import annotations

import socket
import time
from datetime import datetime
from typing import Any, Dict, Optional

try:
    import psutil
except ImportError:
    psutil = None

# Nuancier d'Émotions & Sentiments pour l'Orbe 3D
MOOD_COLORS: Dict[str, str] = {
    "calm": "#00f3ff",       # Cyan technologique serein (mode nominal)
    "friendly": "#ffb700",   # Ambre solaire chaleureux & complice (amis, accueil)
    "success": "#00ff88",    # Vert émeraude victoire & fierté (tests validés, réussites)
    "quantum": "#d900ff",    # Magenta quantique & réflexion profonde (analyses IA)
    "night": "#3d64a8",      # Bleu nuit tamisé & reposant (après 23h, préservation des yeux)
    "caring": "#ff9955",     # Orange doux bienveillance & pause
    "alert": "#ff3366",      # Rouge corail vif alerte système / charge critique
}

# État singleton partagé en mémoire
_HUD_STATE: Dict[str, Any] = {
    "view": "orb",            # 'orb' (orbe / réacteur à arc plasma) | 'quantum' (noyau neuronal) | 'globe' (Terre NASA 3D)
    "locate": False,          # True pour activer la géolocalisation et le zoom radar
    "status": "idle",         # 'idle' | 'listening' | 'thinking' | 'speaking'
    "mood": "calm",           # 'calm' | 'friendly' | 'success' | 'quantum' | 'night' | 'caring' | 'alert'
    "mood_color": "#00f3ff",  # Couleur hexadécimale active de l'orbe
    "circadian_phase": "afternoon",  # 'morning' | 'afternoon' | 'evening' | 'night'
    "pulse_speed": 1.0,       # Multiplicateur de pulsation et vitesse des particules
    "user_transcript": "",    # Dernière phrase prononcée par Moctar
    "assistant_reply": "",    # Dernière réponse vocale formulée par M.O.C.T.A.R
    "target_coords": None,    # {'lat': float, 'lon': float} si position fixée
    "last_command": None,
    "last_updated": time.time(),
}


def get_circadian_phase(hour: Optional[int] = None) -> tuple[str, str, float]:
    """Détermine la phase circadienne, la couleur d'ambiance et la vitesse de pulsation."""
    if hour is None:
        try:
            hour = datetime.now().hour
        except Exception:
            hour = 14

    if 6 <= hour < 12:
        return "morning", "#00f3ff", 1.15     # Matin dynamique cyan
    elif 12 <= hour < 18:
        return "afternoon", "#00d0ff", 1.0    # Après-midi concentré
    elif 18 <= hour < 23:
        return "evening", "#ffb700", 0.9      # Soirée chaleureuse ambre
    else:
        return "night", "#3d64a8", 0.65       # Nuit tamisée bleue reposante


def deduce_mood_from_text(reply_text: str, user_text: str = "") -> str:
    """Déduit l'émotion et le sentiment appropriés pour l'orbe à partir des répliques."""
    combined = f"{user_text} {reply_text}".lower()

    # 1. Alerte ou anomalie critique
    if any(k in combined for k in ("alerte", "surchauffe", "critique", "anomalie", "saturation ram", "bloque plusieurs attaques", "rouge corail", "orbe rouge")):
        return "alert"

    # 2. Sommeil & Repos nocturne
    if any(k in combined for k in ("bonne nuit", "sommeil", "encore au travail", "dormir", "fatigue", "fatigué", "souffler un peu", "mode nuit", "bleu nuit")):
        return "night"

    # 3. Victoire, Succès & Célébration
    if any(k in combined for k in ("machine de guerre", "au vert", "succes", "succès", "parfaitement", "bien joue", "bien joué", "grosse session", "tests valides", "tests validés", "termine avec succes", "victoire", "vert émeraude", "vert emeraude", "orbe vert")):
        return "success"

    # 4. Amis, Accueil & Chaleur humaine
    if any(k in combined for k in ("ravi de faire ta connaissance", "ravi de te revoir", "enchante", "enchanté", "bienvenue", "sacree equipe", "sacrée équipe", "toujours la pour toi", "toujours là pour toi", "mon ami", "frerot", "frérot", "chaleureux", "chaleureuse", "ambre", "orbe en or", "orbe ambre")):
        return "friendly"

    # 5. Réflexion quantique & Analyse
    if any(k in combined for k in ("quantique", "analyse semantique", "analyse sémantique", "neural", "modele", "modèle", "algorithme", "reflexion", "réflexion", "magenta", "violet", "orbe violet")):
        return "quantum"

    # 6. Mode neutre / cyan
    if any(k in combined for k in ("cyan", "serein", "orbe par defaut", "orbe par défaut")):
        return "calm"

    # 7. Rythme circadien par défaut
    hour = datetime.now().hour
    if hour >= 23 or hour < 6:
        return "night"
    elif 18 <= hour < 23:
        return "friendly"
    return "calm"


def set_hud_mood(
    mood: str,
    color: Optional[str] = None,
    pulse_speed: Optional[float] = None,
) -> Dict[str, Any]:
    """Définit l'humeur émotionnelle et la couleur de l'orbe 3D."""
    m = (mood or "calm").lower().strip()
    if m in MOOD_COLORS:
        _HUD_STATE["mood"] = m
        _HUD_STATE["mood_color"] = color or MOOD_COLORS[m]
    else:
        _HUD_STATE["mood"] = "calm"
        _HUD_STATE["mood_color"] = color or MOOD_COLORS["calm"]

    if pulse_speed is not None:
        _HUD_STATE["pulse_speed"] = float(pulse_speed)
    elif m == "night":
        _HUD_STATE["pulse_speed"] = 0.65
    elif m in ("success", "friendly"):
        _HUD_STATE["pulse_speed"] = 1.3
    elif m == "alert":
        _HUD_STATE["pulse_speed"] = 1.8
    else:
        _HUD_STATE["pulse_speed"] = 1.0

    _HUD_STATE["last_updated"] = time.time()
    return get_hud_state()


def get_hud_state() -> Dict[str, Any]:
    """Retourne l'état HUD enrichi des métriques matérielles et du rythme circadien."""
    state = dict(_HUD_STATE)

    # Synchronisation de la phase circadienne
    phase, default_color, default_pulse = get_circadian_phase()
    state["circadian_phase"] = phase

    # Si l'état d'humeur est en phase neutre (calm), adapter automatiquement au rythme circadien
    if _HUD_STATE["mood"] == "calm":
        if phase == "night":
            state["mood"] = "night"
            state["mood_color"] = MOOD_COLORS["night"]
            state["pulse_speed"] = 0.65
        elif phase == "evening":
            state["mood"] = "friendly"
            state["mood_color"] = MOOD_COLORS["friendly"]
            state["pulse_speed"] = 0.9

    state["cpu_percent"] = 12.0
    state["ram_percent"] = 45.0
    state["ram_used_gb"] = 7.2
    state["ram_total_gb"] = 16.0
    state["hostname"] = "moctar-Precision-M4800"

    if psutil is not None:
        try:
            state["cpu_percent"] = round(float(psutil.cpu_percent(interval=None)), 1)
            mem = psutil.virtual_memory()
            state["ram_percent"] = round(float(mem.percent), 1)
            state["ram_used_gb"] = round(float(mem.used) / (1024**3), 1)
            state["ram_total_gb"] = round(float(mem.total) / (1024**3), 1)
            state["hostname"] = socket.gethostname()
        except Exception:
            pass

    # Télémétrie NeuroVision intégrée
    try:
        from vision_engine import VisionEngine
        v_engine = VisionEngine.get_instance()
        if v_engine.is_running():
            state["vision"] = v_engine.get_metrics()
            if _HUD_STATE["mood"] == "calm" and v_engine.fatigue_score >= 60:
                state["mood"] = "caring"
                state["mood_color"] = MOOD_COLORS["caring"]
                state["pulse_speed"] = 0.8
        else:
            state["vision"] = {"active": False}
    except Exception:
        state["vision"] = {"active": False}

    return state


def set_hud_view(
    view: str,
    locate: bool = False,
    coords: Optional[Dict[str, float]] = None,
    command: Optional[str] = None,
) -> Dict[str, Any]:
    """Bascule la vue 3D ('globe', 'plasma', 'quantum' ou 'orb') et gère la géolocalisation."""
    v = (view or "").lower().strip()
    if v in ("globe", "earth", "terre"):
        view_clean = "globe"
    elif v in ("quantum", "core", "neural", "quantique"):
        view_clean = "quantum"
    elif v in ("plasma", "reactor", "arc", "reacteur"):
        view_clean = "plasma"
    elif v in ("orb", "orbe"):
        view_clean = "orb"
    else:
        view_clean = "orb"

    _HUD_STATE["view"] = view_clean
    _HUD_STATE["locate"] = bool(locate)
    if coords is not None:
        _HUD_STATE["target_coords"] = coords
    if command:
        _HUD_STATE["last_command"] = command
    _HUD_STATE["last_updated"] = time.time()
    return get_hud_state()


def set_hud_voice_status(
    status: str,
    user_transcript: Optional[str] = None,
    assistant_reply: Optional[str] = None,
) -> Dict[str, Any]:
    """Met à jour l'état de l'orbe ('idle', 'listening', 'thinking', 'speaking') et les textes affichés."""
    if status in ("idle", "listening", "thinking", "speaking"):
        _HUD_STATE["status"] = status
    if user_transcript is not None:
        _HUD_STATE["user_transcript"] = user_transcript
    if assistant_reply is not None:
        _HUD_STATE["assistant_reply"] = assistant_reply
    _HUD_STATE["last_updated"] = time.time()
    return get_hud_state()


def reset_hud_state() -> Dict[str, Any]:
    """Réinitialise l'état HUD vers l'orbe neutre."""
    _HUD_STATE.update({
        "view": "orb",
        "locate": False,
        "status": "idle",
        "mood": "calm",
        "mood_color": MOOD_COLORS["calm"],
        "pulse_speed": 1.0,
        "user_transcript": "",
        "assistant_reply": "",
        "target_coords": None,
        "last_command": None,
        "last_updated": time.time(),
    })
    return get_hud_state()
