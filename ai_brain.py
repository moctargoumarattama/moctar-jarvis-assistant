import logging
import os
import unicodedata
from collections import Counter
from datetime import datetime
from functools import lru_cache

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv():
        return False
try:
    from openai import OpenAI
except Exception:
    OpenAI = None


DEFAULT_MODEL = "gpt-4o-mini"
logger = logging.getLogger("jarvis.ai")


def normalize_prompt(text):
    if not text:
        return ""
    lowered = text.strip().lower()
    normalized = unicodedata.normalize("NFD", lowered)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def _format_due_label(due_at_raw):
    try:
        due_at = datetime.fromisoformat(due_at_raw)
    except Exception:
        return due_at_raw

    today = datetime.now().date()
    if due_at.date() == today:
        return due_at.strftime("%H:%M")
    return due_at.strftime("%d/%m %H:%M")


class LocalBrain:
    @staticmethod
    def _todo_priority_score(todo, *, focus_project=""):
        content = (todo.get("content", "") or "").lower()
        score = 0
        if any(marker in content for marker in ["urgent", "critique", "asap", "immediat", "important", "priorite"]):
            score += 50
        if any(marker in content for marker in ["aujourd", "ce matin", "ce soir", "maintenant", "deadline"]):
            score += 25
        if "demain" in content:
            score += 10
        if any(marker in content for marker in ["plus tard", "quand possible"]):
            score -= 10
        if focus_project and focus_project.lower() in content:
            score += 20

        created_at = todo.get("created_at", "")
        if created_at:
            try:
                created = datetime.fromisoformat(created_at)
                score += min(max((datetime.now() - created).days, 0), 14)
            except Exception:
                pass
        return score

    def prioritize_tasks(self, *, todos=None, insights=None, top_k=5, **_):
        todos = todos or []
        insights = insights or {}
        focus_project = (insights.get("active_project", "") or "").strip()
        ranked = sorted(
            todos,
            key=lambda todo: self._todo_priority_score(todo, focus_project=focus_project),
            reverse=True,
        )
        return ranked[:top_k]

    def _routine_window(self, now=None):
        now = now or datetime.now()
        if now.hour < 12:
            return "morning"
        if now.hour >= 18:
            return "evening"
        return "day"

    def build_auto_routine(self, *, todos=None, reminders=None, now=None, mode="auto", **_):
        todos = todos or []
        reminders = reminders or []
        window = self._routine_window(now=now)
        if mode == "morning":
            window = "morning"
        if mode == "evening":
            window = "evening"

        prioritized = self.prioritize_tasks(todos=todos, top_k=3)
        lines = []
        if window == "morning":
            lines.append("Routine matin auto :")
            lines.append("- Verifie tes 3 priorites et bloque ton premier sprint focus.")
            lines.append("- Traite le rappel le plus proche avant midi.")
        elif window == "evening":
            lines.append("Routine soir auto :")
            lines.append("- Termine une tache impactante et ferme les actions ouvertes.")
            lines.append("- Prepare les priorites de demain et planifie les rappels.")
        else:
            lines.append("Routine auto :")
            lines.append("- Lance un sprint focus de 25 minutes sur la tache la plus utile.")
            lines.append("- Clarifie la prochaine action concrete avant de changer de sujet.")

        if reminders:
            reminder = reminders[0]
            lines.append(
                f"- Point de vigilance : {reminder.get('content', '')} avant {_format_due_label(reminder.get('due_at', ''))}."
            )
        if prioritized:
            lines.append("- Priorites conseillees :")
            for todo in prioritized:
                lines.append(f"  - {todo.get('content', '')}")
        return "\n".join(lines)

    def summarize_priorities(self, *, todos=None, insights=None, **_):
        prioritized = self.prioritize_tasks(todos=todos or [], insights=insights or {}, top_k=5)
        if not prioritized:
            return "Aucune tache a prioriser pour le moment."
        lines = ["Priorisation intelligente :"]
        for index, todo in enumerate(prioritized, start=1):
            lines.append(f"{index}. {todo.get('content', '')}")
        return "\n".join(lines)

    def project_focus_mode(self, *, todos=None, insights=None, history=None, project_hint="", **_):
        todos = todos or []
        insights = insights or {}
        history = history or []

        active_project = (project_hint or insights.get("active_project", "")).strip()
        if not active_project:
            for item in reversed(history):
                if item.get("intent") in {"open_project", "launch_project_server"} and item.get("target"):
                    active_project = item["target"]
                    break

        if not active_project:
            return "Mode focus projet: aucun projet actif detecte. Dis par exemple 'mode focus projet noor_express'."

        matching = [
            todo for todo in todos if active_project.lower() in (todo.get("content", "") or "").lower()
        ]
        if not matching:
            matching = self.prioritize_tasks(todos=todos, insights=insights, top_k=3)

        lines = [f"Mode focus projet actif : {active_project}"]
        if matching:
            lines.append("- Sprint conseille (top taches) :")
            for todo in matching[:3]:
                lines.append(f"  - {todo.get('content', '')}")
        else:
            lines.append("- Aucune tache ouverte. Tu peux ajouter des todos lies au projet.")
        lines.append("- Prochaine etape : bloque 25 minutes sans interruption sur la premiere action.")
        return "\n".join(lines)

    def explain_capabilities(self):
        return (
            "Mode local actif. Intelligence locale active. Je peux prioriser tes todos, declencher des routines matin/soir, "
            "activer un mode focus projet, resumer tes notes/fichiers locaux intelligemment, te rappeler les actions proches, "
            "guider tes recherches YouTube/Google et piloter les actions systeme, projets, energie et IoT sans GPT ni cloud."
        )

    def _focus_summary(self, history):
        if not history:
            return ""

        intents = [
            item.get("intent", "")
            for item in history
            if item.get("intent") not in {"chat_fallback", "empty", "confirm_yes", "confirm_no"}
        ]
        if not intents:
            return ""

        intent, count = Counter(intents).most_common(1)[0]
        if count < 2:
            return ""
        return f"Focus recent detecte : {intent}."

    def generate_daily_brief(
        self,
        *,
        todos=None,
        reminders=None,
        history=None,
        preferences=None,
        insights=None,
        now=None,
        **_,
    ):
        todos = todos or []
        reminders = reminders or []
        history = history or []
        preferences = preferences or {}
        insights = insights or {}
        now = now or datetime.now()
        prioritized = self.prioritize_tasks(todos=todos, insights=insights, top_k=3)

        lines = ["Brief local :"]
        if reminders:
            reminder = reminders[0]
            lines.append(
                f"- Rappel prioritaire a {_format_due_label(reminder.get('due_at', ''))} : {reminder.get('content', '')}"
            )
        else:
            lines.append("- Aucun rappel urgent detecte.")

        if prioritized:
            lines.append("- Priorites ouvertes :")
            for todo in prioritized:
                lines.append(f"  - {todo.get('content', '')}")
        else:
            lines.append("- Aucun todo actif pour le moment.")

        favorite_music = preferences.get("favorite_music")
        if favorite_music:
            lines.append(f"- Preference retenue : musique {favorite_music}.")

        focus_summary = self._focus_summary(history[-8:])
        if focus_summary:
            lines.append(f"- {focus_summary}")

        active_project = insights.get("active_project", "")
        if active_project:
            lines.append(f"- Projet focus detecte : {active_project}.")

        routine_window = self._routine_window(now=now)
        if routine_window in {"morning", "evening"}:
            label = "matin" if routine_window == "morning" else "soir"
            lines.append(f"- Routine {label} auto disponible.")

        return "\n".join(lines)

    def suggest_next_action(self, *, todos=None, reminders=None, history=None, insights=None, **_):
        todos = todos or []
        reminders = reminders or []
        history = history or []
        insights = insights or {}

        if reminders:
            reminder = reminders[0]
            return (
                f"Prochaine action recommandee : prepare '{reminder.get('content', '')}' "
                f"avant {_format_due_label(reminder.get('due_at', ''))}."
            )

        prioritized = self.prioritize_tasks(todos=todos, insights=insights, top_k=1)
        if prioritized:
            return f"Prochaine action recommandee : commence par '{prioritized[0].get('content', '')}'."

        if history:
            last_target = history[-1].get("target", "").strip()
            if last_target:
                return f"Tu peux reprendre sur '{last_target}' ou me demander un brief du jour."

        return "Aucune urgence locale detectee. Tu peux me demander un brief du jour, creer une note ou ajouter un todo."

    def youtube_guidance(self, prompt, *, preferences=None, **_):
        preferences = preferences or {}
        favorite_music = preferences.get("favorite_music")
        if favorite_music:
            return (
                f"Pour YouTube, je peux lancer une recherche locale ou une lecture inspiree de '{favorite_music}'. "
                "Dis par exemple : cherche sur youtube energie solaire ou mets de la musique."
            )
        return (
            "Pour YouTube, je peux lancer une recherche locale, ouvrir une playlist ou transformer un sujet en action. "
            "Dis par exemple : cherche sur youtube energie solaire, playlist afrobeat ou mets du ninho."
        )

    def answer(
        self,
        prompt,
        *,
        todos=None,
        reminders=None,
        history=None,
        preferences=None,
        session_turn=None,
        insights=None,
    ):
        normalized = normalize_prompt(prompt)

        if not normalized:
            return self.explain_capabilities()

        if any(token in normalized for token in ["que peux tu faire", "aide", "help", "capacites", "capable"]):
            return self.explain_capabilities()

        if "youtube" in normalized:
            return self.youtube_guidance(prompt, preferences=preferences)

        if any(token in normalized for token in ["brief", "journee", "priorites", "organise", "organisation"]):
            return self.generate_daily_brief(
                todos=todos,
                reminders=reminders,
                history=history,
                preferences=preferences,
                insights=insights,
            )

        if any(token in normalized for token in ["quoi faire", "prochaine action", "prochaine tache", "maintenant"]):
            return self.suggest_next_action(
                todos=todos,
                reminders=reminders,
                history=history,
                insights=insights,
            )

        if any(token in normalized for token in ["priorise", "prioriser", "priorisation", "priorites"]):
            return self.summarize_priorities(todos=todos, insights=insights)

        if any(token in normalized for token in ["routine matin", "routine du matin"]):
            return self.build_auto_routine(todos=todos, reminders=reminders, mode="morning")

        if any(token in normalized for token in ["routine soir", "routine du soir"]):
            return self.build_auto_routine(todos=todos, reminders=reminders, mode="evening")

        if any(token in normalized for token in ["mode focus", "focus projet", "mode projet"]):
            return self.project_focus_mode(todos=todos, history=history, insights=insights)

        if session_turn and session_turn.get("target"):
            return (
                "Mode local actif. Dernier sujet retenu : "
                f"{session_turn['target']}. Dis-moi si tu veux un brief, une priorisation ou une recherche guidee."
            )

        return (
            "Mode local actif. Je peux te faire un brief du jour, prioriser tes taches, "
            "ou guider une recherche YouTube/Google sans dependance externe."
        )


def get_openai_api_key():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Add it to your environment or a local .env file."
        )
    return api_key


@lru_cache(maxsize=1)
def get_openai_client():
    if OpenAI is None:
        raise RuntimeError("openai package is missing")
    return OpenAI(api_key=get_openai_api_key())


def ask_gpt(prompt):
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    response = get_openai_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Tu es Jarvis, assistant intelligent et rapide."},
            {"role": "user", "content": prompt},
        ],
    )

    return response.choices[0].message.content


def safe_ask_gpt(prompt, fallback="Je ne peux pas utiliser l'IA pour le moment."):
    try:
        return ask_gpt(prompt)
    except Exception:
        return fallback
