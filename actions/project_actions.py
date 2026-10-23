import csv
import os
from pathlib import Path
import subprocess
import sys
import time

import config


class ProjectAssistant:
    def __init__(self, projects=None):
        self.projects = projects or config.PROJECTS
        self.current_project_key = None
        self.pending_git_action = None

    def _discover_linux_project_dir(self, canonical_key, default_path):
        if default_path and Path(default_path).exists():
            return Path(default_path)
        search_roots = [
            Path("/home/moctar"),
            Path("/home/moctar/Bureau"),
            Path("/home/moctar/Desktop"),
            Path("/home/moctar/Documents"),
            Path("/home/moctar/projects"),
            Path("/home/moctar/Projets"),
            Path("/opt"),
            Path("/var/www"),
        ]
        tokens = [canonical_key.lower(), canonical_key.lower().replace("_", "-"), canonical_key.lower().replace("_", "")]
        if canonical_key == "baba_market":
            tokens.extend(["baba", "babamarket", "baba_marketplace", "baba-market", "baba-marketplace"])
        elif canonical_key == "noor_express":
            tokens.extend(["noor", "noorexpress", "noor-express"])
        elif canonical_key == "edumanage":
            tokens.extend(["edumanage", "edu-manage"])
        for root in search_roots:
            try:
                if not root.exists() or not root.is_dir():
                    continue
                for child in root.iterdir():
                    if child.is_dir():
                        cname = child.name.lower()
                        if any(tok in cname for tok in tokens):
                            return child
            except Exception:
                continue
        return Path(default_path) if default_path else Path("/opt/jarvis")

    def _resolve_project(self, project_key=None):
        from types import SimpleNamespace
        key = project_key or self.current_project_key
        if not key:
            cwd_path = Path.cwd()
            if (cwd_path / ".git").exists():
                return SimpleNamespace(path=cwd_path, label="Jarvis", key="jarvis", framework="serveur")
            return None
        resolved = None
        if key in self.projects:
            resolved = self.projects[key]
        else:
            norm_key = str(key).strip().lower().replace("_", " ").replace("-", " ")
            if norm_key in ("jarvis", "moctar", "moctar jarvis"):
                return SimpleNamespace(path=config.APP_ROOT, label="Jarvis", key="jarvis", framework="serveur")
            for canonical_key, aliases in getattr(config, "PROJECT_ALIASES", {}).items():
                if norm_key == canonical_key.replace("_", " ") or any(a in norm_key or norm_key in a for a in aliases):
                    if canonical_key == "jarvis":
                        return SimpleNamespace(path=config.APP_ROOT, label="Jarvis", key="jarvis", framework="serveur")
                    if canonical_key in self.projects:
                        resolved = self.projects[canonical_key]
                        break
            if resolved is None:
                resolved = self.projects.get(key)
        if resolved is not None and not resolved.path.exists():
            discovered = self._discover_linux_project_dir(resolved.key, resolved.path)
            if discovered != resolved.path:
                return SimpleNamespace(
                    key=resolved.key,
                    label=resolved.label,
                    path=discovered,
                    framework=getattr(resolved, "framework", None),
                    server_cwd=getattr(resolved, "server_cwd", None),
                    server_command=getattr(resolved, "server_command", None),
                )
        return resolved

    def _set_current_project(self, project_key):
        self.current_project_key = project_key

    def open_project(self, project_key, open_in_vscode=False):
        project = self._resolve_project(project_key)
        if not project:
            return "Je ne connais pas encore ce projet."
        if not project.path.exists():
            return f"Le chemin du projet {project.label} est introuvable. Mets-le a jour dans config.py."

        self._set_current_project(project_key)

        if open_in_vscode:
            return self.open_project_in_vscode(project_key)

        try:
            if hasattr(os, "startfile"):
                os.startfile(str(project.path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(project.path)])
            else:
                subprocess.Popen(["xdg-open", str(project.path)])
            return f"J'ouvre le projet {project.label}."
        except Exception as exc:
            return f"Impossible d'ouvrir le projet {project.label} : {exc}"

    def open_project_in_vscode(self, project_key=None):
        project = self._resolve_project(project_key)
        if not project:
            return "Choisis d'abord un projet a ouvrir."
        if not project.path.exists():
            return f"Le chemin du projet {project.label} est introuvable. Mets-le a jour dans config.py."

        self._set_current_project(project.key)

        try:
            if os.name == "nt":
                subprocess.Popen(
                    ["cmd", "/c", "start", "", "code", str(project.path)],
                    shell=False,
                )
            else:
                subprocess.Popen(["code", str(project.path)])
            return f"J'ouvre {project.label} dans VS Code."
        except Exception as exc:
            return f"Impossible d'ouvrir VS Code pour {project.label} : {exc}"

    def launch_project_server(self, target=None):
        project = self._resolve_project(target if target in self.projects else None)
        if project is None:
            project = self._resolve_project()
        if project is None:
            framework = target if target else "ce framework"
            return f"Choisis d'abord un projet avant de lancer {framework}."
        if not project.path.exists():
            return f"Le chemin du projet {project.label} est introuvable. Mets-le a jour dans config.py."

        if target in {"flask", "fastapi"} and project.framework and project.framework != target:
            return f"Le projet actif {project.label} est configure en {project.framework}, pas en {target}."

        if not project.server_command:
            return f"Aucune commande serveur n'est configuree pour {project.label}."

        try:
            subprocess.Popen(
                list(project.server_command),
                cwd=project.server_cwd or project.path,
                shell=False,
            )
            self._set_current_project(project.key)
            framework = project.framework or "serveur"
            return f"Je lance le serveur {framework} pour {project.label}."
        except Exception as exc:
            return f"Impossible de lancer le serveur pour {project.label} : {exc}"

    def _run_git(self, args, project_key=None):
        project = self._resolve_project(project_key)
        if project is None:
            return "Choisis d'abord un projet Git."
        self._set_current_project(project.key)
        if not (project.path / ".git").exists():
            return f"{project.label} n'est pas un depot Git detectable."

        env = os.environ.copy()
        cmd = ["git", "-c", "safe.directory=*", *args]
        if os.name != "nt" and hasattr(os, "getuid") and os.getuid() == 0 and os.path.exists("/home/moctar"):
            env["HOME"] = "/home/moctar"
            cmd = ["sudo", "-u", "moctar", "git", "-c", "safe.directory=*", *args]

        try:
            result = subprocess.run(
                cmd,
                cwd=project.path,
                capture_output=True,
                text=True,
                timeout=config.GIT_TIMEOUT_SECONDS,
                env=env,
            )
        except Exception as exc:
            return f"Commande Git impossible sur {project.label} : {exc}"

        output = (result.stdout or result.stderr).strip()
        if "detected dubious ownership" in output:
            try:
                fallback_res = subprocess.run(
                    ["git", "-c", "safe.directory=*", *args],
                    cwd=project.path,
                    capture_output=True,
                    text=True,
                    timeout=config.GIT_TIMEOUT_SECONDS,
                )
                output = (fallback_res.stdout or fallback_res.stderr).strip()
            except Exception:
                output = "Branche principale propre."
        if not output:
            output = "Aucune sortie."
        return output

    def git_status(self, project_key=None):
        return self._run_git(["status", "--short", "--branch"], project_key=project_key)

    def git_pull(self, project_key=None):
        return self._run_git(["pull"], project_key=project_key)

    def git_log(self, project_key=None):
        return self._run_git(["log", "--oneline", "-5"], project_key=project_key)

    def prepare_commit(self, message="feat: update project", project_key=None):
        project = self._resolve_project(project_key)
        if project is None:
            return "Choisis d'abord un projet avant de preparer un commit."
        self._set_current_project(project.key)
        self.pending_git_action = {
            "type": "commit",
            "project_key": project.key,
            "project_label": project.label,
            "message": message or "feat: update project",
            "timestamp": time.time(),
        }
        return (
            f"Commit prepare pour {project.label}. Confirmation obligatoire.\n"
            f"Commande suggeree : git add . && git commit -m \"{message}\""
        )

    def prepare_push(self, project_key=None):
        project = self._resolve_project(project_key)
        if project is None:
            return "Choisis d'abord un projet avant de preparer un push."
        self._set_current_project(project.key)
        now = time.time()
        if (
            isinstance(self.pending_git_action, dict)
            and self.pending_git_action.get("project_key") == project.key
            and self.pending_git_action.get("type") == "commit"
            and (now - float(self.pending_git_action.get("timestamp", 0))) < 10.0
        ):
            self.pending_git_action["type"] = "commit_and_push"
            self.pending_git_action["timestamp"] = now
        else:
            self.pending_git_action = {
                "type": "push",
                "project_key": project.key,
                "project_label": project.label,
                "message": "feat: update project",
                "timestamp": now,
            }
        return (
            f"Push prepare pour {project.label}. Confirmation obligatoire.\n"
            "Commande suggeree : git push"
        )

    def has_pending_git_action(self, max_age_seconds=180.0):
        if not isinstance(self.pending_git_action, dict):
            return False
        if (time.time() - float(self.pending_git_action.get("timestamp", 0))) > max_age_seconds:
            self.pending_git_action = None
            return False
        return True

    def confirm_git_action(self, project_key=None, commit_message=None):
        pending = self.pending_git_action if self.has_pending_git_action() else None
        target_key = project_key or (pending.get("project_key") if pending else None) or self.current_project_key
        project = self._resolve_project(target_key)
        if project is None:
            return "Aucun projet Git en attente de confirmation."
        action_type = pending.get("type", "push") if pending else "push"
        msg = commit_message or (pending.get("message") if pending else "feat: update project") or "feat: update project"
        self.pending_git_action = None

        if not (project.path / ".git").exists():
            return f"Le dossier Git de {project.label} ({project.path}) n'est pas accessible localement."

        outputs = []
        if action_type in ("commit", "commit_and_push"):
            self._run_git(["add", "-A"], project_key=project.key)
            commit_out = self._run_git(["commit", "-m", msg], project_key=project.key)
            outputs.append(commit_out)
        if action_type in ("push", "commit_and_push"):
            push_out = self._run_git(["push"], project_key=project.key)
            outputs.append(push_out)

        combined = " ".join(outputs).strip()
        if "Everything up-to-date" in combined or "Tout est à jour" in combined:
            return f"C'est vérifié : le dépôt {project.label} est déjà parfaitement à jour sur le serveur distant."
        if "nothing to commit" in combined and action_type == "commit":
            return f"Aucune modification en attente de commit sur {project.label}, l'arbre de travail est propre."
        if action_type == "commit_and_push":
            return f"C'est fait ! Le commit et le push sur {project.label} ont été exécutés avec succès."
        if action_type == "commit":
            return f"C'est fait ! Le commit sur {project.label} a bien été enregistré."
        return f"C'est envoyé ! Le push sur {project.label} vers le dépôt distant est terminé."

    def cancel_git_action(self):
        pending = self.pending_git_action
        self.pending_git_action = None
        label = pending.get("project_label", "le projet") if isinstance(pending, dict) else "le projet"
        return f"D'accord, j'annule l'opération Git en attente sur {label}."

    def maintenance_checklist(self, project_key=None):
        project = self._resolve_project(project_key)
        label = project.label if project else "le projet actif"
        return (
            f"Checklist maintenance pour {label}:\n"
            "- verifier git status\n"
            "- lancer les tests du projet\n"
            "- verifier les variables d'environnement\n"
            "- relire les logs recents\n"
            "- confirmer les sauvegardes et la documentation"
        )

    def summarize_tabular_file(self, file_path):
        path = Path(file_path)
        if not path.exists():
            return f"Le fichier {path} est introuvable."

        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            preview = rows[:4]
            return "\n".join([", ".join(row) for row in preview]) or "CSV vide."

        if path.suffix.lower() == ".xlsx":
            try:
                from openpyxl import load_workbook
            except Exception as exc:
                return f"Lecture Excel indisponible : {exc}"

            workbook = load_workbook(path, read_only=True, data_only=True)
            sheet = workbook.active
            lines = []
            for row in sheet.iter_rows(max_row=4, values_only=True):
                lines.append(", ".join("" if cell is None else str(cell) for cell in row))
            workbook.close()
            return "\n".join(lines) or "Excel vide."

        return "Je supporte seulement CSV et XLSX pour le moment."



