import ast
import base64
import logging
import json
import operator
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
import socket
from threading import Event, Thread
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import config
from knowledge_library import KnowledgeLibrary

try:
    from google import genai
except ImportError:
    genai = None

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv():
        return False


DEFAULT_MODEL = config.OLLAMA_MODEL
DEFAULT_BASE_URL = config.OLLAMA_BASE_URL
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", str(config.OLLAMA_TIMEOUT_SECONDS)))
GENERATION_CONFIG = {
    "temperature": 0.2,
    "max_tokens": 350,
    "top_p": 0.8,
}
OLLAMA_MAX_PREDICT = GENERATION_CONFIG["max_tokens"]
OLLAMA_TEMPERATURE = GENERATION_CONFIG["temperature"]
MOCTAR_SYSTEM_PROMPT = (
    "Tu es M.O.C.T.A.R, l'OS personnel et copilote d'ingénierie de Moctar. "
    "Tu opères sur un Dell Precision M4800 et gères son infrastructure : "
    "DevOps, Linux, Solaire/Énergie, IoT, Projets. "
    "Tu aides Moctar dans ses activites quotidiennes. "
    "Réponds en français directement dès le premier mot : aucune politesse, phrase de transition ou conclusion inutile. "
    "Sois précis et factuel ; privilégie les commandes Bash/Python directes, "
    "les métriques chiffrées et les listes courtes. "
    "Limite la réponse à 2 à 4 phrases ou au bloc de code exact requis, sauf demande explicite de Moctar. "
    "Pour une action système ou un calcul déterministe, exécute l'outil disponible "
    "ou renvoie le résultat brut sans paraphrase. "
    "Utilise les observations réelles et le contexte pertinent ; n'invente aucun fait ni résultat d'action."
)
OLLAMA_STOP = ("Observation:", "<|im_end|>", "\n\nUser:")
HYBRID_OLLAMA_TIMEOUT_SECONDS = 15.0
logger = logging.getLogger("jarvis.ai")


class OllamaRequestError(RuntimeError):
    pass


class OllamaTimeoutError(OllamaRequestError):
    pass


def _ai_provider():
    provider = os.getenv("AI_PROVIDER", config.ai_provider).strip().lower()
    return provider if provider in {"ollama", "gemini", "hybrid"} else "hybrid"


def _with_ollama_deadline(callback, timeout):
    """Bound the whole local request, including connect and response decoding."""
    completed = Event()
    outcome = {}

    def run():
        try:
            outcome["result"] = callback()
        except Exception as exc:
            outcome["error"] = exc
        finally:
            completed.set()

    # A stalled local server must not hold up cloud fallback or process shutdown.
    Thread(target=run, daemon=True, name="jarvis-ollama").start()
    if not completed.wait(timeout):
        raise OllamaTimeoutError("Ollama request timed out")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


_ALLOWED_MATH_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_ALLOWED_MATH_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def normalize_prompt(text):
    if not text:
        return ""
    lowered = text.strip().lower()
    normalized = unicodedata.normalize("NFD", lowered)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def can_answer_general_questions():
    """Use the configured AI provider when general AI is enabled."""
    load_dotenv()
    return bool(config.AI_SETTINGS.get("enable_general_ai", True))


def _ollama_settings():
    load_dotenv()
    model = (
        os.getenv("OLLAMA_MODEL")
        or config.AI_SETTINGS.get("default_model", DEFAULT_MODEL)
    ).strip() or DEFAULT_MODEL
    base_url = (
        os.getenv("OLLAMA_BASE_URL")
        or config.AI_SETTINGS.get("base_url", DEFAULT_BASE_URL)
    ).strip() or DEFAULT_BASE_URL
    return model, base_url


def _ollama_vision_model():
    load_dotenv()
    return (os.getenv("OLLAMA_VISION_MODEL") or "").strip()


def _normalize_media_paths(media_paths):
    if not media_paths:
        return []
    if isinstance(media_paths, (str, Path)):
        raw_paths = [media_paths]
    else:
        raw_paths = list(media_paths)

    normalized = []
    seen = set()
    for item in raw_paths:
        text = str(item or "").strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _is_image_path(path):
    return Path(path).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def _is_video_path(path):
    return Path(path).suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _encode_image_file(path):
    with open(path, "rb") as handle:
        return base64.b64encode(handle.read()).decode("ascii")


def _ollama_chat_url(base_url):
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/api/chat"


def _ollama_inference_options(max_predict=OLLAMA_MAX_PREDICT, temperature=OLLAMA_TEMPERATURE):
    """Keep local inference concise while respecting smaller caller budgets."""
    return {
        "num_predict": max(1, min(int(max_predict), GENERATION_CONFIG["max_tokens"])),
        "temperature": max(0.1, min(float(temperature), GENERATION_CONFIG["temperature"])),
        "top_p": GENERATION_CONFIG["top_p"],
        "stop": list(OLLAMA_STOP),
    }


def _looks_like_open_question(prompt):
    normalized = normalize_prompt(prompt)
    if not normalized:
        return False
    if "?" in prompt:
        return True

    starters = (
        "comment ",
        "pourquoi ",
        "pourquoi",
        "explique ",
        "peux tu ",
        "peux-tu ",
        "ou ",
        "ou est ",
        "ou se trouve ",
        "quand ",
        "combien ",
        "combien font ",
        "combien fait ",
        "calcule ",
        "calcule moi ",
        "que ",
        "qu est ce",
        "qui ",
        "quel ",
        "quelle ",
        "quels ",
        "quelles ",
        "dis moi ",
        "raconte ",
        "aide moi ",
        "c est quoi ",
        "ca veut dire ",
    )
    if normalized.startswith(starters):
        return True
    if any(token in normalized for token in (" aide ", " explique ", " comment ", " pourquoi ", " ou ", " quand ")):
        return True
    return _extract_math_expression(prompt) != ""


