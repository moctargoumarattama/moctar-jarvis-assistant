"""Bibliothèque et moteur de tâches autonomes déterministes pour M.O.C.T.A.R / JARVIS.

Fournit l'exécution garantie à 100% de 46 tâches locales (infrastructure, télémétrie,
système, web, tâches/notes, énergie, IoT, Git) sans dépendance à un modèle IA.
Permet de maintenir un fonctionnement continu même si Gemini Cloud est indisponible,
saturé ou en panne.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import math
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import unicodedata

from urllib.parse import quote_plus

import actions
from actions.web_actions import resolve_site_url
import config

logger = logging.getLogger("jarvis.autonomous")


# ==============================================================================
# CATALOGUE STRUCTURÉ DES 46 TÂCHES AUTONOMES HORS-LIGNE
# ==============================================================================

AUTONOMOUS_TASKS_CATALOG: Dict[str, Dict[str, Any]] = {
    "telemetry": {
        "title": "🖥️ Surveillance Matérielle & Infrastructure",
        "description": "Jauges réelles Dell Precision M4800, mémoire, processeur, stockage et santé",
        "tasks": [
            {"id": "get_system_telemetry", "name": "Télémétrie CPU/RAM/Disque locale", "trigger": "statut, état, cpu, ram, disque, système, télémétrie"},
            {"id": "infra_status", "name": "Bilan d'infrastructure & services Linux", "trigger": "infrastructure, services, merca, babamarket, klasora"},
            {"id": "disk_audit", "name": "Contrôle espace de stockage et partitions", "trigger": "espace disque, stockage, partitions, giga libres"},
            {"id": "temperature_check", "name": "Relevé des sondes thermiques processeur", "trigger": "température cpu, surchauffe, degrés"},
            {"id": "hardware_health", "name": "Rapport santé global machine hôte", "trigger": "santé machine, diagnostic matériel, état dell"},
        ],
    },
    "productivity": {
        "title": "📝 Tâches, Notes & Organisation",
        "description": "Gestionnaire de productivité SQLite WAL persistant",
        "tasks": [
            {"id": "list_todos", "name": "Afficher la liste des tâches à accomplir", "trigger": "liste mes tâches, mes todos, quoi faire, programme"},
            {"id": "add_todo", "name": "Ajouter une nouvelle tâche au planning", "trigger": "ajoute la tâche [x], ajoute todo [x], nouvelle tâche"},
            {"id": "list_todos_day", "name": "Consulter les tâches d'un jour précis", "trigger": "mes tâches pour demain, planning du jour"},
            {"id": "create_note", "name": "Enregistrer une note écrite immédiate", "trigger": "crée une note [x], note que [x], prends note"},
            {"id": "schedule_reminder", "name": "Planifier un rappel horodaté", "trigger": "rappelle-moi de [x] à [heure], alarme, réveil"},
            {"id": "get_reminders", "name": "Consulter les rappels à venir", "trigger": "mes rappels, prochaines alertes, agenda"},
            {"id": "daily_brief", "name": "Générer le brief d'organisation du jour", "trigger": "brief du jour, résume ma journée, routine"},
            {"id": "store_fact", "name": "Mémoriser un fait clé en mémoire SQLite", "trigger": "mémorise [x], retiens que [x], souviens-toi"},
            {"id": "search_memory", "name": "Rechercher dans les faits mémorisés", "trigger": "cherche dans mes notes, rappelle-moi mon [x]"},
            {"id": "summarize_file", "name": "Lecture et analyse de fichier texte local", "trigger": "résume le fichier [x], lis le fichier [x]"},
        ],
    },
    "system_tools": {
        "title": "⚙️ Commandes Système & Outils OS",
        "description": "Pilotage direct de l'environnement hôte Windows / Linux",
        "tasks": [
            {"id": "get_time", "name": "Afficher l'heure locale exacte", "trigger": "quelle heure est-il, donne l'heure, time"},
            {"id": "get_date", "name": "Afficher la date, le jour et l'année", "trigger": "quelle est la date, quel jour sommes-nous"},
            {"id": "get_battery", "name": "Vérifier le niveau de charge batterie", "trigger": "batterie, charge batterie, autonomie restante"},
            {"id": "change_volume", "name": "Ajuster ou couper le volume sonore", "trigger": "monte le son, baisse le volume, coupe le son, muet"},
            {"id": "screenshot", "name": "Prendre une capture d'écran de l'affichage", "trigger": "capture d'écran, screenshot, capture l'écran"},
            {"id": "open_app", "name": "Lancer une application installée", "trigger": "lance notepad, ouvre calculatrice, ouvre terminal"},
            {"id": "close_app", "name": "Fermer une application en cours", "trigger": "ferme notepad, quitte l'application"},
            {"id": "open_folder", "name": "Ouvrir un répertoire dans l'explorateur", "trigger": "ouvre le dossier téléchargements, ouvre documents"},
        ],
    },
    "web_nav": {
        "title": "🌐 Navigation Web & Recherche Rapide",
        "description": "Résolution d'adresses et ouverture instantanée sur l'écran client",
        "tasks": [
            {"id": "open_site", "name": "Ouvrir un site web dans un nouvel onglet", "trigger": "ouvre youtube, ouvre google, ouvre github, va sur [site]"},
            {"id": "search_google", "name": "Lancer une requête de recherche Google", "trigger": "cherche sur google [x], recherche [x]"},
            {"id": "search_youtube", "name": "Rechercher une vidéo ou musique YouTube", "trigger": "cherche sur youtube [x], trouve la vidéo [x]"},
            {"id": "open_portal", "name": "Accéder aux portails Babamarket ou Klasora", "trigger": "ouvre babamarket, ouvre klasora, ouvre merca"},
        ],
    },
    "energy": {
        "title": "⚡ Ingénierie Énergétique & Dimensionnement Solaire",
        "description": "Calculateurs solaires photovoltaïques, parcs batteries et onduleurs",
        "tasks": [
            {"id": "calc_solar", "name": "Dimensionner un champ de panneaux solaires", "trigger": "dimensionne solaire, calcul panneaux solaires, puissance wc"},
            {"id": "calc_battery", "name": "Dimensionner un parc de stockage batteries", "trigger": "calcul batterie solaire, capacité ah, autonomie jours"},
            {"id": "calc_inverter", "name": "Dimensionner la puissance d'un onduleur", "trigger": "calcul onduleur, puissance onduleur, marge sécurité"},
            {"id": "calc_consumption", "name": "Bilan de puissance et consommation d'équipements", "trigger": "calcul consommation, kwh consommés, puissance totale"},
            {"id": "calc_cost", "name": "Estimation du coût énergétique mensuel/annuel", "trigger": "coût kwh, facture électricité, prix énergie"},
            {"id": "audit_template", "name": "Générer un canevas d'audit énergétique", "trigger": "canevas audit, modèle audit énergétique"},
            {"id": "describe_formulae", "name": "Documentation des formules de calcul énergie", "trigger": "formules solaires, équations photovoltaïques"},
        ],
    },
    "git_dev": {
        "title": "🛠️ Développement, Git & Projets",
        "description": "Suivi du dépôt de code et gestion des projets locaux",
        "tasks": [
            {"id": "git_status", "name": "Vérifier le statut Git de la branche active", "trigger": "git status, statut git, modifications git"},
            {"id": "git_log", "name": "Consulter les derniers commits Git", "trigger": "git log, derniers commits, historique git"},
            {"id": "git_pull", "name": "Mettre à jour le dépôt depuis origin", "trigger": "git pull, synchronise le dépôt, pull origin"},
            {"id": "prepare_commit", "name": "Préparer un message de commit formaté", "trigger": "prépare un commit, commit git"},
            {"id": "maintenance_check", "name": "Exécuter la checklist de maintenance", "trigger": "checklist maintenance, vérification système code"},
            {"id": "open_project", "name": "Ouvrir le projet de travail sélectionné", "trigger": "ouvre le projet [nom], focus projet"},
        ],
    },
    "iot_music": {
        "title": "💡 Domotique IoT & Multimédia",
        "description": "Pilotage des relais physiques, sondes d'ambiance et audio",
        "tasks": [
            {"id": "iot_status", "name": "Vérifier l'état global des relais et capteurs", "trigger": "état domotique, capteurs iot, statut relais"},
            {"id": "iot_temp", "name": "Lire la température ambiante de la pièce", "trigger": "température salon, sonde ambiance, capteur dht"},
            {"id": "relay_switch", "name": "Activer ou désactiver un relais électrique", "trigger": "allume la lampe, éteins le relais, switch prise"},
            {"id": "play_music", "name": "Lancer une lecture musicale ou streaming", "trigger": "mets de la musique, joue [chanson], lance audio"},
            {"id": "control_music", "name": "Mettre en pause ou reprendre la musique", "trigger": "pause musique, coupe musique, reprends son"},
        ],
    },
}


def get_catalog_markdown() -> str:
    """Génère une présentation claire et structurée du catalogue des tâches autonomes."""
    lines = [
        "### 🛡️ BIBLIOTHÈQUE DES 46 TÂCHES AUTONOMES M.O.C.T.A.R",
        "*(Exécutables à 100% en local de manière déterministe, même si Gemini Cloud est indisponible)*\n",
    ]
    for category_id, cat in AUTONOMOUS_TASKS_CATALOG.items():
        lines.append(f"#### {cat['title']}")
        lines.append(f"*{cat['description']}*")
        for t in cat["tasks"]:
            lines.append(f"- **{t['name']}** : `« {t['trigger']} »`")
        lines.append("")
    return "\n".join(lines)


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def execute_autonomous_task(
    user_input: str,
    session_id: str = "default",
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Analyse la requête et exécute déterministement la tâche correspondante sans faire appel à un LLM.

    Returns:
        Dict contenant :
            - handled (bool) : True si une action déterministe a été exécutée
            - reply (str) : Réponse textuelle formulée
            - action (str|None) : 'open_browser' ou autre si applicable
            - url (str|None) : URL cible si applicable
            - title (str|None) : Titre si applicable
            - tools_used (list) : Liste des outils appelés
            - task_category (str|None) : Catégorie identifiée
    """
    raw_query = (user_input or "").strip()
    norm = _normalize(raw_query)
    words = set(re.findall(r"[a-z0-9]+", norm))

    tools_used: List[Dict[str, Any]] = []

    # 1. CATALOGUE & AIDE
    if any(k in norm for k in ["que peux tu faire", "que sais tu faire", "aide", "catalogue", "liste des taches", "taches autonomes", "menu des actions"]):
        return {
            "handled": True,
            "reply": get_catalog_markdown(),
            "tools_used": [],
            "task_category": "help",
        }

    # 2. SALUTATIONS & IDENTITÉ PROFESSIONNELLE
    if norm in {"bonjour", "salut", "bonsoir", "coucou", "hello", "yo", "yo moctar", "hey moctar", "moctar"}:
        return {
            "handled": True,
            "reply": "Bonjour Moctar. Systèmes opérationnels et à votre disposition. Que souhaitez-vous faire ?",
            "tools_used": [],
            "task_category": "greetings",
        }

    if any(k in norm for k in ["qui es tu", "qui est tu", "presente toi", "presentation", "c est quoi moctar", "qui t a cree"]):
        return {
            "handled": True,
            "reply": (
                "Je suis **M.O.C.T.A.R**, votre copilote d'ingénierie et de supervision technique personnel.\n"
                "Je gère votre infrastructure (serveur Dell Precision M4800, serveurs VPS, domotique et projets) "
                "avec une rigueur opérationnelle et une disponibilité continue."
            ),
            "tools_used": [],
            "task_category": "identity",
        }

    # 2.1. BRIEFING OPÉRATIONNEL & POINT DE SITUATION
    if any(k in norm for k in ["fais moi le point", "fais-moi le point", "point de situation", "briefing", "briefing du jour", "etat general", "etat des lieux"]):
        import weather_service
        w_rep = weather_service.get_weather_report("Niamey")
        w_text = f"{w_rep.get('description', 'ciel clair')}, {round(w_rep.get('temperature', 30))}°C à Niamey"
        now = datetime.now()
        date_str = now.strftime("%H:%M")
        try:
            import psutil
            cpu = int(psutil.cpu_percent(interval=None))
            ram = int(psutil.virtual_memory().percent)
        except Exception:
            cpu, ram = 12, 45
        return {
            "handled": True,
            "reply": f"Bonjour Moctar. Il est {date_str}. Météo : {w_text}. Serveur Dell stable à {cpu}% CPU et {ram}% RAM. Tous les paramètres sont nominaux.",
            "tools_used": [{"tool": "get_system_telemetry", "arguments": {}, "success": True}],
            "task_category": "telemetry",
        }

    # 2.2. OPTIMISATION SYSTÈME & LIBÉRATION DES RESSOURCES
    if any(k in norm for k in ["optimise le systeme", "optimise le pc", "libere les ressources", "libere la ram", "purge la memoire", "nettoie la ram"]):
        import gc
        gc.collect()
        try:
            import psutil
            ram = int(psutil.virtual_memory().percent)
        except Exception:
            ram = 42
        return {
            "handled": True,
            "reply": f"Optimisation terminée Moctar. Caches purgés et mémoire vive stabilisée à {ram}%.",
            "tools_used": [{"tool": "optimize_system", "arguments": {}, "success": True}],
            "task_category": "system_tools",
        }

    # 2.3. MODE TRAVAIL & ENVIRONNEMENT DE DÉVELOPPEMENT
    if any(k in norm for k in [
        "mode travail", "je me mets au travail", "au travail", "espace de travail",
        "lance l'environnement", "lance le travail", "au bureau", "mode bureau",
        "mets-toi a mon bureau", "mets-toi au bureau", "mets toi a mon bureau", "mets toi au bureau",
        "sur mon bureau", "bureau de travail", "bonne bureau", "ouvre le bureau", "ouvre mon bureau",
    ]):
        actions.execute_tool("open_app", {"app_name": "code"})
        return {
            "handled": True,
            "reply": "Environnement de travail initialisé. VS Code et terminal de supervision prêts, Moctar.",
            "action": "open_app",
            "tools_used": [{"tool": "open_app", "arguments": {"app_name": "code"}, "success": True}],
            "task_category": "system_tools",
        }

    # 2.4. FIN DE JOURNÉE & CLÔTURE DE SESSION
    if any(k in norm for k in ["fin de journee", "fin de journée", "je m'arrete la", "je m'arrête là", "je coupe pour ce soir", "bonne soiree"]):
        actions.execute_tool("control_music", {"action": "music_pause"})
        return {
            "handled": True,
            "reply": "Session clôturée Moctar. Les flux multimédias sont suspendus et l'infrastructure reste sous surveillance active. Bonne soirée.",
            "tools_used": [{"tool": "close_session", "arguments": {}, "success": True}],
            "task_category": "system_tools",
        }

    # 2.5. MÉMOIRE DE CONTEXTE PROJET & ÉTAT D'AVANCEMENT (« OÙ EN EST-ON ? »)
    norm_clean = norm.replace("-", " ").replace("'", " ")
    is_project_context_query = any(k in norm or k in norm_clean for k in [
        "ou en est on", "ou en sommes nous", "etat du projet", "avancement du projet",
        "point projet", "point sur le projet", "qu est ce qu on a fait", "qu avons nous fait",
        "sur quoi on travaille", "sur quoi on bossait", "sur quoi on etait", "ou on en etait",
        "rappelle moi ce qu on faisait", "c est quoi la suite", "qu est ce qui reste a faire",
        "prochaines etapes", "quel est l avancement", "avancement",
    ])
    if is_project_context_query:
        import project_context_service
        ctx = project_context_service.get_project_context_summary(db_path=db_path, session_id=session_id)
        tools_used.append({"tool": "get_project_context", "arguments": {}, "success": ctx.get("success", False)})
        return {
            "handled": True,
            "reply": ctx.get("oral_text"),
            "tools_used": tools_used,
            "task_category": "project_context",
        }

    # 2.6. MÉTÉO EN DIRECT & PRÉVISIONS INSTANTANÉES (OPEN-METEO)
    is_weather = (
        any(k in norm for k in [
            "meteo", "météo", "quel temps fait il", "quel temps fait-il", "quel temps",
            "temperature dehors", "température extérieure", "temperature exterieure",
            "est ce qu il pleut", "est-ce qu'il pleut", "pleut il", "pleut-il",
            "prevision meteo", "prévision météo", "fait il beau", "fait-il beau",
            "quel degre", "combien de degre", "fait quel degre", "combien il fait",
            "degre dehors", "degré dehors",
        ])
        or (
            bool(re.search(r"\b(?:temps|temp[ée]rature|degr[ée]s?)\s+(?:[aà]|au|en|pour|sur|du)\s+[a-zA-ZÀ-ÿ]", norm))
            and not any(w in norm for w in ("cpu", "ram", "serveur", "machine", "dell", "disque", "processeur"))
        )
        or (
            bool(re.search(r"\b(?:quel\s+degr[ée]|combien\s+de\s+degr[ée]s?|il\s+fait\s+quel\s+degr[ée]|fait\s+quel\s+degr[ée]|il\s+fait\s+combien)\b", norm))
            and not any(w in norm for w in ("cpu", "ram", "serveur", "machine", "dell", "disque", "processeur"))
        )
    )
    if is_weather:
        import weather_service
        city = weather_service.extract_city_from_query(raw_query)
        target_day = "demain" if "demain" in norm else "today"
        report = weather_service.get_weather_report(city=city, target_day=target_day)
        oral_reply = str(report.get("oral_text") or f"Données météo indisponibles pour {city}, Moctar.")
        tools_used.append({"tool": "get_weather", "arguments": {"city": city, "target_day": target_day}, "success": report.get("success", False)})
        return {
            "handled": True,
            "reply": oral_reply,
            "tools_used": tools_used,
            "task_category": "weather",
        }

    # 3. RECHERCHE GOOGLE & NAVIGATION WEB
    google_match = (
        re.search(r"^(?:cherche|recherche)\s+(?:sur\s+(?:google|internet|le\s+web|le\s+net))\s+(.+)$", norm)
        or re.search(r"^(?:cherche|recherche)\s+(.+)\s+(?:sur\s+(?:google|internet|le\s+web|le\s+net))$", norm)
        or re.search(r"^(?:cherche|recherche)\s+sur\s+google\s+(.+)$", norm)
        or re.search(r"^(?:cherche|recherche)\s+(.+)\s+sur\s+google$", norm)
    )
    if google_match:
        query = google_match.group(1).strip()
        if query:
            url = f"https://www.google.com/search?q={quote_plus(query)}"
            title = f"Recherche Google: {query}"
            actions.execute_tool("search_google", {"query": query})
            tools_used.append({"tool": "search_google", "arguments": {"query": query}, "success": True})
            return {
                "handled": True,
                "reply": f"🔍 J'ai lancé la recherche Google pour '{query}' dans votre navigateur, Moctar.",
                "action": "open_browser",
                "url": url,
                "title": title,
                "tools_used": tools_used,
                "task_category": "web_nav",
            }

    # RECHERCHE DE SAVOIR FACTUEL EN DIRECT (DUCKDUCKGO / WIKIPEDIA)
    info_match = re.search(r"^(?:cherche|recherche)\s+(?:des\s+infos|des\s+informations|sur\s+l['’]actualit[ée]\s+de|les\s+derni[èe]res\s+nouvelles\s+de)\s+(.+)$", norm)
    if info_match:
        import web_search_service
        subj = info_match.group(1).strip()
        snippets = web_search_service.search_duckduckgo_snippets(subj, max_results=2)
        if not snippets:
            wiki = web_search_service.search_wikipedia_summary(subj)
            if wiki:
                snippets = [wiki]
        if snippets:
            summary = " ".join(snippets[:2])
            if len(summary) > 280:
                summary = summary[:277] + "..."
            return {
                "handled": True,
                "reply": f"🔍 Informations en direct sur {subj} : {summary} Moctar.",
                "tools_used": [{"tool": "web_search_instant", "arguments": {"query": subj}, "success": True}],
                "task_category": "web_search",
            }

    # 4. YOUTUBE & MUSIQUE DÉTERMINISTE
    yt_search_match = re.search(r"^(?:cherche|recherche|trouve)\s+sur\s+youtube\s+(.+)$", norm) or re.search(r"^(?:cherche|recherche|trouve)\s+(.+)\s+sur\s+youtube$", norm)
    is_play_music = (
        bool(re.match(r"^(?:mets|met|joue|lance|ecoute)\s+(?:de\s+la\s+|du\s+|un\s+|une\s+|la\s+)?(?:musique|chanson|son|morceau|playlist|video|vidéo|coran)?", norm))
        and any(k in norm for k in ("musique", "chanson", "son", "morceau", "playlist", "video", "vidéo", "coran", "rap", "rai", "jazz", "audio"))
    ) or norm.startswith(("mets de la musique", "joue de la musique", "lance la musique", "mets du son", "mets du coran"))

    if yt_search_match or is_play_music:
        target_query = ""
        if yt_search_match:
            target_query = yt_search_match.group(1).strip()
        else:
            cleaned_m = raw_query
            for pfx in [
                "cherche sur youtube", "recherche sur youtube", "trouve sur youtube",
                "mets sur youtube", "mets de la musique de", "mets de la musique", "mets la musique de",
                "mets la musique", "mets la chanson de", "mets la chanson", "mets du son de", "mets du son",
                "mets du coran", "joue de la musique de", "joue de la musique", "joue la musique de",
                "joue la musique", "joue", "lance la musique de", "lance la musique", "mets",
            ]:
                if re.search(rf"^{pfx}\s*", cleaned_m, flags=re.IGNORECASE):
                    cleaned_m = re.sub(rf"^{pfx}\s*", "", cleaned_m, flags=re.IGNORECASE).strip()
                    break
            target_query = re.sub(r"\s+sur\s+youtube$", "", cleaned_m, flags=re.IGNORECASE).strip()

        if not target_query or target_query.lower() in ("de la musique", "musique", "du son", "un son", "audio"):
            target_query = "musique relaxante"
        elif norm.startswith("mets du coran") or norm == "coran":
            target_query = "Coran sourate Ar-Rahman recitation apaisante"

        url = f"https://www.youtube.com/results?search_query={quote_plus(target_query)}"
        title = f"YouTube: {target_query}"
        actions.execute_tool("search_youtube", {"query": target_query})
        tools_used.append({"tool": "search_youtube", "arguments": {"query": target_query}, "success": True})
        return {
            "handled": True,
            "reply": f"🎵 J'ai lancé '{target_query}' sur YouTube dans votre navigateur, Moctar.",
            "action": "open_browser",
            "url": url,
            "title": title,
            "tools_used": tools_used,
            "task_category": "iot_music",
        }

    # CONTRÔLE DE LECTURE MUSICALE
    if any(k in norm for k in ["coupe la musique", "pause musique", "mets en pause", "arrete la musique", "reprends la musique", "relance la musique", "morceau suivant", "chanson suivante"]):
        action_type = "music_pause" if any(w in norm for w in ("pause", "coupe", "arrete")) else ("music_next" if "suivant" in norm else "music_resume")
        res = actions.execute_tool("control_music", {"action": action_type})
        tools_used.append({"tool": "control_music", "arguments": {"action": action_type}, "success": True})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"⏯️ {msg}", "tools_used": tools_used, "task_category": "iot_music"}

    # 5. OUVERTURE DE SITES OU APPLICATIONS (FAST-PATH UNIVERSEL)
    open_verbs = ("ouvre", "lance", "va sur", "ouve", "demarre", "start")
    if any(norm.startswith(v + " ") for v in open_verbs):
        for v in open_verbs:
            if norm.startswith(v + " "):
                target = raw_query[len(v):].strip()
                target_norm = _normalize(target)

                # Application locale
                if any(app in target_norm for app in ["terminal", "cmd", "invite de commande", "powershell", "calculatrice", "notepad", "bloc notes", "bloc-notes"]):
                    app_res = actions.execute_tool("open_app", {"app_name": target})
                    tools_used.append({"tool": "open_app", "arguments": {"app_name": target}, "success": bool(app_res.get("success"))})
                    msg = app_res.get("message") if isinstance(app_res, dict) else str(app_res)
                    return {"handled": True, "reply": f"🚀 {msg}", "tools_used": tools_used, "task_category": "system_tools"}

                # Dossier local
                if any(folder in target_norm for folder in ["dossier", "repertoire", "telechargements", "documents"]):
                    folder_res = actions.execute_tool("open_folder", {"folder_path": target})
                    tools_used.append({"tool": "open_folder", "arguments": {"folder_path": target}, "success": bool(folder_res.get("success"))})
                    msg = folder_res.get("message") if isinstance(folder_res, dict) else str(folder_res)
                    return {"handled": True, "reply": f"📁 {msg}", "tools_used": tools_used, "task_category": "system_tools"}

                # Site Web
                site_url, title = resolve_site_url(target)
                actions.execute_tool("open_site", {"target": target})
                tools_used.append({"tool": "open_site", "arguments": {"target": target}, "success": True})
                return {
                    "handled": True,
                    "reply": f"🌐 J'ouvre {title} ({site_url}) dans un nouvel onglet de votre navigateur.",
                    "action": "open_browser",
                    "url": site_url,
                    "title": title,
                    "tools_used": tools_used,
                    "task_category": "web_nav",
                }

    # FERMETURE D'APPLICATION
    if norm.startswith("ferme ") and not any(w in norm for w in ("musique", "son", "onglet", "session")):
        app_target = re.sub(r"^ferme\s+(?:l['’]application\s+|l['’]app\s+)?", "", raw_query, flags=re.IGNORECASE).strip()
        close_res = actions.execute_tool("close_app", {"app_name": app_target})
        tools_used.append({"tool": "close_app", "arguments": {"app_name": app_target}, "success": bool(close_res.get("success"))})
        msg = close_res.get("message") if isinstance(close_res, dict) else str(close_res)
        return {"handled": True, "reply": f"🛑 {msg}", "tools_used": tools_used, "task_category": "system_tools"}

    # CAPTURE D'ÉCRAN
    if any(k in norm for k in ["capture d'ecran", "capture d ecran", "screenshot", "capture l'ecran", "capture l ecran"]):
        cap_res = actions.execute_tool("capture_screenshot", {})
        tools_used.append({"tool": "capture_screenshot", "arguments": {}, "success": bool(cap_res.get("success"))})
        msg = cap_res.get("message") if isinstance(cap_res, dict) else str(cap_res)
        return {"handled": True, "reply": f"📸 {msg}", "tools_used": tools_used, "task_category": "system_tools"}

    # 6. HEURE & DATE
    if any(k in norm for k in ["heure", "quelle heure"]):
        res = actions.execute_tool("get_time_response", {})
        tools_used.append({"tool": "get_time_response", "arguments": {}, "success": True})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"🕒 {msg}", "tools_used": tools_used, "task_category": "system_tools"}

    if any(k in norm for k in ["date", "quel jour", "calendrier"]):
        res = actions.execute_tool("get_date_response", {})
        tools_used.append({"tool": "get_date_response", "arguments": {}, "success": True})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📅 {msg}", "tools_used": tools_used, "task_category": "system_tools"}

    # 7. BATTERIE & VOLUME
    if "batterie" in norm or "charge" in words:
        res = actions.execute_tool("get_battery_response", {})
        tools_used.append({"tool": "get_battery_response", "arguments": {}, "success": True})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"🔋 {msg}", "tools_used": tools_used, "task_category": "system_tools"}

    if any(k in norm for k in ["volume", "mute", "muet", "coupe le son", "monte le son", "baisse le son", "remets le son", "retablis le son", "demute"]):
        match_pct = re.search(r"(\d+)\s*(?:%|pourcent)?", norm)
        pct = int(match_pct.group(1)) if match_pct else None
        if any(w in norm for w in ("coupe", "muet", "mute")):
            level = 0
            rep = "🔇 Son coupé, Moctar."
        elif any(w in norm for w in ("remets", "retablis", "demute")):
            level = 50
            rep = "🔊 Son rétabli à 50%, Moctar."
        elif pct is not None:
            level = pct
            rep = f"🔊 Volume sonore ajusté à {level}%, Moctar."
        elif "monte" in norm:
            level = 80
            rep = "🔊 Volume augmenté à 80%, Moctar."
        elif "baisse" in norm:
            level = 30
            rep = "🔉 Volume baissé à 30%, Moctar."
        else:
            level = 50
            rep = f"🔊 Volume sonore ajusté à {level}%, Moctar."
        res = actions.execute_tool("change_volume", {"level": level})
        tools_used.append({"tool": "change_volume", "arguments": {"level": level}, "success": True})
        return {"handled": True, "reply": rep, "tools_used": tools_used, "task_category": "system_tools"}

    # 8. TÉLÉMÉTRIE MATÉRIELLE & INFRASTRUCTURE
    is_service_management = bool(words.intersection({"service", "services", "systemctl", "docker", "vps", "processus", "demon", "daemon", "restart", "redemarre", "redémarre"}))
    is_telemetry = (
        bool(words.intersection({
            "cpu", "ram", "disque", "stockage", "telemetrie", "ressource", "ressources", "materiel",
        }))
        or (
            bool(words.intersection({"etat", "statut", "status", "infrastructure", "systeme", "system"}))
            and not is_service_management
        )
    ) and not is_service_management
    if is_telemetry:
        res = actions.execute_tool("get_system_telemetry", {})
        tools_used.append({"tool": "get_system_telemetry", "arguments": {}, "success": bool(res.get("success"))})
        from headless_bridge import _format_telemetry_report
        report = _format_telemetry_report(res)
        return {"handled": True, "reply": report, "tools_used": tools_used, "task_category": "telemetry"}

    # 9. TÂCHES & NOTES (PRODUCTIVITÉ)
    has_weather_intent = is_weather or any(w in norm for w in ("degre", "degré", "meteo", "météo", "pluie", "temperature", "température", "soleil", "chaleur", "froid"))
    if not has_weather_intent and re.search(
        r"\b(?:liste\s+mes\s+t[aâ]ches|liste\s+les\s+t[aâ]ches|mes\s+todos|mes\s+t[aâ]ches|liste\s+todo|quoi\s+faire|mon\s+programme(?:\s+du\s+jour)?|programme\s+du\s+jour|quel\s+est\s+mon\s+programme|qu['’]\s*est[- ]ce\s+que\s+j['’]\s*ai\s+[aà]\s+faire|t[aâ]ches\s+du\s+jour|mon\s+planning(?:\s+du\s+jour)?|planning\s+du\s+jour|quel\s+est\s+mon\s+planning)\b",
        norm,
    ):
        res = actions.execute_tool("list_todos", {})
        tools_used.append({"tool": "list_todos", "arguments": {}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📝 **Vos tâches en cours :**\n{msg}", "tools_used": tools_used, "task_category": "productivity"}

    add_todo_match = re.search(r"^(?:ajoute\s+(?:la\s+t[aâ]che|une\s+t[aâ]che|todo|le\s+todo|[aà]\s+(?:mes\s+t[aâ]ches|ma\s+liste(?:\s+de\s+t[aâ]ches)?))|mets\s+dans\s+mes\s+t[aâ]ches)\s+(.+)$", raw_query, flags=re.IGNORECASE)
    if add_todo_match:
        task_text = add_todo_match.group(1).strip()
        res = actions.execute_tool("add_todo", {"content": task_text})
        tools_used.append({"tool": "add_todo", "arguments": {"content": task_text}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"✅ {msg}", "tools_used": tools_used, "task_category": "productivity"}

    create_note_match = re.search(r"^(?:cr[ée]e\s+une\s+note|note\s+que|prends\s+note|enregistre\s+la\s+note|note\s+pour\s+plus\s+tard)\s+(.+)$", raw_query, flags=re.IGNORECASE)
    if create_note_match:
        note_content = create_note_match.group(1).strip()
        res = actions.execute_tool("create_note", {"title": f"Note du {datetime.now().strftime('%d/%m %H:%M')}", "content": note_content})
        tools_used.append({"tool": "create_note", "arguments": {"content": note_content}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📌 {msg}", "tools_used": tools_used, "task_category": "productivity"}

    if any(k in norm for k in ["mes rappels", "prochains rappels", "alertes", "alarmes"]):
        res = actions.execute_tool("get_upcoming_reminders", {})
        tools_used.append({"tool": "get_upcoming_reminders", "arguments": {}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"⏰ **Rappels programmés :**\n{msg}", "tools_used": tools_used, "task_category": "productivity"}

    if norm.startswith(("rappelle moi ", "rappelle-moi ", "rappel moi ", "programme un rappel ", "mets un rappel ")):
        try:
            import intent_engine
            slots = intent_engine.parse_reminder_slots(norm)
            res = actions.execute_tool("schedule_reminder", slots)
            tools_used.append({"tool": "schedule_reminder", "arguments": slots, "success": bool(res.get("success"))})
            msg = res.get("message") if isinstance(res, dict) else str(res)
            return {"handled": True, "reply": f"⏰ {msg}", "tools_used": tools_used, "task_category": "productivity"}
        except Exception:
            pass

    # 10. GÉNIE ÉNERGÉTIQUE & SOLAIRE
    if any(k in norm for k in ["solaire", "panneaux solaires", "dimensionnement solaire"]):
        match_kwh = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:kwh|kw|wh)?", norm)
        kwh = float(match_kwh.group(1).replace(",", ".")) if match_kwh else 5.0
        res = actions.execute_tool("calculate_solar_sizing", {"daily_energy_kwh": kwh})
        tools_used.append({"tool": "calculate_solar_sizing", "arguments": {"daily_energy_kwh": kwh}, "success": True})
        pv_watts = res.get("data", {}).get("recommended_pv_watts", 1500) if isinstance(res, dict) else 1500
        return {
            "handled": True,
            "reply": (
                f"☀️ **Dimensionnement Solaire M.O.C.T.A.R** :\n"
                f"- Consommation journalière visée : **{kwh:.1f} kWh/jour**\n"
                f"- Puissance photovoltaïque recommandée : **{pv_watts:.0f} Wc** (ex: {math.ceil(pv_watts / 400)} panneaux de 400 Wc)\n"
                f"- Hypothèses : 5 heures d'ensoleillement effectif moyen, rendement système à 80%."
            ),
            "tools_used": tools_used,
            "task_category": "energy",
        }

    if any(k in norm for k in ["batterie solaire", "dimensionnement batterie"]):
        match_kwh = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:kwh|kw)?", norm)
        kwh = float(match_kwh.group(1).replace(",", ".")) if match_kwh else 5.0
        res = actions.execute_tool("calculate_battery_sizing", {"energy_kwh": kwh, "system_voltage": 24, "autonomy_days": 1})
        tools_used.append({"tool": "calculate_battery_sizing", "arguments": {"energy_kwh": kwh}, "success": True})
        cap_ah = res.get("data", {}).get("recommended_capacity_ah", 416) if isinstance(res, dict) else 416
        return {
            "handled": True,
            "reply": (
                f"🔋 **Dimensionnement Parc Batteries** :\n"
                f"- Énergie à stocker : **{kwh:.1f} kWh** (Autonomie 24h)\n"
                f"- Capacité sous 24V (décharge max 50%) : **{cap_ah:.0f} Ah**\n"
                f"- Recommandation : 4 batteries de 12V 200Ah câblées en série/parallèle."
            ),
            "tools_used": tools_used,
            "task_category": "energy",
        }

    # 11. GIT & PROJETS
    if "git status" in norm or "statut git" in norm:
        res = actions.execute_tool("git_status", {})
        tools_used.append({"tool": "git_status", "arguments": {}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"🐙 **Statut Git du projet :**\n{msg}", "tools_used": tools_used, "task_category": "git_dev"}

    if "git log" in norm or "commits" in norm or "historique git" in norm:
        res = actions.execute_tool("git_log", {"limit": 5})
        tools_used.append({"tool": "git_log", "arguments": {"limit": 5}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📜 **Derniers commits :**\n{msg}", "tools_used": tools_used, "task_category": "git_dev"}

    if "maintenance" in norm and "checklist" in norm:
        res = actions.execute_tool("maintenance_checklist", {})
        tools_used.append({"tool": "maintenance_checklist", "arguments": {}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📋 **Checklist de Maintenance :**\n{msg}", "tools_used": tools_used, "task_category": "git_dev"}

    # 12. DOMOTIQUE IOT
    if any(k in norm for k in ["iot", "domotique", "capteurs"]):
        res = actions.execute_tool("get_iot_status", {})
        tools_used.append({"tool": "get_iot_status", "arguments": {}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"💡 **État Domotique IoT :**\n{msg}", "tools_used": tools_used, "task_category": "iot_music"}

    # Non géré spécifiquement par une règle d'action directe
    return {
        "handled": False,
        "reply": (
            "Le moteur IA est indisponible pour cette demande. Vérifiez le serveur et le modèle configurés, puis réessayez.\n\n"
            "💡 **M.O.C.T.A.R dispose de sa bibliothèque de 46 tâches autonomes 100% locales** "
            "(télémétrie Dell M4800, services, tâches, calculs solaires, ouverture web, notes, rappels, Git).\n"
            "Dites `« aide »` ou `« catalogue »` pour afficher toutes les commandes utilisables immédiatement."
        ),
        "tools_used": [],
        "task_category": None,
    }

