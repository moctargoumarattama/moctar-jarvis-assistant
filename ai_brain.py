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
    def explain_capabilities(self):
        return (
            "Mode local actif. Intelligence locale active. Je peux prioriser tes todos, resumer ta journee, "
            "te rappeler les actions proches, guider tes recherches YouTube/Google et piloter "
            "les actions systeme, projets, energie et IoT sans GPT ni cloud."
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

    def generate_daily_brief(self, *, todos=None, reminders=None, history=None, preferences=None, **_):
        todos = todos or []
        reminders = reminders or []
        history = history or []
        preferences = preferences or {}

        lines = ["Brief local :"]
        if reminders:
            reminder = reminders[0]
            lines.append(
                f"- Rappel prioritaire a {_format_due_label(reminder.get('due_at', ''))} : {reminder.get('content', '')}"
            )
        else:
            lines.append("- Aucun rappel urgent detecte.")

        if todos:
            lines.append("- Priorites ouvertes :")
            for todo in todos[:3]:
                lines.append(f"  - {todo.get('content', '')}")
        else:
            lines.append("- Aucun todo actif pour le moment.")

        favorite_music = preferences.get("favorite_music")
        if favorite_music:
            lines.append(f"- Preference retenue : musique {favorite_music}.")

        focus_summary = self._focus_summary(history[-8:])
        if focus_summary:
            lines.append(f"- {focus_summary}")

        return "\n".join(lines)

    def suggest_next_action(self, *, todos=None, reminders=None, history=None, **_):
        todos = todos or []
        reminders = reminders or []
        history = history or []

        if reminders:
            reminder = reminders[0]
            return (
                f"Prochaine action recommandee : prepare '{reminder.get('content', '')}' "
                f"avant {_format_due_label(reminder.get('due_at', ''))}."
            )

        if todos:
            return f"Prochaine action recommandee : commence par '{todos[0].get('content', '')}'."

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

    def answer(self, prompt, *, todos=None, reminders=None, history=None, preferences=None, session_turn=None):
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
            )

        if any(token in normalized for token in ["quoi faire", "prochaine action", "prochaine tache", "maintenant"]):
            return self.suggest_next_action(
                todos=todos,
                reminders=reminders,
                history=history,
            )

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
