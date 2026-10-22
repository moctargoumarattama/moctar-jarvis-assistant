"""Connecteur Météo Haute Performance via Open-Meteo API.

Fournit des données météo en temps réel (température, ressenti, humidité,
vent, codes WMO, prévisions min/max) sans aucune clé API requise, avec
mise en cache instantanée (TTL 10 min) et coordonnées préchargées pour
une latence < 100 ms.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("jarvis.weather")

DEFAULT_CITY = "Niamey"

# Coordonnées pré-chargées pour résolution en 0 ms sans appel réseau de géocodage
PREWARMED_COORDINATES: dict[str, tuple[float, float, str, str]] = {
    "niamey": (13.51366, 2.1098, "Niamey", "Niger"),
    "agadez": (16.97333, 7.99111, "Agadez", "Niger"),
    "maradi": (13.5, 7.10174, "Maradi", "Niger"),
    "zinder": (13.80716, 8.9881, "Zinder", "Niger"),
    "tahoua": (14.88878, 5.26922, "Tahoua", "Niger"),
    "maroc": (34.02088, -6.84165, "Rabat", "Maroc"),
    "rabat": (34.02088, -6.84165, "Rabat", "Maroc"),
    "casablanca": (33.58831, -7.61138, "Casablanca", "Maroc"),
    "marrakech": (31.63416, -7.99994, "Marrakech", "Maroc"),
    "tanger": (35.75946, -5.83395, "Tanger", "Maroc"),
    "france": (48.85341, 2.3488, "Paris", "France"),
    "paris": (48.85341, 2.3488, "Paris", "France"),
    "lyon": (45.74846, 4.84671, "Lyon", "France"),
    "marseille": (43.29695, 5.38107, "Marseille", "France"),
    "bordeaux": (44.84044, -0.5805, "Bordeaux", "France"),
    "toulouse": (43.60426, 1.44367, "Toulouse", "France"),
    "senegal": (14.6937, -17.44406, "Dakar", "Sénégal"),
    "dakar": (14.6937, -17.44406, "Dakar", "Sénégal"),
    "mali": (12.65, -8.0, "Bamako", "Mali"),
    "bamako": (12.65, -8.0, "Bamako", "Mali"),
    "burkina": (12.36566, -1.53388, "Ouagadougou", "Burkina Faso"),
    "burkina faso": (12.36566, -1.53388, "Ouagadougou", "Burkina Faso"),
    "ouagadougou": (12.36566, -1.53388, "Ouagadougou", "Burkina Faso"),
    "cote d'ivoire": (5.30966, -4.01266, "Abidjan", "Côte d'Ivoire"),
    "cote divoire": (5.30966, -4.01266, "Abidjan", "Côte d'Ivoire"),
    "abidjan": (5.30966, -4.01266, "Abidjan", "Côte d'Ivoire"),
    "togo": (6.13748, 1.21227, "Lomé", "Togo"),
    "lome": (6.13748, 1.21227, "Lomé", "Togo"),
    "benin": (6.36536, 2.41833, "Cotonou", "Bénin"),
    "cotonou": (6.36536, 2.41833, "Cotonou", "Bénin"),
    "algerie": (36.7525, 3.04197, "Alger", "Algérie"),
    "alger": (36.7525, 3.04197, "Alger", "Algérie"),
    "tunisie": (36.81897, 10.16579, "Tunis", "Tunisie"),
    "tunis": (36.81897, 10.16579, "Tunis", "Tunisie"),
    "canada": (45.50884, -73.58781, "Montréal", "Canada"),
    "montreal": (45.50884, -73.58781, "Montréal", "Canada"),
    "belgique": (50.85045, 4.34878, "Bruxelles", "Belgique"),
    "bruxelles": (50.85045, 4.34878, "Bruxelles", "Belgique"),
    "suisse": (46.20222, 6.14569, "Genève", "Suisse"),
    "geneve": (46.20222, 6.14569, "Genève", "Suisse"),
}

WMO_DESCRIPTIONS: dict[int, str] = {
    0: "ciel totalement dégagé",
    1: "ensoleillé avec ciel clair",
    2: "partiellement nuageux",
    3: "ciel couvert",
    45: "brouillard",
    48: "brouillard givrant",
    51: "légère bruine",
    53: "bruine modérée",
    55: "forte bruine",
    56: "bruine verglaçante",
    57: "dense bruine verglaçante",
    61: "pluie légère",
    63: "pluie modérée",
    65: "forte pluie",
    66: "pluie verglaçante",
    67: "forte pluie verglaçante",
    71: "faibles chutes de neige",
    73: "chutes de neige modérées",
    75: "fortes chutes de neige",
    77: "grains de neige",
    80: "faibles averses de pluie",
    81: "averses de pluie",
    82: "violentes averses de pluie",
    85: "faibles averses de neige",
    86: "fortes averses de neige",
    95: "orage",
    96: "orage avec grêle",
    99: "orage violent avec forte grêle",
}

_CACHE_TTL_SECONDS = 600.0  # 10 minutes
_WEATHER_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _clean_city_key(city: str) -> str:
    cleaned = (city or "").strip().lower()
    cleaned = re.sub(r"[^\w\s-]", "", cleaned, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_city_from_query(query: str, default: str = DEFAULT_CITY) -> str:
    """Extrait intelligemment le nom de la ville dans une requête vocale ou textuelle."""
    text = (query or "").strip().strip(" ?.!:,;\"'")

    # 1. Vérification prioritaire des villes pré-chargées présentes dans le texte
    text_lower = text.lower()
    for city_key, coord in PREWARMED_COORDINATES.items():
        if re.search(rf"\b{re.escape(city_key)}\b", text_lower):
            return coord[2]

    # 2. Motifs d'extraction avec prépositions
    patterns = [
        r"\b(?:temps\s+fait-il\s+[aà]|temps\s+[aà]|pleut-il\s+[aà]|fait-il\s+beau\s+[aà])\s+([A-Za-zÀ-ÿ\- ]{2,30})\b",
        r"\b(?:m[ée]t[ée]o\s+(?:[aà]|pour|sur|de|vers|en|au|du)\s+|ville\s+de\s+)([A-Za-zÀ-ÿ\- ]{2,30})\b",
        r"\b(?:degr[ée]s?|temp[ée]rature|temps|climat)\s+(?:[aà]|au|en|pour|sur|du)\s+([A-Za-zÀ-ÿ\- ]{2,30})\b",
        r"\b(?:[aà]|au|en|du)\s+([A-Za-zÀ-ÿ\- ]{2,30})\s*[,.]?\s+(?:il\s+fait|quel\s+degr[ée]|combien\s+de\s+degr[ée]s?|quelle\s+temp[ée]rature|il\s+fait\s+chaud|il\s+fait\s+froid)\b",
        r"\b(?:m[ée]t[ée]o|[aà]|pour|sur|de|vers|au|du)\s+([A-Za-zÀ-ÿ\- ]{2,30})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().strip(" ?.!:,;\"'")
            # Supprimer d'éventuelles prépositions restantes en début de chaîne
            candidate = re.sub(r"^(?:[aà]|pour|sur|de|vers|en|du)\s+", "", candidate, flags=re.IGNORECASE).strip()
            # Nettoyer les faux positifs (adverbes, dates...)
            lowered = candidate.lower()
            stop_words = {"demain", "aujourd'hui", "aujourd hui", "ce matin", "ce soir", "actuellement", "dehors", "le monde"}
            if lowered in stop_words or any(lowered.startswith(s + " ") for s in stop_words):
                continue
            # Supprimer d'éventuels suffixes comme "aujourd'hui", "demain"
            for sw in ("aujourd'hui", "aujourd hui", "demain", "ce soir", "maintenant"):
                if candidate.lower().endswith(" " + sw):
                    candidate = candidate[: -len(sw) - 1].strip()
            if candidate and len(candidate) >= 2:
                return candidate.title()

    return default


def geocode_city(city_name: str) -> tuple[float, float, str, str] | None:
    """Résout le nom d'une ville en coordonnées géographiques (lat, lon, nom officiel, pays)."""
    norm = _clean_city_key(city_name)
    if norm in PREWARMED_COORDINATES:
        return PREWARMED_COORDINATES[norm]

    # Appel Open-Meteo Geocoding
    try:
        url = (
            f"https://geocoding-api.open-meteo.com/v1/search?"
            f"name={urllib.parse.quote_plus(city_name)}&count=1&language=fr&format=json"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "JarvisAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results")
            if results and len(results) > 0:
                loc = results[0]
                lat = float(loc["latitude"])
                lon = float(loc["longitude"])
                name = str(loc.get("name") or city_name)
                country = str(loc.get("country") or "")
                # Mise en cache en mémoire des coordonnées
                PREWARMED_COORDINATES[norm] = (lat, lon, name, country)
                return (lat, lon, name, country)
    except Exception as exc:
        logger.warning("Erreur lors du géocodage Open-Meteo pour '%s': %s", city_name, exc)

    return None