def _format_number(value):
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _extract_math_expression(prompt):
    normalized = normalize_prompt(prompt)
    if not normalized or not re.search(r"\d", normalized):
        return ""

    candidate = normalized
    for source, target in (
        ("combien font ", ""),
        ("combien fait ", ""),
        ("calcule moi ", ""),
        ("calcule ", ""),
        ("quel est ", ""),
        ("quelle est ", ""),
        ("c est quoi ", ""),
        ("ca fait combien ", ""),
        ("donne moi le resultat de ", ""),
        ("resultat de ", ""),
    ):
        if candidate.startswith(source):
            candidate = candidate[len(source):].strip()
            break

    replacements = (
        ("×", "*"),
        (" x ", " * "),
        (" fois ", " * "),
        (" multiplie par ", " * "),
        (" multipli e par ", " * "),
        (" plus ", " + "),
        (" moins ", " - "),
        (" divise par ", " / "),
        (" sur ", " / "),
        (" puissance ", " ** "),
        ("^", " ** "),
        (",", "."),
        ("=", " "),
        ("?", " "),
    )
    for source, target in replacements:
        candidate = candidate.replace(source, target)

    candidate = re.sub(r"\s+", " ", candidate).strip()
    if not candidate:
        return ""

    condensed = candidate.replace(" ", "")
    if re.search(r"[a-z]", condensed):
        return ""
    if not re.fullmatch(r"[0-9+\-*/().%]+", condensed):
        return ""
    if not any(symbol in condensed for symbol in ("+", "-", "*", "/", "%")):
        return ""
    return condensed


def _safe_eval_math_expression(expression):
    node = ast.parse(expression, mode="eval")

    def _evaluate(current):
        if isinstance(current, ast.Expression):
            return _evaluate(current.body)
        if isinstance(current, ast.Constant) and isinstance(current.value, (int, float)):
            return current.value
        if isinstance(current, ast.Num):
            return current.n
        if isinstance(current, ast.BinOp) and type(current.op) in _ALLOWED_MATH_BINOPS:
            left = _evaluate(current.left)
            right = _evaluate(current.right)
            return _ALLOWED_MATH_BINOPS[type(current.op)](left, right)
        if isinstance(current, ast.UnaryOp) and type(current.op) in _ALLOWED_MATH_UNARYOPS:
            return _ALLOWED_MATH_UNARYOPS[type(current.op)](_evaluate(current.operand))
        raise ValueError("unsupported expression")

    return _evaluate(node)


def _answer_math_question(prompt):
    expression = _extract_math_expression(prompt)
    if not expression:
        return ""
    try:
        result = _safe_eval_math_expression(expression)
    except ZeroDivisionError:
        return "La division par zero est impossible."
    except Exception:
        return ""
    return _format_number(result)


def _should_use_ollama_assistant(prompt):
    normalized = normalize_prompt(prompt)
    if not normalized:
        return False
    if _extract_math_expression(prompt):
        return False
    if _looks_like_open_question(prompt):
        return True

    markers = (
        "donne moi",
        "propose",
        "aide moi",
        "organise",
        "planifie",
        "plan",
        "idee",
        "idees",
        "strategie",
        "marketing",
        "vente",
        "business",
        "client",
        "ameliore",
        "redige",
        "ecris",
        "prepare",
        "optimise",
        "analyse",
        "compare",
        "resume",
        "activite",
    )
    if any(marker in normalized for marker in markers):
        return True
    return len(normalized.split()) >= 4


