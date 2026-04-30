import re
import unicodedata

import config
from rapidfuzz import fuzz, process


NUMBER_PATTERN = r"(\d+(?:[.,]\d+)?)"
DEVICE_WORDS = (
    "appareil",
    "appareils",
    "lampe",
    "lampes",
    "ampoule",
    "ampoules",
    "machine",
    "machines",
    "equipement",
    "equipements",
    "charge",
    "charges",
    "prise",
    "prises",
)


def normalize(text):
    if not text:
        return ""

    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")

    replacements = {
        "stp": "",
        "svp": "",
        "s il te plait": "",
        "s'il te plait": "",
        "jarvis": "",
        "active": "ouvre",
        "activate": "ouvre",
        "lance": "ouvre",
        "demarre": "ouvre",
        "bloc notes": "bloc-notes",
        "telechargement": "telechargements",
    }

    for old, new in replacements.items():
        pattern = rf"\b{re.escape(old)}\b"
        text = re.sub(pattern, new, text)

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def find_alias(text, aliases, threshold=75):
    choices = []
    mapping = {}

    for key, alias_list in aliases.items():
        for alias in alias_list:
            choices.append(alias)
            mapping[alias] = key

    match = process.extractOne(text, choices, scorer=fuzz.partial_ratio)
    if not match:
        return None, 0

    alias, score, _ = match
    if score < threshold:
        return None, score
    return mapping[alias], score


def extract_after_prefix(text, prefixes):
    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return ""


def parse_reminder_slots(text):
    body = extract_after_prefix(text, ["rappelle moi ", "rappel moi "]).strip()
    if not body:
        return {"message": "", "when_text": ""}

    match = re.match(
        r"(?P<message>.+?)\s+(?:a|pour|demain|le)\s+(?P<when>(?:demain\s+)?[\w:\-/ ]+)$",
        body,
    )
    if match:
        return {
            "message": match.group("message").strip(),
            "when_text": match.group("when").strip(),
        }

    return {"message": body, "when_text": ""}


def remove_music_words(text):
    words = [
        "mets",
        "joue",
        "ouvre",
        "cherche",
        "musique",
        "music",
        "chanson",
        "son",
        "du",
        "de la",
        "des",
        "un",
        "une",
        "le",
        "la",
        "les",
        "moi",
        "pour moi",
    ]

    query = text
    for word in words:
        query = re.sub(rf"\b{re.escape(word)}\b", " ", query)

    return re.sub(r"\s+", " ", query).strip()