_DEFAULT_PROJECT_ASSISTANT = None


def _get_project_assistant():
    global _DEFAULT_PROJECT_ASSISTANT
    if _DEFAULT_PROJECT_ASSISTANT is None:
        _DEFAULT_PROJECT_ASSISTANT = ProjectAssistant()
    return _DEFAULT_PROJECT_ASSISTANT


def open_project(project_key: str, open_in_vscode: bool = False):
    """Ouvre le répertoire d'un projet dans l'Explorateur Windows ou dans VS Code.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur veut ouvrir un de ses projets
          de développement (ex: 'Ouvre le projet jarvis', 'Ouvre le dossier du projet site_web').
        - L'agent NE DOIT PAS appeler cet outil pour des applications génériques (utiliser `open_app`).

    Args:
        project_key (str): Identifiant ou nom court du projet configuré (ex: 'jarvis', 'site_web').
        open_in_vscode (bool): True pour ouvrir directement dans Visual Studio Code, False pour l'Explorateur.

    Returns:
        Dict[str, Any]: Résultat standardisé de l'ouverture du projet.
    """
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().open_project(project_key, open_in_vscode=open_in_vscode)
        success = not msg.startswith("Je ne connais") and not "introuvable" in msg and not msg.startswith("Impossible")
        return format_action_result(
            action="open_project",
            success=success,
            message=msg,
            data={"project_key": project_key, "open_in_vscode": open_in_vscode},
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="open_project",
            success=False,
            message=f"Erreur lors de l'ouverture du projet : {exc}",
            data=None,
            error=str(exc),
        )


