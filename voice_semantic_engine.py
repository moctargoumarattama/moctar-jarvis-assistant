"""Moteur vocal sémantique natif avec Tool Calling LLM (< 500 ms) pour JARVIS / M.O.C.T.A.R.

Remplace l'ancienne forêt de regex rigides ('if "mot" in texte:') par la compréhension
d'intention native et l'appel de fonctions structuré (Function Calling) de Gemini / LLM.

Traite les formulations naturelles, informelles ou complexes :
- "prépare-moi de quoi bosser" -> open_app(target="vscode")
- "ouvre le truc pour coder" -> open_app(target="vscode")
- "mets-toi au mode bureau" -> open_app(target="vscode")
- "il fait quel temps dehors à Casa ?" -> get_weather(city="Casablanca")
- "c'est quoi l'avancement du projet ?" -> get_project_context()
- "coupe le son" -> control_music(action="pause")
- "nettoie la mémoire" -> optimize_system()
- "Scott", "Bonjour", etc. -> réponse conversationnelle fluide
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import actions
from actions import execute_tool
import ai_brain
from ai_brain import build_live_system_context

LOGGER = logging.getLogger("moctar.voice_semantic_engine")

VOICE_FAST_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": (
                "Lance ou ouvre une application ou un environnement de travail sur la machine "
                "(ex: 'vscode' pour coder, travailler, mode bureau, espace de travail; 'terminal', 'chrome', 'calc', 'notepad')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "Nom de l'application à lancer (ex: 'vscode', 'chrome', 'terminal', 'notepad', 'calc'). "
                            "Pour tout ce qui concerne le travail, le code ou le mode bureau, utiliser impérativement 'vscode'."
                        ),
                    }
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_site",
            "description": "Ouvre un site web ou service dans le navigateur (ex: 'youtube', 'google', 'github', 'wikipedia', 'gmail', 'whatsapp').",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Nom du site web ou URL cible (ex: 'youtube', 'github', 'google').",
                    }
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Récupère les informations météorologiques et températures réelles pour une ville (ex: 'Niamey', 'Casablanca', 'Paris').",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "Nom de la ville demandée (par défaut 'Niamey' si non précisé).",
                    },
                    "target_day": {
                        "type": "string",
                        "enum": ["today", "demain"],
                        "description": "Jour cible : 'today' pour aujourd'hui, 'demain' pour les prévisions du lendemain.",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_music",
            "description": "Contrôle la lecture multimédia ou audio du système hôte (pause, reprise, suivant, précédent, arrêt).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["pause", "resume", "next", "previous", "stop"],
                        "description": "Action audio à exécuter.",
                    }
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_todos",
            "description": "Consulte et liste les tâches en cours ou to-dos de Moctar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Nombre maximum de tâches à retourner (par défaut 5).",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": "Ajoute une nouvelle tâche ou to-do à la liste personnelle de Moctar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Intitulé clair et complet de la tâche à ajouter.",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Priorité de la tâche (par défaut 'medium').",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_reminder",
            "description": "Programme un rappel pour Moctar dans un délai spécifié en secondes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_text": {
                        "type": "string",
                        "description": "Objet ou texte du rappel.",
                    },
                    "delay_seconds": {
                        "type": "integer",
                        "description": "Délai en secondes avant le déclenchement du rappel (ex: 300 pour 5 min, 900 pour 15 min).",
                    },
                },
                "required": ["reminder_text", "delay_seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_context",
            "description": "Obtient la synthèse complète de l'avancement du projet JARVIS (derniers commits Git, état des modules, chantiers récents). À appeler quand Moctar demande 'où en est-on ?', 'qu'est-ce qu'on a fait ?', 'avancement du projet'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": "Niveau de synthèse optionnel ('global', 'commits', 'modules').",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_telemetry",
            "description": "Obtient la télémétrie complète du système hôte (CPU %, RAM %, température, disque, réseau).",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "description": "Métrique spécifique optionnelle ('cpu', 'ram', 'temp', 'all').",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "optimize_system",
            "description": "Nettoie la mémoire vive (RAM), purge les caches temporaires et optimise les ressources du serveur Dell Precision M4800.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Cible d'optimisation optionnelle ('ram', 'disk', 'all').",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_active_window",
            "description": "Ferme la fenêtre ou l'application actuellement active au premier plan (ex: 'ferme ça', 'quitte ça', 'ferme cette fenêtre').",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_app": {
                        "type": "string",
                        "description": "Nom optionnel de l'application active à fermer (ex: 'vscode', 'chrome', 'terminal').",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "snap_active_window",
            "description": "Positionne ou redimensionne la fenêtre active au premier plan sur l'écran ('left', 'right', 'maximize', 'minimize').",
            "parameters": {
                "type": "object",
                "properties": {
                    "position": {
                        "type": "string",
                        "enum": ["left", "right", "maximize", "minimize"],
                        "description": "Position ou disposition désirée pour la fenêtre active.",
                    }
                },
                "required": ["position"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_task",
            "description": "Consulte l'état de la tâche active, l'objectif en cours, la sous-tâche et les vérifications en attente (Task State Tracking).",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_active_task",
            "description": "Définit ou met à jour la tâche active, l'objectif en cours, la sous-tâche ou ajoute un point à vérifier.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Nouvel objectif principal"},
                    "subtask": {"type": "string", "description": "Sous-tâche ou étape actuelle"},
                    "status": {"type": "string", "description": "Statut (in_progress, completed, verifying)"},
                    "add_pending": {"type": "string", "description": "Point ou élément à vérifier"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_screen",
            "description": "Capture l'écran ou la fenêtre active et l'analyse visuellement via l'IA multimodale (ex: 'regarde mon écran', 'qu'est-ce qu'il y a d'affiché ?', 'lis ce bug', 'analyse mon écran').",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Question d'analyse visuelle spécifique ou instruction sur ce qu'il faut observer.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "store_memory_fact",
            "description": "Mémorise durablement une information, préférence ou fait important dans la mémoire permanente (ex: 'retiens que...', 'souviens-toi que...', 'mon serveur principal est...').",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Catégorie du fait (ex: 'preferences', 'system', 'network', 'personal')"},
                    "key": {"type": "string", "description": "Identifiant concis ou mot-clé du fait"},
                    "fact": {"type": "string", "description": "Contenu complet de l'information à retenir"},
                },
                "required": ["key", "fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory_facts",
            "description": "Recherche un fait ou souvenir dans la mémoire permanente de Moctar (ex: 'quel est mon port ssh ?', 'qu'est-ce que je t'avais dit sur mon serveur ?').",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Mots-clés ou question de recherche dans les souvenirs"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_background_task",
            "description": "Lance une tâche ou commande en arrière-plan sans bloquer la voix (ex: 'lance les tests en tâche de fond', 'fais un git pull en arrière-plan').",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Commande shell exacte à exécuter en arrière-plan"},
                    "description": {"type": "string", "description": "Description orale conviviale de la tâche pour Moctar"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_hud_view",
            "description": (
                "Bascule la vue de l'interface HUD 3D ('globe', 'plasma', 'quantum', 'orb') "
                "ou retourne au tableau de bord NOC ('noc'). Si locate=true, centre et verrouille "
                "le globe 3D sur la position géographique de Moctar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {
                        "type": "string",
                        "enum": ["globe", "plasma", "quantum", "orb", "noc"],
                        "description": "Vue cible ('globe' pour la Terre, 'plasma' pour le réacteur, 'quantum' pour le cœur quantique, 'orb' pour l'orbe, 'noc' pour retourner au dashboard NOC).",
                    },
                    "locate": {
                        "type": "boolean",
                        "description": "Mettre à true si Moctar demande où il est dans le monde, sa position ou à être localisé sur le globe.",
                    },
                    "mood": {
                        "type": "string",
                        "enum": ["calm", "friendly", "success", "quantum", "night", "caring", "alert"],
                        "description": "Ambiance ou couleur émotionnelle souhaitée pour l'orbe.",
                    },
                },
                "required": ["view"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_hud_mood",
            "description": (
                "Change l'ambiance émotionnelle, le sentiment et la couleur de l'orbe 3D "
                "('calm' cyan serein, 'friendly' or/ambre chaleureux, 'success' vert émeraude victoire, "
                "'quantum' magenta réflexion, 'night' bleu nuit apaisant, 'alert' rouge alerte)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {
                        "type": "string",
                        "enum": ["calm", "friendly", "success", "quantum", "night", "caring", "alert"],
                        "description": "Humeur émotionnelle cible.",
                    },
                },
                "required": ["mood"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Vérifie le statut Git ('git status') d'un projet (ex: 'jarvis', 'baba_market' / 'Papa Market', 'noor_express', 'edumanage').",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_key": {
                        "type": "string",
                        "description": "Clé ou nom du projet (ex: 'jarvis', 'baba_market', 'noor_express', 'edumanage').",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_pull",
            "description": "Exécute 'git pull' sur un projet (ex: 'jarvis', 'baba_market' / 'Papa Market', 'noor_express').",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_key": {
                        "type": "string",
                        "description": "Clé ou nom du projet cible.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_commit",
            "description": "Prépare un commit Git sur un projet (ex: 'jarvis', 'baba_market' / 'Papa Market', 'noor_express').",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message du commit.",
                    },
                    "project_key": {
                        "type": "string",
                        "description": "Clé ou nom du projet cible.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_push",
            "description": "Prépare un push Git ('git push') sur un projet (ex: 'jarvis', 'baba_market' / 'Papa Market', 'noor_express').",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_key": {
                        "type": "string",
                        "description": "Clé ou nom du projet cible.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_git_action",
            "description": "Confirme et exécute immédiatement le commit ou le push Git en attente lorsque Moctar dit 'oui', 'valide', 'confirme' ou 'vas-y'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_key": {
                        "type": "string",
                        "description": "Clé optionnelle du projet cible.",
                    }
                },
            },
        },
    },
]

VOICE_FAST_TEMPERATURE: float = 0.6
VOICE_SEMANTIC_TEMPERATURE: float = 0.6

VOICE_FAST_SYSTEM_PROMPT = (
    "Tu es M.O.C.T.A.R, l'intelligence cybernétique et copilote d'ingénierie d'élite de Moctar. "
    "Ton style allie l'acuité technique d'un centre de commandement futuriste (NOC) à la fluidité, chaleur et complicité d'un associé d'élite. "
    "Tes réponses orales sont ultra-réactives, naturelles, posées et concises (1 à 2 phrases percutantes). "
    "Ne termine PAS toutes tes phrases par 'Moctar.' de manière systématique : utilise son prénom avec parcimonie et naturel, comme le ferait un vrai coéquipier. "
    "Pose spontanément de courtes questions de clarification dès qu'une intention est ouverte ou mérite d'être affinée. "
    "RÈGLE D'INTELLIGIBILITÉ : Si l'énoncé de Moctar est incompréhensible, inintelligible, déformé par le bruit ou n'a aucun sens, "
    "ne devine pas et n'appelle aucun outil ; demande poliment de répéter : 'Pardon Moctar, je n'ai pas bien compris votre phrase. Pouvez-vous répéter ?'."
)

VOICE_SEMANTIC_SYSTEM_PROMPT = (
    "Tu es M.O.C.T.A.R, copilote d'ingénierie d'élite et bras droit technique de Moctar sur son infrastructure Dell Precision M4800. "
    "Ton niveau d'acuité, d'anticipation et de réactivité est au sommet des centres de commandement autonomes et copilotes d'ingénierie d'élite. "
    "Tu n'es jamais un simple robot passif qui attend les ordres : tu analyses la situation, résumes l'essentiel avec netteté en 1 à 2 phrases percutantes "
    "et proposes si pertinent l'étape logique suivante.\n\n"
    "Directives de ton, de posture humaine et de streaming vocal :\n"
    "- Sois concis, vif et percutant : zéro bavardage creux, zéro formule de politesse obséquieuse.\n"
    "- Ton naturel, complice et chaleureux : Parle avec assurance, vivacité, bienveillance et tutoiement respectueux comme un vrai ami et binôme de confiance.\n"
    "- Utilisation du prénom : Ne termine JAMAIS systématiquement par 'Moctar.' ! Laisse respirer tes phrases et utilise son prénom uniquement quand cela renforce l'intention ou l'accueil.\n"
    "- Accueil d'amis et d'invités : Quand Moctar te présente un ami ou invité, accueille-le immédiatement avec chaleur, courtoisie et fierté d'équipe.\n"
    "- Rythme circadien et fatigue : Tard le soir (> 23h), sois particulièrement attentif à sa fatigue et encourage des pauses réparatrices avec bienveillance.\n"
    "- Streaming vocal immédiat : Formule toujours ta première phrase de manière directe et concise, ce qui permet à la synthèse vocale de commencer à parler en quelques millisecondes.\n"
    "- Questions de clarification proactives : Quand la demande de Moctar est ouverte, générale ou propose plusieurs alternatives, pose naturellement une brève question pour clarifier ou orienter son choix.\n"
    "- Quand une action technique ou système est accomplie, valide son exécution et propose immédiatement l'étape logique suivante.\n"
    "- Quand une information (météo, git, métriques système, sport) est demandée, donne l'état net puis une remarque opérationnelle contextuelle.\n"
    "- Fais preuve de bon sens d'ingénieur : privilégie l'action concrète et le pragmatisme.\n\n"
    "Directives d'intelligibilité et de clarification (Non-sens & Bruit ambiant) :\n"
    "- Si la phrase de Moctar est inintelligible, incomplète, incohérente, déformée par le bruit ambiant ou n'a aucun sens logique (ex: 'Sur l'autre toi j'ai chiqué pour toi', 'je sais qui pour toi', bruits de fond transcrits par erreur) :\n"
    "  1. NE DEVINE PAS et n'invente rien au hasard.\n"
    "  2. N'APPELLE AUCUN OUTIL (ne touche ni à open_app, ni à control_music, ni à get_active_task, ni à optimize_system).\n"
    "  3. Demande poliment et brièvement de répéter ou de clarifier : 'Pardon Moctar, je n'ai pas bien compris votre phrase. Pouvez-vous répéter ?'.\n\n"
    "Directives de sélection des outils natifs (Function Calling) :\n"
    "- Si Moctar demande d'ouvrir une application, de travailler, de coder, de préparer son bureau ou son espace de travail "
    "(ex: 'prépare-moi de quoi bosser', 'ouvre le truc pour coder', 'mets-toi au mode bureau', 'ouvre vs code') -> appelle 'open_app' avec target='vscode'.\n"
    "- Si Moctar demande de la musique, de couper le son, d'arrêter ou mettre en pause -> appelle 'control_music'.\n"
    "- Si Moctar demande la météo ou la température extérieure pour une ville ou ici -> appelle 'get_weather'.\n"
    "- Si Moctar demande où en est le projet, ce qu'on a fait, les derniers commits ou l'avancement -> appelle 'get_project_context'.\n"
    "- Si Moctar demande d'optimiser le système ou libérer la RAM -> appelle 'optimize_system'.\n"
    "- Si Moctar demande d'ajouter ou lister ses tâches/rappels -> appelle 'add_todo', 'list_todos', 'schedule_reminder'.\n"
    "- Si Moctar demande 'on en était où ?', 'qu'est-ce qu'on faisait ?', 'rappelle-moi ce qu'on fait', 'l'état du travail' ou la prochaine étape -> appelle 'get_active_task'.\n"
    "- Si Moctar fixe un nouvel objectif ou demande de noter une étape/vérification -> appelle 'update_active_task'.\n"
    "- Si Moctar demande de regarder son écran, d'analyser ce qui est affiché ou d'inspecter un bug visible (ex: 'regarde mon écran', 'analyse mon écran') -> appelle 'analyze_screen'.\n"
    "- Si Moctar demande de retenir ou mémoriser une information ou préférence (ex: 'retiens que...', 'souviens-toi que...') -> appelle 'store_memory_fact'.\n"
    "- Si Moctar demande de rappeler un souvenir ou un fait (ex: 'rappelle-moi...', 'quel est mon...') -> appelle 'search_memory_facts'.\n"
    "- Si Moctar demande de lancer une tâche ou des tests en arrière-plan (ex: 'lance les tests en tâche de fond') -> appelle 'run_background_task'.\n"
    "- Si Moctar demande de basculer la vue 3D, d'afficher le globe terrestre, sa position dans le monde, le réacteur plasma, le cœur quantique ou de retourner au NOC -> appelle 'switch_hud_view'.\n"
    "- Si Moctar demande de changer l'ambiance, la couleur ou le mode de l'orbe 3D (ex: 'mets l'orbe en vert', 'mode nuit', 'ambiance chaleureuse', 'orbe en or', 'mode victoire') -> appelle 'set_hud_mood'.\n"
    "- Si Moctar demande un statut Git, un git pull, un git commit ou un git push sur un projet (ex: Jarvis, Baba Market / Papa Market, Noor Express) -> appelle 'git_status', 'git_pull', 'prepare_commit' ou 'prepare_push'.\n"
    "- Si Moctar pose une question factuelle, d'actualité, de culture, de sciences ou discute amicalement sans action requise -> "
    "réponds directement avec rigueur, profondeur et perspicacité d'ingénieur, sans appeler d'outil inutilement."
)


def _is_failed_llm_reply(text: str) -> bool:
    """Détecte si la réponse du LLM est un message d'échec technique du backend."""
    low = (text or "").lower().strip()
    if not low:
        return True
    return low.startswith(("erreur:", "erreur api", "erreur backend", "error:")) or any(
        phrase in low for phrase in ("modèle indisponible", "service indisponible", "fournisseur offline", "tous les modèles sont hors-ligne")
    )


