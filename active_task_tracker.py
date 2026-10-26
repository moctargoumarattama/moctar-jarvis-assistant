"""Module de Mémoire de Travail Active & Suivi d'État de Tâche (Task State Tracking) pour JARVIS / M.O.C.T.A.R.

Permet à M.O.C.T.A.R de conserver une conscience situationnelle continue de l'objectif
en cours, des sous-tâches, de la dernière opération exécutée et des vérifications
en attente sur la machine Dell Precision M4800, à la manière des copilotes de pointe.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

LOGGER = logging.getLogger("moctar.active_task_tracker")

_DEFAULT_STORAGE_PATH = config.APP_ROOT / "data" / "active_task_state.json"
_LOCAL_FALLBACK_PATH = Path("data/active_task_state.json")


class ActiveTaskTracker:
    """Gestionnaire thread-safe de la mémoire de travail active et du cycle de vie de la tâche en cours."""

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        if storage_path:
            self.storage_path = Path(storage_path)
        elif _DEFAULT_STORAGE_PATH.parent.exists():
            self.storage_path = _DEFAULT_STORAGE_PATH
        else:
            self.storage_path = _LOCAL_FALLBACK_PATH
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = self._load_initial_state()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "goal": "Supervision & assistance d'ingénierie active",
            "active_subtask": "Prêt pour les directives",
            "status": "in_progress",
            "last_action": "Système initialisé",
            "pending_checks": [],
            "notes": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _load_initial_state(self) -> Dict[str, Any]:
        try:
            if self.storage_path.is_file() and self.storage_path.stat().st_size > 0:
                with self.storage_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "goal" in data:
                        return data
        except Exception as exc:
            LOGGER.debug("Impossible de charger l'état tâche persistant (%s), état initial par défaut", exc)
        return self._default_state()

    def _save_state(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.storage_path.open("w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            LOGGER.debug("Erreur écriture active_task_state.json: %s", exc)

    def get_state(self) -> Dict[str, Any]:
        """Retourne une copie immuable de l'état de la tâche active."""
        with self._lock:
            return dict(self._state)

    def set_goal(self, goal: str, subtask: Optional[str] = None) -> Dict[str, Any]:
        """Définit un nouvel objectif de travail principal."""
        with self._lock:
            self._state["goal"] = str(goal).strip()
            if subtask:
                self._state["active_subtask"] = str(subtask).strip()
            self._state["status"] = "in_progress"
            self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_state()
            LOGGER.info("⚡ [TASK STATE]: Nouvel objectif -> '%s'", self._state["goal"])
            return dict(self._state)

    def update_state(
        self,
        goal: Optional[str] = None,
        subtask: Optional[str] = None,
        status: Optional[str] = None,
        last_action: Optional[str] = None,
        add_pending: Optional[Any] = None,
        remove_pending: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Met à jour sélectivement les composantes de la mémoire de travail active."""
        with self._lock:
            if goal:
                self._state["goal"] = str(goal).strip()
            if subtask:
                self._state["active_subtask"] = str(subtask).strip()
            if status:
                self._state["status"] = str(status).strip()
            if last_action:
                self._state["last_action"] = str(last_action).strip()
            if add_pending:
                pending_list = self._state.setdefault("pending_checks", [])
                if isinstance(add_pending, list):
                    for item in add_pending:
                        if item and str(item) not in pending_list:
                            pending_list.append(str(item).strip())
                elif isinstance(add_pending, str) and add_pending.strip():
                    if add_pending.strip() not in pending_list:
                        pending_list.append(add_pending.strip())
            if remove_pending:
                pending_list = self._state.setdefault("pending_checks", [])
                rem = str(remove_pending).strip().lower()
                self._state["pending_checks"] = [p for p in pending_list if rem not in p.lower()]
            if notes:
                notes_list = self._state.setdefault("notes", [])
                if notes.strip() not in notes_list:
                    notes_list.append(notes.strip())
            self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_state()
            return dict(self._state)

    def record_action(self, action_desc: str) -> None:
        """Enregistre la dernière action opérationnelle exécutée."""
        clean = (action_desc or "").strip()
        if not clean:
            return
        with self._lock:
            self._state["last_action"] = clean
            self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_state()

    def complete_task(self, summary: Optional[str] = None) -> Dict[str, Any]:
        """Marque l'objectif actuel comme achevé."""
        with self._lock:
            self._state["status"] = "completed"
            self._state["active_subtask"] = "Objectif achevé"
            if summary:
                self._state["last_action"] = f"Achevé : {summary}"
            self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_state()
            LOGGER.info("⚡ [TASK STATE]: Tâche achevée -> '%s'", self._state.get("goal"))
            return dict(self._state)

    def reset(self) -> None:
        """Réinitialise l'état aux valeurs d'attente (utile pour les tests et nouvelles sessions)."""
        with self._lock:
            self._state = self._default_state()
            self._save_state()

    def format_speech_summary(self) -> str:
        """Formule une synthèse vocale limpide et directe de l'avancement pour Moctar."""
        with self._lock:
            goal = self._state.get("goal", "")
            subtask = self._state.get("active_subtask", "")
            status = self._state.get("status", "in_progress")
            last_action = self._state.get("last_action", "")
            pending = self._state.get("pending_checks", [])

            if status == "completed":
                return f"La tâche '{goal}' est validée et clôturée. Tout est en ordre, Moctar."

            phrases: List[str] = []
            if goal and goal != "Supervision & assistance d'ingénierie active":
                phrases.append(f"On travaille sur : {goal}.")
            if subtask and subtask != "Prêt pour les directives":
                phrases.append(f"Étape en cours : {subtask}.")
            if last_action and last_action != "Système initialisé":
                phrases.append(f"Dernière action validée : {last_action}.")
            if pending:
                phrases.append(f"Il reste à vérifier : {', '.join(pending[:2])}.")

            if not phrases:
                return "Nous sommes en veille opérationnelle sur l'infrastructure. Aucune tâche bloquante en attente, Moctar."

            joined = " ".join(phrases)
            if not re.search(r"\bmoctar[.!?]?$", joined, re.I):
                joined = f"{joined.rstrip('.')} Moctar."
            return joined

    def format_prompt_context(self) -> str:
        """Injecte l'état courant de la tâche active dans le prompt système du LLM."""
        with self._lock:
            goal = self._state.get("goal", "Supervision")
            subtask = self._state.get("active_subtask", "Prêt")
            status = self._state.get("status", "in_progress")
            last_action = self._state.get("last_action", "N/A")
            pending = self._state.get("pending_checks", [])
            pending_str = ", ".join(pending) if pending else "Aucune"

            return (
                "[MÉMOIRE DE TRAVAIL ACTIVE - TASK STATE TRACKING]\n"
                f"- Objectif principal : {goal}\n"
                f"- Étape / Sous-tâche active : {subtask}\n"
                f"- Statut de travail : {status}\n"
                f"- Dernière action réalisée : {last_action}\n"
                f"- Vérifications en attente : {pending_str}\n"
                "Note : Si Moctar demande 'on en était où ?', 'qu'est-ce qu'on faisait ?' ou la prochaine étape, "
                "réfère-toi directement à cet état actif."
            )


_GLOBAL_TRACKER: Optional[ActiveTaskTracker] = None
_GLOBAL_LOCK = threading.Lock()


def get_task_tracker(storage_path: Optional[Path] = None) -> ActiveTaskTracker:
    """Retourne l'instance singleton du traqueur d'état de tâche active."""
    global _GLOBAL_TRACKER
    with _GLOBAL_LOCK:
        if _GLOBAL_TRACKER is None:
            _GLOBAL_TRACKER = ActiveTaskTracker(storage_path=storage_path)
        return _GLOBAL_TRACKER


def get_active_task_state() -> Dict[str, Any]:
    return get_task_tracker().get_state()


def update_active_task_state(**kwargs: Any) -> Dict[str, Any]:
    return get_task_tracker().update_state(**kwargs)


def record_task_action(action_desc: str) -> None:
    get_task_tracker().record_action(action_desc)


def format_active_task_speech() -> str:
    return get_task_tracker().format_speech_summary()


def format_active_task_prompt() -> str:
    return get_task_tracker().format_prompt_context()