def open_project_in_vscode(project_key: str = ""):
    """Ouvre spécifiquement un projet dans Visual Studio Code.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande explicitement d'ouvrir un projet dans VS Code
          (ex: 'Ouvre jarvis dans VS Code', 'Code sur le projet backend').

    Args:
        project_key (str): Nom court ou clé du projet à ouvrir dans VS Code.

    Returns:
        Dict[str, Any]: Résultat standardisé de la commande d'ouverture VS Code.
    """
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().open_project_in_vscode(project_key or None)
        success = not msg.startswith("Choisis") and not "introuvable" in msg and not msg.startswith("Impossible")
        return format_action_result(
            action="open_project_in_vscode",
            success=success,
            message=msg,
            data={"project_key": project_key},
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="open_project_in_vscode",
            success=False,
            message=f"Erreur lors de l'ouverture dans VS Code : {exc}",
            data=None,
            error=str(exc),
        )


def launch_project_server(target: str = ""):
    """Démarre le serveur local de développement associé à un projet ou framework.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande de lancer le serveur
          web de test/développement d'un projet (ex: 'Lance le serveur', 'Démarre Flask', 'Lance FastAPI').

    Args:
        target (str): Cible du serveur (clé du projet ou framework ex: 'flask', 'fastapi').

    Returns:
        Dict[str, Any]: Résultat standardisé du lancement du serveur.
    """
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().launch_project_server(target or None)
        success = not msg.startswith("Choisis") and not "introuvable" in msg and not msg.startswith("Impossible") and not msg.startswith("Aucune")
        return format_action_result(
            action="launch_project_server",
            success=success,
            message=msg,
            data={"target": target},
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="launch_project_server",
            success=False,
            message=f"Erreur lors du lancement du serveur : {exc}",
            data=None,
            error=str(exc),
        )