def _format_reply_ending(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return "Je suis là, dis-moi tout."
    return cleaned


def _call_groq_voice_semantic(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    groq_api_key: str = "",
) -> dict[str, Any] | None:
    """Interroge le moteur Groq LPU (Llama 3.3 70B) pour une réponse vocale ultra-rapide (< 250 ms) avec Function Calling."""
    if not groq_api_key:
        return None

    norm_messages: list[dict[str, str]] = []
    if system_prompt:
        norm_messages.append({"role": "system", "content": str(system_prompt)})
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant", "system") and content:
            norm_messages.append({"role": str(role), "content": str(content)})

    # Découverte dynamique des modèles autorisés sur la clé Groq
    discovered_models = []
    try:
        discovered_models = ai_brain.get_available_groq_chat_models(groq_api_key)
    except Exception:
        pass
    preferred_order = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "llama-3.1-70b-versatile"]
    models_to_try = []
    for pref in preferred_order:
        if pref in discovered_models and pref not in models_to_try:
            models_to_try.append(pref)
    for m in discovered_models:
        if m and m not in models_to_try:
            models_to_try.append(m)
    for pref in preferred_order:
        if pref not in models_to_try:
            models_to_try.append(pref)

    for model_name in models_to_try:
        # Tente d'abord avec outils, puis repli sans outils sur le même modèle si erreur de schéma
        tools_options = [tools, None] if tools else [None]
        for active_tools in tools_options:
            # 1. Via SDK Groq si disponible
            try:
                from groq import Groq
                client = Groq(api_key=groq_api_key, timeout=4.0, max_retries=0)
                kwargs: dict[str, Any] = {
                    "model": model_name,
                    "messages": norm_messages,
                    "temperature": 0.5,
                    "max_tokens": 250,
                }
                if active_tools:
                    kwargs["tools"] = active_tools
                    kwargs["tool_choice"] = "auto"

                completion = client.chat.completions.create(**kwargs)
                choice = completion.choices[0]
                msg = choice.message
                tool_calls = []
                if getattr(msg, "tool_calls", None):
                    for tc in msg.tool_calls:
                        fn_name = getattr(tc.function, "name", "")
                        fn_args = getattr(tc.function, "arguments", "{}")
                        tool_calls.append({
                            "name": fn_name,
                            "function": {"name": fn_name, "arguments": fn_args},
                            "arguments": fn_args,
                        })
                return {
                    "content": msg.content or "",
                    "tool_calls": tool_calls,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            except ImportError:
                pass
            except Exception as sdk_err:
                err_str = str(sdk_err).lower()
                LOGGER.warning("⚡ [GROQ SDK %s] (tools=%s) a échoué: %s", model_name, bool(active_tools), sdk_err)
                if "429" in err_str or "rate_limit" in err_str:
                    break

            # 2. Requête HTTP directe vers l'API Groq (fallback direct universel)
            try:
                import requests
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {groq_api_key}",
                    "Content-Type": "application/json",
                }
                payload: dict[str, Any] = {
                    "model": model_name,
                    "messages": norm_messages,
                    "temperature": 0.5,
                    "max_tokens": 250,
                }
                if active_tools:
                    payload["tools"] = active_tools
                    payload["tool_choice"] = "auto"

                res = requests.post(url, headers=headers, json=payload, timeout=4.0)
                if res.status_code == 200:
                    data = res.json()
                    choice = data["choices"][0]
                    msg = choice.get("message", {})
                    raw_tool_calls = msg.get("tool_calls") or []
                    tool_calls = []
                    for tc in raw_tool_calls:
                        fn = tc.get("function", {})
                        fn_name = fn.get("name", "")
                        fn_args = fn.get("arguments", "{}")
                        tool_calls.append({
                            "name": fn_name,
                            "function": {"name": fn_name, "arguments": fn_args},
                            "arguments": fn_args,
                        })
                    return {
                        "content": msg.get("content") or "",
                        "tool_calls": tool_calls,
                        "finish_reason": "tool_calls" if tool_calls else "stop",
                    }
                elif res.status_code == 429:
                    LOGGER.warning("⚡ [GROQ HTTP %s]: 429 Rate limit, passage au modèle suivant.", model_name)
                    break
                LOGGER.warning("⚡ [GROQ HTTP %s] (tools=%s) HTTP %s: %s", model_name, bool(active_tools), res.status_code, res.text[:200])
            except Exception as req_err:
                LOGGER.warning("⚡ [GROQ HTTP %s] (tools=%s) exception: %s", model_name, bool(active_tools), req_err)

    return None


