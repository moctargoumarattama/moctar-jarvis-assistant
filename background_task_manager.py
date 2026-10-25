"""Gestionnaire de Tâches Autonomes en Arrière-Plan pour JARVIS / M.O.C.T.A.R.

Permet à Moctar de déléguer des tâches longues (ex: tests unitaires, build, git pull, backup)
sans bloquer la conversation vocale (< 300 ms de latence perçue).
À la fin de la tâche, le résultat est consigné et notifié à ProactiveCompanion pour
que M.O.C.T.A.R en fasse le compte-rendu vocal oral ou sur le tableau de bord.
"""

from __future__ import annotations

from copy import deepcopy
import logging
import os
import shlex
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

import config

LOGGER = logging.getLogger("moctar.background_task_manager")


class BackgroundTaskManager:
    """Orchestrateur de processus asynchrones non-bloquants et thread-safe."""

    _instance: Optional[BackgroundTaskManager] = None
    _instance_lock = threading.RLock()

    def __init__(self) -> None:
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> BackgroundTaskManager:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start_task(
        self,
        command: str,
        description: str,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Lance une commande en tâche de fond avec un UUID sans collision et retourne immédiatement."""
        with self._lock:
            tid = task_id or f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            while tid in self._tasks and task_id is None:
                tid = f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            now_ts = time.time()
            task_info: Dict[str, Any] = {
                "id": tid,
                "command": command,
                "description": description or command,
                "state": "PENDING",
                "status": "pending",
                "created_at": now_ts,
                "start_time": now_ts,
                "end_time": None,
                "exit_code": None,
                "output": "",
            }
            self._tasks[tid] = task_info

        thread = threading.Thread(
            target=self._run_worker,
            args=(tid, command, description),
            name=f"bg-worker-{tid}",
            daemon=True,
        )
        thread.start()
        # Détection ultra-rapide (< 850ms) des commandes qui se terminent ou échouent immédiatement
        thread.join(timeout=0.85)
        with self._lock:
            info = deepcopy(self._tasks.get(tid, {}))
            if not thread.is_alive():
                state_upper = str(info.get("state") or "").upper()
                if state_upper == "COMPLETED" or info.get("status") in ("success", "completed"):
                    LOGGER.info("⚡ [BG TASK COMPLETED FAST]: '%s' (%s)", description, tid)
                    return {
                        "success": True,
                        "task_id": tid,
                        "state": "COMPLETED",
                        "status": "completed",
                        "created_at": info.get("created_at"),
                        "start_time": info.get("start_time"),
                        "end_time": info.get("end_time"),
                        "output": info.get("output", ""),
                        "message": f"Tâche '{description}' terminée avec succès : {info.get('output', '')[:200]}",
                    }
                else:
                    LOGGER.warning("⚡ [BG TASK FAILED FAST]: '%s' (%s, code %s)", description, tid, info.get("exit_code"))
                    return {
                        "success": False,
                        "task_id": tid,
                        "state": "FAILED",
                        "status": "failed",
                        "created_at": info.get("created_at"),
                        "start_time": info.get("start_time"),
                        "end_time": info.get("end_time"),
                        "exit_code": info.get("exit_code"),
                        "output": info.get("output", ""),
                        "message": f"Échec immédiat de la tâche '{description}' (code {info.get('exit_code')}) : {info.get('output', '')[:200]}",
                    }

        LOGGER.info("⚡ [BG TASK STARTED]: '%s' (%s, id=%s)", description, command, tid)
        return {
            "success": True,
            "task_id": tid,
            "state": "RUNNING",
            "status": "running",
            "message": f"Tâche '{description}' lancée en tâche de fond.",
        }

    def _run_worker(self, task_id: str, command: str, description: str) -> None:
        """Exécute la commande de façon isolée et capture le compte-rendu."""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["state"] = "RUNNING"
                self._tasks[task_id]["status"] = "running"
                self._tasks[task_id]["start_time"] = time.time()

        out = ""
        code = -1
        cwd = str(config.APP_ROOT)
        env = os.environ.copy()
        run_cmd = command

        # Gestion des droits Linux / Ubuntu sur le serveur Dell Precision M4800
        if os.name != "nt":
            if hasattr(os, "getuid") and os.getuid() == 0 and os.path.isdir("/home/moctar"):
                env["HOME"] = "/home/moctar"
                env["USER"] = "moctar"
                # Prévention des erreurs Git dubious ownership
                try:
                    subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], timeout=2)
                except Exception:
                    pass
                if "git " in command or command.startswith("git"):
                    run_cmd = f"sudo -u moctar bash -c {shlex.quote('cd ' + cwd + ' && ' + command)}"

        try:
            # Sécurité : exécution dans le répertoire de l'application avec timeout
            proc = subprocess.run(
                run_cmd,
                shell=True,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=300,
            )
            code = proc.returncode
            out = (proc.stdout or "").strip()
        except subprocess.TimeoutExpired:
            code = 124
            out = "Dépassement du délai maximal de 5 minutes."
        except Exception as exc:
            code = 1
            out = f"Erreur d'exécution : {exc}"

        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["state"] = "COMPLETED" if code == 0 else "FAILED"
                self._tasks[task_id]["status"] = "completed" if code == 0 else "failed"
                self._tasks[task_id]["end_time"] = time.time()
                self._tasks[task_id]["exit_code"] = code
                self._tasks[task_id]["output"] = out[:1000]

        LOGGER.info(
            "⚡ [BG TASK FINISHED]: '%s' (%s) -> %s (code %d)",
            description,
            task_id,
            "COMPLETED" if code == 0 else "FAILED",
            code,
        )

        # Notification proactive au compagnon
        try:
            from proactive_companion import ProactiveCompanion
            comp = ProactiveCompanion.get_instance()
            result_label = "terminée avec succès" if code == 0 else "terminée avec des erreurs"
            comp._pending_suggestion = {
                "category": "task_completed",
                "message": f"Moctar, la tâche '{description}' est {result_label}.",
                "target": task_id,
                "priority": "normal",
            }
        except Exception:
            pass

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._tasks.get(task_id)
            return deepcopy(item) if item is not None else None

    def list_tasks(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._tasks.values())[-limit:]
            return [deepcopy(item) for item in items]


def run_background_task(command: str, description: str = "") -> Dict[str, Any]:
    """Outil standard pour lancer une commande système en tâche de fond."""
    manager = BackgroundTaskManager.get_instance()
    return manager.start_task(command=command, description=description or command)