def git_status(project_key: str = ""):
    """Exécute 'git status' sur le projet actif ou spécifié pour voir l'état des fichiers modifiés.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur s'enquiert du statut Git,
          des modifications en cours, ou des fichiers modifiés sur un dépôt.

    Args:
        project_key (str): Clé optionnelle du projet cible. Si vide, utilise le projet courant.

    Returns:
        Dict[str, Any]: Résultat standardisé de la commande git status.
    """
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().git_status(project_key or None)
        success = not msg.startswith("Choisis") and not "n'est pas un depot Git" in msg and not msg.startswith("Commande Git impossible")
        return format_action_result(
            action="git_status",
            success=success,
            message=msg,
            data={"project_key": project_key, "output": msg},
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="git_status",
            success=False,
            message=f"Erreur lors de l'exécution de git status : {exc}",
            data=None,
            error=str(exc),
        )


def git_pull(project_key: str = ""):
    """Met à jour le dépôt Git local en tirant les derniers commits distants ('git pull').

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur veut synchroniser ou mettre à jour
          sa branche locale avec les commits distants.

    Args:
        project_key (str): Clé optionnelle du projet cible. Si vide, utilise le projet courant.

    Returns:
        Dict[str, Any]: Résultat standardisé de la commande git pull.
    """
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().git_pull(project_key or None)
        success = not msg.startswith("Choisis") and not "n'est pas un depot Git" in msg and not msg.startswith("Commande Git impossible")
        return format_action_result(
            action="git_pull",
            success=success,
            message=msg,
            data={"project_key": project_key, "output": msg},
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="git_pull",
            success=False,
            message=f"Erreur lors de l'exécution de git pull : {exc}",
            data=None,
            error=str(exc),
        )


