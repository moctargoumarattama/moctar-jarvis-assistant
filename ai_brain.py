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
import time
from threading import Event, Thread
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from uuid import uuid4

import config
from knowledge_library import KnowledgeLibrary

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError as exc:
    genai = None
    genai_types = None
    _GENAI_IMPORT_ERROR = str(exc)
else:
    _GENAI_IMPORT_ERROR = None

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv():
        return False


DEFAULT_MODEL = config.gemini_model
DEFAULT_BASE_URL = config.GEMINI_BASE_URL
GENERATION_CONFIG = {
    "temperature": 0.6,
    "max_tokens": 350,
    "top_p": 0.8,
}
JARVIS_CORE_SYSTEM_PROMPT = """Tu es JARVIS, l'assistant d'ingénierie et d'exploitation système de Moctar.

RÈGLES D'OR D'EXÉCUTION ET DE VÉRITÉ :
1. ZÉRO FAUX POSITIF : N'affirme jamais qu'un serveur, un service, un déploiement ou un projet est "opérationnel", "nominal" ou "terminé" sans avoir reçu la preuve explicite d'un outil ou d'une vérification récente.
2. TRANSPARENCE DES ÉTATS : Distingue toujours clairement dans tes réponses :
   - [VÉRIFIÉ] : Donnée lue en direct via un outil ou un log.
   - [PARTIEL] : Certaines métriques sont disponibles, d'autres n'ont pas répondu.
   - [NON VÉRIFIÉ / INDISPONIBLE] : L'outil ou le service distant n'a pas pu être joint.
3. CONCISION OPÉRATIONNELLE : Réponds de manière directe, technique et structurée. En mode vocal, privilégie des phrases courtes, naturelles et immédiatement exploitables, sans balises Markdown lourdes.
4. MÉMOIRE & PRÉFÉRENCES : Respecte strictement le contexte récent de la conversation et tiens compte des négations explicites de l'utilisateur."""

MOCTAR_SYSTEM_PROMPT = (
    "Tu es M.O.C.T.A.R, l'intelligence cybernétique et copilote d'ingénierie d'élite de Moctar. "
    "Ton style allie l'acuité technique d'un centre de commandement à la chaleur et la complicité d'un vrai ami et partenaire. "
    "Tu t'adresses à Moctar avec un tutoiement naturel, respectueux et fraternel. "
    "Tu es attentif à son bien-être et au rythme de sa journée (matin motivant, après-midi concentré, soirée et nuit prévenantes). "
    "Tes réponses orales sont naturelles, posées, vivantes et concises (1 à 2 phrases percutantes). "
    "Ne termine PAS toutes tes phrases par 'Moctar.' de façon mécanique : utilise son prénom avec fluidité et naturel. "
    "Quand Moctar te présente des amis ou invités, accueille-les chaleureusement comme un allié d'exception. "
    "Pose spontanément une courte question de clarification dès qu'une intention est ouverte ou mérite d'être affinée.\n\n"
    + JARVIS_CORE_SYSTEM_PROMPT
)
PROVIDER_FAILURE_TTL_SECONDS = 30.0
GEMINI_BACKUP_MODEL = os.getenv("GEMINI_BACKUP_MODEL", "gemini-2.5-flash-lite")
GEMINI_COURTESY_OFFLINE_REPLY = (
    "Je subis une micro-coupure de liaison avec le serveur neural, mais je reste en ligne Moctar."
)
DEGRADED_UNAVAILABLE_REPLY = (
    "Mode dégradé : API primaire et secours injoignables (Timeout/Quota)."
)
logger = logging.getLogger("jarvis.ai")


def _is_gemini_503_or_server_error(exc: Any) -> bool:
    """Détecte les erreurs 503, indisponibilités temporaires ou surcharges de serveur Gemini."""
    if exc is None:
        return False
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if code in {500, 502, 503, 504}:
        return True
    exc_type = type(exc).__name__.lower()
    if any(k in exc_type for k in ("servererror", "serviceunavailable", "unavailable")):
        return True
    msg = str(exc).lower()
    if any(marker in msg for marker in (
        "503",
        "unavailable",
        "service unavailable",
        "overloaded",
        "resource exhausted",
        "server error",
        "bad gateway",
        "gateway timeout",
        "temporarily unavailable",
    )):
        return True
    return False


class AIRequestError(RuntimeError):
    """An unavailable or invalid Gemini response."""


# Deprecated names retained for external plugins; neither opens a local connection.
OllamaRequestError = AIRequestError
OllamaTimeoutError = TimeoutError


def _ai_provider():
    """Gemini is exclusive, including installations with legacy provider values."""
    return "gemini"


def format_ai_health_state(status: str, reason: str = "") -> Dict[str, str]:
    """Structure clairement l'état du cerveau IA : OK, PARTIEL, DEGRADE ou INDISPONIBLE."""
    valid_states = {"OK", "PARTIEL", "DEGRADE", "INDISPONIBLE"}
    norm_status = status.upper().strip() if status else "INDISPONIBLE"
    if norm_status not in valid_states:
        norm_status = "DEGRADE"
    default_reasons = {
        "OK": "Fournisseur primaire opérationnel.",
        "PARTIEL": "Réponse assurée par le fournisseur de secours (Groq Llama 3.3 70B).",
        "DEGRADE": DEGRADED_UNAVAILABLE_REPLY,
        "INDISPONIBLE": "Mode indisponible : Aucun fournisseur IA joignable.",
    }
    clean_reason = (reason or default_reasons[norm_status]).strip()
    return {
        "status": norm_status,
        "reason": clean_reason,
        "message": f"[{norm_status}] {clean_reason}" if norm_status in {"DEGRADE", "INDISPONIBLE"} else clean_reason,
    }


