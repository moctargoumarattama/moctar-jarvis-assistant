import csv
import os
from pathlib import Path
import subprocess
import sys

import config


class ProjectAssistant:
    def __init__(self, projects=None):
        self.projects = projects or config.PROJECTS
        self.current_project_key = None

    def _resolve_project(self, project_key=None):
        key = project_key or self.current_project_key
        if not key:
            return None
        return self.projects.get(key)

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
        if not (project.path / ".git").exists():
            return f"{project.label} n'est pas un depot Git detectable."

        try:
            result = subprocess.run(
                ["git", *args],
                cwd=project.path,
                capture_output=True,
                text=True,
                timeout=config.GIT_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            return f"Commande Git impossible sur {project.label} : {exc}"

        output = (result.stdout or result.stderr).strip()
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
        return (
            f"Commit prepare pour {project.label}. Confirmation obligatoire.\n"
            f"Commande suggeree : git add . && git commit -m \"{message}\""
        )

    def prepare_push(self, project_key=None):
        project = self._resolve_project(project_key)
        if project is None:
            return "Choisis d'abord un projet avant de preparer un push."
        return (
            f"Push prepare pour {project.label}. Confirmation obligatoire.\n"
            "Commande suggeree : git push"
        )

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