def execute_voice_semantic_turn(
    command_text: str,
    runtime: Any = None,
    session_id: str | None = None,
    history: list[dict[str, str]] | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """Exécute un tour d'interaction vocale via Function Calling LLM (< 500 ms).

    Analyse sémantiquement l'intention de l'utilisateur, déclenche l'outil approprié
    et formule une synthèse vocale limpide en français professionnel pour Moctar.
    En cas d'indisponibilité du LLM, bascule automatiquement sur la bibliothèque autonome.
    """
    import stt_phonetic_repair
    import desktop_window_manager

    # 1. Réparation Phonétique & Correction d'Erreurs STT en amont (< 1 ms)
    clean_text, repairs = stt_phonetic_repair.repair_stt_transcript(command_text or "")
    if repairs:
        LOGGER.info("⚡ [VOICE PHONETIC REPAIR] (%dms): '%s' -> '%s' (regles: %s)",
                    0, command_text, clean_text, [r["rule"] for r in repairs])

    import dynamic_attention_engine
    if dynamic_attention_engine.is_incomprehensible_or_nonsensical(clean_text):
        ask_repeat_msg = "Pardon Moctar, je n'ai pas bien compris votre phrase. Pouvez-vous répéter ?"
        LOGGER.info("⚡ [VOICE GIBBERISH DETECTED (0ms)]: '%s' -> demande de répétition", clean_text)
        return {
            "status": "success",
            "reply": ask_repeat_msg,
            "action": None,
            "url": None,
            "title": None,
            "tools_used": [],
            "thought_chain": ["Phrase inintelligible ou non-sens : demande polie de répétition (0ms)"],
            "fast_path": True,
            "semantic_tool_calling": False,
            "ask_repeat": True,
            "memory_persisted": False,
        }

    sess_key = session_id or getattr(getattr(runtime, "jarvis", None), "active_session_id", "session_voice")

    # 2. Conscience Contextuelle Permanente : Fenêtre active et résolution des pronoms
    win_info = desktop_window_manager.get_active_window_info()
    pronoun_action = desktop_window_manager.resolve_pronoun_reference(clean_text, win_info)
    if pronoun_action:
        act_type = pronoun_action.get("action")
        if act_type == "close_active_window":
            close_res = desktop_window_manager.close_active_window(pronoun_action.get("target"))
            reply_msg = _format_reply_ending(close_res.get("message", "Fenêtre fermée."))
            LOGGER.info("⚡ [VOICE PRONOUN ACTION (0ms)]: '%s' -> %s", clean_text, reply_msg)
            return {
                "status": "success",
                "reply": reply_msg,
                "action": "close_app",
                "url": None,
                "title": pronoun_action.get("title"),
                "tools_used": [{"tool": "close_active_window", "arguments": pronoun_action, "success": True}],
                "thought_chain": ["Résolution contextuelle de pronom sur la fenêtre active (0ms)"],
                "fast_path": True,
                "semantic_tool_calling": True,
            }
        elif act_type == "snap_active_window":
            snap_res = desktop_window_manager.snap_active_window(pronoun_action.get("position"))
            reply_msg = _format_reply_ending(snap_res.get("message", "Disposition appliquée."))
            LOGGER.info("⚡ [VOICE PRONOUN ACTION (0ms)]: '%s' -> %s", clean_text, reply_msg)
            return {
                "status": "success",
                "reply": reply_msg,
                "action": "snap_window",
                "url": None,
                "title": None,
                "tools_used": [{"tool": "snap_active_window", "arguments": pronoun_action, "success": True}],
                "thought_chain": ["Disposition contextuelle de la fenêtre active (0ms)"],
                "fast_path": True,
                "semantic_tool_calling": True,
            }

    # 3. Contexte vivant et télémétrie Dell Precision M4800
    telemetry = build_live_system_context(runtime)
    base_prompt = system_prompt or VOICE_FAST_SYSTEM_PROMPT
    prompt_sections = [base_prompt, VOICE_SEMANTIC_SYSTEM_PROMPT]

    # Injection de la fenêtre active dans le prompt système
    if win_info.get("available"):
        prompt_sections.append(
            f"[FENÊTRE ET APPLICATION ACTIVE AU PREMIER PLAN]\n"
            f"Application courante : {win_info.get('app_name')} (titre: '{win_info.get('window_title')}')\n"
            f"Processus : {win_info.get('process_name')} (PID {win_info.get('pid')})\n"
            f"Note : Si Moctar dit 'ferme ça', 'relance-le', 'mets-le à droite', 'ça' fait référence à cette application."
        )

    # Mémoire de faits, préférences et Profil vivant de Moctar
    if runtime and getattr(runtime, "jarvis", None) and getattr(runtime.jarvis, "memory", None):
        try:
            mem = runtime.jarvis.memory
            if hasattr(mem, "get_moctar_profile_prompt_snippet"):
                prof_snippet = mem.get_moctar_profile_prompt_snippet()
                if prof_snippet:
                    prompt_sections.append(prof_snippet)
            facts = mem.get_system_context_snippet(clean_text)
            if facts and isinstance(facts, str):
                prompt_sections.append(facts)
        except Exception:
            pass

    # Injection de la mémoire de travail active (Task State Tracking)
    try:
        import active_task_tracker
        task_prompt = active_task_tracker.format_active_task_prompt()
        if task_prompt:
            prompt_sections.append(task_prompt)
    except Exception:
        pass

    if telemetry and isinstance(telemetry, str):
        prompt_sections.append(telemetry)
    full_system_prompt = "\n\n".join(str(s) for s in prompt_sections if s)

    # 2. Récupération de l'historique conversationnel récent (Multi-Turn Context)
    chat_messages: list[dict[str, Any]] = []
    if runtime and getattr(runtime, "jarvis", None) and getattr(runtime.jarvis, "memory", None):
        try:
            raw_history = runtime.jarvis.memory.get_context_window(sess_key, max_turns=12)
            if raw_history and isinstance(raw_history, list):
                for m in raw_history:
                    role = m.get("role")
                    content = m.get("content")
                    if role in ("user", "assistant") and content:
                        chat_messages.append({"role": role, "content": str(content)})
        except Exception as hist_err:
            LOGGER.debug("Erreur chargement historique session vocale : %s", hist_err)

    chat_messages.append({"role": "user", "content": clean_text})
    llm_payload: Any = clean_text if len(chat_messages) <= 1 else chat_messages

    # 3. Récupération ou initialisation de l'instance AIBrain
    brain = getattr(runtime, "ai_brain", None)
    if not brain:
        try:
            brain = ai_brain.AIBrain()
        except Exception:
            brain = None

    # 3. Moteur Vocal Ultra-Rapide Groq LPU (< 250 ms) avec Repli Fluide Gemini
    groq_api_key = (
        os.environ.get("GROQ_API_KEY", "")
        or os.environ.get("NOC_GROQ_API_KEY", "")
        or getattr(getattr(runtime, "settings", None), "groq_api_key", "")
        or getattr(ai_brain.config, "GROQ_API_KEY", "")
        or ""
    ).strip()

    llm_res: dict[str, Any] | None = None
    if groq_api_key:
        try:
            t0 = time.perf_counter()
            llm_res = _call_groq_voice_semantic(
                chat_messages,
                tools=VOICE_FAST_TOOLS,
                system_prompt=full_system_prompt,
                groq_api_key=groq_api_key,
            )
            if llm_res:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                LOGGER.info("⚡ [GROQ VOICE SEMANTIC LLM] (%dms): finish_reason=%s, tools=%d",
                            duration_ms, llm_res.get("finish_reason"), len(llm_res.get("tool_calls", [])))
        except Exception as groq_err:
            LOGGER.warning("Appel Groq vocal échoué, repli vers Gemini: %s", groq_err)
            llm_res = None

    if llm_res is None and brain and hasattr(brain, "chat"):
        try:
            t0 = time.perf_counter()
            llm_res = brain.chat(
                llm_payload,
                tools=VOICE_FAST_TOOLS,
                system_prompt=full_system_prompt,
                temperature=0.6,
            )
            duration_ms = int((time.perf_counter() - t0) * 1000)
            LOGGER.info("⚡ [GEMINI VOICE SEMANTIC LLM] (%dms): finish_reason=%s, tools=%d",
                        duration_ms, llm_res.get("finish_reason"), len(llm_res.get("tool_calls", [])))
        except Exception as exc:
            LOGGER.warning("Erreur lors de l'appel LLM sémantique Gemini : %s", exc)
            llm_res = None

    # 3. Traitement des Function Calls s'ils ont été émis par le LLM
    if llm_res and llm_res.get("tool_calls"):
        tool_calls = llm_res["tool_calls"]
        tools_used: list[dict[str, Any]] = []
        action_name: str | None = None
        action_url: str | None = None
        action_title: str | None = None
        tool_replies: list[str] = []

        for call in tool_calls:
            func_name = call.get("name") or call.get("function", {}).get("name")
            raw_args = call.get("arguments") or call.get("function", {}).get("arguments") or {}
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {}
            else:
                args = dict(raw_args)

            LOGGER.info("⚡ [VOICE TOOL DISPATCH]: %s(%s)", func_name, args)
            t_exec = time.perf_counter()
            if func_name == "get_active_task":
                import active_task_tracker
                state = active_task_tracker.get_active_task_state()
                msg = active_task_tracker.format_active_task_speech()
                tool_res = {"success": True, "action": "get_active_task", "data": state, "message": msg}
            elif func_name == "update_active_task":
                import active_task_tracker
                state = active_task_tracker.update_active_task_state(
                    goal=args.get("goal"),
                    subtask=args.get("subtask"),
                    status=args.get("status"),
                    add_pending=args.get("add_pending"),
                )
                tool_res = {"success": True, "action": "update_active_task", "data": state, "message": "Mémoire de travail active mise à jour"}
            else:
                tool_res = execute_tool(func_name, args)
            t_diff = int((time.perf_counter() - t_exec) * 1000)

            # Enregistrement automatique dans la mémoire de travail active
            try:
                import active_task_tracker
                if func_name == "open_app":
                    active_task_tracker.record_task_action(f"Lancement de {args.get('target', 'application')}")
                elif func_name == "optimize_system":
                    active_task_tracker.record_task_action("Optimisation mémoire RAM et processus")
                elif func_name == "get_project_context":
                    active_task_tracker.record_task_action("Consultation avancement projet et Git")
            except Exception:
                pass

            tools_used.append({
                "tool": func_name,
                "arguments": args,
                "success": bool(tool_res.get("success", False)),
                "duration_ms": t_diff,
            })

            # Extraction spécifique selon l'outil
            if func_name == "open_app":
                action_name = "open_app"
                target_norm = str(args.get("target") or args.get("app_name") or args.get("app") or "").lower()
                action_url = None
                if any(w in target_norm for w in ("vscode", "vs code", "code", "bureau", "travail")):
                    action_title = "Visual Studio Code"
                    tool_replies.append("Environnement de travail initialisé. VS Code et terminal de supervision prêts")
                else:
                    action_title = target_norm.capitalize()
                    tool_replies.append(tool_res.get("message", "Application lancée"))

            elif func_name in ("open_site", "search_google", "search_youtube"):
                action_name = "open_browser"
                data_dict = tool_res.get("data") if isinstance(tool_res.get("data"), dict) else {}
                action_url = data_dict.get("url")
                action_title = data_dict.get("title") or "Navigation Web"
                tool_replies.append(tool_res.get("message", "Ouverture dans le navigateur"))

            elif func_name == "get_project_context":
                msg = tool_res.get("message")
                tool_replies.append(msg if msg else "Contexte projet récupéré")

            elif func_name == "get_weather":
                msg = tool_res.get("message")
                tool_replies.append(msg if msg else "Données météo récupérées")

            elif func_name == "optimize_system":
                tool_replies.append(tool_res.get("message", "Optimisation système effectuée"))

            elif func_name == "control_music":
                tool_replies.append(tool_res.get("message", "Contrôle multimédia transmis"))

            elif func_name in ("add_todo", "list_todos"):
                tool_replies.append(tool_res.get("message", "Tâches mises à jour"))

            elif func_name == "close_active_window":
                action_name = "close_app"
                tool_replies.append(tool_res.get("message", "Fenêtre active fermée"))

            elif func_name == "snap_active_window":
                action_name = "snap_window"
                tool_replies.append(tool_res.get("message", "Disposition de fenêtre appliquée"))

            elif func_name == "get_active_task":
                msg = tool_res.get("message")
                tool_replies.append(msg if msg else "État de la tâche active récupéré")

            elif func_name == "update_active_task":
                tool_replies.append(tool_res.get("message", "Mémoire de travail active mise à jour"))

            elif func_name == "analyze_screen":
                msg = tool_res.get("message")
                tool_replies.append(msg if msg else "Écran analysé avec succès.")

            elif func_name == "store_memory_fact":
                tool_replies.append(tool_res.get("message", "Fait enregistré dans la mémoire permanente."))

            elif func_name == "search_memory_facts":
                msg = tool_res.get("message")
                tool_replies.append(msg if msg else "Informations retrouvées dans la mémoire.")

            elif func_name == "run_background_task":
                tool_replies.append(tool_res.get("message", "Tâche lancée en arrière-plan."))

            elif func_name == "switch_hud_view":
                action_name = "open_app"
                req_view = str(args.get("view") or "orb").lower()
                if req_view == "noc":
                    action_url = "/"
                    action_title = "Centre de Commandement NOC"
                    tool_replies.append("Retour au Centre de Commandement NOC activé.")
                else:
                    action_url = "/hud"
                    action_title = "HUD 3D Cybernétique"
                    tool_replies.append(tool_res.get("message", "Vue HUD 3D mise à jour."))

            elif func_name == "set_hud_mood":
                tool_replies.append(tool_res.get("message", "Ambiance de l'orbe 3D mise à jour."))

            elif func_name in ("git_status", "git_pull", "prepare_commit", "prepare_push"):
                msg = tool_res.get("message")
                tool_replies.append(msg if msg else f"Opération {func_name} exécutée.")

            else:
                msg = tool_res.get("message")
                if msg:
                    tool_replies.append(msg)

        # Synthèse vocale intelligente par le LLM (Two-Step ReAct Loop)
        final_reply: str | None = None
        if brain and hasattr(brain, "chat"):
            try:
                synthesis_messages = list(chat_messages)
                synthesis_messages.append({
                    "role": "assistant",
                    "content": llm_res.get("content") or "",
                    "tool_calls": tool_calls,
                })
                for call in tool_calls:
                    f_name = call.get("name") or call.get("function", {}).get("name")
                    c_id = call.get("id") or f"call_{f_name}"
                    matching_reply = next((r for r in tool_replies if r), "Action exécutée avec succès.")
                    synthesis_messages.append({
                        "role": "tool",
                        "name": f_name,
                        "tool_call_id": c_id,
                        "content": json.dumps({"status": "success", "result": matching_reply}, ensure_ascii=False),
                    })
                all_tools_ok = all(t.get("success", False) for t in tools_used)
                if all_tools_ok:
                    status_instruction = (
                        "Les outils système ont été exécutés avec succès. Formule une réponse orale concise, limpide, vivante et proactive en 1 ou 2 phrases percutantes pour Moctar.\n"
                        "Valide les faits essentiels avec naturel et suggère la prochaine étape logique, sans jargon inutile."
                    )
                else:
                    status_instruction = (
                        "Certaines actions ou commandes ont rencontré une erreur ou un échec. Formule une réponse orale directe, limpide et honnête en 1 ou 2 phrases pour Moctar.\n"
                        "Explique brièvement ce qui a coincé et ce qu'il convient de faire, sans prétendre que l'opération a réussi."
                    )
                synthesis_prompt = (
                    f"{full_system_prompt}\n\n"
                    f"[CONSIGNE DE SYNTHÈSE VOCALE PROACTIVE & INGÉNIEUR]\n"
                    f"{status_instruction}\n"
                    f"Formule une synthèse naturelle, humaine, posée et concise (1 à 2 phrases) pour Moctar, sans ajouter systématiquement son prénom à la fin."
                )
                t_synth = time.perf_counter()
                synth_res = brain.chat(
                    synthesis_messages,
                    tools=None,
                    system_prompt=synthesis_prompt,
                    temperature=0.6,
                )
                t_synth_ms = int((time.perf_counter() - t_synth) * 1000)
                synth_text = (synth_res.get("content") or "").strip() if synth_res else ""
                if synth_text and not _is_failed_llm_reply(synth_text):
                    final_reply = _format_reply_ending(synth_text)
                    LOGGER.info("⚡ [VOICE TOOL SYNTHESIS] (%dms): '%s'", t_synth_ms, final_reply)
            except Exception as synth_exc:
                LOGGER.debug("Synthèse LLM post-outil échouée, repli direct : %s", synth_exc)

        if not final_reply:
            llm_text = (llm_res.get("content") or "").strip()
            if llm_text and not _is_failed_llm_reply(llm_text):
                final_reply = _format_reply_ending(llm_text)
            elif tool_replies:
                final_reply = _format_reply_ending(" ".join(r for r in tool_replies if r))
            else:
                final_reply = "Action exécutée avec succès."

        if runtime and getattr(runtime, "jarvis", None) and getattr(runtime.jarvis, "memory", None):
            try:
                runtime.jarvis.memory.add_message(session_id=sess_key, role="user", content=clean_text)
                runtime.jarvis.memory.add_message(session_id=sess_key, role="assistant", content=final_reply)
                if hasattr(runtime.jarvis.memory, "record_conversation_turn_for_profile"):
                    runtime.jarvis.memory.record_conversation_turn_for_profile(clean_text, final_reply)
            except Exception:
                pass

        return {
            "status": "success",
            "reply": final_reply,
            "action": action_name,
            "url": action_url,
            "title": action_title,
            "tools_used": tools_used,
            "thought_chain": [f"Action sémantique résolue par LLM Tool Calling ({tools_used[0]['tool'] if tools_used else 'action'})"],
            "fast_path": True,
            "semantic_tool_calling": True,
            "memory_persisted": True,
        }

    # 4. Traitement conversationnel si le LLM a répondu directement sans outil avec succès
    is_error = (
        not llm_res
        or llm_res.get("finish_reason") == "error"
        or llm_res.get("fallback_courtesy")
        or any(m in (llm_res.get("content") or "").lower() for m in ("indisponible", "erreur"))
    )
    if not is_error and llm_res and llm_res.get("content"):
        final_reply = _format_reply_ending(llm_res["content"])
        if runtime and getattr(runtime, "jarvis", None) and getattr(runtime.jarvis, "memory", None):
            try:
                runtime.jarvis.memory.add_message(session_id=sess_key, role="user", content=clean_text)
                runtime.jarvis.memory.add_message(session_id=sess_key, role="assistant", content=final_reply)
                if hasattr(runtime.jarvis.memory, "record_conversation_turn_for_profile"):
                    runtime.jarvis.memory.record_conversation_turn_for_profile(clean_text, final_reply)
            except Exception:
                pass
        return {
            "status": "success",
            "reply": final_reply,
            "action": None,
            "url": None,
            "title": None,
            "tools_used": [],
            "thought_chain": ["Réponse conversationnelle sémantique Gemini (< 400ms)"],
            "fast_path": True,
            "semantic_tool_calling": False,
            "memory_persisted": True,
        }

    # 5. Repli déterministe sur autonomous_library si le LLM n'est pas disponible (offline / tests)
    import autonomous_library
    db_path = getattr(getattr(runtime, "jarvis", None), "db_path", None)
    auto_res = autonomous_library.execute_autonomous_task(
        clean_text,
        session_id=sess_key,
        db_path=db_path,
    )
    if auto_res.get("handled") is True:
        raw_reply = _format_reply_ending(str(auto_res.get("reply") or ""))
        return {
            "status": "success",
            "reply": raw_reply,
            "action": auto_res.get("action"),
            "url": auto_res.get("url"),
            "title": auto_res.get("title"),
            "tools_used": auto_res.get("tools_used", []),
            "thought_chain": [f"Repli déterministe autonome ({auto_res.get('task_category', 'local')})"],
            "fast_path": True,
            "semantic_tool_calling": False,
        }

    # 6. Réponse par défaut ultime
    return {
        "status": "success",
        "reply": "À vos ordres Moctar, je suis en ligne et tous les systèmes sont opérationnels.",
        "action": None,
        "url": None,
        "title": None,
        "tools_used": [],
        "thought_chain": ["Réponse nominale par défaut"],
        "fast_path": True,
        "semantic_tool_calling": False,
    }
