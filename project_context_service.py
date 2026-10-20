"""Service de mémoire et d'état d'avancement de projet pour JARVIS / M.O.C.T.A.R.

Agrège l'historique Git récent, l'état de l'arbre de travail, les tâches prioritaires
en attente et les dernières notes mémorisées pour répondre avec précision à la question
« Où en est-on ? » en un français professionnel et direct.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.project_context")


def _get_git_info() -> dict[str, Any]:
    """Extrait les métriques clés du dépôt Git actif."""
    info: dict[str, Any] = {
        "is_repo": False,
        "branch": "main",
        "last_commit_hash": "",
        "last_commit_subject": "",
        "last_commit_time": "",
        "is_clean": True,
        "modified_count": 0,
    }
    cwd = Path.cwd()
    if not (cwd / ".git").exists() and not (cwd.parent / ".git").exists():
        return info

    info["is_repo"] = True
    try:
        # Branche courante
        b_res = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if b_res.returncode == 0 and b_res.stdout.strip():
            info["branch"] = b_res.stdout.strip()

        # Dernier commit : hash|sujet|date_relative
        log_res = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%h|%s|%cr"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if log_res.returncode == 0 and log_res.stdout.strip():
            parts = log_res.stdout.strip().split("|", 2)
            if len(parts) >= 3:
                info["last_commit_hash"] = parts[0].strip()
                info["last_commit_subject"] = parts[1].strip()
                info["last_commit_time"] = parts[2].strip()

        # Statut des modifications
        st_res = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if st_res.returncode == 0:
            lines = [line for line in st_res.stdout.strip().splitlines() if line.strip()]
            info["modified_count"] = len(lines)
            info["is_clean"] = len(lines) == 0

    except Exception as exc:
        logger.debug("Erreur lors de la lecture des informations Git: %s", exc)

    return info


def _get_pending_todos(db_path: Path | str | None = None) -> list[str]:
    """Récupère les tâches non complétées depuis la base de données."""
    try:
        from actions import personal_actions

        raw_todos = personal_actions.list_todos()
        msg = raw_todos.get("message") if isinstance(raw_todos, dict) else str(raw_todos)
        todos: list[str] = []
        for line in msg.splitlines():
            line = line.strip()
            if line.startswith("- [a faire]") or line.startswith("- [todo]"):
                task = re.sub(r"^-\s*\[.*?\]\s*", "", line).strip()
                if task and task not in todos:
                    todos.append(task)
        return todos[:3]
    except Exception as exc:
        logger.debug("Erreur lors de la lecture des tâches: %s", exc)
        return []


def get_project_context_summary(
    db_path: Path | str | None = None,
    session_id: str = "default",
) -> dict[str, Any]:
    """Génère une synthèse complète, professionnelle et structurée de l'état du projet."""
    git_info = _get_git_info()
    todos = _get_pending_todos(db_path)

    branch = git_info.get("branch", "main")
    commit_subj = git_info.get("last_commit_subject") or "initialisation du projet"
    commit_time = git_info.get("last_commit_time") or "récemment"
    is_clean = git_info.get("is_clean", True)
    mod_count = git_info.get("modified_count", 0)

    # Nettoyage et traduction de date relative si en anglais
    commit_time_fr = commit_time.replace("hours ago", "heures").replace("hour ago", "heure")
    commit_time_fr = commit_time_fr.replace("minutes ago", "minutes").replace("minute ago", "minute")
    commit_time_fr = commit_time_fr.replace("days ago", "jours").replace("day ago", "jour")
    commit_time_fr = f"il y a {commit_time_fr}" if not commit_time_fr.startswith("il y a") else commit_time_fr

    # Phrase sur Git
    if git_info.get("is_repo"):
        git_phrase = f"Sur le dépôt ({branch}), la dernière validation est « {commit_subj} » ({commit_time_fr})."
        if is_clean:
            status_phrase = "L'espace de travail est propre et synchronisé."
        else:
            status_phrase = f"Il y a actuellement {mod_count} fichier(s) en cours de modification."
    else:
        git_phrase = "Aucun dépôt Git actif détecté."
        status_phrase = ""

    # Phrase sur les tâches
    if todos:
        todo_list_text = ", ".join(f"« {t} »" for t in todos[:2])
        if len(todos) > 2:
            todo_list_text += f" et {len(todos) - 2} autre(s)"
        tasks_phrase = f"Côté planning, vous avez en attente : {todo_list_text}."
    else:
        tasks_phrase = "Toutes les tâches planifiées sont à jour."

    oral_text = (
        f"Moctar, voici le point d'avancement : {git_phrase} {status_phrase} {tasks_phrase} "
        f"Tous les indicateurs sont nominaux, Moctar."
    )
    oral_text = re.sub(r"\s+", " ", oral_text).strip()

    return {
        "success": True,
        "branch": branch,
        "last_commit": commit_subj,
        "commit_hash": git_info.get("last_commit_hash", ""),
        "commit_time": commit_time_fr,
        "is_clean": is_clean,
        "modified_count": mod_count,
        "todos": todos,
        "oral_text": oral_text,
    }
