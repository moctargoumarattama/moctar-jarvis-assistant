"""Gestionnaire de Tâches Autonomes en Arrière-Plan pour JARVIS / M.O.C.T.A.R.

Permet à Moctar de déléguer des tâches longues (ex: tests unitaires, build, git pull, backup)
sans bloquer la conversation vocale (< 300 ms de latence perçue).
À la fin de la tâche, le résultat est consigné et notifié à ProactiveCompanion pour
que M.O.C.T.A.R en fasse le compte-rendu vocal oral ou sur le tableau de bord.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

import config

LOGGER = logging.getLogger("moctar.background_task_manager")


class BackgroundTaskManager:
    """Orchestrateur de processus asynchrones non-bloquants."""

    _instance: Optional[BackgroundTaskManager] = None

    def __init__(self) -> None:
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> BackgroundTaskManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start_task(
        self,
        command: str,
        description: str,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Lance une commande en tâche de fond et retourne immédiatement."""
        tid = task_id or f"task_{int(time.time())}"
        task_info = {
            "id": tid,
            "command": command,
            "description": description or command,
            "status": "running",
            "start_time": time.time(),
            "end_time": None,
            "exit_code": None,
            "output": "",
        }

        with self._lock:
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
            info = self._tasks.get(tid, {})
            if not thread.is_alive():
                if info.get("status") == "success":
                    LOGGER.info("⚡ [BG TASK COMPLETED FAST]: '%s'", description)
                    return {
                        "success": True,
                        "task_id": tid,
                        "status": "completed",
                        "output": info.get("output", ""),
                        "message": f"Tâche '{description}' terminée avec succès : {info.get('output', '')[:200]}",
                    }
                else:
                    LOGGER.warning("⚡ [BG TASK FAILED FAST]: '%s' (code %s)", description, info.get("exit_code"))
                    return {
                        "success": False,
                        "task_id": tid,
                        "status": "failed",
                        "exit_code": info.get("exit_code"),
                        "output": info.get("output", ""),
                        "message": f"Échec immédiat de la tâche '{description}' (code {info.get('exit_code')}) : {info.get('output', '')[:200]}",
                    }

        LOGGER.info("⚡ [BG TASK STARTED]: '%s' (%s)", description, command)
        return {
            "success": True,
            "task_id": tid,
            "status": "running",
            "message": f"Tâche '{description}' lancée en tâche de fond.",
        }

    def _run_worker(self, task_id: str, command: str, description: str) -> None:
        """Exécute la commande de façon isolée et capture le compte-rendu."""
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
                self._tasks[task_id]["status"] = "success" if code == 0 else "failed"
                self._tasks[task_id]["end_time"] = time.time()
                self._tasks[task_id]["exit_code"] = code
                self._tasks[task_id]["output"] = out[:1000]

        LOGGER.info(
            "⚡ [BG TASK FINISHED]: '%s' -> %s (code %d)",
            description,
            "succès" if code == 0 else "échec",
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
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._tasks.values())[-limit:]


def run_background_task(command: str, description: str = "") -> Dict[str, Any]:
    """Outil standard pour lancer une commande système en tâche de fond."""
    manager = BackgroundTaskManager.get_instance()
    return manager.start_task(command=command, description=description or command)

