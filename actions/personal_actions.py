import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from dateutil import parser as date_parser
except Exception:
    date_parser = None

import config


class PersonalAssistant:
    def __init__(self, notes_dir=None, data_dir=None):
        self.notes_dir = Path(notes_dir or config.NOTES_DIR)
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.todos_file = self.data_dir / "todos.json"
        self.reminders_file = self.data_dir / "reminders.json"
        self._ensure_storage()

    def _ensure_storage(self):
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.todos_file.exists():
            self.todos_file.write_text("[]", encoding="utf-8")
        if not self.reminders_file.exists():
            self.reminders_file.write_text("[]", encoding="utf-8")

    def _load_json(self, path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_json(self, path, data):
        temp_file = path.with_suffix(f".tmp_{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
        try:
            temp_file.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
            temp_file.replace(path)
        except Exception:
            # Fallback direct en cas de verrou de système de fichiers sur Windows
            path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass

    def _slugify(self, text):
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
        return slug or "note"

    def create_note(self, content):
        if not content.strip():
            return "Dis-moi le contenu de la note."
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = self._slugify(content[:40])
        note_path = self.notes_dir / f"{timestamp}_{slug}.txt"
        note_path.write_text(content.strip() + "\n", encoding="utf-8")
        return f"Note creee : {note_path.name}"

    def add_todo(self, content):
        if not content.strip():
            return "Dis-moi le contenu du todo."
        todos = self._load_json(self.todos_file)
        todos.append(
            {
                "id": len(todos) + 1,
                "content": content.strip(),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "done": False,
            }
        )
        self._save_json(self.todos_file, todos)
        return f"Todo ajoute : {content.strip()}"

    def list_todos(self):
        todos = self._load_json(self.todos_file)
        if not todos:
            return "Tu n'as aucun todo."

        lines = ["Todos en cours :"]
        for todo in todos[:10]:
            status = "fait" if todo.get("done") else "a faire"
            lines.append(f"- [{status}] {todo.get('content', '')}")
        return "\n".join(lines)

    def get_open_todos(self, limit=10):
        todos = self._load_json(self.todos_file)
        open_todos = [todo for todo in todos if not todo.get("done")]
        return open_todos[:limit]

    def list_todos_for_day(self, day="tomorrow", now=None):
        reference = now or datetime.now()
        if day != "tomorrow":
            target_date = reference.date()
            day_label = "aujourd'hui"
            content_markers = ("aujourd'hui", "aujourdhui")
        else:
            target_date = (reference + timedelta(days=1)).date()
            day_label = "demain"
            content_markers = ("demain",)

        todo_items = []
        for todo in self._load_json(self.todos_file):
            if todo.get("done"):
                continue
            content = str(todo.get("content", "")).strip()
            due_at_raw = str(todo.get("due_at", "")).strip()
            due_matches = False
            if due_at_raw:
                try:
                    due_matches = datetime.fromisoformat(due_at_raw).date() == target_date
                except ValueError:
                    pass
            if due_matches or any(marker in content.lower() for marker in content_markers):
                todo_items.append(content)

        reminder_items = []
        for reminder in self._load_json(self.reminders_file):
            if reminder.get("notified"):
                continue
            try:
                due_at = datetime.fromisoformat(str(reminder.get("due_at", "")))
            except ValueError:
                continue
            if due_at.date() == target_date:
                reminder_items.append(
                    f"{due_at.strftime('%H:%M')} - {str(reminder.get('content', '')).strip()}"
                )

        if not todo_items and not reminder_items:
            return f"Tu n'as aucune tache ni aucun rappel programme pour {day_label}."

        lines = [f"Pour {day_label} :"]
        lines.extend(f"- Tache : {content}" for content in todo_items[:10])
        lines.extend(f"- Rappel : {content}" for content in reminder_items[:10])
        return "\n".join(lines)

    def search(self, query):
        query_lower = query.lower().strip()
        query_tokens = {token for token in re.findall(r"\w+", query_lower) if token}
        todos = self._load_json(self.todos_file)
        todo_hits = []
        scored_todo_hits = []
        for todo in todos:
            content = todo.get("content", "")
            content_lower = content.lower()
            if query_lower in content_lower:
                todo_hits.append(content)
                continue
            overlap = len(query_tokens.intersection(set(re.findall(r"\w+", content_lower))))
            if overlap > 0:
                scored_todo_hits.append((overlap, content))
        note_hits = []
        scored_note_hits = []
        for note_path in self.notes_dir.glob("*.txt"):
            content = note_path.read_text(encoding="utf-8", errors="ignore")
            if query_lower in content.lower() or query_lower in note_path.name.lower():
                note_hits.append(note_path.name)
                continue
            overlap = len(query_tokens.intersection(set(re.findall(r"\w+", content.lower()))))
            if overlap > 0:
                scored_note_hits.append((overlap, note_path.name))

        if not todo_hits and not note_hits and not scored_todo_hits and not scored_note_hits:
            return "Aucun resultat dans notes et todos."

        lines = []
        if todo_hits:
            lines.append("Todos trouves :")
            lines.extend(f"- {item}" for item in todo_hits[:5])
        elif scored_todo_hits:
            lines.append("Todos pertinents :")
            scored_todo_hits.sort(key=lambda item: item[0], reverse=True)
            lines.extend(f"- {item}" for _score, item in scored_todo_hits[:5])
        if note_hits:
            lines.append("Notes trouvees :")
            lines.extend(f"- {item}" for item in note_hits[:5])
        elif scored_note_hits:
            lines.append("Notes pertinentes :")
            scored_note_hits.sort(key=lambda item: item[0], reverse=True)
            lines.extend(f"- {item}" for _score, item in scored_note_hits[:5])
        return "\n".join(lines)

    def _parse_reminder_time(self, when_text, now=None):
        reference = now or datetime.now()
        cleaned = when_text.strip().lower()
        cleaned = cleaned.replace(" a ", " ")
        cleaned = cleaned.replace(" a", " ")
        cleaned = cleaned.replace("heure", "h")

        if "demain" in cleaned:
            cleaned = cleaned.replace("demain", "").strip()
            reference = reference + timedelta(days=1)

        cleaned = cleaned.replace("h", ":")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if re.fullmatch(r"\d{1,2}", cleaned):
            cleaned = f"{cleaned}:00"

        if date_parser is not None:
            return date_parser.parse(cleaned, default=reference)

        if re.fullmatch(r"\d{1,2}:\d{2}", cleaned):
            hour, minute = map(int, cleaned.split(":"))
            return reference.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2})?", cleaned):
            normalized = cleaned.replace(" ", "T")
            try:
                parsed = datetime.fromisoformat(normalized)
                return parsed
            except ValueError:
                pass

        raise ValueError(f"Impossible de parser l'heure du rappel: {when_text!r}")

    def schedule_reminder(self, content, when_text, now=None):
        if not content.strip():
            return "Dis-moi le message du rappel."
        if not when_text:
            return "Dis-moi aussi l'heure du rappel."

        due_at = self._parse_reminder_time(when_text, now=now)
        reminders = self._load_json(self.reminders_file)
        reminders.append(
            {
                "content": content.strip(),
                "due_at": due_at.isoformat(timespec="seconds"),
                "notified": False,
            }
        )
        self._save_json(self.reminders_file, reminders)
        return f"Rappel programme pour {due_at.strftime('%Y-%m-%d %H:%M')} : {content.strip()}"

    def poll_due_reminders(self, now=None):
        current = now or datetime.now()
        reminders = self._load_json(self.reminders_file)
        due_messages = []

        for reminder in reminders:
            due_at = datetime.fromisoformat(reminder["due_at"])
            if not reminder.get("notified") and due_at <= current:
                reminder["notified"] = True
                due_messages.append(f"Rappel : {reminder['content']}")

        if due_messages:
            self._save_json(self.reminders_file, reminders)
        return due_messages

    def get_upcoming_reminders(self, limit=5, now=None):
        current = now or datetime.now()
        reminders = self._load_json(self.reminders_file)
        upcoming = []
        for reminder in reminders:
            due_at_raw = reminder.get("due_at")
            if not due_at_raw:
                continue
            try:
                due_at = datetime.fromisoformat(due_at_raw)
            except ValueError:
                continue
            if reminder.get("notified"):
                continue
            if due_at >= current:
                item = dict(reminder)
                item["due_at"] = due_at.isoformat(timespec="seconds")
                upcoming.append(item)

        upcoming.sort(key=lambda item: item["due_at"])
        return upcoming[:limit]

    def summarize_file(self, file_path):
        path = Path(file_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            return f"Le fichier {path} est introuvable."

        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return "Le fichier est vide."
        highlights = self._build_smart_highlights(lines)
        preview = "\n".join(f"- {line}" for line in highlights[:4])
        return (
            f"Resume intelligent local ({path.name}) :\n"
            f"- Lignes utiles analysees : {len(lines)}\n"
            f"{preview}"
        )

    def _build_smart_highlights(self, lines):
        if not lines:
            return []

        keywords = {
            "priorite": 5,
            "urgent": 5,
            "todo": 4,
            "action": 4,
            "deadline": 4,
            "prochaine": 3,
            "bloquant": 4,
            "decision": 3,
            "risque": 4,
            "important": 3,
            "focus": 3,
            "projet": 2,
        }
        scored = []
        for index, line in enumerate(lines):
            lower = line.lower()
            score = 1
            score += sum(weight for token, weight in keywords.items() if token in lower)
            if re.search(r"\d{1,2}[:h]\d{2}", lower):
                score += 2
            if len(line) > 130:
                score -= 1
            score += max(0, 3 - min(index, 3))
            scored.append((score, index, line))

        scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        selected = sorted(scored[:4], key=lambda item: item[1])
        return [line for _score, _index, line in selected]



_DEFAULT_PERSONAL_ASSISTANT = None


def _get_personal_assistant():
    global _DEFAULT_PERSONAL_ASSISTANT
    if _DEFAULT_PERSONAL_ASSISTANT is None:
        _DEFAULT_PERSONAL_ASSISTANT = PersonalAssistant()
    return _DEFAULT_PERSONAL_ASSISTANT


def create_note(content: str):
    """Crée et enregistre une note textuelle personnelle sur le système local.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur veut noter, mémoriser,
          consigner ou sauvegarder une information textuelle ou une idée (ex: 'Prends une note',
          'Note que l'adresse est...', 'Rappelle-moi d'écrire cette note').
        - L'agent NE DOIT PAS appeler cet outil pour une tâche à cocher (utiliser `add_todo`)
          ni pour un rappel avec alerte horaire programmée (utiliser `schedule_reminder`).

    Args:
        content (str): Contenu textuel complet de la note à enregistrer.

    Returns:
        Dict[str, Any]: Résultat standardisé de la création de note.
    """
    from actions.base import format_action_result

    try:
        msg = _get_personal_assistant().create_note(content)
        success = not msg.startswith("Dis-moi")
        return format_action_result(
            action="create_note",
            success=success,
            message=msg,
            data={"content": content} if success else None,
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="create_note",
            success=False,
            message=f"Erreur lors de la création de la note : {exc}",
            data=None,
            error=str(exc),
        )


def add_todo(content: str):
    """Ajoute une tâche à la to-do list personnelle de l'utilisateur.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur veut ajouter une tâche à faire,
          un élément de to-do list (ex: 'Ajoute acheter du lait à ma liste', 'Mets dans mes tâches : appeler Marc').
        - L'agent NE DOIT PAS appeler cet outil pour consulter les tâches existantes (utiliser `list_todos`
          ou `list_todos_for_day`).

    Args:
        content (str): Description de la tâche à accomplir.

    Returns:
        Dict[str, Any]: Résultat standardisé de l'ajout de tâche.
    """
    from actions.base import format_action_result

    try:
        msg = _get_personal_assistant().add_todo(content)
        success = not msg.startswith("Dis-moi")
        return format_action_result(
            action="add_todo",
            success=success,
            message=msg,
            data={"content": content} if success else None,
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="add_todo",
            success=False,
            message=f"Erreur lors de l'ajout du todo : {exc}",
            data=None,
            error=str(exc),
        )


def list_todos():
    """Affiche la liste des tâches (to-do items) en cours de l'utilisateur.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande à voir ses tâches,
          sa to-do list, ou ce qu'il a à faire de manière générale.
        - L'agent NE DOIT PAS appeler cet outil si l'utilisateur demande spécifiquement les tâches
          d'un jour précis comme aujourd'hui ou demain (utiliser `list_todos_for_day`).

    Args:
        Aucun argument requis.

    Returns:
        Dict[str, Any]: Résultat standardisé contenant la liste des tâches.
    """
    from actions.base import format_action_result

    try:
        assistant = _get_personal_assistant()
        msg = assistant.list_todos()
        open_todos = assistant.get_open_todos()
        return format_action_result(
            action="list_todos",
            success=True,
            message=msg,
            data=open_todos,
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action="list_todos",
            success=False,
            message=f"Erreur lors de la lecture des todos : {exc}",
            data=None,
            error=str(exc),
        )


def list_todos_for_day(day: str = "demain"):
    """Affiche les tâches et rappels prévus pour un jour donné (aujourd'hui ou demain).

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande son planning,
          ses tâches ou ses rappels pour un jour particulier (ex: 'Qu'ai-je de prévu demain ?',
          'Mon programme d'aujourd'hui').
        - L'agent NE DOIT PAS appeler cet outil pour consulter la to-do list générale sans critère temporel.

    Args:
        day (str): Jour cible pour le filtre. Valeurs recommandées: 'demain' ou 'aujourd hui'.

    Returns:
        Dict[str, Any]: Résultat standardisé contenant les tâches et rappels du jour.
    """
    from actions.base import format_action_result

    try:
        msg = _get_personal_assistant().list_todos_for_day(day=day)
        return format_action_result(
            action="list_todos_for_day",
            success=True,
            message=msg,
            data={"day": day, "summary": msg},
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action="list_todos_for_day",
            success=False,
            message=f"Erreur lors de la récupération des tâches du jour : {exc}",
            data=None,
            error=str(exc),
        )


def search_personal_data(query: str):
    """Recherche une information ou un mot-clé dans l'ensemble des notes et tâches personnelles.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur cherche une note ou une tâche
          précédemment enregistrée (ex: 'Où ai-je noté le code de la porte ?', 'Cherche la note sur le projet X').
        - L'agent NE DOIT PAS appeler cet outil pour effectuer une recherche sur internet
          (utiliser `search_google`).

    Args:
        query (str): Mots-clés de recherche textuelle.

    Returns:
        Dict[str, Any]: Résultat standardisé contenant les éléments trouvés.
    """
    from actions.base import format_action_result

    try:
        msg = _get_personal_assistant().search(query)
        return format_action_result(
            action="search_personal_data",
            success=True,
            message=msg,
            data={"query": query, "results": msg},
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action="search_personal_data",
            success=False,
            message=f"Erreur lors de la recherche dans les notes : {exc}",
            data=None,
            error=str(exc),
        )


def schedule_reminder(content: str, when_text: str):
    """Programme un rappel différé avec échéance temporelle.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil dès que l'utilisateur demande à être rappelé
          d'une action à une heure ou date précise (ex: 'Rappelle-moi de couper le four à 18h',
          'Programme un rappel demain à 9h pour la réunion').
        - L'agent NE DOIT PAS appeler cet outil sans information temporelle explicite.

    Args:
        content (str): Message ou sujet du rappel.
        when_text (str): Moment souhaité pour le rappel (ex: '18:00', 'demain 14h', '2026-10-15 10:00').

    Returns:
        Dict[str, Any]: Résultat standardisé de la programmation du rappel.
    """
    from actions.base import format_action_result

    try:
        msg = _get_personal_assistant().schedule_reminder(content, when_text)
        success = not msg.startswith("Dis-moi")
        return format_action_result(
            action="schedule_reminder",
            success=success,
            message=msg,
            data={"content": content, "when": when_text} if success else None,
            error=None if success else msg,
        )
    except Exception as exc:
        return format_action_result(
            action="schedule_reminder",
            success=False,
            message=f"Erreur lors de la programmation du rappel : {exc}",
            data=None,
            error=str(exc),
        )


def get_upcoming_reminders(limit: int = 5):
    """Récupère les prochains rappels programmés et non encore déclenchés.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande quels sont ses
          prochains rappels ou notifications à venir.

    Args:
        limit (int): Nombre maximal de rappels à renvoyer (défaut: 5).

    Returns:
        Dict[str, Any]: Résultat standardisé listant les rappels à venir.
    """
    from actions.base import format_action_result

    try:
        reminders = _get_personal_assistant().get_upcoming_reminders(limit=limit)
        msg = f"{len(reminders)} rappel(s) à venir." if reminders else "Aucun rappel à venir."
        return format_action_result(
            action="get_upcoming_reminders",
            success=True,
            message=msg,
            data=reminders,
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action="get_upcoming_reminders",
            success=False,
            message=f"Erreur lors de la récupération des rappels : {exc}",
            data=None,
            error=str(exc),
        )


def summarize_text_file(file_path: str):
    """Analyse et résume intelligemment un fichier texte local.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur demande de résumer
          ou d'extraire les points clés d'un document texte local (.txt, .md, .log).
        - L'agent NE DOIT PAS appeler cet outil pour les fichiers tabulaires CSV/Excel (utiliser `summarize_tabular_file`).

    Args:
        file_path (str): Chemin d'accès relatif ou absolu du fichier à analyser.

    Returns:
        Dict[str, Any]: Résultat standardisé avec le résumé des points clés.
    """
    from actions.base import format_action_result

    try:
        msg = _get_personal_assistant().summarize_file(file_path)
        return format_action_result(
            action="summarize_text_file",
            success=not msg.startswith("Le fichier") and not "introuvable" in msg,
            message=msg,
            data={"file_path": file_path, "summary": msg},
            error=msg if "introuvable" in msg else None,
        )
    except Exception as exc:
        return format_action_result(
            action="summarize_text_file",
            success=False,
            message=f"Erreur lors de l'analyse du fichier texte : {exc}",
            data=None,
            error=str(exc),
        )



def store_memory_fact(
    key: str,
    value: str,
    category: str = "general",
    importance: int = 1,
    memory_instance: Optional[Any] = None,
) -> Dict[str, Any]:
    """Enregistre durablement un fait, une préférence ou une configuration dans la mémoire sémantique de JARVIS.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsque l'utilisateur lui demande explicitement
          de retenir, mémoriser, se souvenir ou sauvegarder une information importante ou permanente
          (ex: 'Retiens que mon port SSH est le 2222', 'Mémorise que le mot de passe wifi de l'atelier est XYZ',
          'Souviens-toi que mon adresse IP est 192.168.1.100', 'Garde en mémoire que je préfère le thème sombre').
        - L'agent NE DOIT PAS appeler cet outil pour des tâches temporaires à cocher (utiliser `add_todo`)
          ni pour des notes brutes ou brouillons longs (utiliser `create_note`).

    Args:
        key (str): Identifiant ou nom concis du fait technique/préférence (ex: 'ip_vps', 'ssh_port', 'wifi_password').
            Contraintes: Chaîne non vide sans caractères spéciaux complexes.
        value (str): Contenu exact, valeur ou description de l'information à mémoriser (ex: '192.168.1.100', 'Port 2222').
        category (str): Domaine de classification du fait (ex: 'system', 'network', 'preferences', 'iot', 'general').
            Défaut: 'general'.
        importance (int): Niveau d'importance du fait de 1 à 5. Défaut: 1.
        memory_instance (Optional[Any]): Instance personnalisée d'AssistantMemory pour les tests.

    Returns:
        Dict[str, Any]: Dictionnaire standardisé contenant le résultat de la mémorisation :
            - success (bool): True si le fait a été enregistré dans la base SQLite locale.
            - action (str): 'store_memory_fact'.
            - message (str): Message de confirmation lisible pour l'agent.
            - data (dict | None): Données du fait enregistré {'category': category, 'key': key, 'value': value, 'importance': importance}.
            - error (str | None): Détail de l'erreur en cas d'échec.
    """
    from actions.base import format_action_result
    from assistant_memory import AssistantMemory

    action_name = "store_memory_fact"
    if not key or not str(key).strip() or not value or not str(value).strip():
        return format_action_result(
            action=action_name,
            success=False,
            message="La clé et la valeur du fait à mémoriser ne peuvent pas être vides.",
            data=None,
            error="EmptyKeyOrValueError",
        )

    try:
        mem = memory_instance or AssistantMemory()
        ok = mem.save_fact(category=category, key=key, value=value, importance=importance)
        if ok:
            return format_action_result(
                action=action_name,
                success=True,
                message=f"Fait mémorisé avec succès : [{category}] {key} = {value}",
                data={"category": category, "key": key, "value": value, "importance": importance},
                error=None,
            )
        return format_action_result(
            action=action_name,
            success=False,
            message="Échec de l'enregistrement dans la base de mémoire locale.",
            data=None,
            error="DatabaseInsertError",
        )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Erreur lors de la mémorisation du fait : {exc}",
            data=None,
            error=str(exc),
        )


