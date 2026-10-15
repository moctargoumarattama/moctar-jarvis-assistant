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

    # 2. SALUTATIONS & IDENTITÉ
    if norm in {"bonjour", "salut", "bonsoir", "coucou", "hello", "yo", "yo moctar", "hey moctar", "moctar"}:
        return {
            "handled": True,
            "reply": "Bonjour monsieur. M.O.C.T.A.R à votre service. Tous les systèmes sont opérationnels. Que souhaitez-vous faire ?",
            "tools_used": [],
            "task_category": "greetings",
        }

    if any(k in norm for k in ["qui es tu", "qui est tu", "presente toi", "presentation", "c est quoi moctar", "qui t a cree"]):
        return {
            "handled": True,
            "reply": (
                "Je suis **M.O.C.T.A.R** (*Mission Operations & Cyber-Tactical Autonomous Reactor*), "
                "votre assistant d'ingénierie et de supervision NOC/IoT personnel.\n"
                "Je pilote votre infrastructure (Dell Precision M4800, serveurs VPS, domotique), "
                "vos projets et votre organisation avec une tolérance aux pannes totale."
            ),
            "tools_used": [],
            "task_category": "identity",
        }

    # 3. OUVERTURE DE SITES OU APPLICATIONS (FAST-PATH UNIVERSEL)
    open_verbs = ("ouvre", "lance", "va sur", "ouve", "demarre", "start")
    if any(norm.startswith(v + " ") for v in open_verbs):
        for v in open_verbs:
            if norm.startswith(v + " "):
                target = raw_query[len(v):].strip()
                site_url, title = resolve_site_url(target)
                exec_res = actions.execute_tool("open_site", {"target": target})
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

    # 4. HEURE & DATE
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

    # 5. BATTERIE & VOLUME
    if "batterie" in norm or "charge" in words:
        res = actions.execute_tool("get_battery_response", {})
        tools_used.append({"tool": "get_battery_response", "arguments": {}, "success": True})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"🔋 {msg}", "tools_used": tools_used, "task_category": "system_tools"}

    if any(k in norm for k in ["volume", "mute", "muet", "coupe le son", "monte le son", "baisse le son"]):
        # Détection du niveau ou de l'action
        match_pct = re.search(r"(\d+)\s*(?:%|pourcent)?", norm)
        pct = int(match_pct.group(1)) if match_pct else None
        if "coupe" in norm or "muet" in norm or "mute" in norm:
            level = 0
        elif pct is not None:
            level = pct
        elif "monte" in norm:
            level = 80
        elif "baisse" in norm:
            level = 30
        else:
            level = 50
        res = actions.execute_tool("change_volume", {"level": level})
        tools_used.append({"tool": "change_volume", "arguments": {"level": level}, "success": True})
        return {"handled": True, "reply": f"🔊 Volume sonore ajusté à {level}%.", "tools_used": tools_used, "task_category": "system_tools"}

    # 6. TÉLÉMÉTRIE MATÉRIELLE & INFRASTRUCTURE
    is_telemetry = bool(words.intersection({
        "etat", "statut", "status", "infrastructure", "systeme", "system",
        "cpu", "ram", "disque", "stockage", "telemetrie", "ressource", "ressources", "materiel",
    }))
    if is_telemetry:
        res = actions.execute_tool("get_system_telemetry", {})
        tools_used.append({"tool": "get_system_telemetry", "arguments": {}, "success": bool(res.get("success"))})
        from headless_bridge import _format_telemetry_report
        report = _format_telemetry_report(res)
        return {"handled": True, "reply": report, "tools_used": tools_used, "task_category": "telemetry"}

    # 7. TÂCHES & NOTES (PRODUCTIVITÉ)
    if any(k in norm for k in ["liste mes taches", "liste les taches", "mes todos", "liste todo", "quoi faire", "programme"]):
        res = actions.execute_tool("list_todos", {})
        tools_used.append({"tool": "list_todos", "arguments": {}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📝 **Vos tâches en cours :**\n{msg}", "tools_used": tools_used, "task_category": "productivity"}

    if norm.startswith("ajoute todo ") or norm.startswith("ajoute la tache ") or norm.startswith("ajoute une tache "):
        task_text = re.sub(r"^(?:ajoute todo |ajoute la tache |ajoute une tache )", "", raw_query, flags=re.IGNORECASE).strip()
        res = actions.execute_tool("add_todo", {"content": task_text})
        tools_used.append({"tool": "add_todo", "arguments": {"content": task_text}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"✅ {msg}", "tools_used": tools_used, "task_category": "productivity"}

    if norm.startswith("cree une note ") or norm.startswith("note que ") or norm.startswith("prends note "):
        note_content = re.sub(r"^(?:cree une note |note que |prends note )", "", raw_query, flags=re.IGNORECASE).strip()
        res = actions.execute_tool("create_note", {"title": f"Note du {datetime.now().strftime('%d/%m %H:%M')}", "content": note_content})
        tools_used.append({"tool": "create_note", "arguments": {"content": note_content}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📌 {msg}", "tools_used": tools_used, "task_category": "productivity"}

    if any(k in norm for k in ["mes rappels", "prochains rappels", "alertes"]):
        res = actions.execute_tool("get_upcoming_reminders", {})
        tools_used.append({"tool": "get_upcoming_reminders", "arguments": {}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"⏰ **Rappels programmés :**\n{msg}", "tools_used": tools_used, "task_category": "productivity"}

    # 8. GÉNIE ÉNERGÉTIQUE & SOLAIRE
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

    # 9. GIT & PROJETS
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

    # 10. DOMOTIQUE IOT
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

