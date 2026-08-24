import re
import unicodedata

import config
try:
    from rapidfuzz import fuzz, process
except Exception:
    class _FallbackFuzz:
        @staticmethod
        def partial_ratio(left, right):
            left = left or ""
            right = right or ""
            if not left or not right:
                return 0
            left_tokens = set(left.split())
            right_tokens = set(right.split())
            overlap = len(left_tokens.intersection(right_tokens))
            score = int(100 * overlap / max(len(right_tokens), 1))
            return min(100, max(score, 0))

    class _FallbackProcess:
        @staticmethod
        def extractOne(text, choices, scorer=None):
            scorer = scorer or _FallbackFuzz.partial_ratio
            ranked = [(choice, scorer(text, choice), index) for index, choice in enumerate(choices)]
            if not ranked:
                return None
            ranked.sort(key=lambda item: item[1], reverse=True)
            return ranked[0]

    fuzz = _FallbackFuzz()
    process = _FallbackProcess()


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

REMINDER_PREFIXES = (
    "rappelle moi ",
    "rappelle-moi ",
    "rappel moi ",
    "rappel-moi ",
    "programme moi ",
    "programme-moi ",
    "planifie moi ",
    "planifie-moi ",
)

ALARM_PREFIXES = (
    "mets moi ",
    "mets-moi ",
    "met moi ",
    "met-moi ",
)

REMINDER_NOUNS = ("alarme", "rappel", "alerte", "reveil")


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
        "bloc notes": "bloc-notes",
        "telechargement": "telechargements",
    }

    for old, new in replacements.items():
        pattern = rf"\b{re.escape(old)}\b"
        text = re.sub(pattern, new, text)

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def find_alias(text, aliases, threshold=75, exact=False):
    """Return an explicit alias match, never a loose partial fuzzy match.

    Matching an alias such as ``code`` anywhere in a sentence used to open VS
    Code for phrases like "je fais du code".  For voice commands, a missed
    command is preferable to launching the wrong application.
    """
    normalized = normalize(text)
    matches = []
    for key, alias_list in aliases.items():
        for alias in alias_list:
            candidate = normalize(alias)
            matched = normalized == candidate if exact else bool(
                re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", normalized)
            )
            if candidate and matched:
                matches.append((len(candidate), key))

    if not matches:
        return None, 0

    # Prefer the most specific alias: "youtube music" before "youtube".
    matches.sort(reverse=True)
    return matches[0][1], 100


def extract_after_prefix(text, prefixes):
    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return ""


def _contains_time_hint(text):
    return bool(
        re.search(
            r"\b(?:a|pour|demain|le)\b|\b\d{1,2}(?:[:h]\d{0,2})?\b",
            text,
        )
    )


def looks_like_reminder_request(text):
    if not text:
        return False

    if text.startswith(REMINDER_PREFIXES):
        return True

    has_reminder_noun = any(noun in text for noun in REMINDER_NOUNS)
    if text.startswith(ALARM_PREFIXES):
        return has_reminder_noun

    return has_reminder_noun and _contains_time_hint(text)


def parse_reminder_slots(text):
    body = (text or "").strip()
    if not body:
        return {"message": "", "when_text": ""}

    stripped_body = extract_after_prefix(body, REMINDER_PREFIXES + ALARM_PREFIXES)
    if any(body.startswith(prefix) for prefix in REMINDER_PREFIXES + ALARM_PREFIXES):
        body = stripped_body

    default_message = ""
    noun_match = re.match(
        r"^(?:l[' ]?|la\s+|le\s+|les\s+|un\s+|une\s+)?(?P<noun>alarme|rappel|alerte|reveil)\b(?:\s+de\s+)?",
        body,
    )
    if noun_match:
        noun = noun_match.group("noun")
        default_message = noun if noun != "rappel" else ""
        body = body[noun_match.end():].strip()

    if not body:
        return {"message": default_message, "when_text": ""}

    match = re.match(
        r"(?P<message>.+?)\s+(?:a|pour|demain|le)\s+(?P<when>(?:demain\s+)?[\w:\-/ ]+)$",
        body,
    )
    if match:
        message = match.group("message").strip()
        return {
            "message": message or default_message,
            "when_text": match.group("when").strip(),
        }

    when_match = re.match(
        r"^(?:a|pour|demain|le)\s+(?P<when>(?:demain\s+)?[\w:\-/ ]+)$",
        body,
    )
    if when_match:
        return {
            "message": default_message,
            "when_text": when_match.group("when").strip(),
        }

    return {"message": body or default_message, "when_text": ""}


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