def get_ai_status():
    provider = _ai_provider()
    cloud_ready = bool(genai is not None and os.getenv("GEMINI_API_KEY", config.GEMINI_API_KEY).strip())
    cloud_model = os.getenv("GEMINI_MODEL", config.gemini_model).strip() or config.gemini_model
    model, base_url = _ollama_settings()
    status = {
        "mode": "local", "provider": provider, "model": model,
        "base_url": base_url, "online": False, "ollama_online": False,
        "server_online": False, "model_available": False,
        "label": f"IA: Local | {model}",
    }
    if provider == "gemini":
        status.update(model=cloud_model, base_url="", online=cloud_ready,
                      mode="gemini" if cloud_ready else "local",
                      label=f"IA: Gemini | {cloud_model}")
        return status

    tags_url = f"{base_url.rstrip('/')}/api/tags"
    try:
        with urlopen(tags_url, timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = payload.get("models") if isinstance(payload, dict) else []
        available_models = {
            str(item.get("name") or "").strip()
            for item in models
            if isinstance(item, dict)
        }
        # An available server does not mean that the configured model is installed.
        requested_model = model if ":" in model.rsplit("/", 1)[-1] else f"{model}:latest"
        is_online = model in available_models or requested_model in available_models
        mode = "ollama" if is_online else "local"
        label = f"IA: {'Ollama' if is_online else 'Modele absent'} | {model}"
        status.update({
            "mode": mode,
            "model": model,
            "base_url": base_url,
            "online": is_online,
            "ollama_online": is_online,
            "server_online": True,
            "model_available": is_online,
            "label": label,
        })
    except Exception:
        pass
    # This reports configuration readiness; status polling never spends API quota.
    if provider == "hybrid" and not status["online"] and cloud_ready:
        status.update(mode="gemini", model=cloud_model, base_url="", online=True,
                      label=f"IA: Gemini | {cloud_model}")
    return status


def extract_ollama_message(payload):
    if not isinstance(payload, dict):
        raise RuntimeError("invalid Ollama response")

    message = payload.get("message") or {}
    if isinstance(message, dict) and ("content" in message or message.get("tool_calls")):
        return message

    choices = payload.get("choices") or []
    if choices:
        first_choice = choices[0] or {}
        message = first_choice.get("message") or {}
        if isinstance(message, dict) and ("content" in message or message.get("tool_calls")):
            return message

    raise RuntimeError("Ollama response did not include a message")


def _extract_ollama_content(payload):
    message = extract_ollama_message(payload)
    content = message.get("content")
    if content is None:
        raise RuntimeError("Ollama response did not include message content")
    return str(content)


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
    def __init__(self, knowledge_library=None):
        self.knowledge_library = knowledge_library or KnowledgeLibrary()

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
        local_fallback = "\n".join(lines)
        prompt = (
            "Construis une routine d'action courte et intelligente a partir du contexte local. "
            f"Fenetre: {window}. "
            "Donne 3 a 5 actions utiles dans le bon ordre et termine par une seule phrase de focus."
        )
        return self._ask_personal_assistant(
            prompt,
            fallback=local_fallback,
            mode="productivity",
            todos=todos,
            reminders=reminders,
            extra_context=f"routine_window={window}",
        )

    def summarize_priorities(self, *, todos=None, insights=None, **_):
        prioritized = self.prioritize_tasks(todos=todos or [], insights=insights or {}, top_k=5)
        if not prioritized:
            return "Aucune tache a prioriser pour le moment."
        lines = ["Priorisation intelligente :"]
        for index, todo in enumerate(prioritized, start=1):
            lines.append(f"{index}. {todo.get('content', '')}")
        local_fallback = "\n".join(lines)
        prompt = (
            "Classe les priorites ouvertes en te basant sur le contexte local. "
            "Donne les 3 a 5 priorites les plus importantes avec une justification courte pour chacune."
        )
        return self._ask_personal_assistant(
            prompt,
            fallback=local_fallback,
            mode="productivity",
            todos=todos,
            insights=insights,
        )

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
        local_fallback = "\n".join(lines)
        prompt = (
            f"Construis un mini plan focus concret pour le projet {active_project}. "
            "Propose le meilleur sprint immediat, 2 etapes suivantes et un point de vigilance."
        )
        return self._ask_personal_assistant(
            prompt,
            fallback=local_fallback,
            mode="project",
            todos=todos,
            history=history,
            insights=insights,
            extra_context=f"project_hint={active_project}",
        )

    def explain_capabilities(self):
        base = (
            "Mode local actif. Je peux prioriser tes todos, declencher des routines matin/soir, "
            "activer un mode focus projet, resumer tes notes et fichiers, te rappeler les actions proches, "
            "guider tes recherches YouTube/Google et piloter les actions systeme, projets, energie et IoT."
        )
        if can_answer_general_questions():
            return (
                f"{base} Avec l'IA configuree, je peux aussi t'aider pour l'organisation du travail, "
                "les idees marketing, les plans d'action, les explications, la redaction de messages "
                "et les decisions quotidiennes en restant coherent avec ton contexte local."
            )
        return f"{base} Un moteur IA disponible permet aussi d'enrichir ces reponses."

    @staticmethod
    def _shorten_text(text, limit=140):
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."

    def _build_context_snapshot(
        self,
        *,
        todos=None,
        reminders=None,
        history=None,
        preferences=None,
        insights=None,
        session_turn=None,
        extra_context="",
    ):
        todos = todos or []
        reminders = reminders or []
        history = history or []
        preferences = preferences or {}
        insights = insights or {}

        lines = [f"Date locale: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

        active_project = (insights.get("active_project", "") or "").strip()
        if active_project:
            lines.append(f"Projet actif: {active_project}")

        last_focus_topic = (insights.get("last_focus_topic", "") or "").strip()
        if last_focus_topic:
            lines.append(f"Dernier sujet de focus: {self._shorten_text(last_focus_topic, 100)}")

        top_intents = insights.get("top_intents", {}) or {}
        if top_intents:
            ranked = sorted(top_intents.items(), key=lambda item: item[1], reverse=True)[:3]
            summary = ", ".join(f"{intent}:{count}" for intent, count in ranked)
            lines.append(f"Usages frequents: {summary}")

        if todos:
            lines.append("Todos ouverts:")
            for todo in todos[:5]:
                lines.append(f"- {self._shorten_text(todo.get('content', ''), 100)}")

        if reminders:
            lines.append("Rappels proches:")
            for reminder in reminders[:4]:
                due_label = _format_due_label(reminder.get("due_at", ""))
                lines.append(
                    f"- {self._shorten_text(reminder.get('content', ''), 90)}"
                    f" | echeance {due_label}"
                )

        if preferences:
            parts = []
            for key, value in list(preferences.items())[:4]:
                parts.append(f"{key}={self._shorten_text(value, 40)}")
            if parts:
                lines.append(f"Preferences: {', '.join(parts)}")

        if history:
            lines.append("Historique recent:")
            for item in history[-4:]:
                intent = item.get("intent", "")
                target = self._shorten_text(item.get("target", ""), 70)
                response = self._shorten_text(item.get("response", ""), 90)
                lines.append(f"- {intent} | cible={target or '-'} | reponse={response or '-'}")

        if session_turn:
            lines.append(
                "Dernier echange session: "
                f"{session_turn.get('intent', '')} -> {self._shorten_text(session_turn.get('target', ''), 90)}"
            )

        if extra_context:
            lines.append(f"Contexte additionnel: {self._shorten_text(extra_context, 180)}")

        return "\n".join(lines)

    @staticmethod
    def _contains_any_markers(text, markers):
        return any(re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text) for marker in markers)

    @staticmethod
    def _detect_assistant_mode(prompt, *, context=""):
        normalized = normalize_prompt(" ".join(part for part in [prompt, context] if part))
        if LocalBrain._contains_any_markers(
            normalized,
            ["redige", "ecris", "email", "message", "publication", "post", "whatsapp", "facebook"],
        ):
            return "writing"
        if LocalBrain._contains_any_markers(
            normalized,
            ["marketing", "strategie", "vente", "business", "offre", "promotion", "campagne"],
        ):
            return "strategy"
        if LocalBrain._contains_any_markers(
            normalized,
            ["projet", "bug", "code", "python", "script", "serveur", "git", "api", "flask", "fastapi"],
        ):
            return "project"
        if LocalBrain._contains_any_markers(
            normalized,
            ["journee", "demain", "priorite", "tache", "routine", "agenda", "planning", "objectif", "organise"],
        ):
            return "productivity"
        if _looks_like_open_question(prompt):
            return "question"
        return "general"

    @staticmethod
    def _assistant_system_prompt(mode):
        base = MOCTAR_SYSTEM_PROMPT
        specifics = {
            "question": (
                " Reponds en une ou deux phrases maximum. "
                "Pour une question factuelle simple, donne d'abord la reponse exacte."
            ),
            "productivity": (
                " Produis une reponse orientee execution: priorites, ordre d'action, blocages et prochaine etape. "
                "Sois concret et compact."
            ),
            "project": (
                " Raisonne comme un copilote de projet technique et operationnel. "
                "Donne des etapes realistes, des verifications utiles et des risques si necessaire."
            ),
            "strategy": (
                " Agis comme un conseiller pragmatique pour le business, le marketing et les clients. "
                "Propose des idees utiles, differenciantes et applicables rapidement."
            ),
            "writing": (
                " Si la demande concerne un texte, privilegie une formulation propre, humaine, convaincante et exploitable directement."
            ),
            "general": (
                " Garde un ton naturel et utile. Si une liste aide, fais une liste courte."
            ),
        }
        return base + specifics.get(mode, specifics["general"])

    @staticmethod
    def _assistant_runtime_settings(mode):
        if mode == "question":
            return {"max_predict": 96, "temperature": GENERATION_CONFIG["temperature"]}
        if mode in {"productivity", "project"}:
            return {"max_predict": 180, "temperature": GENERATION_CONFIG["temperature"]}
        if mode in {"strategy", "writing"}:
            return {"max_predict": 220, "temperature": GENERATION_CONFIG["temperature"]}
        return {"max_predict": 160, "temperature": GENERATION_CONFIG["temperature"]}

    def _ask_personal_assistant(
        self,
        prompt,
        *,
        fallback,
        mode=None,
        todos=None,
        reminders=None,
        history=None,
        preferences=None,
        insights=None,
        session_turn=None,
        extra_context="",
    ):
        if not can_answer_general_questions():
            return fallback

        resolved_mode = mode or self._detect_assistant_mode(prompt, context=extra_context)
        runtime = self._assistant_runtime_settings(resolved_mode)
        context_snapshot = self._build_context_snapshot(
            todos=todos,
            reminders=reminders,
            history=history,
            preferences=preferences,
            insights=insights,
            session_turn=session_turn,
            extra_context=extra_context,
        )
        user_prompt = (
            f"Contexte local pertinent:\n{context_snapshot}\n\n"
            f"Demande utilisateur:\n{prompt}\n\n"
            "Reponse:"
        )
        return safe_ask_ollama(
            user_prompt,
            fallback=fallback,
            system_prompt=self._assistant_system_prompt(resolved_mode),
            max_predict=runtime["max_predict"],
            temperature=runtime["temperature"],
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

        local_fallback = "\n".join(lines)
        prompt = (
            "Genere un brief du jour intelligent a partir du contexte local. "
            "Structure attendue: priorite immediate, actions suivantes, risque ou vigilance, puis une phrase de focus."
        )
        return self._ask_personal_assistant(
            prompt,
            fallback=local_fallback,
            mode="productivity",
            todos=todos,
            reminders=reminders,
            history=history,
            preferences=preferences,
            insights=insights,
        )

    def suggest_next_action(self, *, todos=None, reminders=None, history=None, insights=None, **_):
        todos = todos or []
        reminders = reminders or []
        history = history or []
        insights = insights or {}

        if reminders:
            reminder = reminders[0]
            local_fallback = (
                f"Prochaine action recommandee : prepare '{reminder.get('content', '')}' "
                f"avant {_format_due_label(reminder.get('due_at', ''))}."
            )
        else:
            prioritized = self.prioritize_tasks(todos=todos, insights=insights, top_k=1)
            if prioritized:
                local_fallback = f"Prochaine action recommandee : commence par '{prioritized[0].get('content', '')}'."
            elif history:
                last_target = history[-1].get("target", "").strip()
                if last_target:
                    local_fallback = f"Tu peux reprendre sur '{last_target}' ou me demander un brief du jour."
                else:
                    local_fallback = "Aucune urgence locale detectee. Tu peux me demander un brief du jour, creer une note ou ajouter un todo."
            else:
                local_fallback = "Aucune urgence locale detectee. Tu peux me demander un brief du jour, creer une note ou ajouter un todo."

        return self._ask_personal_assistant(
            "Choisis la meilleure prochaine action concrete a faire maintenant.",
            fallback=local_fallback,
            mode="productivity",
            todos=todos,
            reminders=reminders,
            history=history,
            insights=insights,
        )

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

    @staticmethod
    def _social_media_summary(media_paths):
        media_paths = _normalize_media_paths(media_paths)
        if not media_paths:
            return ""

        image_count = sum(1 for path in media_paths if _is_image_path(path))
        video_count = sum(1 for path in media_paths if _is_video_path(path))
        other_count = max(len(media_paths) - image_count - video_count, 0)

        parts = []
        if image_count:
            parts.append(f"{image_count} photo(s)")
        if video_count:
            parts.append(f"{video_count} video(s)")
        if other_count:
            parts.append(f"{other_count} fichier(s)")

        names = [Path(path).stem.replace("_", " ").replace("-", " ") for path in media_paths[:3]]
        summary = ", ".join(parts)
        if names:
            summary += f" - apercu: {', '.join(names)}"
        return summary

    @staticmethod
    def _social_hashtags(title, platform):
        tokens = [
            token
            for token in normalize_prompt(title).replace("'", " ").split()
            if len(token) >= 4 and token.isalpha()
        ]
        unique_tokens = []
        seen = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            unique_tokens.append(token.capitalize())
            if len(unique_tokens) >= 3:
                break

        defaults = {
            "facebook": ["Communaute", "Actualite"],
            "whatsapp": ["Info", "Partage"],
        }
        chosen = unique_tokens or defaults.get(platform, ["Annonce"])
        return " ".join(f"#{token}" for token in chosen)

    def _local_social_caption(self, message, platform, title="", media_paths=None):
        platform = (platform or "social").strip().lower()
        title = (title or "").strip()
        base_message = (message or "").strip()
        media_summary = self._social_media_summary(media_paths)
        hashtags = self._social_hashtags(title or base_message, platform)

        if not base_message:
            if title:
                base_message = f"{title} est disponible."
            elif media_summary:
                base_message = f"Nouvelle publication avec {media_summary}."
            else:
                base_message = "Nouvelle publication disponible."

        if platform == "whatsapp":
            parts = [base_message]
            if media_summary:
                parts.append(f"Je vous partage {media_summary}.")
            parts.append("Dites-moi si vous voulez plus d'informations.")
            return " ".join(parts)

        parts = [base_message]
        if title and normalize_prompt(title) not in normalize_prompt(base_message):
            parts.insert(0, f"{title}.")
        if media_summary:
            parts.append(f"Au programme: {media_summary}.")
        parts.append("Votre avis nous interesse en commentaire.")
        parts.append(hashtags)
        return " ".join(part for part in parts if part).strip()

    def enhance_social_post(self, message, *, platform="", title="", media_paths=None):
        media_paths = _normalize_media_paths(media_paths)
        local_fallback = self._local_social_caption(
            message,
            platform,
            title=title,
            media_paths=media_paths,
        )
        if not can_answer_general_questions():
            return local_fallback

        media_summary = self._social_media_summary(media_paths)
        instructions = (
            "Tu es M.O.C.T.A.R, assistant local specialise dans la redaction de publications sociales. "
            "Tu ameliore la legende sans perdre l'intention du message. "
            "Style: clair, naturel, convaincant, coherent et utile. "
            "Pour WhatsApp: ton direct, chaleureux, court. "
            "Pour Facebook: plus narratif, engageant, avec une phrase d'accroche et un appel a l'action discret. "
            "Si le message est vide, cree une legende complete a partir du titre et du contexte media. "
            "Renvoie uniquement le texte final."
        )
        prompt = (
            f"Plateforme: {platform or 'social'}\n"
            f"Titre: {title or '(aucun)'}\n"
            f"Media: {media_summary or 'aucun'}\n"
            f"Message original:\n{message or '(vide)'}"
        )

        image_paths = [path for path in media_paths if _is_image_path(path) and Path(path).exists()][:3]
        ollama_model = _ollama_vision_model() if image_paths else ""
        try:
            return ask_ollama(
                prompt,
                images=image_paths,
                system_prompt=instructions,
                model=ollama_model or None,
                max_predict=160,
                temperature=0.35,
            )
        except Exception:
            return local_fallback

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
        allow_ollama=True,
    ):
        normalized = normalize_prompt(prompt)

        if not normalized:
            return self.explain_capabilities()

        math_answer = _answer_math_question(prompt)
        if math_answer:
            return math_answer

        if any(greeting in normalized.split() for greeting in ["bonjour", "salut", "hello", "coucou"]):
            return "Bonjour. Je suis pret. Dis-moi simplement ce que tu veux faire."

        if any(token in normalized for token in ["comment vas tu", "ca va", "tu vas bien"]):
            return "Je vais bien. Je suis pret a t'aider."

        if any(token in normalized for token in ["merci", "bravo", "super"]):
            return "Avec plaisir."

        if any(token in normalized for token in ["qui es tu", "presente toi", "ton nom"]):
            return "Je suis M.O.C.T.A.R, ton assistant local. Je peux agir sur ton ordinateur, tes projets, tes notes et tes taches."

        if any(
            token in normalized
            for token in ["que peux tu faire", "qu est ce que tu peux faire", "montre tes capacites"]
        ) or normalized in {"aide", "help"}:
            return self.explain_capabilities()

        if "youtube" in normalized:
            return self.youtube_guidance(prompt, preferences=preferences)

        if any(
            token in normalized
            for token in ["brief du jour", "resume ma journee", "organise ma journee", "rappelle mes priorites"]
        ):
            return self.generate_daily_brief(
                todos=todos,
                reminders=reminders,
                history=history,
                preferences=preferences,
                insights=insights,
            )

        if any(
            token in normalized
            for token in ["quoi faire maintenant", "prochaine action", "prochaine tache", "prochaine priorite"]
        ):
            return self.suggest_next_action(
                todos=todos,
                reminders=reminders,
                history=history,
                insights=insights,
            )

        if any(
            token in normalized
            for token in ["priorise mes taches", "priorise mes todos", "priorisation des taches", "classe mes priorites"]
        ):
            return self.summarize_priorities(todos=todos, insights=insights)

        if any(token in normalized for token in ["routine matin", "routine du matin"]):
            return self.build_auto_routine(todos=todos, reminders=reminders, mode="morning")

        if any(token in normalized for token in ["routine soir", "routine du soir"]):
            return self.build_auto_routine(todos=todos, reminders=reminders, mode="evening")

        if any(token in normalized for token in ["mode focus", "focus projet", "mode projet"]):
            return self.project_focus_mode(todos=todos, history=history, insights=insights)

        fallback = self.knowledge_library.lookup(prompt) or (
            "Je n'ai pas assez d'information pour repondre proprement. Reformule ta demande."
        )
        if allow_ollama and _should_use_ollama_assistant(prompt):
            return self._ask_personal_assistant(
                prompt,
                fallback=fallback,
                todos=todos,
                reminders=reminders,
                history=history,
                preferences=preferences,
                insights=insights,
                session_turn=session_turn,
            )
        return fallback

    def chat(self, prompt, *, context="", todos=None, reminders=None, history=None, preferences=None, insights=None, session_turn=None):
        prompt = (prompt or "").strip()
        context = (context or "").strip()
        if not prompt:
            return self.explain_capabilities()

        local_fallback = self.knowledge_library.lookup(prompt) or (
            "Je n'ai pas de reponse fiable pour le moment."
        )
        return self._ask_personal_assistant(
            prompt,
            fallback=local_fallback,
            todos=todos,
            reminders=reminders,
            history=history,
            preferences=preferences,
            insights=insights,
            session_turn=session_turn,
            extra_context=context,
        )


def chat_ollama(
    messages,
    *,
    tools=None,
    model=None,
    max_predict=OLLAMA_MAX_PREDICT,
    temperature=OLLAMA_TEMPERATURE,
    think=False,
    timeout=None,
):
    default_model, base_url = _ollama_settings()
    model = (model or default_model).strip() or default_model
    url = _ollama_chat_url(base_url)
    payload = {
        "model": model,
        "messages": list(messages),
        "stream": False,
    }
    if tools:
        payload["tools"] = list(tools)

    inference_options = _ollama_inference_options(max_predict, temperature)
    if url.endswith("/chat/completions"):
        payload.update(
            {
                "max_tokens": inference_options["num_predict"],
                "temperature": inference_options["temperature"],
                "top_p": inference_options["top_p"],
                "stop": inference_options["stop"],
            }
        )
    else:
        payload.update(
            {
                "think": bool(think) and model != config.OLLAMA_MODEL,
                "keep_alive": "5m",
                "options": inference_options,
            }
        )

    payload_bytes = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=payload_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS if timeout is None else timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise OllamaTimeoutError("Ollama request timed out") from exc
    except URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise OllamaTimeoutError("Ollama request timed out") from exc
        raise OllamaRequestError("Ollama is unavailable") from exc
    except (HTTPError, ValueError) as exc:
        raise OllamaRequestError("Ollama request failed") from exc
    if not isinstance(response_payload, dict):
        raise RuntimeError("invalid Ollama response")
    return response_payload


def ask_ollama(
    prompt,
    *,
    images=None,
    system_prompt=None,
    model=None,
    max_predict=OLLAMA_MAX_PREDICT,
    temperature=OLLAMA_TEMPERATURE,
    tools=None,
    think=False,
    return_full_response=False,
):
    user_message = {"role": "user", "content": prompt}
    encoded_images = []
    for image_path in _normalize_media_paths(images):
        if not _is_image_path(image_path):
            continue
        if not Path(image_path).exists():
            continue
        encoded_images.append(_encode_image_file(image_path))
    if encoded_images:
        user_message["images"] = encoded_images

    messages = [
        {
            "role": "system",
            "content": system_prompt or (
                MOCTAR_SYSTEM_PROMPT + " "
                "Pour les sujets medicaux, juridiques ou financiers, signale les limites et conseille une verification professionnelle."
            ),
        },
        user_message,
    ]
    provider = _ai_provider()

    def local_request():
        options = {}
        if provider == "hybrid":
            options["timeout"] = min(OLLAMA_TIMEOUT_SECONDS, HYBRID_OLLAMA_TIMEOUT_SECONDS)
        payload = chat_ollama(
            messages, tools=tools, model=model, max_predict=max_predict,
            temperature=temperature, think=think, **options,
        )
        message = extract_ollama_message(payload)
        if not message.get("content") and not message.get("tool_calls"):
            raise OllamaRequestError("Ollama returned an empty message")
        return payload

    def cloud_request():
        brain = AIBrain(api_type="gemini")
        try:
            reply = brain.chat(messages, tools=tools, temperature=temperature)
        finally:
            if brain.gemini_client is not None:
                brain.gemini_client.close()
        if reply.get("finish_reason") == "error":
            raise OllamaRequestError("AI providers are unavailable")
        message = {"role": "assistant", "content": reply.get("content") or ""}
        if reply.get("tool_calls"):
            message["tool_calls"] = [
                {"id": call["id"], "type": "function", "function": {
                    "name": call["name"], "arguments": call["arguments"],
                }} for call in reply["tool_calls"]
            ]
        return {"message": message, "done_reason": reply["finish_reason"]}

    if provider == "gemini":
        response_payload = cloud_request()
    elif provider == "hybrid":
        try:
            response_payload = _with_ollama_deadline(
                local_request, min(OLLAMA_TIMEOUT_SECONDS, HYBRID_OLLAMA_TIMEOUT_SECONDS),
            )
        except Exception:
            response_payload = cloud_request()
    else:
        response_payload = local_request()
    if return_full_response or tools is not None:
        return response_payload
    return _extract_ollama_content(response_payload)


def safe_ask_ollama(prompt, fallback="Je ne peux pas utiliser l'IA pour le moment.", **kwargs):
    try:
        return ask_ollama(prompt, **kwargs)
    except Exception:
        return fallback



# ==============================================================================
# CLASSE AIBrain : COUCHE UNIVERSELLE AVEC SUPPORT NATIF TOOL CALLING
# ==============================================================================

class AIBrain:
    """Couche d'inférence universelle pour LLM avec support avancé du Function Calling.

    Prend en charge nativement :
    - Les modèles Ollama locaux (/api/chat)
    - Gemini et le repli hybride Ollama / Gemini
    - Les API compatibles OpenAI (/v1/chat/completions)
    - La normalisation stricte des réponses en format unifié :
      {
          "content": Optional[str],
          "tool_calls": List[Dict[str, Any]],  # [{"id": str, "name": str, "arguments": dict}]
          "finish_reason": str                  # 'stop', 'tool_calls', ou 'error'
      }
    - Le parsing résilient des arguments pour petits modèles locaux
    - La gestion robuste des erreurs réseau et timeouts
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        api_type: Optional[str] = None,
    ) -> None:
        load_dotenv()
        env_model, env_base_url = _ollama_settings()
        requested_type = (api_type or _ai_provider()).lower()
        self.ai_provider = _ai_provider() if requested_type == "auto" else requested_type
        # Explicit Ollama configuration must not inherit the cloud API settings.
        default_base_url = (
            env_base_url
            if requested_type in {"ollama", "gemini", "hybrid"} or os.getenv("AI_PROVIDER")
            else os.getenv("OPENAI_BASE_URL") or env_base_url
        )
        self.base_url = (base_url or default_base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.timeout = float(timeout or os.getenv("LLM_TIMEOUT_SECONDS") or OLLAMA_TIMEOUT_SECONDS)

        # Détection automatique du type de backend
        if requested_type == "auto" and (os.getenv("AI_PROVIDER") or self.ai_provider == "gemini"):
            self.api_type = "gemini" if self.ai_provider == "gemini" else "ollama"
        elif requested_type == "auto":
            endpoint = urlsplit(self.base_url)
            is_ollama = endpoint.hostname in {"localhost", "127.0.0.1", "::1"} or endpoint.port == 11434
            if "/v1" in self.base_url or "openai.com" in self.base_url or (self.api_key and not is_ollama):
                self.api_type = "openai"
            else:
                self.api_type = "ollama"
        else:
            self.api_type = "ollama" if requested_type == "hybrid" else requested_type

        if self.api_type == "openai":
            self.ai_provider = "openai"

        backend_model = os.getenv("OPENAI_MODEL") if self.api_type == "openai" else env_model
        self.model = (model or backend_model or env_model or DEFAULT_MODEL).strip()
        # The bridge may pass an explicit Ollama model, so keep cloud selection separate.
        self.gemini_model = os.getenv("GEMINI_MODEL", config.gemini_model).strip() or config.gemini_model
        self.gemini_client = None
        gemini_key = os.getenv("GEMINI_API_KEY", config.GEMINI_API_KEY).strip()
        if gemini_key and genai is not None:
            try:
                self.gemini_client = genai.Client(
                    api_key=gemini_key,
                    http_options={"timeout": max(1, int(self.timeout * 1000))},
                )
            except Exception:
                logger.warning("Initialisation Gemini indisponible")

        logger.info(
            "AIBrain initialisé [type=%s, model=%s, base_url=%s, timeout=%ss]",
            self.api_type,
            self.model,
            self.base_url,
            self.timeout,
        )

    @staticmethod
    def _parse_arguments(raw_args: Any) -> Dict[str, Any]:
        """Convertit de manière résiliente des arguments en dictionnaire Python.

        Gère les cas où le modèle retourne un dictionnaire natif, une chaîne JSON propre,
        du markdown JSON ou une chaîne JSON imparfaite.
        """
        if isinstance(raw_args, dict):
            return raw_args
        if not raw_args:
            return {}
        if isinstance(raw_args, str):
            cleaned = raw_args.strip()
            # Nettoyage des balises Markdown ```json ... ```
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned).strip()
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            # Recherche regex d'un objet JSON { ... }
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    pass
        return {}

    @classmethod
    def _extract_text_tool_calls(cls, content: str) -> List[Dict[str, Any]]:
        """Extrait les tool calls rédigés sous forme de texte brut par certains modèles locaux."""
        tool_calls: List[Dict[str, Any]] = []
        if not content:
            return tool_calls

        # Format 1 : XML-like <tool_call> ... </tool_call>
        xml_matches = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", content, re.DOTALL)
        for block in xml_matches:
            parsed = cls._parse_arguments(block)
            name = parsed.get("name") or parsed.get("tool") or parsed.get("function")
            if name:
                args = parsed.get("arguments") or parsed.get("parameters") or {}
                tool_calls.append({
                    "id": f"call_{name}_{uuid4().hex[:8]}",
                    "name": name,
                    "arguments": cls._parse_arguments(args),
                })

        # Format 2 : [TOOL_CALLS] [...]
        if not tool_calls and "[TOOL_CALLS]" in content:
            after = content.split("[TOOL_CALLS]", 1)[1].strip()
            try:
                parsed_list = json.loads(after)
                if isinstance(parsed_list, list):
                    for item in parsed_list:
                        if isinstance(item, dict) and "name" in item:
                            tool_calls.append({
                                "id": f"call_{item['name']}_{uuid4().hex[:8]}",
                                "name": item["name"],
                                "arguments": cls._parse_arguments(item.get("arguments", {})),
                            })
            except Exception:
                pass

        # Format 3 : JSON direct dans le texte {"name": "...", "arguments": {...}}
        if not tool_calls and "{" in content and "}" in content:
            json_matches = re.findall(r"(\{(?:[^{}]|(?:\{[^{}]*\}))*\})", content, re.DOTALL)
            for jm in json_matches:
                try:
                    obj = json.loads(jm)
                    if isinstance(obj, dict):
                        name = obj.get("name") or obj.get("action") or obj.get("tool") or obj.get("function")
                        if name and isinstance(name, str):
                            args = obj.get("arguments") or obj.get("action_input") or obj.get("parameters") or obj.get("args") or {}
                            if isinstance(args, str):
                                args = cls._parse_arguments(args)
                            tool_calls.append({
                                "id": f"call_{name}_{uuid4().hex[:8]}",
                                "name": name,
                                "arguments": args if isinstance(args, dict) else {"target": str(args)},
                            })
                except Exception:
                    pass

        # Format 4 : ReAct textuel Action: <tool>\nAction Input: <input>
        if not tool_calls and "Action:" in content:
            react_matches = re.findall(r"Action:\s*([a-zA-Z0-9_-]+)\s*(?:\n|\r\n)Action Input:\s*(.+?)(?=(?:\nAction:|\Z))", content, re.DOTALL | re.IGNORECASE)
            for act_name, act_input in react_matches:
                act_name = act_name.strip()
                parsed_args = cls._parse_arguments(act_input.strip())
                if not isinstance(parsed_args, dict):
                    parsed_args = {"target": act_input.strip().strip("\"'")}
                tool_calls.append({
                    "id": f"call_{act_name}_{uuid4().hex[:8]}",
                    "name": act_name,
                    "arguments": parsed_args,
                })

        return tool_calls

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Exécute un tour de conversation avec support universel du Tool Calling.

        Args:
            messages: Historique des messages au format [{"role": "...", "content": "..."}].
            tools: Schémas JSON des outils disponibles au format OpenAI/Ollama.
            temperature: Température d'échantillonnage (0.2 par defaut).

        Returns:
            Dict[str, Any] normalisé :
            {
                "content": Optional[str],
                "tool_calls": List[Dict[str, Any]],
                "finish_reason": str
            }
        """
        if self.ai_provider == "gemini":
            return self._chat_gemini(messages, tools=tools, temperature=0.2 if temperature is None else temperature)
        if self.api_type == "openai":
            return self._chat_openai(messages=messages, tools=tools, temperature=0.2 if temperature is None else temperature)
        if self.ai_provider == "hybrid":
            try:
                result = _with_ollama_deadline(
                    lambda: self._chat_ollama(
                        messages=messages, tools=tools,
                        temperature=OLLAMA_TEMPERATURE if temperature is None else temperature,
                    ),
                    min(self.timeout, HYBRID_OLLAMA_TIMEOUT_SECONDS),
                )
                if result.get("finish_reason") != "error" and (result.get("content") or result.get("tool_calls")):
                    return result
            except Exception:
                pass
            logger.info("Repli automatique vers Gemini")
            return self._chat_gemini(messages, tools=tools, temperature=0.2 if temperature is None else temperature)
        return self._chat_ollama(
            messages=messages, tools=tools,
            temperature=OLLAMA_TEMPERATURE if temperature is None else temperature,
        )

    def _chat_gemini(self, messages, tools=None, temperature=0.2):
        """Translate model/tool turns without letting the SDK execute local actions."""
        error = {"content": "Le moteur IA est indisponible pour le moment.",
                 "tool_calls": [], "finish_reason": "error"}
        if self.gemini_client is None:
            return error
        try:
            contents = []
            instructions = []
            for message in messages:
                role = message.get("role")
                content = message.get("content")
                if role == "system":
                    if content:
                        instructions.append(str(content))
                    continue
                parts = []
                if role == "tool":
                    observation = content
                    if isinstance(observation, str):
                        try:
                            observation = json.loads(observation)
                        except ValueError:
                            pass
                    if not isinstance(observation, dict):
                        observation = {"result": observation}
                    function_response = {"name": message["name"], "response": observation}
                    if message.get("tool_call_id"):
                        function_response["id"] = message["tool_call_id"]
                    parts.append({"function_response": function_response})
                else:
                    if content:
                        parts.append({"text": str(content)})
                    for encoded in message.get("images") or []:
                        data = base64.b64decode(encoded)
                        mime_type = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
                        parts.append({"inline_data": {"mime_type": mime_type, "data": data}})
                    for call in message.get("tool_calls") or []:
                        function = call.get("function") or call
                        function_call = {"name": function["name"],
                                         "args": self._parse_arguments(function.get("arguments"))}
                        if call.get("id"):
                            function_call["id"] = call["id"]
                        parts.append({"function_call": function_call})
                if not parts:
                    continue
                gemini_role = "model" if role == "assistant" else "user"
                if contents and contents[-1]["role"] == gemini_role:
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": gemini_role, "parts": parts})

            generation_config = {
                "temperature": temperature,
                "max_output_tokens": GENERATION_CONFIG["max_tokens"],
                "top_p": GENERATION_CONFIG["top_p"],
                "automatic_function_calling": {"disable": True},
                "http_options": {"timeout": max(1, int(self.timeout * 1000))},
            }
            if instructions:
                generation_config["system_instruction"] = "\n\n".join(instructions)
            if tools:
                declarations = []
                for tool in tools:
                    function = tool.get("function") or tool
                    declarations.append({
                        "name": function["name"], "description": function.get("description", ""),
                        "parameters_json_schema": function.get("parameters") or {"type": "object", "properties": {}},
                    })
                generation_config["tools"] = [{"function_declarations": declarations}]
            response = self.gemini_client.models.generate_content(
                model=self.gemini_model, contents=contents, config=generation_config,
            )
            candidates = response.candidates or []
            if not candidates or not candidates[0].content:
                return error
            text_parts = []
            tool_calls = []
            for part in candidates[0].content.parts or []:
                if part.thought:
                    continue
                if part.text:
                    text_parts.append(part.text)
                if part.function_call:
                    call = part.function_call
                    tool_calls.append({"id": call.id or f"call_{call.name}_{uuid4().hex[:8]}",
                                       "name": call.name, "arguments": dict(call.args or {})})
            content = "".join(text_parts) or None
            if not content and not tool_calls:
                return error
            if not tool_calls and content:
                tool_calls = self._extract_text_tool_calls(content)
            return {"content": content, "tool_calls": tool_calls,
                    "finish_reason": "tool_calls" if tool_calls else "stop"}
        except Exception:
            # Do not expose credentials or transport details in user-facing replies.
            logger.warning("Appel Gemini indisponible")
            return error

    def _chat_ollama(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = OLLAMA_TEMPERATURE,
    ) -> Dict[str, Any]:
        endpoint = f"{self.base_url}/api/chat"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "stream": False,
            "options": _ollama_inference_options(temperature=temperature),
        }
        if tools:
            payload["tools"] = list(tools)

        req = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            request_timeout = min(self.timeout, HYBRID_OLLAMA_TIMEOUT_SECONDS) if self.ai_provider == "hybrid" else self.timeout
            with urlopen(req, timeout=request_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
            logger.error("Erreur HTTP %s (%s) Ollama: %s", exc.code, exc.reason, err_body)
            # Si /api/chat retourne 404, tenter un fallback automatique vers /v1/chat/completions
            if exc.code == 404:
                logger.info("Ollama /api/chat a retourné 404, tentative via OpenAI endpoint...")
                return self._chat_openai(messages, tools=tools, temperature=temperature)
            return {
                "content": f"Erreur HTTP {exc.code} du serveur Ollama : {exc.reason}",
                "tool_calls": [],
                "finish_reason": "error",
            }
        except (URLError, TimeoutError, socket.timeout) as exc:
            logger.error("Erreur de connexion/timeout Ollama: %s", exc)
            return {
                "content": f"Le serveur Ollama ({self.base_url}) est inaccessible ou a expiré : {exc}",
                "tool_calls": [],
                "finish_reason": "error",
            }
        except Exception as exc:
            logger.exception("Erreur inattendue Ollama: %s", exc)
            return {
                "content": f"Erreur inattendue lors de la communication avec Ollama : {exc}",
                "tool_calls": [],
                "finish_reason": "error",
            }

        return self._normalize_ollama_payload(data)

    def _chat_openai(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        if self.base_url.endswith("/v1"):
            endpoint = f"{self.base_url}/chat/completions"
        elif "/v1/" in self.base_url or self.base_url.endswith("/chat/completions"):
            endpoint = self.base_url
        else:
            endpoint = f"{self.base_url}/v1/chat/completions"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": GENERATION_CONFIG["max_tokens"],
            "top_p": GENERATION_CONFIG["top_p"],
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"

        req = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            request_timeout = min(self.timeout, HYBRID_OLLAMA_TIMEOUT_SECONDS) if self.ai_provider == "hybrid" else self.timeout
            with urlopen(req, timeout=request_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
            logger.error("Erreur HTTP %s (%s) API OpenAI: %s", exc.code, exc.reason, err_body)
            return {
                "content": f"Erreur HTTP {exc.code} du serveur compatible OpenAI : {exc.reason}",
                "tool_calls": [],
                "finish_reason": "error",
            }
        except (URLError, TimeoutError, socket.timeout) as exc:
            logger.error("Erreur de connexion/timeout API OpenAI: %s", exc)
            return {
                "content": f"L'API OpenAI ({self.base_url}) est inaccessible ou a expiré : {exc}",
                "tool_calls": [],
                "finish_reason": "error",
            }
        except Exception as exc:
            logger.exception("Erreur inattendue API OpenAI: %s", exc)
            return {
                "content": f"Erreur inattendue lors de l'appel OpenAI : {exc}",
                "tool_calls": [],
                "finish_reason": "error",
            }

        return self._normalize_openai_payload(data)

    def _normalize_ollama_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise la réponse brute d'Ollama (/api/chat)."""
        msg = data.get("message") or {}
        content = msg.get("content") or None
        raw_tool_calls = msg.get("tool_calls") or []

        normalized_tool_calls: List[Dict[str, Any]] = []
        for tc in raw_tool_calls:
            func = tc.get("function") or {}
            name = func.get("name")
            if not name:
                continue
            args = self._parse_arguments(func.get("arguments"))
            call_id = tc.get("id") or f"call_{name}_{uuid4().hex[:8]}"
            normalized_tool_calls.append({
                "id": call_id,
                "name": name,
                "arguments": args,
            })

        # Extraction de tool calls textuels si le modèle les a insérés dans content
        if not normalized_tool_calls and content:
            text_calls = self._extract_text_tool_calls(content)
            if text_calls:
                normalized_tool_calls = text_calls

        finish_reason = "tool_calls" if normalized_tool_calls else (data.get("done_reason") or "stop")
        return {
            "content": content,
            "tool_calls": normalized_tool_calls,
            "finish_reason": finish_reason,
        }

    def _normalize_openai_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise la réponse brute compatible OpenAI (/v1/chat/completions)."""
        choices = data.get("choices") or [{}]
        choice = choices[0] if choices else {}
        msg = choice.get("message") or {}
        content = msg.get("content") or None
        raw_tool_calls = msg.get("tool_calls") or []

        normalized_tool_calls: List[Dict[str, Any]] = []
        for tc in raw_tool_calls:
            func = tc.get("function") or {}
            name = func.get("name")
            if not name:
                continue
            args = self._parse_arguments(func.get("arguments"))
            call_id = tc.get("id") or f"call_{name}_{uuid4().hex[:8]}"
            normalized_tool_calls.append({
                "id": call_id,
                "name": name,
                "arguments": args,
            })

        if not normalized_tool_calls and content:
            text_calls = self._extract_text_tool_calls(content)
            if text_calls:
                normalized_tool_calls = text_calls

        finish_reason = choice.get("finish_reason") or ("tool_calls" if normalized_tool_calls else "stop")
        return {
            "content": content,
            "tool_calls": normalized_tool_calls,
            "finish_reason": finish_reason,
        }