def search_memory_facts(
    query: str,
    limit: int = 3,
    category: Optional[str] = None,
    memory_instance: Optional[Any] = None,
) -> Dict[str, Any]:
    """Recherche activement dans la mémoire permanente de JARVIS les faits, configurations ou préférences mémorisés.

    Critères d'utilisation:
        - L'agent DOIT appeler cet outil lorsqu'il a besoin d'une information passée,
          d'une configuration enregistrée ou d'une préférence utilisateur pour répondre à la demande
          (ex: 'Quelle est l'IP de mon serveur ?', 'Quel est mon mot de passe wifi ?', 'Quel est mon port SSH ?').
        - L'agent NE DOIT PAS appeler cet outil pour faire une recherche générale sur internet (utiliser `search_google`)
          ni pour chercher dans les fichiers de code projet (utiliser `search_personal_data` ou `git_log`).

    Args:
        query (str): Mots-clés ou question ciblant le fait à retrouver (ex: 'ip serveur', 'port ssh', 'wifi').
        limit (int): Nombre maximal de faits à retourner. Défaut: 3.
        category (Optional[str]): Filtrer par domaine/catégorie spécifique (ex: 'network', 'iot').
        memory_instance (Optional[Any]): Instance personnalisée d'AssistantMemory pour les tests.

    Returns:
        Dict[str, Any]: Dictionnaire standardisé contenant les faits correspondants :
            - success (bool): True si la recherche a abouti (même avec 0 résultat).
            - action (str): 'search_memory_facts'.
            - message (str): Résumé textuel lisible des faits trouvés.
            - data (list): Liste des faits pertinents trouvés avec score de pertinence.
            - error (str | None): Détail de l'erreur en cas d'incident technique.
    """
    from actions.base import format_action_result
    from assistant_memory import AssistantMemory

    action_name = "search_memory_facts"
    if not query or not str(query).strip():
        return format_action_result(
            action=action_name,
            success=False,
            message="La requête de recherche en mémoire ne peut pas être vide.",
            data=[],
            error="EmptyQueryError",
        )

    try:
        mem = memory_instance or AssistantMemory()
        results = mem.search_facts(query=query, limit=limit, category=category)
        if not results:
            return format_action_result(
                action=action_name,
                success=True,
                message=f"Aucun fait correspondant trouvé dans la mémoire permanente pour '{query}'.",
                data=[],
                error=None,
            )

        summary_lines = [f"{len(results)} fait(s) trouvé(s) en mémoire :"]
        for r in results:
            summary_lines.append(f"- [{r['category']}] {r['key']} : {r['content']}")

        return format_action_result(
            action=action_name,
            success=True,
            message="\n".join(summary_lines),
            data=results,
            error=None,
        )
    except Exception as exc:
        return format_action_result(
            action=action_name,
            success=False,
            message=f"Erreur lors de la recherche dans la mémoire permanente : {exc}",
            data=[],
            error=str(exc),
        )