def extract_music_query(text):
    """Keep the title/artist intact while removing only a leading request."""
    match = re.match(r"^(?:mets|met|joue|ecoute|lance)(?:[- ]?moi)?\s+(.+)$", text)
    body = match.group(1).strip() if match else text.strip()
    body = re.sub(r"^(?:moi\s+)?(?:une?\s+)?(?:de la\s+|du\s+|des\s+)?", "", body)
    body = re.sub(r"^(?:musique|music|chanson|son)\b", "", body).strip()
    body = re.sub(r"^(?:de\s+|du\s+|des\s+|la\s+|le\s+|les\s+)", "", body).strip()
    return "" if body in {"", "musique", "music", "chanson", "son"} else body


def extract_action_target(text, verbs):
    match = re.match(rf"^(?:{'|'.join(verbs)})\s+(.+)$", text)
    if not match:
        return text
    return re.sub(
        r"^(?:le |la |les |un |une |mon |ma |l |application |app |logiciel |programme |site |navigateur )+",
        "",
        match.group(1).strip(),
    )


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


def parse_focus_target(text):
    project_key, _score = find_alias(text, config.PROJECT_ALIASES)
    if project_key:
        return project_key
    match = re.search(r"(?:focus projet|mode focus(?: projet)?)\s+(?:sur\s+)?(.+)$", text)
    if not match:
        return ""
    return (match.group(1) or "").strip()


