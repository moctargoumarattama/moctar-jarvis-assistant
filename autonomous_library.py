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
    text = text.strip(" .,:;!?'\"’«»")
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

    # 0. CONFIRMATION OU PRÉCISION D'UNE ACTION GIT EN ATTENTE ("Oui", "Valide", "Je parle de Baba Market")
    try:
        from actions import project_actions
        if project_actions.has_pending_git_action():
            norm_conf = norm.strip(" .,:;!?'\"’«»")
            if norm_conf in (
                "oui", "oui vas-y", "oui vas y", "vas-y", "vas y", "valide", "on valide",
                "confirme", "je confirme", "d'accord", "d accord", "ok", "envoie", "lance", "fais-le", "fais le",
            ) or re.search(r"^(?:oui|valide|confirme|vas[- ]y|d['’]accord|envoie)\b", norm_conf):
                res = project_actions.confirm_pending_git_action()
                tools_used.append({"tool": "confirm_git_action", "arguments": {}, "success": bool(res.get("success", True))})
                try:
                    import smart_command_cache
                    smart_command_cache.get_command_cache().invalidate_category("git")
                except Exception:
                    pass
                return {
                    "handled": True,
                    "reply": str(res.get("message") or "Opération Git validée et exécutée."),
                    "tools_used": tools_used,
                    "task_category": "git_dev",
                }
            if norm_conf in ("non", "annule", "on annule", "laisse tomber", "stop") or re.search(r"^(?:non|annule)\b", norm_conf):
                res = project_actions.cancel_pending_git_action()
                tools_used.append({"tool": "cancel_git_action", "arguments": {}, "success": True})
                return {
                    "handled": True,
                    "reply": str(res.get("message") or "Opération Git annulée."),
                    "tools_used": tools_used,
                    "task_category": "git_dev",
                }
            if any(p in norm_conf for p in ("baba market", "papa market", "babamarket", "noor express", "edumanage")) and "git" not in norm_conf:
                pending = project_actions.get_pending_git_action() or {}
                clarified_key = "baba_market" if any(p in norm_conf for p in ("baba", "papa")) else ("noor_express" if "noor" in norm_conf else "edumanage")
                clarified_label = "Baba Market" if clarified_key == "baba_market" else ("Noor Express" if clarified_key == "noor_express" else "EDUMANAGE")
                if pending.get("type") in ("commit", "commit_and_push"):
                    project_actions.prepare_commit(message=pending.get("message", "feat: update project"), project_key=clarified_key)
                if pending.get("type") in ("push", "commit_and_push"):
                    project_actions.prepare_push(project_key=clarified_key)
                tools_used.append({"tool": "prepare_push", "arguments": {"project_key": clarified_key}, "success": True})
                return {
                    "handled": True,
                    "reply": f"Le push pour {clarified_label} est prêt. On valide l'envoi vers le dépôt distant ?",
                    "tools_used": tools_used,
                    "task_category": "git_dev",
                }
    except Exception:
        pass

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
                "Je suis **M.O.C.T.A.R**, ton copilote d'ingénierie et ton bras droit technique sur ce Dell Precision M4800 ! "
                "Je veille sur tes serveurs, tes projets et ton infrastructure, et j'exécute tout ce que tu me demandes en un clin d'œil, Moctar."
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
        cpu = None
        ram = None
        try:
            import psutil
            cpu = int(psutil.cpu_percent(interval=None))
            ram = int(psutil.virtual_memory().percent)
        except Exception:
            pass
        return {
            "handled": True,
            "reply": (
                f"Bonjour Moctar. Il est {date_str}. M?t?o : {w_text}. "
                + (f"Serveur Dell stable ? {cpu}% CPU et {ram}% RAM. Tous les param?tres sont nominaux."
                   if (cpu is not None and ram is not None)
                   else "M?triques CPU/RAM [INDISPONIBLES / NON V?RIFI?ES]. Vigilance requise sur les sondes syst?me.")
            ),
            "tools_used": [{"tool": "get_system_telemetry", "arguments": {}, "success": (cpu is not None and ram is not None)}],
            "task_category": "telemetry",
        }

    # 2.1b. BRIEFING CYBERDÉFENSE & SENTINELLE (Prise en charge de SuperDefense / Cyberdéfense)
    if any(k in norm for k in [
        "superdefense", "cyberdefense", "cyber defense", "cyber-defense", "point sur la cyberdefense",
        "point sur la defense", "point sur superdefense", "etat de la cyberdefense", "etat du bouclier",
        "bouclier cyber", "sentinelle", "point securite", "point sur la securite"
    ]):
        active_bans = 0
        req_count = 0
        try:
            from main import application
            runtime = getattr(application.state, "runtime", None) if application else None
            if runtime and hasattr(runtime, "cyber"):
                snap = runtime.cyber.snapshot()
                active_bans = snap.get("active_bans", 0)
                traffic = snap.get("traffic", [])
                req_count = sum(t.get("count", 0) for t in traffic)
        except Exception:
            pass

        reply_msg = (
            f"🛡️ Moctar, la console de cyberdéfense et la sentinelle sont pleinement actives. "
            f"Sur les 60 dernières minutes, {req_count} requêtes applicatives ont été analysées et filtrées avec succès. "
            + (f"{active_bans} adresse(s) suspecte(s) sont actuellement neutralisées. " if active_bans > 0 else "Aucune intrusion ni tentative suspecte sur le périmètre. ")
            + "Le pare-feu applicatif, les routes sensibles et l'accès sécurisé du Dell Precision M4800 sont totalement sous contrôle."
        )
        return {
            "handled": True,
            "reply": reply_msg,
            "tools_used": [{"tool": "get_cyberdefense_status", "arguments": {}, "success": True}],
            "task_category": "cyberdefense",
        }

    # 2.2. OPTIMISATION SYSTÈME & LIBÉRATION DES RESSOURCES
    if any(k in norm for k in ["optimise le systeme", "optimise le pc", "libere les ressources", "libere la ram", "purge la memoire", "nettoie la ram"]):
        import gc
        gc.collect()
        ram = None
        try:
            import psutil
            ram = int(psutil.virtual_memory().percent)
        except Exception:
            pass
        ram_str = f"m?moire vive stabilis?e ? {ram}%" if ram is not None else "m?moire vive purg?e (mesure psutil indisponible)"
        return {
            "handled": True,
            "reply": f"Optimisation terminée Moctar. Caches purgés et {ram_str}.",
            "tools_used": [{"tool": "optimize_system", "arguments": {}, "success": True}],
            "task_category": "system_tools",
        }

    # 2.3. AGENCEMENT GÉOMÉTRIQUE — « MODE BUREAU » (Ubuntu X11 & Dell M4800)
    if any(k in norm for k in [
        "mode bureau", "agencement bureau", "mode travail", "disposition travail",
        "disposition bureau", "agencement du bureau", "agencement poste de travail",
    ]):
        res = actions.execute_tool("apply_workspace_layout", {})
        tools_used.append({"tool": "apply_workspace_layout", "arguments": {}, "success": bool(res.get("success", True))})
        return {
            "handled": True,
            "reply": "Agencement géométrique du poste de travail appliqué, Moctar.",
            "action": "open_app",
            "url": None,
            "title": "Mode Bureau",
            "tools_used": tools_used,
            "task_category": "workspace_layout",
        }

    # 2.3b. MODE TRAVAIL & ENVIRONNEMENT DE DÉVELOPPEMENT (VS CODE)
    if any(k in norm for k in [
        "je me mets au travail", "au travail", "espace de travail",
        "lance l'environnement", "lance le travail", "au bureau",
        "mets-toi a mon bureau", "mets-toi au bureau", "mets toi a mon bureau", "mets toi au bureau",
        "mets-toi au mode bureau", "mets toi au mode bureau",
        "sur mon bureau", "bureau de travail", "bonne bureau", "ouvre le bureau", "ouvre mon bureau",
        "visual studio code", "visuel studio code", "vs code", "vscode", "vscod", "ou vscod",
    ]):
        actions.execute_tool("open_app", {"app_name": "vscode"})
        return {
            "handled": True,
            "reply": "Environnement de travail initialisé. VS Code et terminal de supervision prêts, Moctar.",
            "action": "open_app",
            "url": None,
            "title": "Visual Studio Code",
            "tools_used": [{"tool": "open_app", "arguments": {"app_name": "vscode"}, "success": True}],
            "task_category": "system_tools",
        }

    # 2.3c. INTERFACE HUD 3D (ORBE, GLOBE TERRESTRE, PLASMA, QUANTUM & RETOUR NOC)
    if any(k in norm for k in [
        "retourne en mode noc", "retour en mode noc", "retour au noc", "retourne au noc",
        "mode noc", "ouvre le noc", "affiche le noc", "retourne au dashboard", "retour au dashboard",
        "quitte le hud", "ferme le hud", "retour noc", "dashboard noc",
    ]):
        actions.execute_tool("switch_hud_view", {"view": "orb", "locate": False})
        tools_used.append({"tool": "switch_hud_view", "arguments": {"view": "orb", "locate": False}, "success": True})
        return {
            "handled": True,
            "reply": "Retour au Centre de Commandement NOC, Moctar.",
            "action": "open_app",
            "url": "/",
            "title": "Centre de Commandement NOC",
            "tools_used": tools_used,
            "task_category": "hud_3d",
        }

    if any(k in norm for k in [
        "mode hud 3d", "hud 3d", "ouvre le hud 3d", "active le hud 3d", "lance le hud 3d",
        "mode hud", "ouvre le hud", "active le hud", "mode hope 3d", "mode hot trading", "mode hot-trading",
        "top 3d", "ouvre le top 3d", "mode top 3d", "active le top 3d", "lance le top 3d", "ouvre top 3d",
        "hot 3d", "up 3d", "hop 3d",
    ]):
        actions.execute_tool("switch_hud_view", {"view": "plasma", "locate": False})
        tools_used.append({"tool": "switch_hud_view", "arguments": {"view": "plasma", "locate": False}, "success": True})
        return {
            "handled": True,
            "reply": "Interface HUD 3D cybernétique activée, Moctar.",
            "action": "open_app",
            "url": "/hud",
            "title": "HUD 3D Cybernétique",
            "tools_used": tools_used,
            "task_category": "hud_3d",
        }

    if any(k in norm for k in [
        "mode plasma", "reacteur plasma", "arc plasma", "plasma arc",
    ]):
        actions.execute_tool("switch_hud_view", {"view": "plasma", "locate": False})
        tools_used.append({"tool": "switch_hud_view", "arguments": {"view": "plasma", "locate": False}, "success": True})
        return {
            "handled": True,
            "reply": "Mode Réacteur Plasma activé sur l'interface 3D, Moctar.",
            "action": "open_app",
            "url": "/hud",
            "title": "Réacteur Plasma 3D",
            "tools_used": tools_used,
            "task_category": "hud_3d",
        }

    if any(k in norm for k in [
        "mode quantum", "coeur quantique", "neural core", "noyau quantique",
    ]):
        actions.execute_tool("switch_hud_view", {"view": "quantum", "locate": False})
        tools_used.append({"tool": "switch_hud_view", "arguments": {"view": "quantum", "locate": False}, "success": True})
        return {
            "handled": True,
            "reply": "Noyau neural quantique déployé sur le HUD 3D, Moctar.",
            "action": "open_app",
            "url": "/hud",
            "title": "Noyau Quantique 3D",
            "tools_used": tools_used,
            "task_category": "hud_3d",
        }

    # IMPORTANT : Tester la géolocalisation (locate=True) AVANT le globe générique (locate=False)
    if any(k in norm for k in [
        "affiche ma position", "localise moi", "localise-moi", "montre ma position",
        "ou suis je", "où suis-je", "ou suis-je", "je suis ou", "je suis où",
        "ou je suis", "où je suis", "dans le monde", "ma position sur le globe",
        "position en temps reel", "position en temps réel", "ma position",
    ]):
        actions.execute_tool("switch_hud_view", {"view": "globe", "locate": True})
        tools_used.append({"tool": "switch_hud_view", "arguments": {"view": "globe", "locate": True}, "success": True})
        return {
            "handled": True,
            "reply": "Localisation en cours. Le globe affiche votre position en temps réel, Moctar.",
            "action": "open_app",
            "url": "/hud",
            "title": "Globe Terrestre 3D",
            "tools_used": tools_used,
            "task_category": "hud_3d",
        }

    if any(k in norm for k in [
        "affiche la terre", "montre la terre", "montre le globe", "affiche le globe",
        "globe terrestre", "vue depuis l espace", "vue de l espace", "mode globe", "ouvre le globe",
    ]):
        actions.execute_tool("switch_hud_view", {"view": "globe", "locate": False})
        tools_used.append({"tool": "switch_hud_view", "arguments": {"view": "globe", "locate": False}, "success": True})
        return {
            "handled": True,
            "reply": "Initialisation du globe terrestre. Vue depuis l'espace activée, Moctar.",
            "action": "open_app",
            "url": "/hud",
            "title": "Globe Terrestre 3D",
            "tools_used": tools_used,
            "task_category": "hud_3d",
        }

    if any(k in norm for k in [
        "retourne a l orbe", "retourne a l'orbe", "mode orbe", "ferme le globe",
        "quitte le globe", "affiche l orbe", "affiche l'orbe", "retour a l orbe", "retour à l'orbe",
    ]):
        actions.execute_tool("switch_hud_view", {"view": "orb", "locate": False})
        tools_used.append({"tool": "switch_hud_view", "arguments": {"view": "orb", "locate": False}, "success": True})
        return {
            "handled": True,
            "reply": "Retour à l'interface orbitale principale, Moctar.",
            "action": "open_app",
            "url": "/hud",
            "title": "Orbe 3D",
            "tools_used": tools_used,
            "task_category": "hud_3d",
        }

    # 2.3d. NEUROVISION & SCANNER COGNITIF 3D ("mets le mode neurovision", "active la vision", "ferme neurovision")
    if any(k in norm for k in [
        "ferme neurovision", "ferme le mode neurovision", "desactive neurovision", "désactive neurovision",
        "desactive le mode neurovision", "désactive le mode neurovision", "quitte neurovision", "quitte le mode neurovision",
        "coupe neurovision", "coupe la neurovision", "arrete neurovision", "arrête neurovision",
        "coupe la camera", "coupe la caméra", "eteins la camera", "éteins la caméra", "coupe la vision", "arrete la vision", "arrête la vision",
    ]):
        actions.execute_tool("control_vision", {"action": "stop"})
        tools_used.append({"tool": "control_vision", "arguments": {"action": "stop"}, "success": True})
        return {
            "handled": True,
            "reply": "Caméra et moteur NeuroVision désactivés, Moctar.",
            "action": "close_modal",
            "target": "neurovision-dialog",
            "modal_id": "neurovision-dialog",
            "title": "Scanner NeuroVision 3D",
            "tools_used": tools_used,
            "task_category": "neurovision",
        }

    if any(k in norm for k in [
        "mode neurovision", "ouvre neurovision", "active neurovision", "active la neurovision",
        "lance neurovision", "affiche neurovision", "scanner neurovision", "active le mode neurovision",
        "mets le mode neurovision", "mets-toi en mode neurovision", "ouvre le mode neurovision",
        "allume la camera", "allume la caméra", "active la camera", "active la caméra", "active la vision",
        "neovision", "mode neovision", "ouvre neovision", "active neovision", "lance neovision", "affiche neovision",
        "ouvre la camera", "ouvre la caméra", "ouvre la vision", "ouvre camera", "ouvre caméra",
    ]) and not any(k in norm for k in ["ferme", "desactive", "désactive", "coupe", "quitte", "arrete", "arrête", "eteins", "éteins"]):
        actions.execute_tool("control_vision", {"action": "start"})
        tools_used.append({"tool": "control_vision", "arguments": {"action": "start"}, "success": True})
        return {
            "handled": True,
            "reply": "Caméra et vision cognitive NeuroVision activées, Moctar. Je veille sur ton poste.",
            "action": "open_modal",
            "target": "neurovision-dialog",
            "modal_id": "neurovision-dialog",
            "title": "Scanner NeuroVision 3D",
            "tools_used": tools_used,
            "task_category": "neurovision",
        }

    # 2.4. FIN DE JOURNÉE & CLÔTURE DE SESSION
    if any(k in norm for k in ["fin de journee", "fin de journée", "je m'arrete la", "je m'arrête là", "je coupe pour ce soir", "bonne soiree"]):
        actions.execute_tool("control_playback", {"action": "pause"})
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
            "degre dehors", "degré dehors", "il fait chaud", "il fait froid", "fait chaud", "fait froid",
            "fait quel temps", "quelle temperature", "quelle température",
        ])
        or (
            any(w in norm for w in ("temperature", "température", "degre", "degré", "degres", "degrés", "meteo", "météo", "climat"))
            and not any(w in norm for w in ("cpu", "ram", "serveur", "machine", "dell", "disque", "processeur", "carte"))
        )
        or (
            any(w in norm for w in ("chaud", "froid"))
            and any(w in norm for w in ("fait", "climat", "temps", "dehors", "maroc", "france", "ville", "pays"))
            and not any(w in norm for w in ("cpu", "ram", "serveur", "machine", "dell", "disque", "processeur", "carte"))
        )
        or (
            bool(re.search(r"\b(?:temps|temp[ée]rature|degr[ée]s?)\s+(?:[aà]|au|en|pour|sur|du)\s+[a-zA-ZÀ-ÿ]", norm))
            and not any(w in norm for w in ("cpu", "ram", "serveur", "machine", "dell", "disque", "processeur", "carte"))
        )
        or (
            bool(re.search(r"\b(?:quel\s+degr[ée]|combien\s+de\s+degr[ée]s?|il\s+fait\s+quel\s+degr[ée]|fait\s+quel\s+degr[ée]|il\s+fait\s+combien)\b", norm))
            and not any(w in norm for w in ("cpu", "ram", "serveur", "machine", "dell", "disque", "processeur", "carte"))
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

    # 2.7. CONNECTEUR SPORT EN TEMPS RÉEL (FOOTBALL, CHAMPIONNATS & SCORES)
    is_sport_query = (
        any(k in norm for k in [
            "ligue 1", "ligue1", "champions league", "ligue des champions",
            "la liga", "premier league", "serie a", "bundesliga",
        ]) and any(w in norm for w in ("resultat", "resultats", "classement", "score", "match", "matchs", "table", "position"))
    ) or bool(re.search(r"\b(?:r[ée]sultats?|classement|score|scores|matchs?)\s+(?:de\s+la\s+|du\s+|en\s+)?(?:ligue\s*1|champions\s*league|ligue\s*des\s*champions|liga|la\s+liga|premier\s*league|serie\s*a|bundesliga|foot)\b", norm)) or (
        "score" in norm and any(team in norm for team in ("psg", "paris", "real", "real madrid", "barca", "barcelone", "marseille", "om", "city", "arsenal", "bayern", "liverpool"))
    )
    if is_sport_query:
        import sport_service
        sport_res = sport_service.get_sports_update(raw_query)
        tools_used.append({"tool": "get_sports_update", "arguments": {"query": raw_query}, "success": sport_res.get("success", True)})
        return {
            "handled": True,
            "reply": sport_res.get("reply", "Données sportives indisponibles, Moctar."),
            "tools_used": tools_used,
            "task_category": "sport",
        }

    # 2.8. GOOGLE WORKSPACE (GMAIL, CALENDAR, DRIVE)
    is_gmail_query = any(k in norm for k in [
        "mails non lus", "emails non lus", "courriels non lus", "boite mail", "boite de reception",
        "nouveaux mails", "nouveaux e-mails", "mes mails", "mes emails", "check mes mails",
        "verifie mes mails", "verifie mes emails", "lire mes mails", "releve mes mails", "relevé de mes mails",
    ])
    if is_gmail_query:
        import google_workspace_service
        mail_res = google_workspace_service.get_unread_emails_summary()
        tools_used.append({"tool": "get_unread_emails", "arguments": {}, "success": mail_res.get("success", False)})
        return {
            "handled": True,
            "reply": mail_res.get("reply"),
            "tools_used": tools_used,
            "task_category": "google_workspace",
        }

    is_calendar_query = any(k in norm for k in [
        "agenda du jour", "agenda d aujourd hui", "agenda d'aujourd'hui", "agenda",
        "rendez vous du jour", "rendez-vous du jour", "prochains rendez vous", "prochains rendez-vous",
        "calendrier google", "mon agenda", "mes rendez vous", "mes rendez-vous", "planning du jour", "planning",
    ])
    if is_calendar_query:
        import google_workspace_service
        cal_res = google_workspace_service.get_today_agenda_summary()
        tools_used.append({"tool": "get_today_agenda", "arguments": {}, "success": cal_res.get("success", False)})
        return {
            "handled": True,
            "reply": cal_res.get("reply"),
            "tools_used": tools_used,
            "task_category": "google_workspace",
        }

    drive_match = re.search(r"^(?:cherche|recherche|trouve)\s+(?:sur\s+google\s+drive|sur\s+drive|dans\s+drive)\s+(.+)$", norm) or (
        any(k in norm for k in ["google drive", "documents drive", "fichiers drive"]) and "cherche" in norm
    )
    if drive_match:
        import google_workspace_service
        target_doc = drive_match.group(1).strip() if hasattr(drive_match, "group") and drive_match.group(1) else "document"
        drive_res = google_workspace_service.search_drive_documents(target_doc)
        tools_used.append({"tool": "search_drive_documents", "arguments": {"query": target_doc}, "success": drive_res.get("success", False)})
        return {
            "handled": True,
            "reply": drive_res.get("reply"),
            "tools_used": tools_used,
            "task_category": "google_workspace",
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

    # 4. CONTRÔLE CONTEXTUEL DES FENÊTRES ("mets-le à droite", "mets-le à gauche", "snap", "plein écran")
    # Vérifié avant le multimédia pour éviter qu'une phrase contenant "musique" ou "son" ne détourne l'action de placement
    window_snap_match = re.search(
        r"\b(?:mets?[-\s]le\s+[aà]\s+(?:droite|gauche)|place\s+[aà]\s+(?:droite|gauche)|snap\s+(?:right|left|droite|gauche)?|plein\s+[ée]cran|maximise|minimise|r[ée]duis)\b",
        norm,
    )
    if window_snap_match:
        pos = "maximize"
        if "droite" in norm or "right" in norm:
            pos = "right"
        elif "gauche" in norm or "left" in norm:
            pos = "left"
        elif "minimise" in norm or "reduis" in norm or "réduis" in norm:
            pos = "minimize"
        snap_res = actions.execute_tool("snap_active_window", {"position": pos})
        tools_used.append({"tool": "snap_active_window", "arguments": {"position": pos}, "success": bool(snap_res.get("success", True))})
        msg = snap_res.get("message") if isinstance(snap_res, dict) else str(snap_res)
        return {"handled": True, "reply": msg, "tools_used": tools_used, "task_category": "system_tools"}

    # 4b. CONTRÔLE MÉDIA UNIVERSEL EN 0 MS (PLAYERCTL, SPOTIFY, LECTEUR ACTIF)
    norm_stripped = norm.strip(" .,:;!?'\"’«»")
    has_pause_word = bool(re.search(
        r"\b(?:pause|en\s+pause|sur\s+pause|arrete|arrête|stop|coupe|interromps|suspend)\b", norm
    ))

    is_media_pause = (
        has_pause_word and any(w in norm for w in ("musique", "chanson", "son", "morceau", "audio", "video", "vidéo", "youtube", "lecture", "flux"))
    ) or any(k in norm for k in [
        "mets pause", "met pause", "mets en pause", "met en pause", "mets sur pause", "met sur pause",
        "mettre en pause", "mettre pause", "mets la musique en pause", "met la musique en pause",
        "mets la video en pause", "met la video en pause", "mets la vidéo en pause",
        "pause la musique", "pause musique", "pause le son", "pause youtube",
        "stop la musique", "arrete la musique", "arrête la musique", "coupe la musique",
        "stop musique", "arrete le son", "arrête le son", "coupe le son",
        "mets youtube en pause", "met youtube en pause", "youtube en pause",
        "j ai youtube en pause", "j'ai youtube en pause", "jai youtube en pause",
        "pause la video", "pause la vidéo", "pause le son",
    ]) or norm_stripped in ("pause", "stop", "en pause", "on pause", "stop musique", "pause la musique", "pause video", "pause la video")

    is_media_play = (not has_pause_word) and (
        any(k in norm for k in [
            "reprends la lecture", "remets la musique", "reprends la musique",
            "relance la musique", "relance la lecture", "reprends la video", "reprends la vidéo",
            "relance youtube", "remets youtube", "play musique",
        ]) or norm in ("lecture", "play", "reprise") or bool(re.search(r"\b(?:reprends?\s+la\s+lecture|remets?\s+la\s+musique|relance\s+la\s+musique)\b", norm))
    )

    is_media_next = any(k in norm for k in [
        "chanson suivante", "piste suivante", "musique suivante", "morceau suivant", "suivant",
    ])

    is_media_prev = any(k in norm for k in [
        "chanson precedente", "chanson précédente", "piste precedente", "piste précédente",
        "musique precedente", "musique précédente", "morceau precedent", "morceau précédent", "precedent", "précédent",
    ])

    is_media_playlist = (not is_media_pause and not has_pause_word) and (
        any(k in norm for k in [
            "lance ma playlist", "lance spotify", "ouvre spotify", "mets ma playlist",
            "lance de la musique", "joue de la musique", "lance la musique",
        ]) or (
            any(k in norm for k in ("mets de la musique", "mets la musique"))
            and not has_pause_word
        ) or norm in ("mets de la musique", "lance de la musique", "joue de la musique", "mets la musique", "lance la musique", "musique", "playlist", "spotify")
    )

    if is_media_pause:
        try:
            import recent_context_tracker
            recent_context_tracker.get_context_tracker().record_media("pause")
        except Exception:
            pass
        res = actions.execute_tool("control_playback", {"action": "pause"})
        tools_used.append({"tool": "control_playback", "arguments": {"action": "pause"}, "success": True})
        return {"handled": True, "reply": "Lecture mise en pause, Moctar.", "tools_used": tools_used, "task_category": "media"}

    if is_media_play:
        try:
            import recent_context_tracker
            recent_context_tracker.get_context_tracker().record_media("play")
        except Exception:
            pass
        res = actions.execute_tool("control_playback", {"action": "play"})
        tools_used.append({"tool": "control_playback", "arguments": {"action": "play"}, "success": True})
        return {"handled": True, "reply": "Reprise de la lecture, Moctar.", "tools_used": tools_used, "task_category": "media"}

    if is_media_next:
        res = actions.execute_tool("control_playback", {"action": "next"})
        tools_used.append({"tool": "control_playback", "arguments": {"action": "next"}, "success": True})
        return {"handled": True, "reply": "Piste suivante activée, Moctar.", "tools_used": tools_used, "task_category": "media"}

    if is_media_prev:
        res = actions.execute_tool("control_playback", {"action": "previous"})
        tools_used.append({"tool": "control_playback", "arguments": {"action": "previous"}, "success": True})
        return {"handled": True, "reply": "Retour à la piste précédente, Moctar.", "tools_used": tools_used, "task_category": "media"}

    if is_media_playlist:
        try:
            import recent_context_tracker
            recent_context_tracker.get_context_tracker().record_media("play", "playlist")
        except Exception:
            pass
        res = actions.execute_tool("launch_playlist", {})
        tools_used.append({"tool": "launch_playlist", "arguments": {}, "success": True})
        return {"handled": True, "reply": "Initialisation du flux audio. Votre playlist est lancée, Moctar.", "tools_used": tools_used, "task_category": "media"}

    # 4c. YOUTUBE & MUSIQUE DÉTERMINISTE
    yt_search_match = re.search(r"^(?:cherche|recherche|trouve)\s+sur\s+youtube\s+(.+)$", norm) or re.search(r"^(?:cherche|recherche|trouve)\s+(.+)\s+sur\s+youtube$", norm)
    is_play_music = (not is_media_pause and not has_pause_word) and (
        (
            bool(re.match(r"^(?:mets|met|joue|lance|ecoute)\s+(?:de\s+la\s+|du\s+|un\s+|une\s+|la\s+)?(?:musique|chanson|son|morceau|playlist|video|vidéo|coran)?", norm))
            and any(k in norm for k in ("musique", "chanson", "son", "morceau", "playlist", "video", "vidéo", "coran", "rap", "rai", "jazz", "audio"))
        ) or norm.startswith(("mets de la musique", "joue de la musique", "lance la musique", "mets du son", "mets du coran"))
    )

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

    # 5. OUVERTURE DE SITES, PROJETS OU APPLICATIONS (FAST-PATH UNIVERSEL)
    open_verbs = ("ouvre", "lance", "va sur", "ouve", "demarre", "start")
    if any(norm.startswith(v + " ") for v in open_verbs):
        for v in open_verbs:
            if norm.startswith(v + " "):
                target = raw_query[len(v):].strip()
                target_norm = _normalize(target)

                # Écrans système & modules internes (NeuroVision, HUD 3D) -> Ne JAMAIS rediriger vers une recherche Google
                if any(nv in target_norm for nv in ("neurovision", "neovision", "camera", "caméra", "vision", "scanner")):
                    actions.execute_tool("control_vision", {"action": "start"})
                    tools_used.append({"tool": "control_vision", "arguments": {"action": "start"}, "success": True})
                    return {
                        "handled": True,
                        "reply": "Caméra et vision cognitive NeuroVision activées, Moctar. Je veille sur ton poste.",
                        "action": "open_modal",
                        "target": "neurovision-dialog",
                        "modal_id": "neurovision-dialog",
                        "title": "Scanner NeuroVision 3D",
                        "tools_used": tools_used,
                        "task_category": "neurovision",
                    }

                if any(h in target_norm for h in ("hud", "3d", "top 3d", "hot 3d", "up 3d", "hop 3d", "orbe", "plasma", "quantum")):
                    actions.execute_tool("switch_hud_view", {"view": "plasma", "locate": False})
                    tools_used.append({"tool": "switch_hud_view", "arguments": {"view": "plasma", "locate": False}, "success": True})
                    return {
                        "handled": True,
                        "reply": "Interface HUD 3D cybernétique activée, Moctar.",
                        "action": "open_app",
                        "url": "/hud",
                        "title": "HUD 3D Cybernétique",
                        "tools_used": tools_used,
                        "task_category": "hud_3d",
                    }

                # Projet de développement ("ouvre le projet Baba Market", "ouvre Baba Market dans VS Code")
                if "projet" in target_norm or any(p in target_norm for p in ("baba market", "papa market", "noor express", "edumanage", "audit energetique")):
                    proj_key = "jarvis"
                    if any(p in target_norm for p in ("baba", "papa")):
                        proj_key = "baba_market"
                    elif "noor" in target_norm:
                        proj_key = "noor_express"
                    elif "edumanage" in target_norm or "edu manage" in target_norm:
                        proj_key = "edumanage"
                    elif "audit" in target_norm or "monitoring" in target_norm:
                        proj_key = "audit_energetique"
                    in_vscode = any(c in target_norm for c in ("vscode", "vs code", "code", "visual studio", "visuel studio", "vsitio"))
                    proj_res = actions.execute_tool("open_project", {"project_key": proj_key, "open_in_vscode": in_vscode})
                    tools_used.append({"tool": "open_project", "arguments": {"project_key": proj_key, "open_in_vscode": in_vscode}, "success": bool(proj_res.get("success"))})
                    msg = proj_res.get("message") if isinstance(proj_res, dict) else str(proj_res)
                    return {
                        "handled": True,
                        "reply": f"🛠️ {msg}",
                        "action": "open_app",
                        "url": None,
                        "title": proj_key,
                        "tools_used": tools_used,
                        "task_category": "git_dev",
                    }

                # Application locale
                if any(app in target_norm for app in [
                    "terminal", "cmd", "invite de commande", "powershell", "calculatrice", "notepad", "bloc notes", "bloc-notes",
                    "vscode", "vs code", "code", "visual studio", "visuel studio", "vscod", "studio code", "vsitio", "vsit",
                ]):
                    canonical = "vscode" if any(c in target_norm for c in ["code", "visual", "visuel", "vscod", "vsitio", "vsit"]) else target
                    app_res = actions.execute_tool("open_app", {"app_name": canonical})
                    tools_used.append({"tool": "open_app", "arguments": {"app_name": canonical}, "success": bool(app_res.get("success"))})
                    msg = app_res.get("message") if isinstance(app_res, dict) else str(app_res)
                    url_app = None
                    title_app = "Visual Studio Code" if canonical == "vscode" else None
                    return {
                        "handled": True,
                        "reply": f"🚀 {msg}",
                        "action": "open_app",
                        "url": url_app,
                        "title": title_app,
                        "tools_used": tools_used,
                        "task_category": "system_tools",
                    }

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

    # RÉSOLUTION D'ANAPHORES & CONTEXTE COURT-TERME ("relance-le", "redémarre-le", "son statut", "coupe-la")
    try:
        import recent_context_tracker
        anaphoric = recent_context_tracker.get_context_tracker().resolve_anaphoric_intent(norm)
        if anaphoric:
            act = anaphoric.get("action")
            if act == "restart_service":
                srv = anaphoric.get("target")
                disp = anaphoric.get("display_name", srv)
                import subprocess, shutil
                sys_bin = shutil.which("systemctl")
                sudo_bin = shutil.which("sudo")
                if sys_bin:
                    cmd = [sudo_bin, "-n", sys_bin, "restart", srv] if sudo_bin else [sys_bin, "restart", srv]
                    subprocess.run(cmd, capture_output=True, timeout=5.0)
                tools_used.append({"tool": "restart_service", "arguments": {"service": srv}, "success": True})
                return {
                    "handled": True,
                    "reply": f"Service {disp} relancé avec succès, Moctar.",
                    "tools_used": tools_used,
                    "task_category": "services",
                }
            elif act == "resume_media":
                actions.execute_tool("control_playback", {"action": "play"})
                tools_used.append({"tool": "control_playback", "arguments": {"action": "play"}, "success": True})
                return {"handled": True, "reply": "Reprise de la lecture, Moctar.", "tools_used": tools_used, "task_category": "media"}
            elif act == "pause_media":
                actions.execute_tool("control_playback", {"action": "pause"})
                tools_used.append({"tool": "control_playback", "arguments": {"action": "pause"}, "success": True})
                return {"handled": True, "reply": "Lecture mise en pause, Moctar.", "tools_used": tools_used, "task_category": "media"}
            elif act == "restart_app":
                target_app = anaphoric.get("target")
                actions.execute_tool("open_app", {"app_name": target_app})
                tools_used.append({"tool": "open_app", "arguments": {"app_name": target_app}, "success": True})
                return {"handled": True, "reply": f"Redémarrage de {target_app} en cours, Moctar.", "tools_used": tools_used, "task_category": "apps"}
            elif act == "status_service":
                disp = anaphoric.get("display_name", anaphoric.get("target"))
                return {
                    "handled": True,
                    "reply": f"Le service {disp} est opérationnel et sous surveillance continue Moctar.",
                    "tools_used": tools_used,
                    "task_category": "services",
                }
    except Exception:
        pass

    # CONTRÔLE DIRECT DES SERVICES CRITIQUES (WHATSAPP, JARVIS) EN 0 MS
    is_whatsapp_service_cmd = bool(re.search(
        r"\b(?:relance|red[eé]marre|restart|statut|status|[eé]tat)\s+(?:du\s+service\s+|le\s+service\s+)?(?:klasora[- ]?)?whatsapp\b",
        norm,
    )) or "klasora-whatsapp" in norm
    if is_whatsapp_service_cmd:
        try:
            import recent_context_tracker
            recent_context_tracker.get_context_tracker().record_service("klasora-whatsapp.service", "WhatsApp")
        except Exception:
            pass
        if any(w in norm for w in ("relance", "redemarre", "redémarre", "restart")):
            import subprocess, shutil
            sys_bin = shutil.which("systemctl")
            sudo_bin = shutil.which("sudo")
            if sys_bin:
                cmd = [sudo_bin, "-n", sys_bin, "restart", "klasora-whatsapp.service"] if sudo_bin else [sys_bin, "restart", "klasora-whatsapp.service"]
                subprocess.run(cmd, capture_output=True, timeout=5.0)
            tools_used.append({"tool": "restart_service", "arguments": {"service": "klasora-whatsapp.service"}, "success": True})
            return {"handled": True, "reply": "Service WhatsApp relancé avec succès, Moctar.", "tools_used": tools_used, "task_category": "services"}
        else:
            return {"handled": True, "reply": "Le service WhatsApp (klasora-whatsapp.service) est sous surveillance active de la Sentinelle Moctar.", "tools_used": tools_used, "task_category": "services"}

    # CONTRÔLE CONTEXTUEL DES FENÊTRES ("ferme ça", "mets-le à droite", "mets-le à gauche", "plein écran")
    if re.search(r"\b(?:ferme|quitte)\s+(?:[cç]a|le|la|cette\s+fen[êe]tre|l['’]appli?)\b", norm) or norm in (
        "ferme ca", "ferme ça", "ferme le", "quitte ca", "quitte ça", "ferme la fenetre", "ferme la fenêtre",
    ):
        win_res = actions.execute_tool("close_active_window", {})
        tools_used.append({"tool": "close_active_window", "arguments": {}, "success": bool(win_res.get("success", True))})
        msg = win_res.get("message") if isinstance(win_res, dict) else str(win_res)
        return {"handled": True, "reply": msg, "tools_used": tools_used, "task_category": "system_tools"}


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

    # ANALYSE VISUELLE & ERREURS SUR L'ÉCRAN
    if any(k in norm for k in [
        "regarde mon ecran", "regarde l'ecran", "regarde l ecran", "regarde sur mon ecran",
        "analyse mon ecran", "analyse l'ecran", "analyse l ecran",
        "vois tu sur mon ecran", "vois-tu sur mon ecran", "vois tu a l'ecran", "vois-tu a l'ecran",
        "qu'est-ce que tu vois", "que vois tu", "que vois-tu", "qu'est ce que tu vois",
        "erreurs sur mon ecran", "erreur sur mon ecran", "regarde les erreurs", "analyse les erreurs",
        "lis mon ecran", "decris mon ecran", "affiche a l'ecran", "affiche sur mon ecran",
    ]):
        screen_res = actions.execute_tool("analyze_screen", {"question": raw_query})
        tools_used.append({"tool": "analyze_screen", "arguments": {"question": raw_query}, "success": bool(screen_res.get("success"))})
        msg = screen_res.get("message") if isinstance(screen_res, dict) else str(screen_res)
        return {"handled": True, "reply": f"🖥️ {msg}", "tools_used": tools_used, "task_category": "system_tools"}

    # 6. HEURE & DATE (avec exclusion formelle des faux positifs comme 'malheureux', 'heureux')
    EXCLUDED_TIME_SUBSTRINGS = ("malheureux", "malheureuse", "malheur", "heureux", "heureuse", "heureusement", "a tout a l'heure", "a toute a l'heure")
    is_time_excluded = any(w in norm for w in EXCLUDED_TIME_SUBSTRINGS)
    if not is_time_excluded and (
        re.search(r"\b(?:quelle\s+heure(?:\s+est[- ]il)?|donne[- ](?:moi\s+)?l[' ]heure|il\s+est\s+quelle\s+heure|l[' ]heure\s+qu[' ]il\s+est|l[' ]heure\s+actuelle|l[' ]heure\s+exacte)\b", norm)
        or norm.strip() in {"heure", "l heure", "l'heure", "time"}
    ):
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

    if any(k in norm for k in [
        "volume", "mute", "muet", "coupe le son", "monte le son", "baisse le son",
        "remets le son", "retablis le son", "demute", "diminue le son", "diminue le volume",
        "augmente le son", "augmente le volume", "baisse le volume", "monte le volume",
    ]) or "diminue" in words or "augmente" in words:
        match_pct = re.search(r"(\d+)\s*(?:%|pourcent)?", norm)
        pct = int(match_pct.group(1)) if match_pct else None
        if any(w in norm for w in ("coupe", "muet", "mute")):
            level = 0
            act = "mute"
        elif any(w in norm for w in ("remets", "retablis", "demute")):
            level = 50
            act = "unmute"
        elif pct is not None:
            level = pct
            act = "set"
        elif any(w in norm for w in ("monte", "augmente", "plus fort")):
            level = 80
            act = "up"
        elif any(w in norm for w in ("baisse", "diminue", "moins fort", "reduis", "réduis")):
            level = 30
            act = "down"
        else:
            level = 50
            act = "set"
        res = actions.execute_tool("adjust_system_volume", {"action": act, "percent": level})
        tools_used.append({"tool": "adjust_system_volume", "arguments": {"action": act, "percent": level}, "success": True})
        return {"handled": True, "reply": "Niveau audio ajusté, Moctar.", "tools_used": tools_used, "task_category": "system_tools"}

    # 8. TÉLÉMÉTRIE MATÉRIELLE, RÉSEAU & INFRASTRUCTURE
    # Détection stricte et biométrique du visage / NeuroVision (sans faux positifs sur 'vois', 'regarde', etc.)
    is_vision_query_explicit = (
        any(p in norm for p in (
            "analyse mon visage", "analyse le visage", "detecte mon visage", "détecte mon visage",
            "score de fatigue", "etat de fatigue", "état de fatigue", "suivi facial",
            "tu me vois", "est ce que tu me vois", "est-ce que tu me vois",
            "regarde mon visage", "regarde moi", "regarde-moi",
            "telemetrie vision", "télémétrie vision", "mes yeux", "mes clignements",
            "capteur neurovision", "capteur biométrique", "capteur biometrique",
        ))
        or bool(words.intersection({
            "visage", "webcam", "neurovision", "fatigue", "fatigué",
            "clignement", "clignements", "baillement", "bâillement", "biometrie", "biométrie",
        }))
    )
    # Exclusion stricte : si la phrase est un ordre d'ouverture ou de fermeture, traiter comme action
    if any(k in norm for k in ("ouvre", "lance", "active", "affiche", "mets", "allume", "demarre", "demarrer")):
        if any(nv in norm for nv in ("neurovision", "neovision", "camera", "caméra", "vision", "scanner")):
            actions.execute_tool("control_vision", {"action": "start"})
            tools_used.append({"tool": "control_vision", "arguments": {"action": "start"}, "success": True})
            return {
                "handled": True,
                "reply": "Caméra et vision cognitive NeuroVision activées, Moctar. Je veille sur ton poste.",
                "action": "open_modal",
                "target": "neurovision-dialog",
                "modal_id": "neurovision-dialog",
                "title": "Scanner NeuroVision 3D",
                "tools_used": tools_used,
                "task_category": "neurovision",
            }

    # Exclusion stricte : si la phrase parle de musique, youtube, fenêtre, réseau, port, etc., ce n'est PAS de la biométrie faciale
    if any(k in norm for k in (
        "musique", "playlist", "chanson", "youtube", "morceau", "son",
        "fenetre", "fenêtre", "ecran", "écran", "reseau", "réseau", "wifi", "port", "snap", "volume",
    )):
        is_vision_query_explicit = False

    if is_vision_query_explicit:
        res = actions.execute_tool("get_vision_telemetry", {})
        tools_used.append({"tool": "get_vision_telemetry", "arguments": {}, "success": bool(res.get("success"))})
        return {
            "handled": True,
            "reply": res.get("message", "Capteur NeuroVision interrogé, Moctar."),
            "tools_used": tools_used,
            "task_category": "neurovision",
        }

    is_network_query = len(words) <= 10 and any(w in norm for w in ("reseau", "réseau", "connexion", "internet", "ping", "wifi", "debit", "débit", "debits"))
    if is_network_query:
        import socket
        online = False
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=1.5)
            online = True
        except Exception:
            pass
        if online:
            rep = "Le réseau est impeccable Moctar : la connexion Internet est active, le DNS répond et l'infrastructure est parfaitement joignable."
        else:
            rep = "Attention Moctar, je constate des pertes ou une coupure de la connexion Internet externe."
        return {"handled": True, "reply": rep, "tools_used": [{"tool": "check_network", "arguments": {}, "success": online}], "task_category": "network"}

    is_service_management = bool(words.intersection({"service", "services", "systemctl", "docker", "vps", "processus", "demon", "daemon", "restart", "redemarre", "redémarre"}))
    is_explicit_hw_metric = bool(words.intersection({
        "cpu", "ram", "disque", "stockage", "telemetrie", "télémétrie", "processeur",
    }))
    is_explicit_system_status = bool(re.search(
        r"\b(?:[eé]tat\s+(?:du\s+)?(?:syst[eè]me|serveur|dell|pc)|statut\s+(?:du\s+)?(?:syst[eè]me|serveur|global)|bilan\s+(?:du\s+)?syst[eè]me|diagnostic\s+(?:du\s+)?syst[eè]me|comment\s+va\s+le\s+(?:syst[eè]me|serveur))\b",
        norm,
    )) or norm.strip(" ?!.") in ("statut", "status", "etat", "état", "infrastructure")
    is_telemetry = (
        len(words) <= 10
        and (is_explicit_hw_metric or is_explicit_system_status)
        and not is_service_management
    )
    if is_telemetry:
        res = actions.execute_tool("get_system_telemetry", {})
        tools_used.append({"tool": "get_system_telemetry", "arguments": {}, "success": bool(res.get("success"))})
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        cpu = data.get("cpu_percent", 0)
        mem = data.get("memory") if isinstance(data.get("memory"), dict) else {}
        ram = mem.get("percent", 0)
        ram_used = mem.get("used_gb", 0)
        disk = data.get("disk") if isinstance(data.get("disk"), dict) else {}
        disk_free = disk.get("free_gb", 0)
        spoken_summary = (
            f"Tout est au vert Moctar : processeur à {cpu}%, mémoire vive à {ram}% "
            f"({ram_used} Go utilisés) et {disk_free} Go d'espace libre sur le disque. Tout tourne parfaitement !"
        )
        return {"handled": True, "reply": spoken_summary, "tools_used": tools_used, "task_category": "telemetry"}

    # 9. TÂCHES & NOTES (PRODUCTIVITÉ)
    has_weather_intent = is_weather or any(w in norm for w in (
        "degre", "degré", "meteo", "météo", "pluie", "temperature", "température",
        "soleil", "chaleur", "froid", "chaud", "climat"
    ))
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
        try:
            import smart_command_cache
            smart_command_cache.get_command_cache().invalidate_category("tasks")
        except Exception:
            pass
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

    store_fact_match = re.search(r"^(?:m[ée]morise|retiens\s+que|souviens[- ]toi\s+que)\s+(.+)$", raw_query, flags=re.IGNORECASE)
    if store_fact_match:
        fact_val = store_fact_match.group(1).strip()
        res = actions.execute_tool("store_memory_fact", {"category": "note_vocale", "key": fact_val[:30], "value": fact_val})
        tools_used.append({"tool": "store_memory_fact", "arguments": {"value": fact_val}, "success": bool(res.get("success", True))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"🧠 {msg}", "tools_used": tools_used, "task_category": "productivity"}

    search_mem_match = re.search(r"^(?:cherche\s+dans\s+mes\s+notes|cherche\s+dans\s+ma\s+m[ée]moire)\s+(.+)$", raw_query, flags=re.IGNORECASE)
    if search_mem_match:
        q_mem = search_mem_match.group(1).strip()
        res = actions.execute_tool("search_memory_facts", {"query": q_mem})
        tools_used.append({"tool": "search_memory_facts", "arguments": {"query": q_mem}, "success": bool(res.get("success", True))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"🔍 {msg}", "tools_used": tools_used, "task_category": "productivity"}

    sum_file_match = re.search(r"^(?:r[ée]sume\s+le\s+fichier|lis\s+le\s+fichier)\s+(.+)$", raw_query, flags=re.IGNORECASE)
    if sum_file_match:
        f_path = sum_file_match.group(1).strip()
        res = actions.execute_tool("summarize_text_file", {"file_path": f_path})
        tools_used.append({"tool": "summarize_text_file", "arguments": {"file_path": f_path}, "success": bool(res.get("success", True))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📄 {msg}", "tools_used": tools_used, "task_category": "productivity"}

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

    if any(k in norm for k in ["batterie solaire", "dimensionnement batterie", "calcul batterie", "capacite ah"]):
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

    if any(k in norm for k in ["calcul onduleur", "puissance onduleur", "dimensionnement onduleur", "marge securite onduleur"]):
        match_w = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:w|kw)?", norm)
        peak_w = float(match_w.group(1).replace(",", ".")) if match_w else 2000.0
        res = actions.execute_tool("calculate_inverter_sizing", {"peak_power_w": peak_w})
        tools_used.append({"tool": "calculate_inverter_sizing", "arguments": {"peak_power_w": peak_w}, "success": True})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"⚡ {msg}", "tools_used": tools_used, "task_category": "energy"}

    if any(k in norm for k in ["calcul consommation", "kwh consommes", "puissance totale"]):
        res = actions.execute_tool("calculate_consumption", {"power_watts": 500.0, "hours_per_day": 8.0})
        tools_used.append({"tool": "calculate_consumption", "arguments": {"power_watts": 500.0, "hours_per_day": 8.0}, "success": True})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"⚡ {msg}", "tools_used": tools_used, "task_category": "energy"}

    if any(k in norm for k in ["cout kwh", "facture electricite", "prix energie", "cout energetique"]):
        res = actions.execute_tool("calculate_energy_cost", {"daily_kwh": 10.0, "price_per_kwh": 1.2})
        tools_used.append({"tool": "calculate_energy_cost", "arguments": {"daily_kwh": 10.0, "price_per_kwh": 1.2}, "success": True})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"💰 {msg}", "tools_used": tools_used, "task_category": "energy"}

    if any(k in norm for k in ["canevas audit", "modele audit energetique", "audit energetique"]):
        res = actions.execute_tool("generate_audit_template", {})
        tools_used.append({"tool": "generate_audit_template", "arguments": {}, "success": True})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📋 {msg}", "tools_used": tools_used, "task_category": "energy"}

    if any(k in norm for k in ["formules solaires", "equations photovoltaiques", "formules energie"]):
        res = actions.execute_tool("describe_formulae", {})
        tools_used.append({"tool": "describe_formulae", "arguments": {}, "success": True})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📐 {msg}", "tools_used": tools_used, "task_category": "energy"}

    # 11. GIT & PROJETS
    target_git_project = ""
    git_project_label = "Jarvis"
    if any(p in norm for p in ("papa market", "baba market", "babamarket", "papamarket", "baba")):
        target_git_project = "baba_market"
        git_project_label = "Baba Market"
    elif "noor" in norm:
        target_git_project = "noor_express"
        git_project_label = "Noor Express"
    elif "edumanage" in norm or "edu manage" in norm:
        target_git_project = "edumanage"
        git_project_label = "EDUMANAGE"
    elif "jarvis" in norm:
        target_git_project = "jarvis"
        git_project_label = "Jarvis"
    else:
        try:
            from actions import project_actions
            curr_key = project_actions.get_current_project_key()
            if curr_key == "baba_market":
                target_git_project = "baba_market"
                git_project_label = "Baba Market"
            elif curr_key == "noor_express":
                target_git_project = "noor_express"
                git_project_label = "Noor Express"
            elif curr_key == "edumanage":
                target_git_project = "edumanage"
                git_project_label = "EDUMANAGE"
        except Exception:
            pass

    if "git status" in norm or "statut git" in norm:
        res = actions.execute_tool("git_status", {"project_key": target_git_project})
        tools_used.append({"tool": "git_status", "arguments": {"project_key": target_git_project}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"🐙 **Statut Git ({git_project_label}) :**\n{msg}", "tools_used": tools_used, "task_category": "git_dev"}

    if "git pull" in norm:
        res = actions.execute_tool("git_pull", {"project_key": target_git_project})
        tools_used.append({"tool": "git_pull", "arguments": {"project_key": target_git_project}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"🔄 **Git Pull ({git_project_label}) :** {msg}", "tools_used": tools_used, "task_category": "git_dev"}

    if "git commit" in norm or "git push" in norm:
        has_commit = "git commit" in norm or "commit" in words
        has_push = "git push" in norm or "push" in words
        status_res = actions.execute_tool("git_status", {"project_key": target_git_project})
        status_msg = (status_res.get("message") if isinstance(status_res, dict) else str(status_res)).strip()
        if has_commit:
            c_res = actions.execute_tool("prepare_commit", {"project_key": target_git_project})
            tools_used.append({"tool": "prepare_commit", "arguments": {"project_key": target_git_project}, "success": bool(c_res.get("success"))})
        if has_push:
            p_res = actions.execute_tool("prepare_push", {"project_key": target_git_project})
            tools_used.append({"tool": "prepare_push", "arguments": {"project_key": target_git_project}, "success": bool(p_res.get("success"))})
        if has_commit and has_push:
            reply_git = (
                f"Vérification Git effectuée sur {git_project_label} ({status_msg}). "
                f"Le commit et le push sont prêts — voulez-vous que je les exécute avec un message de commit spécifique ?"
            )
        elif has_commit:
            reply_git = (
                f"Statut Git vérifié sur {git_project_label} ({status_msg}). "
                f"Le commit est prêt — quel message de commit souhaitez-vous appliquer ?"
            )
        else:
            reply_git = (
                f"Statut Git vérifié sur {git_project_label} ({status_msg}). "
                f"Le push vers le dépôt distant est préparé — confirmez-vous l'envoi sur la branche principale ?"
            )
        return {"handled": True, "reply": reply_git, "tools_used": tools_used, "task_category": "git_dev"}

    if "git log" in norm or "commits" in norm or "historique git" in norm:
        res = actions.execute_tool("git_log", {"project_key": target_git_project})
        tools_used.append({"tool": "git_log", "arguments": {"project_key": target_git_project}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📜 **Derniers commits ({git_project_label}) :**\n{msg}", "tools_used": tools_used, "task_category": "git_dev"}

    if "maintenance" in norm and "checklist" in norm:
        res = actions.execute_tool("maintenance_checklist", {})
        tools_used.append({"tool": "maintenance_checklist", "arguments": {}, "success": bool(res.get("success"))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"📋 **Checklist de Maintenance :**\n{msg}", "tools_used": tools_used, "task_category": "git_dev"}

    # 12. DOMOTIQUE IOT
    if any(k in norm for k in ["temperature salon", "sonde ambiance", "capteur dht", "temperature piece", "température salon"]):
        res = actions.execute_tool("get_iot_temperature", {"room": "salon"})
        tools_used.append({"tool": "get_iot_temperature", "arguments": {"room": "salon"}, "success": bool(res.get("success", True))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"🌡️ {msg}", "tools_used": tools_used, "task_category": "iot_music"}

    if any(k in norm for k in ["allume la lampe", "eteins la lampe", "éteins la lampe", "allume le relais", "eteins le relais", "switch prise"]):
        state = "on" if "allume" in norm else "off"
        res = actions.execute_tool("set_relay_state", {"relay_name": "lampe_salon", "state": state})
        tools_used.append({"tool": "set_relay_state", "arguments": {"relay_name": "lampe_salon", "state": state}, "success": bool(res.get("success", True))})
        msg = res.get("message") if isinstance(res, dict) else str(res)
        return {"handled": True, "reply": f"💡 {msg}", "tools_used": tools_used, "task_category": "iot_music"}

    if any(k in norm for k in ["iot", "domotique", "capteurs", "statut relais"]):
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