def get_weather_report(
    city: str | None = None,
    target_day: str = "today",
) -> dict[str, Any]:
    """Récupère la météo instantanée pour une ville donnée via Open-Meteo avec cache TTL.

    Returns:
        dict contenant:
            - success (bool)
            - city (str)
            - country (str)
            - temperature (float) en °C
            - apparent_temperature (float) en °C
            - humidity (int) en %
            - wind_speed (float) en km/h
            - weather_code (int) WMO
            - description (str) description claire en français
            - temp_max (float)
            - temp_min (float)
            - oral_text (str) phrase fluide calibrée pour le vocal JARVIS
    """
    target_city = (city or DEFAULT_CITY).strip()
    cache_key = f"{_clean_city_key(target_city)}:{target_day}"
    now_t = time.monotonic()

    cached = _WEATHER_CACHE.get(cache_key)
    if cached and (now_t - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    geo = geocode_city(target_city)
    if not geo:
        # Repli si ville introuvable
        return {
            "success": False,
            "city": target_city,
            "country": "",
            "oral_text": f"Je n'ai pas pu localiser la ville de {target_city} pour obtenir la météo, Moctar.",
        }

    lat, lon, official_name, country = geo

    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
            f"&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=auto"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "JarvisAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=4.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        current = data.get("current", {})
        daily = data.get("daily", {})

        temp = float(current.get("temperature_2m", 0.0))
        apparent_temp = float(current.get("apparent_temperature", temp))
        humidity = int(current.get("relative_humidity_2m", 0))
        wind_speed = float(current.get("wind_speed_10m", 0.0))
        wmo_code = int(current.get("weather_code", 0))
        desc = WMO_DESCRIPTIONS.get(wmo_code, "conditions calmes")

        temp_max_list = daily.get("temperature_2m_max", [])
        temp_min_list = daily.get("temperature_2m_min", [])

        temp_max = float(temp_max_list[0]) if temp_max_list else round(temp + 3.0, 1)
        temp_min = float(temp_min_list[0]) if temp_min_list else round(temp - 3.0, 1)

        location_display = official_name
        if country and country.lower() not in official_name.lower():
            location_display = f"{official_name} ({country})"

        if target_day in ("demain", "tomorrow") and len(temp_max_list) > 1:
            wmo_tomorrow = int(daily.get("weather_code", [0, 0])[1])
            desc_tomorrow = WMO_DESCRIPTIONS.get(wmo_tomorrow, "temps stable")
            t_max_tom = float(temp_max_list[1])
            t_min_tom = float(temp_min_list[1])
            oral_text = (
                f"Demain à {location_display}, prévoyez un {desc_tomorrow} avec des températures "
                f"entre {round(t_min_tom)}°C et {round(t_max_tom)}°C, Moctar."
            )
        else:
            oral_text = (
                f"À {location_display}, il fait {round(temp)}°C avec un {desc}. "
                f"Prévisions entre {round(temp_min)}°C et {round(temp_max)}°C, Moctar."
            )

        result: dict[str, Any] = {
            "success": True,
            "city": official_name,
            "country": country,
            "temperature": temp,
            "apparent_temperature": apparent_temp,
            "humidity": humidity,
            "wind_speed": wind_speed,
            "weather_code": wmo_code,
            "description": desc,
            "temp_max": temp_max,
            "temp_min": temp_min,
            "oral_text": oral_text,
        }

        _WEATHER_CACHE[cache_key] = (now_t, result)
        return result

    except Exception as exc:
        logger.warning("Erreur Open-Meteo pour '%s': %s", official_name, exc)
        return {
            "success": False,
            "city": official_name,
            "country": country,
            "oral_text": f"Impossible de contacter le service météo pour {official_name} actuellement, Moctar.",
        }
