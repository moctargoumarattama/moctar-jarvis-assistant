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
    """Extrait les métriques clés du dépôt Git actif avec suivi explicite de vérification."""
    info: dict[str, Any] = {
        "is_repo": False,
        "verified": False,
        "error": None,
        "branch": "",
        "last_commit_hash": "",
        "last_commit_subject": "",
        "last_commit_time": "",
        "is_clean": True,
        "modified_count": 0,
    }
    cwd = Path.cwd()
    if not (cwd / ".git").exists() and not (cwd.parent / ".git").exists():
        info["error"] = "Aucun dépôt Git détecté"
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
        branch_ok = b_res.returncode == 0
        if branch_ok and b_res.stdout.strip():
            info["branch"] = b_res.stdout.strip()

        # Dernier commit : hash|sujet|date_relative
        log_res = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%h|%s|%cr"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        log_ok = log_res.returncode == 0 and bool(log_res.stdout.strip())
        if log_ok:
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
        status_ok = st_res.returncode == 0
        if status_ok:
            lines = [line for line in st_res.stdout.strip().splitlines() if line.strip()]
            info["modified_count"] = len(lines)
            info["is_clean"] = len(lines) == 0

        info["verified"] = bool(branch_ok and log_ok and status_ok)
        if not info["verified"]:
            info["error"] = "Lecture partielle ou échec d'une commande Git"

    except Exception as exc:
        logger.debug("Erreur lors de la lecture des informations Git: %s", exc)
        info["verified"] = False
        info["error"] = str(exc)

    return info


def _get_pending_todos_with_status(db_path: Path | str | None = None) -> tuple[list[str], bool]:
    """Récupère les tâches non complétées et indique si la source a pu être vérifiée."""
    try:
        from actions import personal_actions

        raw_todos = personal_actions.list_todos()
        if isinstance(raw_todos, dict) and raw_todos.get("success") is False:
            return [], False
        msg = raw_todos.get("message") if isinstance(raw_todos, dict) else str(raw_todos)
        todos: list[str] = []
        for line in msg.splitlines():
            line = line.strip()
            if line.startswith("- [a faire]") or line.startswith("- [todo]"):
                task = re.sub(r"^-\s*\[.*?\]\s*", "", line).strip()
                if task and task not in todos:
                    todos.append(task)
        return todos[:3], True
    except Exception as exc:
        logger.debug("Erreur lors de la lecture des tâches: %s", exc)
        return [], False


def _get_pending_todos(db_path: Path | str | None = None) -> list[str]:
    """Récupère les tâches non complétées depuis la base de données."""
    todos, _ = _get_pending_todos_with_status(db_path)
    return todos


def get_project_context_summary(
    db_path: Path | str | None = None,
    session_id: str = "default",
) -> dict[str, Any]:
    """Génère une synthèse complète et vérifiée de l'état du projet.

    Distingue explicitement 3 états :
    - VERIFIE (métriques Git et planning lues avec succès)
    - PARTIEL (certaines sources manquent ou n'ont pas répondu)
    - NON_VERIFIE / ERREUR (impossible de lire l'état réel)
    """
    git_info = _get_git_info()
    todos, todos_verified = _get_pending_todos_with_status(db_path)
    git_verified = bool(git_info.get("is_repo") and git_info.get("verified"))

    if git_verified and todos_verified:
        verification_state = "VERIFIE"
    elif git_verified or todos_verified:
        verification_state = "PARTIEL"
    else:
        verification_state = "NON_VERIFIE / ERREUR"

    branch = git_info.get("branch") or "inconnue"
    commit_subj = git_info.get("last_commit_subject") or ""
    commit_time = git_info.get("last_commit_time") or ""
    is_clean = git_info.get("is_clean", True)
    mod_count = git_info.get("modified_count", 0)

    commit_time_fr = ""
    if commit_time:
        commit_time_fr = commit_time.replace("hours ago", "heures").replace("hour ago", "heure")
        commit_time_fr = commit_time_fr.replace("minutes ago", "minutes").replace("minute ago", "minute")
        commit_time_fr = commit_time_fr.replace("days ago", "jours").replace("day ago", "jour")
        commit_time_fr = f"il y a {commit_time_fr}" if not commit_time_fr.startswith("il y a") else commit_time_fr

    # Phrase sur Git
    if git_verified:
        git_phrase = (
            f"Sur le dépôt ({branch}), la dernière validation est « {commit_subj} »"
            + (f" ({commit_time_fr})." if commit_time_fr else ".")
        )
        if is_clean:
            status_phrase = "L'espace de travail Git est propre."
        else:
            status_phrase = f"Il y a actuellement {mod_count} fichier(s) en cours de modification."
    elif git_info.get("is_repo"):
        git_phrase = "L'état complet du dépôt Git n'a pas pu être vérifié."
        status_phrase = ""
    else:
        git_phrase = "Aucun dépôt Git actif n'a pu être vérifié dans ce répertoire."
        status_phrase = ""

    # Phrase sur les tâches
    if todos_verified:
        if todos:
            todo_list_text = ", ".join(f"« {t} »" for t in todos[:2])
            if len(todos) > 2:
                todo_list_text += f" et {len(todos) - 2} autre(s)"
            tasks_phrase = f"Côté planning, vous avez en attente : {todo_list_text}."
        else:
            tasks_phrase = "Aucune tâche en attente dans le planning."
    else:
        tasks_phrase = "Le planning des tâches est actuellement indisponible ou non vérifié."

    if verification_state == "VERIFIE":
        state_conclusion = "État de vérification : [VERIFIE] — Toutes les sources du projet ont été lues avec succès."
    elif verification_state == "PARTIEL":
        state_conclusion = "État de vérification : [PARTIEL] — Certaines sources du projet sont absentes ou partielles."
    else:
        state_conclusion = "État de vérification : [NON_VERIFIE / ERREUR] — Impossible de lire l'état réel du projet."

    oral_text = (
        f"Moctar, voici le point d'avancement [{verification_state}] : "
        f"{git_phrase} {status_phrase} {tasks_phrase} {state_conclusion}"
    )
    oral_text = re.sub(r"\s+", " ", oral_text).strip()

    return {
        "success": verification_state != "NON_VERIFIE / ERREUR",
        "status": verification_state,
        "verification_status": verification_state,
        "git_verified": git_verified,
        "todos_verified": todos_verified,
        "branch": branch,
        "last_commit": commit_subj or "non vérifié",
        "commit_hash": git_info.get("last_commit_hash", ""),
        "commit_time": commit_time_fr or "non vérifié",
        "is_clean": is_clean,
        "modified_count": mod_count,
        "todos": todos,
        "oral_text": oral_text,
    }