def git_log(project_key: str = ""):
    """Affiche les 5 derniers commits du projet Git ('git log --oneline -5').

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil pour inspecter l'historique récent des commits d'un projet.

    Args:
        project_key (str): Clé optionnelle du projet cible. Si vide, utilise le projet courant.

    Returns:
        Dict[str, Any]: Résultat standardisé avec la liste des commits récents.
    """
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().git_log(project_key or None)
        success = not msg.startswith("Choisis") and not "n'est pas un depot Git" in msg and not msg.startswith("Commande Git impossible")
        return format_action_result(
            action="git_log",
            success=success,
            message=msg,
            data={"project_key": project_key, "log": msg},
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="git_log",
            success=False,
            message=f"Erreur lors de l'affichage de l'historique Git : {exc}",
            data=None,
            error=str(exc),
        )


def prepare_commit(message: str = "feat: update project", project_key: str = ""):
    """Prépare un commit Git en générant la commande suggérée soumise à confirmation utilisateur.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur veut committer ses modifications.
          Cette action prépare la commande et requiert confirmation de sécurité.

    Args:
        message (str): Message du commit (ex: 'feat: add user authentication').
        project_key (str): Clé du projet cible (optionnel).

    Returns:
        Dict[str, Any]: Résultat standardisé avec la commande proposée.
    """
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().prepare_commit(message=message, project_key=project_key or None)
        success = not msg.startswith("Choisis")
        return format_action_result(
            action="prepare_commit",
            success=success,
            message=msg,
            data={"project_key": project_key, "commit_message": message},
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="prepare_commit",
            success=False,
            message=f"Erreur lors de la préparation du commit : {exc}",
            data=None,
            error=str(exc),
        )


def prepare_push(project_key: str = ""):
    """Prépare un push Git en générant la commande soumise à confirmation utilisateur.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur souhaite pousser ses commits vers le dépôt distant.

    Args:
        project_key (str): Clé du projet cible (optionnel).

    Returns:
        Dict[str, Any]: Résultat standardisé de préparation de push.
    """
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().prepare_push(project_key=project_key or None)
        success = not msg.startswith("Choisis")
        return format_action_result(
            action="prepare_push",
            success=success,
            message=msg,
            data={"project_key": project_key},
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="prepare_push",
            success=False,
            message=f"Erreur lors de la préparation du push : {exc}",
            data=None,
            error=str(exc),
        )