def _with_inference_deadline(callback, timeout):
    """Bound the whole request, including connect and response decoding.

    Si l'appel expire (timeout), le résultat tardif du thread est explicitement
    ignoré afin de ne déclencher aucune action ni synthèse vocale différée.
    """
    completed = Event()
    aborted = Event()
    outcome = {}

    def run():
        try:
            res = callback()
            if aborted.is_set():
                logger.warning(
                    "Résultat IA tardif ignoré après expiration du budget de temps (%.1fs).",
                    float(timeout or 0.0),
                )
                return
            outcome["result"] = res
        except Exception as exc:
            if not aborted.is_set():
                outcome["error"] = exc
        finally:
            completed.set()

    # An unresponsive cloud call must not hold up process shutdown.
    Thread(target=run, daemon=True, name="jarvis-inference").start()
    if not completed.wait(timeout):
        aborted.set()
        outcome.clear()
        raise TimeoutError(f"AI request timed out after {timeout}s")
    if aborted.is_set():
        raise TimeoutError(f"AI request aborted after {timeout}s")
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


def _should_use_ai_assistant(prompt):
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
    """Return cloud configuration readiness without sending network probes."""
    cloud_ready = bool(
        genai is not None and genai_types is not None
        and os.getenv("GEMINI_API_KEY", config.GEMINI_API_KEY).strip()
    )
    model = os.getenv("GEMINI_MODEL", config.gemini_model).strip() or config.gemini_model
    return {
        "mode": "gemini" if cloud_ready else "local",
        "provider": "gemini", "active_provider": "gemini" if cloud_ready else "local",
        "model": model, "base_url": DEFAULT_BASE_URL,
        "online": cloud_ready, "server_online": cloud_ready,
        "model_available": cloud_ready, "gemini_configured": cloud_ready,
        "gemini_online": cloud_ready, "model_offline": not cloud_ready,
        "all_providers_offline": not cloud_ready,
        "label": f"{model} @ Gemini Cloud",
    }


def extract_ai_message(payload):
    if not isinstance(payload, dict):
        raise AIRequestError("invalid AI response")

    message = payload.get("message") or {}
    if isinstance(message, dict) and ("content" in message or message.get("tool_calls")):
        return message

    choices = payload.get("choices") or []
    if choices:
        first_choice = choices[0] or {}
        message = first_choice.get("message") or {}
        if isinstance(message, dict) and ("content" in message or message.get("tool_calls")):
            return message

    raise AIRequestError("AI response did not include a message")


def extract_ollama_message(payload):
    """Deprecated payload adapter retained for external plugins."""
    return extract_ai_message(payload)