def detect_intent(raw_text):
    text = normalize(raw_text)

    if not text:
        return {"intent": "empty", "target": "", "confidence": 0, "raw": raw_text, "slots": {}}

    if text in {"oui", "ok", "vas y", "go", "confirme", "confirmer"}:
        return {"intent": "confirm_yes", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if text in {"non", "annule", "annuler", "laisse tomber"}:
        return {"intent": "confirm_no", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if looks_like_reminder_request(text):
        slots = parse_reminder_slots(text)
        return {
            "intent": "remind_me",
            "target": slots.get("message", ""),
            "confidence": 92,
            "raw": raw_text,
            "slots": slots,
        }

    if text.startswith(("comment ", "pourquoi ", "explique ", "explique-moi ")):
        return {"intent": "chat_fallback", "target": text, "confidence": 90, "raw": raw_text, "slots": {}}

    if any(
        phrase in text
        for phrase in [
            "arrete la musique",
            "stop la musique",
            "stop musique",
            "mets la musique en pause",
            "met la musique en pause",
        ]
    ):
        return {"intent": "music_pause", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if text in {"reprends la musique", "continue la musique", "relance la musique"}:
        return {"intent": "music_resume", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if text in {"suivant", "piste suivante", "chanson suivante", "musique suivante"}:
        return {"intent": "music_next", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if text in {"precedent", "piste precedente", "chanson precedente", "musique precedente"}:
        return {"intent": "music_previous", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if text in {"stop", "arrete", "quitte", "stop listening"} or any(
        phrase in text for phrase in ["arrete toi", "arrete moctar", "ferme moctar"]
    ):
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

    if any(
        token in text
        for token in [
            "quel jour",
            "quelle date",
            "date d aujourd hui",
            "date du jour",
            "jour d aujourd hui",
            "quel jour sommes nous",
            "on est quel jour",
            "on est quelle date",
            "quelle est la date d aujourd hui",
        ]
    ):
        return {"intent": "date", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["batterie", "niveau batterie", "battery"]):
        return {"intent": "battery", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["capture", "screenshot", "capture ecran"]):
        return {"intent": "screenshot", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["augmente volume", "monte volume", "volume up"]):
        return {"intent": "volume_up", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["baisse volume", "diminue volume", "volume down"]):
        return {"intent": "volume_down", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

    if text in {"mets le son", "met le son", "remets le son", "remet le son", "retablis le son"}:
        return {"intent": "volume_up", "target": "", "confidence": 100, "raw": raw_text, "slots": {}}

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

    if any(
        token in text
        for token in [
            "brief du jour",
            "resume ma journee",
            "organise ma journee",
            "rappelle mes priorites",
        ]
    ):
        return {"intent": "daily_brief", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(
        token in text
        for token in [
            "quoi faire maintenant",
            "quelle est ma prochaine tache",
            "quelle est ma prochaine action",
            "prochaine priorite",
        ]
    ):
        return {"intent": "next_action", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(
        token in text
        for token in [
            "priorise mes taches",
            "priorise mes todos",
            "priorisation des taches",
            "classe mes priorites",
        ]
    ):
        return {"intent": "prioritize_tasks", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["routine matin", "routine du matin", "brief matin"]):
        return {"intent": "routine_morning", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["routine soir", "routine du soir", "brief soir"]):
        return {"intent": "routine_evening", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(token in text for token in ["mode focus", "focus projet", "mode projet"]):
        return {
            "intent": "focus_mode",
            "target": parse_focus_target(text),
            "confidence": 92,
            "raw": raw_text,
            "slots": {},
        }

    if any(
        token in text
        for token in [
            "que peux tu faire",
            "qu est ce que tu peux faire",
            "aide moi",
            "montre tes capacites",
        ]
    ):
        return {"intent": "assistant_capabilities", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

    if any(
        token in text
        for token in [
            "planificateur",
            "ouvre le planificateur",
            "taches automatiques",
            "taches planifiees",
            "programmer une publication",
            "publication automatique",
            "planifier une tache",
        ]
    ):
        return {"intent": "open_scheduler", "target": "", "confidence": 95, "raw": raw_text, "slots": {}}

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

    open_verbs = ["ouvre", "ouvrir", "active", "activate", "lance", "demarre"]
    if any(token in text for token in open_verbs):
        action_target = extract_action_target(text, open_verbs)
        folder_key, folder_score = find_alias(action_target, config.FOLDER_ALIASES, exact=True)
        if folder_key:
            return {"intent": "open_folder", "target": folder_key, "confidence": folder_score, "raw": raw_text, "slots": {}}

        site_key, site_score = find_alias(action_target, config.SITE_ALIASES, exact=True)
        if site_key:
            return {"intent": "open_site", "target": site_key, "confidence": site_score, "raw": raw_text, "slots": {}}

        app_key, app_score = find_alias(action_target, config.APP_ALIASES, exact=True)
        if app_key:
            return {"intent": "open_app", "target": app_key, "confidence": app_score, "raw": raw_text, "slots": {}}

    close_verbs = ["ferme", "close"]
    if any(token in text for token in close_verbs):
        action_target = extract_action_target(text, close_verbs)
        app_key, score = find_alias(action_target, config.APP_ALIASES, exact=True)
        if app_key:
            return {"intent": "close_app", "target": app_key, "confidence": score, "raw": raw_text, "slots": {}}

    # A music request must start as a request.  This avoids interpreting
    # ordinary sentences such as "je joue au foot" as a song title.
    if text in {"musique", "music"}:
        return {"intent": "play_music", "target": "", "confidence": 90, "raw": raw_text, "slots": {}}

    if re.match(r"^(?:mets|met|joue|ecoute)\b", text) or (
        text.startswith("lance") and any(word in text for word in ["musique", "music", "chanson", "son"])
    ):
        target = extract_music_query(text)
        return {"intent": "play_music", "target": target, "confidence": 90, "raw": raw_text, "slots": {}}

    return {"intent": "chat_fallback", "target": text, "confidence": 50, "raw": raw_text, "slots": {}}