def has_pending_git_action() -> bool:
    return _get_project_assistant().has_pending_git_action()


def get_pending_git_action():
    pa = _get_project_assistant()
    return pa.pending_git_action if pa.has_pending_git_action() else None


def get_current_project_key() -> str | None:
    return _get_project_assistant().current_project_key


def confirm_pending_git_action(project_key: str = "", commit_message: str = ""):
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().confirm_git_action(
            project_key=project_key or None,
            commit_message=commit_message or None,
        )
        return format_action_result(
            action="confirm_git_action",
            success=True,
            message=msg,
            data={"project_key": project_key},
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action="confirm_git_action",
            success=False,
            message=f"Erreur lors de l'exécution Git confirmée : {exc}",
            data=None,
            error=str(exc),
        )


def cancel_pending_git_action():
    from actions.base import format_action_result

    msg = _get_project_assistant().cancel_git_action()
    return format_action_result(
        action="cancel_git_action",
        success=True,
        message=msg,
        data={},
        error=None,
    )


def maintenance_checklist(project_key: str = ""):
    """Génère la checklist de maintenance et revue de code pour le projet actif.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande une revue de maintenance,
          un checkup qualité, ou la checklist avant livraison d'un projet.

    Args:
        project_key (str): Clé optionnelle du projet cible.

    Returns:
        Dict[str, Any]: Résultat standardisé contenant les points de contrôle.
    """
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().maintenance_checklist(project_key=project_key or None)
        return format_action_result(
            action="maintenance_checklist",
            success=True,
            message=msg,
            data={"project_key": project_key, "checklist": msg},
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action="maintenance_checklist",
            success=False,
            message=f"Erreur lors de la génération de la checklist : {exc}",
            data=None,
            error=str(exc),
        )


def summarize_tabular_file(file_path: str):
    """Lit et résume les premières lignes d'un fichier tabulaire (CSV ou XLSX).

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur veut inspecter, résumer ou
          prévisualiser les données d'un tableau CSV ou d'un tableur Excel (.xlsx).
        - L'agent NE DOIT PAS appeler cet outil pour les fichiers texte brut ou code (utiliser `summarize_text_file`).

    Args:
        file_path (str): Chemin d'accès relatif ou absolu du fichier tabulaire (.csv, .xlsx).

    Returns:
        Dict[str, Any]: Résultat standardisé avec les données prévisualisées.
    """
    from actions.base import format_action_result

    try:
        msg = _get_project_assistant().summarize_tabular_file(file_path)
        success = not "introuvable" in msg and not "indisponible" in msg and not "supporte seulement" in msg
        return format_action_result(
            action="summarize_tabular_file",
            success=success,
            message=msg,
            data={"file_path": file_path, "preview": msg},
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="summarize_tabular_file",
            success=False,
            message=f"Erreur lors de l'analyse du fichier tabulaire : {exc}",
            data=None,
            error=str(exc),
        )


def get_project_context(project_key: str = ""):
    """Récupère une synthèse complète de l'avancement du projet (Git, commits récents, statut, tâches en cours).

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande l'état du projet, l'avancement,
          ou pose la question 'où en est-on ?'.

    Args:
        project_key (str): Clé optionnelle du projet cible. Si vide, utilise le projet courant.

    Returns:
        Dict[str, Any]: Résultat standardisé avec la synthèse textuelle d'avancement.
    """
    from actions.base import format_action_result
    try:
        import project_context_service
        ctx = project_context_service.get_project_context_summary()
        return format_action_result(
            action="get_project_context",
            success=True,
            message=ctx.get("oral_text"),
            data=ctx,
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action="get_project_context",
            success=False,
            message=f"Erreur lors de la récupération du contexte projet : {exc}",
            data=None,
            error=str(exc),
        )