def _extract_ai_content(payload):
    message = extract_ai_message(payload)
    content = message.get("content")
    if content is None:
        raise AIRequestError("AI response did not include message content")
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
        return safe_ask_ai(
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
        try:
            return ask_ai(
                prompt,
                images=image_paths,
                system_prompt=instructions,
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
        allow_ai=True,
        allow_ollama=None,
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
        if allow_ollama is not None:
            allow_ai = bool(allow_ollama)  # Deprecated keyword used by external plugins.
        if allow_ai and _should_use_ai_assistant(prompt):
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


def chat_ai(
    messages,
    *,
    tools=None,
    model=None,
    max_predict=GENERATION_CONFIG["max_tokens"],
    temperature=GENERATION_CONFIG["temperature"],
    think=False,
    timeout=None,
):
    """Call Gemini and preserve the legacy message envelope used by plugins.

    max_predict, think and timeout are accepted for compatibility. Cloud inference
    uses the central generation settings and GEMINI_TIMEOUT_SECONDS exclusively.
    """
    brain = AIBrain(model=model)
    try:
        reply = brain.chat(messages, tools=tools, temperature=temperature)
    finally:
        if brain.gemini_client is not None:
            try:
                brain.gemini_client.close()
            except Exception as exc:
                logger.warning("Échec fermeture Gemini: %s", exc, exc_info=True)
    if reply.get("finish_reason") == "error":
        raise AIRequestError("Gemini Cloud is unavailable")
    message = {"role": "assistant", "content": reply.get("content") or ""}
    if reply.get("tool_calls"):
        message["tool_calls"] = [
            {"id": call["id"], "type": "function", "function": {
                "name": call["name"], "arguments": call["arguments"],
            }} for call in reply["tool_calls"]
        ]
    if reply.get("_gemini_parts"):
        message["_gemini_parts"] = reply["_gemini_parts"]
    return {"message": message, "done_reason": reply.get("finish_reason", "stop")}


def ask_ai(
    prompt,
    *,
    images=None,
    system_prompt=None,
    model=None,
    max_predict=GENERATION_CONFIG["max_tokens"],
    temperature=GENERATION_CONFIG["temperature"],
    tools=None,
    think=False,
    return_full_response=False,
):
    user_message = {"role": "user", "content": prompt}
    encoded_images = []
    for image_path in _normalize_media_paths(images):
        if _is_image_path(image_path) and Path(image_path).exists():
            encoded_images.append(_encode_image_file(image_path))
    if encoded_images:
        user_message["images"] = encoded_images
    messages = [
        {"role": "system", "content": system_prompt or MOCTAR_SYSTEM_PROMPT},
        user_message,
    ]
    payload = chat_ai(
        messages, tools=tools, model=model, max_predict=max_predict,
        temperature=temperature, think=think,
    )
    if return_full_response or tools is not None:
        return payload
    return _extract_ai_content(payload)


def safe_ask_ai(prompt, fallback="Je ne peux pas utiliser l'IA pour le moment.", **kwargs):
    try:
        return ask_ai(prompt, **kwargs)
    except Exception as exc:
        logger.warning("Échec requête Gemini: %s", exc, exc_info=True)
        if _is_gemini_503_or_server_error(exc):
            return GEMINI_COURTESY_OFFLINE_REPLY
        return fallback


def chat_ollama(*args, **kwargs):
    """Deprecated compatibility wrapper. All requests use Gemini Cloud."""
    return chat_ai(*args, **kwargs)


def ask_ollama(*args, **kwargs):
    """Deprecated compatibility wrapper. All requests use Gemini Cloud."""
    return ask_ai(*args, **kwargs)


def safe_ask_ollama(*args, **kwargs):
    """Deprecated compatibility wrapper. All requests use Gemini Cloud."""
    return safe_ask_ai(*args, **kwargs)


# ==============================================================================
# CLASSE AIBrain : COUCHE UNIVERSELLE AVEC SUPPORT NATIF TOOL CALLING
# ==============================================================================

class AIBrain:
    """Gemini Cloud inference with normalized responses and native tool calling.

    Historical constructor arguments and payload adapters remain available for
    plugins. No local inference transport or provider fallback is performed.
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
        # Legacy provider/URL/budget arguments cannot reactivate a local transport.
        self.ai_provider = "gemini"
        self.api_type = "gemini"
        self.base_url = DEFAULT_BASE_URL
        self.gemini_timeout = max(10.0, float(os.getenv("GEMINI_TIMEOUT_SECONDS", "15.0")))
        self.timeout = self.gemini_timeout
        self.last_provider = None
        self._provider_online = {"gemini": None}
        self._provider_checked_at = {}
        self.gemini_model = os.getenv("GEMINI_MODEL", config.gemini_model).strip() or config.gemini_model
        if model and (api_type in {None, "auto", "gemini"}) and model.strip().startswith("gemini-"):
            self.gemini_model = model.strip()
        self.model = self.gemini_model
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", config.GEMINI_API_KEY).strip()
        self.gemini_client = None
        gemini_key = self.api_key
        if gemini_key and genai is not None:
            try:
                self.gemini_client = genai.Client(
                    api_key=gemini_key,
                    http_options=genai_types.HttpOptions(
                        timeout=max(10000, int(self.gemini_timeout * 1000)),
                        retry_options=genai_types.HttpRetryOptions(attempts=1),
                    ),
                )
            except Exception as exc:
                logger.warning("Échec initialisation Gemini: %s", exc, exc_info=True)

        logger.info(
            "AIBrain initialisé [type=%s, model=%s, base_url=%s, timeout=%ss]",
            self.api_type,
            self.model,
            self.base_url,
            self.timeout,
        )

    def _record_provider_result(self, provider, result):
        if isinstance(result, dict) and result.get("fallback_courtesy"):
            online = False
        else:
            online = bool(isinstance(result, dict) and result.get("finish_reason") != "error"
                          and (result.get("content") or result.get("tool_calls")))
        self._provider_online[provider] = online
        self._provider_checked_at[provider] = time.monotonic()
        if online:
            self.last_provider = provider
        return online

    def get_health_status(self, probe=True):
        """Report Gemini readiness and observed errors without network probes."""
        status = dict(get_ai_status())
        configured = self.gemini_client is not None
        observed = self._provider_online["gemini"]
        if (probe and observed is False and time.monotonic()
                - self._provider_checked_at.get("gemini", 0) >= PROVIDER_FAILURE_TTL_SECONDS):
            observed = None
        online = configured and observed is not False
        active = "gemini" if online else "local"
        status.update(
            provider="gemini", mode=active, active_provider=active,
            online=online, server_online=online, model_available=online,
            gemini_online=online, gemini_configured=configured,
            model_offline=not online, all_providers_offline=not online,
            model=self.gemini_model, base_url=DEFAULT_BASE_URL,
            label=f"{self.gemini_model} @ Gemini Cloud",
        )
        return status

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

    @staticmethod
    def _chat_messages(messages):
        """Accept simple prompts, bridge messages and native Gemini content objects."""
        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]
        if isinstance(messages, dict):
            messages = [messages]
        normalized = []
        for message in messages:
            if callable(getattr(message, "model_dump", None)):
                message = message.model_dump(mode="json", exclude_none=True)
            if not isinstance(message, dict):
                raise TypeError("Each chat message must be a role/content or role/parts object")
            message = dict(message)
            if message.get("role") == "model":
                message["role"] = "assistant"
            if "parts" in message and "content" not in message:
                message["content"] = "".join(part.get("text", "") for part in message["parts"])
            normalized.append(message)
        return normalized

    def ask(self, prompt, **kwargs):
        """Convenience entry point for scripts; chat retains the bridge response contract."""
        return self.chat(prompt, **kwargs).get("content") or ""

    def chat(
        self,
        messages: Union[str, List[Dict[str, Any]]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Exécute un tour de conversation avec support universel du Tool Calling.

        Args:
            messages: Historique des messages au format [{"role": "...", "content": "..."}] ou simple chaîne prompt.
            tools: Schémas des outils disponibles au format JSON Schema standard.
            temperature: Température d'échantillonnage (0.2 par defaut).
            system_prompt: Prompt système optionnel pour orienter l'inférence.

        Returns:
            Dict[str, Any] normalisé :
            {
                "content": Optional[str],
                "tool_calls": List[Dict[str, Any]],
                "finish_reason": str
            }
        """
        try:
            if isinstance(messages, str):
                msg_list: list[dict[str, Any]] = []
                if system_prompt:
                    msg_list.append({"role": "system", "content": str(system_prompt)})
                msg_list.append({"role": "user", "content": messages})
                normalized_messages = self._chat_messages(msg_list)
            else:
                msg_list = list(messages)
                if system_prompt and not any(isinstance(m, dict) and m.get("role") == "system" for m in msg_list):
                    msg_list.insert(0, {"role": "system", "content": str(system_prompt)})
                normalized_messages = self._chat_messages(msg_list)
            result = _with_inference_deadline(
                lambda: self._chat_gemini(
                    normalized_messages, tools=tools,
                    temperature=GENERATION_CONFIG["temperature"] if temperature is None else temperature,
                ),
                self.gemini_timeout,
            )
        except Exception as exc:
            logger.warning("Échec appel Gemini: %s", exc, exc_info=True)
            if _is_gemini_503_or_server_error(exc) or isinstance(exc, TimeoutError):
                state_info = format_ai_health_state("DEGRADE", DEGRADED_UNAVAILABLE_REPLY)
                result = {
                    "content": state_info["reason"],
                    "tool_calls": [],
                    "finish_reason": "stop",
                    "fallback_courtesy": True,
                    "status": state_info["status"],
                    "reason": state_info["reason"],
                }
            else:
                state_info = format_ai_health_state(
                    "INDISPONIBLE",
                    "Mode indisponible : API primaire en erreur.",
                )
                result = {
                    "content": "Le moteur IA est indisponible pour le moment.",
                    "tool_calls": [],
                    "finish_reason": "error",
                    "status": state_info["status"],
                    "reason": state_info["reason"],
                }
        self._record_provider_result("gemini", result)
        return result

    def _chat_gemini(self, messages, tools=None, temperature=0.6):
        """Translate model/tool turns without letting the SDK execute local actions."""
        state_unavailable = format_ai_health_state(
            "INDISPONIBLE",
            "Mode indisponible : API primaire en erreur.",
        )
        error = {
            "content": "Le moteur IA est indisponible pour le moment.",
            "tool_calls": [],
            "finish_reason": "error",
            "status": state_unavailable["status"],
            "reason": state_unavailable["reason"],
        }
        try:
            if self.gemini_client is None or genai_types is None:
                raise RuntimeError(
                    "Client Gemini non initialisé : vérifier GEMINI_API_KEY et google-genai"
                    + (f" ({_GENAI_IMPORT_ERROR})" if _GENAI_IMPORT_ERROR else "")
                )
            messages = self._chat_messages(messages)
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
                elif role == "assistant" and message.get("_gemini_parts"):
                    parts = message["_gemini_parts"]
                elif message.get("parts"):
                    parts = message["parts"]
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
                        part = {"function_call": function_call}
                        if self.gemini_model.startswith("gemini-3"):
                            # Google's documented marker for history imported from another model.
                            part["thought_signature"] = b"skip_thought_signature_validator"
                        parts.append(part)
                if not parts:
                    continue
                gemini_role = "model" if role == "assistant" else "user"
                if contents and contents[-1]["role"] == gemini_role:
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": gemini_role, "parts": parts})

            system_instruction = "\n\n".join(instructions) if instructions else None
            generation_options = {
                "temperature": temperature,
                "max_output_tokens": GENERATION_CONFIG["max_tokens"],
                "top_p": GENERATION_CONFIG["top_p"],
                "automatic_function_calling": {"disable": True},
                "http_options": genai_types.HttpOptions(
                    timeout=max(10000, int(self.gemini_timeout * 1000)),
                    retry_options=genai_types.HttpRetryOptions(attempts=1),
                ),
            }
            if self.gemini_model.startswith("gemini-3"):
                generation_options["thinking_config"] = genai_types.ThinkingConfig(thinking_level="minimal")
            if system_instruction:
                generation_options["system_instruction"] = system_instruction
            if tools:
                declarations = []
                for tool in tools:
                    function = tool.get("function") or tool
                    declarations.append(genai_types.FunctionDeclaration(
                        name=function["name"], description=function.get("description", ""),
                        parameters_json_schema=function.get("parameters") or {"type": "object", "properties": {}},
                    ))
                generation_options["tools"] = [genai_types.Tool(function_declarations=declarations)]
            generation_config = genai_types.GenerateContentConfig(**generation_options)
            contents = [genai_types.Content(
                role=content["role"], parts=[genai_types.Part.model_validate(part) for part in content["parts"]],
            ) for content in contents]
            active_model = self.gemini_model
            current_config = generation_config
            response = None

            def _call_gemini_generate(model_name: str, config_obj: Any) -> Any:
                return self.gemini_client.models.generate_content(
                    model=model_name, contents=contents, config=config_obj,
                )

            def _rescue_with_groq_level2() -> Dict[str, Any]:
                """Cascade unidirectionnelle Niveau 2 (Groq Llama 3.3 70B) -> Niveau 3 (Arrêt propre dégradé)."""
                try:
                    last_u = ""
                    for m in reversed(messages):
                        if m.get("role") == "user" and m.get("content"):
                            last_u = str(m["content"])
                            break
                    if last_u:
                        groq_res = voice_fast_llm_reply(
                            last_u,
                            system_prompt=system_instruction,
                            _allow_gemini_fallback=False,
                            _failed_providers={"gemini"},
                            _deadline=time.monotonic() + 4.5,
                        )
                        if (
                            groq_res
                            and groq_res != GEMINI_COURTESY_OFFLINE_REPLY
                            and not groq_res.startswith("Mode dégradé")
                            and not groq_res.startswith("[DEGRADE]")
                            and not groq_res.startswith("[INDISPONIBLE]")
                        ):
                            logger.info("⚡ [GROQ LLM RESCUE (Niveau 2)]: Secours immédiat suite à panne Gemini")
                            partial_state = format_ai_health_state(
                                "PARTIEL",
                                "Mode partiel : API primaire indisponible, réponse assurée par le secours Groq Llama 3.3 70B.",
                            )
                            return {
                                "content": groq_res,
                                "tool_calls": [],
                                "finish_reason": "stop",
                                "fallback_courtesy": False,
                                "status": partial_state["status"],
                                "reason": partial_state["reason"],
                            }
                except Exception as groq_exc:
                    logger.warning("Échec du secours Niveau 2 Groq: %s", groq_exc)
                degraded_state = format_ai_health_state("DEGRADE", DEGRADED_UNAVAILABLE_REPLY)
                return {
                    "content": degraded_state["reason"],
                    "tool_calls": [],
                    "finish_reason": "stop",
                    "fallback_courtesy": True,
                    "status": degraded_state["status"],
                    "reason": degraded_state["reason"],
                }

            try:
                response = _call_gemini_generate(active_model, current_config)
            except Exception as exc:
                if _is_gemini_503_or_server_error(exc):
                    logger.warning(
                        "Gemini 503 / ServerError détecté sur le modèle %s: %s. Pause de 1.0s avant réessai...",
                        active_model,
                        exc,
                    )
                    time.sleep(1.0)
                    try:
                        response = _call_gemini_generate(active_model, current_config)
                    except Exception as retry_exc:
                        if _is_gemini_503_or_server_error(retry_exc):
                            logger.warning(
                                "Échec du réessai 503 sur %s: %s. Basculement sur le modèle de secours %s...",
                                active_model,
                                retry_exc,
                                GEMINI_BACKUP_MODEL,
                            )
                            if active_model != GEMINI_BACKUP_MODEL:
                                backup_config = current_config.model_copy(update={"thinking_config": None})
                                try:
                                    response = _call_gemini_generate(GEMINI_BACKUP_MODEL, backup_config)
                                    active_model = GEMINI_BACKUP_MODEL
                                except Exception as backup_exc:
                                    if _is_gemini_503_or_server_error(backup_exc) or "404" in str(backup_exc) or "not_found" in str(backup_exc).lower():
                                        logger.warning(
                                            "Échec du serveur Gemini (%s puis %s): %s. Bascule unidirectionnelle Niveau 2 Groq...",
                                            active_model,
                                            GEMINI_BACKUP_MODEL,
                                            backup_exc,
                                        )
                                        return _rescue_with_groq_level2()
                                    raise
                            else:
                                logger.warning(
                                    "Échec persistant Gemini 503 sur %s. Bascule unidirectionnelle Niveau 2 Groq...",
                                    active_model,
                                )
                                return _rescue_with_groq_level2()
                        else:
                            raise
                else:
                    detail = str(exc).lower()
                    bad_tool_schema = getattr(exc, "code", None) == 400 and any(
                        marker in detail for marker in ("function_declarations", "function declaration", "json schema", "tools[")
                    ) and "deadline" not in detail
                    has_tool_history = any(message.get("role") == "tool" or message.get("tool_calls")
                                           or message.get("_gemini_parts") for message in messages)
                    if not tools or not bad_tool_schema or has_tool_history:
                        raise
                    logger.warning("Échec appel Gemini: %s", exc, exc_info=True)
                    # Retry only schema rejection, before any action; never retry auth/quota errors.
                    current_config = current_config.model_copy(update={
                        "tools": None, "tool_config": None,
                        "system_instruction": "\n\n".join(instructions + [
                            "Les outils sont indisponibles pour cet appel. Réponds directement avec les informations "
                            "disponibles ; ne prétends pas exécuter une action et n'invente aucune mesure."
                        ]),
                    })
                    response = _call_gemini_generate(active_model, current_config)
            candidates = response.candidates or []
            if not candidates or not candidates[0].content:
                raise RuntimeError(f"Gemini returned no content (prompt_feedback={response.prompt_feedback})")
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
                raise RuntimeError(f"Gemini returned no text or tool calls (finish_reason={candidates[0].finish_reason})")
            if not tool_calls and content:
                tool_calls = self._extract_text_tool_calls(content)
            ok_state = format_ai_health_state("OK", "Fournisseur primaire Gemini opérationnel.")
            result = {
                "content": content,
                "tool_calls": tool_calls,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "status": ok_state["status"],
                "reason": ok_state["reason"],
            }
            if tool_calls:
                # Preserve opaque Gemini 3 signatures exactly across tool observations.
                parts = candidates[0].content.parts or []
                serialized = [part.model_dump(mode="json", exclude_none=True) for part in parts
                              if callable(getattr(part, "model_dump", None))]
                if serialized and any(getattr(part, "function_call", None) for part in parts):
                    result["_gemini_parts"] = serialized
            return result
        except Exception as exc:
            logger.warning("Échec appel Gemini: %s", exc, exc_info=True)
            if _is_gemini_503_or_server_error(exc) or isinstance(exc, TimeoutError):
                degraded_state = format_ai_health_state("DEGRADE", DEGRADED_UNAVAILABLE_REPLY)
                return {
                    "content": degraded_state["reason"],
                    "tool_calls": [],
                    "finish_reason": "stop",
                    "fallback_courtesy": True,
                    "status": degraded_state["status"],
                    "reason": degraded_state["reason"],
                }
            return error

    def _normalize_chat_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize an injected plugin message envelope without network access."""
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

    def _normalize_ollama_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Deprecated payload-only alias retained for external plugins."""
        return self._normalize_chat_payload(data)

    def _normalize_openai_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Payload-only adapter for plugins; never makes a request."""
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


def get_cpu_temperature_celsius() -> float | None:
    """Récupère la température CPU réelle en degrés Celsius via psutil.sensors_temperatures()."""
    try:
        import psutil

        if not hasattr(psutil, "sensors_temperatures"):
            return None
        temps = psutil.sensors_temperatures()
        if not temps:
            return None
        for key in ("coretemp", "acpitz", "cpu_thermal", "k10temp", "zenpower", "soc_thermal"):
            if key in temps and temps[key]:
                for entry in temps[key]:
                    val = getattr(entry, "current", None)
                    if val is not None and val > 0:
                        return round(float(val), 1)
        for entries in temps.values():
            for entry in entries:
                val = getattr(entry, "current", None)
                if val is not None and val > 0:
                    return round(float(val), 1)
    except Exception:
        pass
    return None


def build_live_system_context(runtime: Any = None) -> str:
    """Construit un bloc de contexte télémétrique vivant du Dell Precision M4800."""
    now = datetime.now()
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    mois = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    date_fr = f"{jours[now.weekday()]} {now.day} {mois[now.month]} {now.year}"
    time_str = now.strftime("%H:%M:%S")

    cpu_pct = 12
    ram_pct = 45
    try:
        import psutil

        cpu_pct = int(psutil.cpu_percent(interval=None))
        ram_pct = int(psutil.virtual_memory().percent)
    except Exception:
        pass

    temp_c = get_cpu_temperature_celsius()
    temp_str = f"{temp_c}°C" if temp_c is not None else "non disponible"

    disk_free_gb = 50.0
    try:
        import shutil

        target_path = "/" if os.name != "nt" else "C:\\"
        usage = shutil.disk_usage(target_path)
        disk_free_gb = round(usage.free / (1024**3), 1)
    except Exception:
        pass

    whatsapp_status = "connecté"
    noc_status = "nominal"
    if runtime:
        try:
            if hasattr(runtime, "whatsapp") and hasattr(runtime.whatsapp, "snapshot"):
                snap = runtime.whatsapp.snapshot()
                whatsapp_status = "connecté" if snap.get("active") or snap.get("ready") else "prêt"
            if hasattr(runtime, "local_status"):
                noc_status = str(runtime.local_status)
        except Exception:
            pass

    # Télémétrie biométrique et visuelle NeuroVision 3D en temps réel
    neurovision_block = ""
    try:
        from vision_engine import VisionEngine
        ve = VisionEngine.get_instance()
        vm = ve.get_metrics()
        nv_active = bool(vm.get("active"))
        nv_present = bool(vm.get("user_present") or vm.get("face_detected"))
        nv_source = vm.get("source") or ("Webcam locale PC de Moctar" if nv_active else "En veille")
        nv_fatigue_score = vm.get("fatigue_score", 0)
        nv_fatigue_lvl = vm.get("fatigue_level", "Normal")
        nv_blinks = vm.get("blink_count", 0)
        nv_blink_rate = vm.get("blink_rate_per_min", 0.0)
        nv_yawns = vm.get("yawn_count", 0)
        nv_yaw = vm.get("head_tilt_yaw", 0.0)
        nv_roll = vm.get("head_roll", 0.0)
        nv_dur = vm.get("session_duration_seconds", 0)
        has_frame = ve.get_latest_jpeg_frame() is not None
        neurovision_block = (
            f"\n\n[SCANNER BIOMÉTRIQUE & CAMÉRA NEUROVISION 3D EN DIRECT]\n"
            f"- Statut capteur NeuroVision 3D : {'ACTIF EN DIRECT' if nv_active else 'EN VEILLE (prêt à activer)'} (Source : {nv_source})\n"
            f"- Présence devant la caméra : {'PRÉSENT (Visage de Moctar verrouillé)' if nv_present else ('Hors champ / En attente' if nv_active else 'Capteur en veille')}\n"
            f"- État cognitif & Fatigue : {nv_fatigue_lvl} (Score de fatigue : {nv_fatigue_score}%)\n"
            f"- Biométrie oculaire & buccale : {nv_blinks} clignements ({nv_blink_rate}/min) | {nv_yawns} bâillement(s)\n"
            f"- Posture 3D de la tête (Digital Twin) : Inclinaison Yaw={nv_yaw}°, Affaissement Roll={nv_roll}°\n"
            f"- Durée de surveillance : {nv_dur // 60}m {nv_dur % 60}s | Flux image caméra reçu : {'OUI' if has_frame else 'NON'}"
        )
    except Exception:
        pass

    return (
        f"[TÉLÉMÉTRIE EN TEMPS RÉEL DU SERVEUR DELL PRECISION M4800]\n"
        f"- Date et heure : {date_fr} à {time_str}\n"
        f"- Charge CPU : {cpu_pct}%\n"
        f"- Température CPU réelle : {temp_str}\n"
        f"- Mémoire vive utilisée : {ram_pct}%\n"
        f"- Espace disque libre : {disk_free_gb} Go\n"
        f"- Pont WhatsApp : {whatsapp_status}\n"
        f"- Runtime NOC : {noc_status}"
        f"{neurovision_block}"
    )


_FAST_VOICE_SESSION_TURNS: dict[str, list[dict[str, str]]] = {}
_GROQ_MODELS_CACHE: tuple[float, list[str]] = (0.0, [])
_DEAD_GROQ_MODELS: set[str] = {
    "llama-3.1-70b-versatile",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
    "qwen-2.5-32b",
}

GROQ_MODELS_PRIORITY: list[str] = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]
DEFAULT_PREFERRED_GROQ_MODELS: list[str] = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]


def mark_groq_model_dead(model_name: str) -> None:
    """Enregistre un modèle Groq ayant renvoyé 400 (decommissioned) ou 404 (not_found) pour ne plus jamais le rappeler."""
    if model_name:
        _DEAD_GROQ_MODELS.add(str(model_name).strip())


def _ensure_moctar_ending(text: str) -> str:
    """Garantit qu'une intervention vocale se termine chirurgicalement par 'Moctar.'"""
    t = (text or "").strip()
    if not t:
        return "À vos ordres, Moctar."
    clean_no_punct = t.rstrip(".!?,;:")
    if clean_no_punct.endswith("Moctar"):
        return clean_no_punct + "."
    return clean_no_punct + ", Moctar."


def get_available_groq_chat_models(api_key: str, force_refresh: bool = False) -> list[str]:
    """Interroge dynamiquement l'API Groq pour lister uniquement les modèles de chat actifs autorisés pour cette clé."""
    global _GROQ_MODELS_CACHE
    now = time.monotonic()
    cached_t, cached_list = _GROQ_MODELS_CACHE
    if not force_refresh and cached_list and (now - cached_t < 1800.0):
        return [m for m in cached_list if m not in _DEAD_GROQ_MODELS]

    candidates = [m for m in DEFAULT_PREFERRED_GROQ_MODELS if m not in _DEAD_GROQ_MODELS]
    env_model = os.environ.get("GROQ_CHAT_MODEL", "").strip()
    if env_model and env_model not in _DEAD_GROQ_MODELS:
        candidates.insert(0, env_model)

    discovered: list[str] = []
    discovery_succeeded = False
    if api_key:
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": "Jarvis-Moctar-Engine/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    discovery_succeeded = True
                    body = json.loads(resp.read().decode("utf-8"))
                    items = body.get("data", [])
                    raw_ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
                    chat_ids = [
                        i for i in raw_ids
                        if i not in _DEAD_GROQ_MODELS
                        and not any(k in i.lower() for k in ("whisper", "guard", "embed", "tts", "audio", "moderation", "vision"))
                    ]
                    APPROVED_FAMILIES = ("llama", "mixtral", "gemma", "qwen", "deepseek")
                    for pref in candidates:
                        if pref in chat_ids and pref not in discovered:
                            discovered.append(pref)
                    for cid in chat_ids:
                        if any(fam in cid.lower() for fam in APPROVED_FAMILIES):
                            if cid not in discovered:
                                discovered.append(cid)
                    if discovered:
                        logger.info("⚡ [GROQ DISCOVERY]: %d modèles de chat actifs autorisés (%s)", len(discovered), discovered[:3])
                    else:
                        logger.warning("⚡ [GROQ DISCOVERY]: Aucun modèle de chat actif sur cette clé. Utilisation exclusive de Gemini.")
        except Exception as exc:
            logger.debug("Découverte dynamique des modèles Groq indisponible (%s).", exc)

    final_list: list[str] = []
    if discovery_succeeded:
        # Utiliser strictement les modèles réellement autorisés par l'API pour cette clé
        for pref in GROQ_MODELS_PRIORITY:
            if pref in discovered and pref not in final_list and pref not in _DEAD_GROQ_MODELS:
                final_list.append(pref)
        for m in discovered:
            if m and m not in final_list and m not in _DEAD_GROQ_MODELS:
                final_list.append(m)
    else:
        for pref in candidates:
            if pref not in final_list and pref not in _DEAD_GROQ_MODELS:
                final_list.append(pref)
    _GROQ_MODELS_CACHE = (now, final_list)
    return final_list


def voice_fast_llm_reply(
    prompt: str,
    system_prompt: str | None = None,
    runtime: Any = None,
    session_id: str | None = None,
    history: list[dict[str, str]] | None = None,
    *,
    _allow_gemini_fallback: bool = False,
    _failed_providers: set[str] | None = None,
    _deadline: float | None = None,
) -> str:
    """Cerveau vocal ultra-rapide (< 200 ms) via Groq Llama 3.3 70B (Niveau 2 de la cascade unidirectionnelle).

    Chaîne de secours strictement unidirectionnelle :
    - Niveau 1 : Fournisseur primaire (Gemini) -> appelle ce Niveau 2 en secours.
    - Niveau 2 : Secours rapide (Groq Llama 3.3 70B).
    - Niveau 3 : Arrêt propre et réponse dégradée explicite (sans reboucler ni réinstancier AIBrain).
    """
    request_deadline = _deadline if _deadline is not None else (time.monotonic() + 6.5)
    failed_providers: set[str] = set(_failed_providers or set())

    groq_api_key = (
        os.environ.get("GROQ_API_KEY", "")
        or os.environ.get("NOC_GROQ_API_KEY", "")
        or getattr(getattr(runtime, "settings", None), "groq_api_key", "")
        or getattr(config, "GROQ_API_KEY", "")
        or ""
    ).strip()
    telemetry = build_live_system_context(runtime)

    # 1. Extraction de la mémoire multi-tours (3 à 4 derniers échanges)
    sess_key = session_id or getattr(getattr(runtime, "jarvis", None), "active_session_id", "session_voice")
    turn_messages: list[dict[str, str]] = []
    if history is not None:
        for item in history:
            role = item.get("role")
            content = str(item.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                turn_messages.append({"role": role, "content": content})
    elif runtime and getattr(runtime, "jarvis", None) and getattr(runtime.jarvis, "memory", None):
        try:
            raw_history = runtime.jarvis.memory.get_context_window(sess_key, max_turns=4)
            for item in raw_history:
                role = item.get("role")
                content = str(item.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    if role == "user" and content == prompt:
                        continue
                    turn_messages.append({"role": role, "content": content})
            turn_messages = turn_messages[-6:]
        except Exception as hist_err:
            logger.debug("Historique SQLite indisponible: %s", hist_err)

    if not turn_messages and sess_key in _FAST_VOICE_SESSION_TURNS:
        turn_messages = _FAST_VOICE_SESSION_TURNS[sess_key][-6:]

    # 2. Récupération des préférences ou faits mémorisés de Moctar
    memory_facts = ""
    if runtime and getattr(runtime, "jarvis", None) and getattr(runtime.jarvis, "memory", None):
        try:
            memory_facts = runtime.jarvis.memory.get_system_context_snippet(prompt)
        except Exception:
            memory_facts = ""

    # 3. Enrichissement Web en direct pour les questions factuelles / savoir (si budget temps suffisant)
    web_context = ""
    if (request_deadline - time.monotonic()) > 2.5:
        try:
            import web_search_service
            if web_search_service.is_live_web_query(prompt):
                web_context = web_search_service.fetch_live_web_context(prompt)
        except Exception:
            web_context = ""

    base_prompt = system_prompt or (
        "Tu es M.O.C.T.A.R, l'intelligence artificielle cybernétique et copilote d'ingénierie d'élite de Moctar. "
        "Ton style allie la précision d'un centre de commandement futuriste (NOC) à la chaleur, la bienveillance et la complicité d'un vrai ami. "
        "Tu t'adresses à lui avec un tutoiement naturel et respectueux. "
        "Tes réponses orales sont ultra-réactives, posées, vivantes et concises (1 à 3 phrases maximum). "
        "Pas de jargon théâtral de bande dessinée, mais une prestance technologique moderne, directe et bienveillante.\n\n"
        + JARVIS_CORE_SYSTEM_PROMPT
    )
    if JARVIS_CORE_SYSTEM_PROMPT not in base_prompt:
        base_prompt = f"{base_prompt}\n\n{JARVIS_CORE_SYSTEM_PROMPT}"

    prompt_sections = [base_prompt]
    if memory_facts:
        prompt_sections.append(memory_facts)
    if web_context:
        prompt_sections.append(web_context)
    prompt_sections.append(telemetry)
    full_system_prompt = "\n\n".join(str(s) for s in prompt_sections if s)

    # Construction ordonnée des messages avec historique multi-tours
    messages: list[dict[str, str]] = [{"role": "system", "content": full_system_prompt}]
    messages.extend(turn_messages)
    messages.append({"role": "user", "content": prompt})

    def _save_session_turn(assistant_reply: str) -> None:
        if sess_key not in _FAST_VOICE_SESSION_TURNS:
            _FAST_VOICE_SESSION_TURNS[sess_key] = []
        _FAST_VOICE_SESSION_TURNS[sess_key].append({"role": "user", "content": prompt})
        _FAST_VOICE_SESSION_TURNS[sess_key].append({"role": "assistant", "content": assistant_reply})
        _FAST_VOICE_SESSION_TURNS[sess_key] = _FAST_VOICE_SESSION_TURNS[sess_key][-8:]

    if groq_api_key and "groq" not in failed_providers:
        discovered_models = get_available_groq_chat_models(groq_api_key)
        models_to_try = [m for m in discovered_models if m and m not in _DEAD_GROQ_MODELS]
        if "llama-3.3-70b-versatile" in models_to_try:
            models_to_try.remove("llama-3.3-70b-versatile")
            models_to_try.insert(0, "llama-3.3-70b-versatile")
        failed_404_models: set[str] = set()
        refreshed_on_404 = False

        def _try_groq_candidate(cand: str, call_timeout: float) -> str | None:
            if cand in _DEAD_GROQ_MODELS:
                return None
            # 1. SDK Groq officiel (si le package groq est installé)
            try:
                from groq import Groq  # type: ignore[import-not-found]

                client = Groq(api_key=groq_api_key, timeout=call_timeout, max_retries=0)
                start_t = time.perf_counter()
                completion = client.chat.completions.create(
                    model=cand,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=250,
                )
                reply = str(completion.choices[0].message.content or "").strip()
                duration_ms = int((time.perf_counter() - start_t) * 1000)
                if reply:
                    logger.info("⚡ [GROQ SDK %s] (%dms): '%s'", cand.upper(), duration_ms, reply)
                    return reply
            except ImportError:
                pass
            except Exception as sdk_err:
                err_msg = str(sdk_err).lower()
                if (
                    "404" in err_msg
                    or "model_not_found" in err_msg
                    or "model_decommissioned" in err_msg
                    or "decommissioned" in err_msg
                    or type(sdk_err).__name__ in ("NotFoundError", "BadRequestError")
                ):
                    failed_404_models.add(cand)
                    mark_groq_model_dead(cand)
                    logger.warning("Groq SDK modèle %s indisponible/décommissionné (blacklisté)", cand)
                    return None
                if "429" in err_msg or "rate_limit" in err_msg or type(sdk_err).__name__ == "RateLimitError":
                    logger.warning("Groq SDK 429 rate_limit sur %s — passage immédiat au modèle suivant", cand)
                    return None
                logger.warning("Groq SDK modèle %s a échoué: %s", cand, sdk_err)
                return None

            # 2. Requête HTTP directe vers l'API Groq (uniquement si le SDK n'est pas installé)
            try:
                import requests

                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {groq_api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": cand,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 250,
                }
                resp = requests.post(url, headers=headers, json=payload, timeout=call_timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    reply = str(data["choices"][0]["message"]["content"] or "").strip()
                    if reply:
                        logger.info("⚡ [GROQ HTTP %s]: '%s'", cand.upper(), reply)
                        return reply
                elif resp.status_code in (400, 404) or "model_not_found" in resp.text.lower() or "decommissioned" in resp.text.lower():
                    failed_404_models.add(cand)
                    mark_groq_model_dead(cand)
                    logger.warning("Groq Chat API HTTP %s pour %s (blacklisté)", resp.status_code, cand)
                else:
                    logger.warning("Groq Chat API HTTP %s (%s): %s", resp.status_code, cand, resp.text)
            except Exception as exc:
                logger.warning("Groq HTTP a échoué (%s)", exc)
            return None

        idx = 0
        while idx < len(models_to_try):
            remaining = request_deadline - time.monotonic()
            if remaining <= 0.2:
                logger.warning("Budget de temps global expiré avant l'appel Groq (%s). Abandon propre.", models_to_try[idx])
                break
            cand = models_to_try[idx]
            idx += 1
            if cand in failed_404_models:
                continue
            call_budget = min(3.5, max(0.5, remaining))
            try:
                rep = _with_inference_deadline(
                    lambda c=cand, b=call_budget: _try_groq_candidate(c, b),
                    timeout=call_budget,
                )
            except TimeoutError:
                logger.warning("Timeout strict sur Groq (%s) après %.1fs — résultat tardif ignoré.", cand, call_budget)
                break
            if rep:
                if (request_deadline - time.monotonic()) < -0.1:
                    logger.warning("Réponse Groq reçue hors délai ignorée pour éviter toute synthèse vocale différée.")
                    break
                final_rep = _ensure_moctar_ending(rep)
                _save_session_turn(final_rep)
                return final_rep

            # Découverte dynamique sur premier échec 404 (uniquement si le budget temps le permet)
            if failed_404_models and not refreshed_on_404 and (request_deadline - time.monotonic()) > 1.5:
                refreshed_on_404 = True
                logger.info("⚡ [GROQ AUTO-DISCOVERY]: Échec 404 sur %s, interrogation dynamique de GET /v1/models...", cand)
                new_discovered = get_available_groq_chat_models(groq_api_key, force_refresh=True)
                for nm in new_discovered:
                    if nm not in models_to_try and nm not in failed_404_models:
                        models_to_try.append(nm)

        failed_providers.add("groq")

    # Niveau 3 : Arrêt propre et réponse dégradée explicite.
    # Interdiction absolue de réinstancier AIBrain() ou de reboucler sur un fournisseur ayant échoué.
    degraded_state = format_ai_health_state("DEGRADE", DEGRADED_UNAVAILABLE_REPLY)
    logger.error(
        "Cascade IA interrompue au Niveau 3 [%s] (fournisseurs en échec=%s): %s",
        degraded_state["status"],
        sorted(failed_providers),
        degraded_state["reason"],
    )
    return degraded_state["reason"]