def parse_number(raw_value):
    try:
        return float(str(raw_value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def extract_number(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return parse_number(match.group(1))
    return None


def normalize_ratio(value):
    if value is None:
        return None
    if value > 1:
        return value / 100
    return value


def compact_slots(**values):
    return {key: value for key, value in values.items() if value is not None}


def parse_energy_consumption_slots(text):
    quantity = extract_number(
        text,
        [rf"{NUMBER_PATTERN}\s*(?:{'|'.join(DEVICE_WORDS)})\b"],
    )
    power_watts = extract_number(text, [rf"{NUMBER_PATTERN}\s*(?:w|watt|watts)\b"])
    duration_hours = extract_number(
        text,
        [
            rf"pendant\s*{NUMBER_PATTERN}\s*(?:h|heure|heures)\b",
            rf"{NUMBER_PATTERN}\s*(?:h|heure|heures)\b",
        ],
    )

    if quantity is None and power_watts is not None and duration_hours is not None:
        quantity = 1.0

    return compact_slots(
        quantity=quantity,
        power_watts=power_watts,
        duration_hours=duration_hours,
    )


def parse_energy_cost_slots(text):
    energy_kwh = extract_number(text, [rf"{NUMBER_PATTERN}\s*kwh\b"])
    price_per_kwh = extract_number(
        text,
        [
            rf"(?:a|au prix de|prix)\s*{NUMBER_PATTERN}\s*(?:dh|mad|dirham|dirhams|euro|euros|fcfa)?\b",
            rf"{NUMBER_PATTERN}\s*(?:dh|mad|dirham|dirhams|euro|euros|fcfa)\b",
        ],
    )
    return compact_slots(energy_kwh=energy_kwh, price_per_kwh=price_per_kwh)


def parse_solar_sizing_slots(text):
    daily_energy_kwh = extract_number(text, [rf"{NUMBER_PATTERN}\s*kwh\b"])
    sun_hours = extract_number(
        text,
        [
            rf"{NUMBER_PATTERN}\s*(?:h|heure|heures)\s*de soleil\b",
            rf"{NUMBER_PATTERN}\s*(?:sun hours|heures soleil)\b",
        ],
    )
    system_efficiency = normalize_ratio(
        extract_number(
            text,
            [
                rf"rendement\s*{NUMBER_PATTERN}\b",
                rf"efficacite\s*{NUMBER_PATTERN}\b",
                rf"efficiency\s*{NUMBER_PATTERN}\b",
            ],
        )
    )
    return compact_slots(
        daily_energy_kwh=daily_energy_kwh,
        sun_hours=sun_hours,
        system_efficiency=system_efficiency,
    )


def parse_battery_sizing_slots(text):
    energy_kwh = extract_number(text, [rf"{NUMBER_PATTERN}\s*kwh\b"])
    system_voltage = extract_number(text, [rf"{NUMBER_PATTERN}\s*(?:v|volt|volts)\b"])
    depth_of_discharge = normalize_ratio(
        extract_number(
            text,
            [
                rf"dod\s*{NUMBER_PATTERN}\b",
                rf"profondeur de decharge\s*{NUMBER_PATTERN}\b",
                rf"decharge\s*{NUMBER_PATTERN}\b",
            ],
        )
    )
    autonomy_days = extract_number(
        text,
        [
            rf"{NUMBER_PATTERN}\s*(?:jour|jours)\s*(?:d autonomie|autonomie)?\b",
            rf"autonomie\s*{NUMBER_PATTERN}\s*(?:jour|jours)?\b",
        ],
    )
    return compact_slots(
        energy_kwh=energy_kwh,
        system_voltage=system_voltage,
        depth_of_discharge=depth_of_discharge,
        autonomy_days=autonomy_days,
    )


def parse_inverter_sizing_slots(text):
    total_power_watts = extract_number(text, [rf"{NUMBER_PATTERN}\s*(?:w|watt|watts)\b"])
    safety_margin = normalize_ratio(
        extract_number(
            text,
            [
                rf"marge(?: de securite)?\s*{NUMBER_PATTERN}\b",
                rf"safety margin\s*{NUMBER_PATTERN}\b",
            ],
        )
    )
    return compact_slots(
        total_power_watts=total_power_watts,
        safety_margin=safety_margin,
    )


def parse_energy_audit_target(text):
    match = re.search(r"audit energetique(?:\s+(?:pour|de))?\s*(?P<target>.+)?$", text)
    if not match:
        return ""

    target = (match.group("target") or "").strip()
    target = re.sub(r"^(?:template|modele|fiche)\s+", "", target).strip()
    return target


def extract_relay_id(text):
    relay_value = extract_number(text, [rf"relais\s*{NUMBER_PATTERN}\b", rf"relay\s*{NUMBER_PATTERN}\b"])
    if relay_value is None:
        return 1
    return int(relay_value)


def detect_intent(raw_text):
    text = normalize(raw_text)

    if not text:
        return {"intent": "empty", "target": "", "confidence": 0, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["stop", "quitte", "arrete", "ferme moctar", "stop listening"]):
        return {"intent": "stop", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["consommation", "consommation energie", "energie consommee"]):
        return {
            "intent": "energy_consumption",
            "target": "",
            "confidence": 95,
            "raw": raw_text,
            "slots": parse_energy_consumption_slots(text),
        }

    if "cout" in text and "kwh" in text:
        return {
            "intent": "energy_cost",
            "target": "",
            "confidence": 95,
            "raw": raw_text,
            "slots": parse_energy_cost_slots(text),
        }

    if any(token in text for token in ["dimensionne solaire", "dimensionnement solaire", "taille solaire"]):
        return {
            "intent": "solar_sizing",
            "target": "",
            "confidence": 95,
            "raw": raw_text,
            "slots": parse_solar_sizing_slots(text),
        }

    if any(token in text for token in ["dimensionne batterie", "dimensionnement batterie", "taille batterie"]):
        return {
            "intent": "battery_sizing",
            "target": "",
            "confidence": 95,
            "raw": raw_text,
            "slots": parse_battery_sizing_slots(text),
        }

    if any(token in text for token in ["dimensionne onduleur", "dimensionnement onduleur", "taille onduleur"]):
        return {
            "intent": "inverter_sizing",
            "target": "",
            "confidence": 95,
            "raw": raw_text,
            "slots": parse_inverter_sizing_slots(text),
        }

    if "audit energetique" in text and any(token in text for token in ["template", "modele", "fiche"]):
        return {
            "intent": "energy_audit_template",
            "target": parse_energy_audit_target(text),
            "confidence": 95,
            "raw": raw_text,
            "slots": {},
        }

    if "relais" in text and any(token in text for token in ["allume", "active", "ouvre", "demarre"]):
        return {
            "intent": "iot_relay_on",
            "target": "",
            "confidence": 95,
            "raw": raw_text,
            "slots": {"relay_id": extract_relay_id(text)},
        }

    if "relais" in text and any(token in text for token in ["eteins", "coupe", "desactive", "ferme"]):
        return {
            "intent": "iot_relay_off",
            "target": "",
            "confidence": 95,
            "raw": raw_text,
            "slots": {"relay_id": extract_relay_id(text)},
        }

    if "temperature" in text and any(token in text for token in ["iot", "capteur", "sonde"]):
        return {"intent": "iot_temperature", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["iot", "capteur", "capteurs", "sonde", "relais"]) and any(
        token in text for token in ["etat", "status", "statut"]
    ):
        return {"intent": "iot_status", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["heure", "quelle heure", "donne l heure"]):
        return {"intent": "time", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["batterie", "niveau batterie", "battery"]):
        return {"intent": "battery", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["capture", "screenshot", "capture ecran"]):
        return {"intent": "screenshot", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["augmente volume", "monte volume", "volume up"]):
        return {"intent": "volume_up", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["baisse volume", "diminue volume", "volume down"]):
        return {"intent": "volume_down", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["coupe le son", "coupe son", "mute", "silence"]):
        return {"intent": "mute", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if "cherche sur youtube" in text or "recherche sur youtube" in text:
        query = text.replace("cherche sur youtube", "").replace("recherche sur youtube", "").strip()
        return {"intent": "search_youtube", "target": query, "confidence": 95, "raw": raw_text, "slots": {}}

    if "cherche sur google" in text or "recherche sur google" in text:
        query = text.replace("cherche sur google", "").replace("recherche sur google", "").strip()
        return {"intent": "search_google", "target": query, "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["prepare git status", "prepare status git", "git status"]):
        return {"intent": "git_status", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["prepare git pull", "git pull"]):
        return {"intent": "git_pull", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["prepare git log", "git log"]):
        return {"intent": "git_log", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["prepare commit", "preparer commit", "prepare un commit"]):
        return {"intent": "git_commit_prepare", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["prepare push", "preparer push", "prepare un push"]):
        return {"intent": "git_push_prepare", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if text.startswith("cree une note ") or text.startswith("creer une note "):
        target = extract_after_prefix(text, ["cree une note ", "creer une note "])
        return {"intent": "create_note", "target": target, "confidence": 95, "raw": raw_text, "slots": {}}

    if text.startswith("ajoute todo ") or text.startswith("ajoute une todo "):
        target = extract_after_prefix(text, ["ajoute todo ", "ajoute une todo "])
        return {"intent": "add_todo", "target": target, "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["liste mes todos", "liste les todos", "mes todos", "liste todo"]):
        return {"intent": "list_todos", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if text.startswith("resume le fichier ") or text.startswith("resume fichier "):
        target = extract_after_prefix(text, ["resume le fichier ", "resume fichier "])
        return {"intent": "summarize_file", "target": target, "confidence": 95, "raw": raw_text, "slots": {}}

    if text.startswith("rappelle moi ") or text.startswith("rappel moi "):
        slots = parse_reminder_slots(text)
        return {
            "intent": "remind_me",
            "target": slots.get("message", ""),
            "confidence": 90,
            "raw": raw_text,
            "slots": slots,
        }

    if text.startswith("cherche dans mes notes ") or text.startswith("cherche dans mes todos "):
        target = text.split(" ", 4)[-1].strip()
        return {"intent": "search_personal", "target": target, "confidence": 90, "raw": raw_text, "slots": {}}

    if "playlist" in text:
        target = remove_music_words(text)
        if not target:
            target = config.DEFAULT_SITE_SEARCH_QUERY
        if "playlist" not in target:
            target = "playlist " + target
        return {"intent": "playlist", "target": target, "confidence": 95, "raw": raw_text, "slots": {}}

    if "serveur" in text:
        project_key, score = find_alias(text, config.PROJECT_ALIASES)
        if project_key:
            return {"intent": "launch_project_server", "target": project_key, "confidence": score, "raw": raw_text, "slots": {}}
        if "flask" in text:
            return {"intent": "launch_project_server", "target": "flask", "confidence": 90, "raw": raw_text, "slots": {}}
        if "fastapi" in text:
            return {"intent": "launch_project_server", "target": "fastapi", "confidence": 90, "raw": raw_text, "slots": {}}

    if "projet" in text:
        project_key, score = find_alias(text, config.PROJECT_ALIASES)
        if project_key:
            slots = {}
            if "vscode" in text or "visual studio" in text:
                slots["editor"] = "vscode"
            return {"intent": "open_project", "target": project_key, "confidence": score, "raw": raw_text, "slots": slots}

    music_markers = ["musique", "music", "chanson", "son", "mets", "joue"]
    if any(marker in text for marker in music_markers):
        target = remove_music_words(text)
        return {"intent": "play_music", "target": target, "confidence": 90, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["ouvre", "ouvrir", "active"]):
        folder_key, folder_score = find_alias(text, config.FOLDER_ALIASES)
        if folder_key:
            return {"intent": "open_folder", "target": folder_key, "confidence": folder_score, "raw": raw_text, "slots": {}}

        site_key, site_score = find_alias(text, config.SITE_ALIASES)
        if site_key:
            return {"intent": "open_site", "target": site_key, "confidence": site_score, "raw": raw_text, "slots": {}}

        app_key, app_score = find_alias(text, config.APP_ALIASES)
        if app_key:
            return {"intent": "open_app", "target": app_key, "confidence": app_score, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["ferme", "close"]):
        app_key, score = find_alias(text, config.APP_ALIASES)
        if app_key:
            return {"intent": "close_app", "target": app_key, "confidence": score, "raw": raw_text, "slots": {}}

    app_key, app_score = find_alias(text, config.APP_ALIASES, threshold=90)
    if app_key:
        return {"intent": "open_app", "target": app_key, "confidence": app_score, "raw": raw_text, "slots": {}}

    site_key, site_score = find_alias(text, config.SITE_ALIASES, threshold=90)
    if site_key:
        return {"intent": "open_site", "target": site_key, "confidence": site_score, "raw": raw_text, "slots": {}}

    return {"intent": "chat_fallback", "target": text, "confidence": 50, "raw": raw_text, "slots": {}}
