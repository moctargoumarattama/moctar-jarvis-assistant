import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

import ai_brain
import config
from agent_core import AgentCore
from actions import (
    energy_actions,
    iot_actions,
    music_actions,
    personal_actions,
    project_actions,
    system_actions,
    web_actions,
)
from assistant_memory import AssistantMemory, SessionMemory, UserMemoryStore
from assistant_plugins import build_default_registry
from assistant_security import SecurityPolicy
from intent_engine import detect_intent
from scheduler import scheduler_store as sched_store
from scheduler import scheduler_core as sched_core


logger = logging.getLogger("jarvis")
LOCAL_RECOMMENDATIONS = [
    "Ninho - Lettre a une femme",
    "Aya Nakamura - Djadja",
    "Wizkid - Essence",
    "Burna Boy - Last Last",
]
MUSIC_FOLLOWUP_ALIASES = {
    "dams": "Damso",
    "damse": "Damso",
}
INCOMPLETE_LOCAL_COMMANDS = {
    "active": {
        "prefix": "ouvre",
        "prompt": "Que veux-tu activer ?",
        "accepted": {"open_app", "open_folder", "open_project", "open_site"},
    },
    "cherche": {
        "prefix": "cherche sur google",
        "prompt": "Que veux-tu chercher sur Google ?",
        "accepted": {"search_google"},
    },
    "close": {
        "prefix": "ferme",
        "prompt": "Quelle application veux-tu fermer ?",
        "accepted": {"close_app"},
    },
    "demarre": {
        "prefix": "ouvre",
        "prompt": "Que veux-tu demarrer ?",
        "accepted": {"open_app", "open_folder", "open_project", "open_site"},
    },
    "ferme": {
        "prefix": "ferme",
        "prompt": "Quelle application veux-tu fermer ?",
        "accepted": {"close_app"},
    },
    "lance": {
        "prefix": "ouvre",
        "prompt": "Que veux-tu lancer ?",
        "accepted": {"open_app", "open_folder", "open_project", "open_site"},
    },
    "open": {
        "prefix": "ouvre",
        "prompt": "Que veux-tu ouvrir ?",
        "accepted": {"open_app", "open_folder", "open_project", "open_site"},
    },
    "ouvre": {
        "prefix": "ouvre",
        "prompt": "Que veux-tu ouvrir ?",
        "accepted": {"open_app", "open_folder", "open_project", "open_site"},
    },
    "ouvrir": {
        "prefix": "ouvre",
        "prompt": "Que veux-tu ouvrir ?",
        "accepted": {"open_app", "open_folder", "open_project", "open_site"},
    },
    "recherche": {
        "prefix": "cherche sur google",
        "prompt": "Que veux-tu chercher sur Google ?",
        "accepted": {"search_google"},
    },
}


class AssistantCore:
    def __init__(self):
        config.ensure_runtime_directories()
        self.project_assistant = project_actions.ProjectAssistant()
        self.personal_assistant = personal_actions.PersonalAssistant()
        self.security = SecurityPolicy()
        self.session_memory = SessionMemory()
        self.user_memory = UserMemoryStore()
        self.local_brain = ai_brain.LocalBrain()
        self.registry = build_default_registry(self)
        self.memory = AssistantMemory()
        self.agent = AgentCore(self.registry, self.security, memory=self.memory)
        self.current_session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        self.pending_confirmation = None
        self.pending_music_request = False
        self.pending_followup = None
        self.music_playing = False
        self.music_paused_for_command = False
        self._announced_scheduler_task_ids = set()

    @staticmethod
    def _format_user_facing_response(response):
        if isinstance(response, str) and response in {
            "__STOP__", "__OPEN_SCHEDULER__", "__OPEN_HISTORY__",
            "STOP", "OPEN_SCHEDULER", "OPEN_HISTORY"
        }:
            return response

        if hasattr(response, "response"):
            response = getattr(response, "response")
        elif hasattr(response, "message"):
            response = getattr(response, "message")
        elif isinstance(response, dict):
            response = response.get("message") or response.get("response") or str(response)

        text = str(response or "").strip()
        if not text:
            return "Je n'ai pas de reponse pour le moment."
        return text

    def recommend_music(self, mood="musique populaire du moment"):
        preferred = self.user_memory.get_preference("favorite_music")
        if preferred:
            return preferred
        # A deterministic fallback is less surprising than Python's salted hash.
        return LOCAL_RECOMMENDATIONS[0]

    def poll_background_messages(self):
        messages = self.personal_assistant.poll_due_reminders()
        # Also surface scheduler tasks that need confirmation
        pending = sched_core.get_pending_tasks()
        for task in pending:
            platform = task["platform"].capitalize()
            messages.append(
                f"⏳ Tâche planifiée prête : [{platform}] {task['title']} — "
                "Confirmez dans le Planificateur."
            )
        return messages

    @staticmethod
    def _format_number(value, decimals=2):
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        formatted = f"{value:.{decimals}f}"
        return formatted.rstrip("0").rstrip(".")

    def poll_background_messages(self):
        messages = list(self.personal_assistant.poll_due_reminders())
        pending = sched_core.get_pending_tasks()
        pending_ids = {task["id"] for task in pending}
        self._announced_scheduler_task_ids.intersection_update(pending_ids)
        for task in pending:
            if task["id"] in self._announced_scheduler_task_ids:
                continue
            platform = task["platform"].capitalize()
            messages.append(
                f"Une tache planifiee est prete : [{platform}] {task['title']}. "
                "Confirmez dans le planificateur."
            )
            self._announced_scheduler_task_ids.add(task["id"])
        return messages

    def _missing_energy_data_response(self, intro):
        return f"{intro} {energy_actions.describe_formulae()}"

    def _handle_energy_intent(self, intent, target, slots):
        if intent == "energy_consumption":
            required = ("quantity", "power_watts", "duration_hours")
            if any(slots.get(key) is None for key in required):
                return self._missing_energy_data_response(
                    "Pour calculer la consommation, j'ai besoin de la quantite, de la puissance en watts et de la duree en heures."
                )

            result = energy_actions.calculate_consumption(
                quantity=slots["quantity"],
                power_watts=slots["power_watts"],
                duration_hours=slots["duration_hours"],
            )
            return (
                f"Consommation estimee : {self._format_number(result['energy_kwh'])} kWh "
                f"pour {self._format_number(result['total_power_watts'])} W installes sur "
                f"{self._format_number(result['duration_hours'])} h."
            )

        if intent == "energy_cost":
            required = ("energy_kwh", "price_per_kwh")
            if any(slots.get(key) is None for key in required):
                return self._missing_energy_data_response(
                    "Pour calculer le cout, j'ai besoin de l'energie en kWh et du prix unitaire du kWh."
                )

            result = energy_actions.calculate_energy_cost(
                energy_kwh=slots["energy_kwh"],
                price_per_kwh=slots["price_per_kwh"],
            )
            return (
                f"Cout estime : {self._format_number(result['total_cost'])} "
                f"pour {self._format_number(result['energy_kwh'])} kWh a "
                f"{self._format_number(result['price_per_kwh'])} par kWh."
            )

        if intent == "solar_sizing":
            if slots.get("daily_energy_kwh") is None:
                return self._missing_energy_data_response(
                    "Pour dimensionner le solaire, j'ai besoin au minimum de l'energie journaliere en kWh."
                )

            result = energy_actions.calculate_solar_sizing(
                daily_energy_kwh=slots["daily_energy_kwh"],
                sun_hours=slots.get("sun_hours", 5),
                system_efficiency=slots.get("system_efficiency", 0.8),
            )
            return (
                f"Puissance solaire recommandee : {self._format_number(result['recommended_pv_watts'])} Wc "
                f"pour {self._format_number(result['daily_energy_kwh'])} kWh par jour."
            )

        if intent == "battery_sizing":
            if slots.get("energy_kwh") is None:
                return self._missing_energy_data_response(
                    "Pour dimensionner la batterie, j'ai besoin de l'energie en kWh."
                )

            result = energy_actions.calculate_battery_sizing(
                energy_kwh=slots["energy_kwh"],
                system_voltage=slots.get("system_voltage", 12),
                depth_of_discharge=slots.get("depth_of_discharge", 0.5),
                autonomy_days=slots.get("autonomy_days", 1),
            )
            return (
                f"Capacite batterie recommandee : {self._format_number(result['recommended_capacity_ah'])} Ah "
                f"en {self._format_number(result['system_voltage'])} V avec "
                f"{self._format_number(result['autonomy_days'])} jour(s) d'autonomie."
            )

        if intent == "inverter_sizing":
            if slots.get("total_power_watts") is None:
                return self._missing_energy_data_response(
                    "Pour dimensionner l'onduleur, j'ai besoin de la puissance totale en watts."
                )

            result = energy_actions.calculate_inverter_sizing(
                total_power_watts=slots["total_power_watts"],
                safety_margin=slots.get("safety_margin", 0.25),
            )
            return (
                f"Onduleur recommande : {self._format_number(result['recommended_inverter_watts'])} W "
                f"avec une marge de {self._format_number(result['safety_margin'] * 100)}%."
            )

        if intent == "energy_audit_template":
            topic = target or "site standard"
            return energy_actions.generate_audit_template(topic)

        return "Je n'ai pas compris cette demande energie."

    def _remember(self, intent, target, response):
        self.session_memory.add_turn(intent, target, response)
        self.user_memory.record_interaction(intent, target, response)
        if hasattr(self, "memory") and self.memory:
            try:
                user_content = (target or "").strip() or intent
                self.memory.add_message(self.current_session_id, role="user", content=user_content)
                self.memory.add_message(self.current_session_id, role="assistant", content=str(response or ""))
            except Exception as exc:
                logger.warning("Erreur enregistrement interaction en mémoire SQLite : %s", exc)

    def get_active_session_id(self) -> str:
        """Renvoie l'identifiant de la session actuellement active."""
        return self.current_session_id

    def set_active_session(self, session_id: str) -> bool:
        """Bascule la session active et recharge son contexte dans la mémoire court terme."""
        if not hasattr(self, "memory") or not self.memory:
            return False
        clean_id = (session_id or "").strip()
        if not clean_id:
            return False

        messages = self.memory.get_session_messages(clean_id)
        self.current_session_id = clean_id
        self.session_memory.clear()
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role in {"user", "assistant"} and content:
                self.session_memory.add(role=role, content=content)
        logger.info("Session active basculée sur '%s' avec %d messages rechargés.", clean_id, len(messages))
        return True

    def start_new_session(self, title: Optional[str] = None) -> str:
        """Démarre une nouvelle session de conversation avec un identifiant unique."""
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_id = f"session_{now_str}_{uuid4().hex[:6]}"
        self.current_session_id = new_id
        self.session_memory.clear()
        if hasattr(self, "memory") and self.memory:
            default_title = title or f"Nouvelle session ({datetime.now().strftime('%d/%m %H:%M')})"
            self.memory.create_or_update_session(new_id, title=default_title)
        logger.info("Nouvelle session de conversation démarrée : %s", new_id)
        return new_id

    def expects_followup(self):
        return bool(self.pending_music_request or self.pending_followup)

    def reset_session(self):
        """Clear transient conversation state without deleting persisted user data."""
        self.pending_confirmation = None
        self.pending_music_request = False
        self.pending_followup = None
        self.session_memory.clear()
        self.start_new_session()

    def _begin_pending_followup(self, intent_data):
        raw = intent_data.get("raw") or intent_data.get("target", "")
        normalized = ai_brain.normalize_prompt(raw)
        specification = INCOMPLETE_LOCAL_COMMANDS.get(normalized)
        if specification is None:
            if intent_data.get("intent") == "search_google" and not intent_data.get("target", "").strip():
                specification = INCOMPLETE_LOCAL_COMMANDS["cherche"]
            else:
                return None

        self.pending_followup = {
            "prefix": specification["prefix"],
            "prompt": specification["prompt"],
            "accepted": set(specification["accepted"]),
        }
        return specification["prompt"]

    def _resolve_pending_followup(self, intent_data):
        pending = self.pending_followup
        if not pending:
            return intent_data, None

        intent = intent_data.get("intent", "")
        target = (intent_data.get("target", "") or "").strip()
        if intent in {"confirm_no", "stop"}:
            self.pending_followup = None
            return intent_data, None

        if intent != "chat_fallback":
            self.pending_followup = None
            return intent_data, None

        answer = (intent_data.get("raw") or target).strip()
        if not answer:
            return intent_data, pending["prompt"]

        resolved = detect_intent(f"{pending['prefix']} {answer}")
        if resolved.get("intent") in pending["accepted"] and resolved.get("target", "").strip():
            self.pending_followup = None
            return resolved, None

        return (
            intent_data,
            f"Je n'ai pas reconnu la cible. {pending['prompt']}",
        )

    def _resolve_confirmation(self, intent):
        if intent == "confirm_no":
            pending = self.pending_confirmation
            self.pending_confirmation = None
            if pending and pending.get("agent_pending") is not None:
                agent_result = self.agent.resume(
                    pending["agent_pending"],
                    confirmed=False,
                )
                return {
                    "intent": "confirm_no",
                    "target": pending.get("target", ""),
                    "response": agent_result.response,
                }
            return {
                "intent": "confirm_no",
                "target": "",
                "response": "Action annulee.",
            }
        if intent == "confirm_yes":
            if not self.pending_confirmation:
                return {
                    "intent": "confirm_yes",
                    "target": "",
                    "response": "Aucune action en attente de confirmation.",
                }
            intent_data = self.pending_confirmation
            self.pending_confirmation = None
            if intent_data.get("agent_pending") is not None:
                agent_result = self.agent.resume(
                    intent_data["agent_pending"],
                    confirmed=True,
                    fallback=lambda: "L'action a ete confirmee, mais Ollama est indisponible pour terminer la reponse.",
                )
                if agent_result.pending_action is not None:
                    self.pending_confirmation = self._agent_confirmation_payload(agent_result.pending_action)
                return {
                    "intent": intent_data["intent"],
                    "target": intent_data.get("target", ""),
                    "response": agent_result.response,
                }
            return {
                "intent": intent_data["intent"],
                "target": intent_data.get("target", ""),
                "response": self._execute_intent(intent_data, skip_confirmation=True),
            }
        return None

    def _execute_intent(self, intent_data, skip_confirmation=False):
        intent = intent_data["intent"]
        route = self.registry.get(intent)
        if route is None:
            return self._handle_local_chat_fallback(intent_data)

        if not self.security.is_domain_allowed(route.domain):
            return f"Permission refusee pour le domaine {route.domain}."

        if not skip_confirmation and self.security.requires_confirmation(intent):
            self.pending_confirmation = intent_data
            return self.security.confirmation_prompt(intent)

        return route.handler(intent_data)

    def handle_intent(self, intent_data):
        intent = intent_data["intent"]
        target = intent_data.get("target", "")

        logger.info("Intent detected: %s", intent_data)

        if intent == "empty":
            response = self._format_user_facing_response("Je n'ai rien entendu.")
            self._remember(intent, target, response)
            return response
        if intent == "stop":
            self.pending_followup = None
            return "__STOP__"

        intent_data, followup_response = self._resolve_pending_followup(intent_data)
        intent = intent_data["intent"]
        target = intent_data.get("target", "")
        if followup_response is not None:
            response = self._format_user_facing_response(followup_response)
            self._remember("pending_followup", target, response)
            return response

        followup_prompt = self._begin_pending_followup(intent_data)
        if followup_prompt is not None:
            response = self._format_user_facing_response(followup_prompt)
            self._remember(intent, target, response)
            return response

        if self.pending_music_request and intent == "chat_fallback" and target.strip():
            music_target = re.sub(
                r"^(?:je veux|je voudrais|mets|met|joue|ecoute)(?:\s+(?:la )?musique)?\s+",
                "",
                target.strip(),
            )
            music_target = re.sub(r"^(?:musique|chanson|son)\s+", "", music_target).strip()
            music_target = MUSIC_FOLLOWUP_ALIASES.get(
                ai_brain.normalize_prompt(music_target),
                music_target,
            )
            intent_data = {"intent": "play_music", "target": music_target or target.strip(), "slots": {}}
            intent = "play_music"
            target = intent_data["target"]

        confirmation_result = self._resolve_confirmation(intent)
        if confirmation_result is not None:
            confirmation_result["response"] = self._format_user_facing_response(confirmation_result["response"])
            self._remember(
                confirmation_result["intent"],
                confirmation_result.get("target", ""),
                confirmation_result["response"],
            )
            return confirmation_result["response"]

        if self._should_use_agent(intent_data):
            response = self._format_user_facing_response(self._handle_agent_request(intent_data))
        else:
            response = self._format_user_facing_response(self._execute_intent(intent_data))
        self._remember(intent, target, response)
        return response

    def _handle_open_app(self, intent_data):
        return system_actions.open_app(intent_data.get("target", ""))

    def _handle_close_app(self, intent_data):
        return system_actions.close_app(intent_data.get("target", ""))

    def _handle_open_folder(self, intent_data):
        target = intent_data.get("target", "")
        if target in config.PROJECTS:
            return self.project_assistant.open_project(target)
        return system_actions.open_folder(target)

    def _handle_time(self, _intent_data):
        return system_actions.get_time_response()

    def _handle_date(self, _intent_data):
        return system_actions.get_date_response()

    def _handle_battery(self, _intent_data):
        return system_actions.get_battery_response()

    def _handle_system_telemetry(self, _intent_data=None):
        return system_actions.get_system_telemetry()

    def _handle_screenshot(self, _intent_data):
        return system_actions.capture_screenshot()

    def _handle_analyze_screen(self, intent_data):
        question = (
            (intent_data.get("slots") or {}).get("question")
            or intent_data.get("target")
            or "Que vois-tu à l'écran ? Résume et analyse précisément ce qui est affiché."
        )
        return system_actions.analyze_screen(question=str(question))

    def _handle_volume_up(self, _intent_data):
        return system_actions.change_volume("volume_up")

    def _handle_volume_down(self, _intent_data):
        return system_actions.change_volume("volume_down")

    def _handle_mute(self, _intent_data):
        return system_actions.change_volume("mute")

    def _handle_open_site(self, intent_data):
        return web_actions.open_site(intent_data.get("target", ""))

    def _handle_search_google(self, intent_data):
        return web_actions.search_google(intent_data.get("target", ""))

    def _handle_search_youtube(self, intent_data):
        return web_actions.search_youtube(intent_data.get("target", ""))

    def _handle_playlist(self, intent_data):
        return music_actions.open_playlist(intent_data.get("target", ""))

    def _handle_play_music(self, intent_data):
        target = intent_data.get("target", "").strip()
        if not target:
            self.pending_music_request = True
            return "Quelle musique veux-tu que je lance ?"

        self.pending_music_request = False
        response = music_actions.play_music(target)
        if response.startswith("J'ouvre"):
            self.music_playing = True
            self.music_paused_for_command = False
        return response

    def _handle_music_control(self, intent_data):
        action = intent_data["intent"]
        if action == "music_pause" and self.music_paused_for_command:
            self.music_paused_for_command = False
            self.music_playing = False
            return "Je mets la musique en pause."
        response = music_actions.control_music(action)
        if action == "music_pause":
            self.music_playing = False
            self.music_paused_for_command = False
        elif action == "music_resume":
            self.music_playing = True
            self.music_paused_for_command = False
        return response

    def pause_music_for_command(self):
        """Pause only music started by the assistant, never toggle unknown media."""
        if not self.music_playing:
            return False
        response = music_actions.control_music("music_pause")
        if response.startswith("Je mets"):
            self.music_playing = False
            self.music_paused_for_command = True
            return True
        return False

    def resume_music_after_command(self):
        if not self.music_paused_for_command:
            return False
        response = music_actions.control_music("music_resume")
        self.music_paused_for_command = False
        if response.startswith("Je reprends"):
            self.music_playing = True
            return True
        return False

    def _handle_open_project(self, intent_data):
        slots = intent_data.get("slots", {})
        open_in_vscode = slots.get("editor") == "vscode"
        return self.project_assistant.open_project(intent_data.get("target", ""), open_in_vscode=open_in_vscode)

    def _handle_launch_project_server(self, intent_data):
        return self.project_assistant.launch_project_server(intent_data.get("target", "") or None)

    def _handle_git_status(self, intent_data):
        return self.project_assistant.git_status(intent_data.get("target", "") or None)

    def _handle_git_pull(self, _intent_data):
        return self.project_assistant.git_pull()

    def _handle_git_log(self, intent_data):
        return self.project_assistant.git_log(intent_data.get("target", "") or None)

    def _handle_git_commit_prepare(self, _intent_data):
        return self.project_assistant.prepare_commit()

    def _handle_git_push_prepare(self, _intent_data):
        return self.project_assistant.prepare_push()

    def _handle_create_note(self, intent_data):
        return self.personal_assistant.create_note(intent_data.get("target", ""))

    def _handle_add_todo(self, intent_data):
        return self.personal_assistant.add_todo(intent_data.get("target", ""))

    @staticmethod
    def _scheduled_publications_for_day(day="tomorrow", now=None):
        reference = now or datetime.now()
        target_date = (reference + timedelta(days=1)).date() if day == "tomorrow" else reference.date()
        matching = []
        try:
            tasks = sched_store.list_tasks(status="active")
        except Exception:
            logger.exception("Unable to read scheduled tasks for agenda")
            return matching

        for task in tasks:
            frequency = str(task.get("frequency", "daily")).lower()
            if frequency == "weekly":
                if task.get("weekday") is None:
                    continue
                if int(task["weekday"]) != target_date.weekday():
                    continue
            matching.append(task)
        return matching

    def _handle_list_todos(self, intent_data):
        if intent_data.get("slots", {}).get("day") == "tomorrow":
            response = self.personal_assistant.list_todos_for_day("tomorrow")
            publications = self._scheduled_publications_for_day("tomorrow")
            if not publications:
                return response

            publication_lines = [
                f"- Publication : {task.get('send_time', '')} "
                f"[{str(task.get('platform', '')).capitalize()}] {task.get('title', '')}"
                for task in publications[:10]
            ]
            if response.startswith("Tu n'as aucune"):
                return "Pour demain :\n" + "\n".join(publication_lines)
            return response + "\n" + "\n".join(publication_lines)
        return self.personal_assistant.list_todos()

    def _handle_remind_me(self, intent_data):
        slots = intent_data.get("slots", {})
        return self.personal_assistant.schedule_reminder(
            slots.get("message", intent_data.get("target", "")),
            slots.get("when_text", ""),
        )

    def _handle_summarize_file(self, intent_data):
        target = intent_data.get("target", "")
        lower = target.lower()
        if lower.endswith(".csv") or lower.endswith(".xlsx"):
            return self.project_assistant.summarize_tabular_file(target)
        return self.personal_assistant.summarize_file(target)

    def _handle_search_personal(self, intent_data):
        return self.personal_assistant.search(intent_data.get("target", ""))

    def _handle_energy_plugin(self, intent_data):
        return self._handle_energy_intent(
            intent_data["intent"],
            intent_data.get("target", ""),
            intent_data.get("slots", {}),
        )

    def _handle_iot_status(self, _intent_data):
        return iot_actions.get_iot_status()

    def _handle_iot_temperature(self, _intent_data):
        return iot_actions.get_iot_temperature()

    def _handle_iot_relay_on(self, intent_data):
        slots = intent_data.get("slots", {})
        return iot_actions.set_relay_state(slots.get("relay_id", 1), True)

    def _handle_iot_relay_off(self, intent_data):
        slots = intent_data.get("slots", {})
        return iot_actions.set_relay_state(slots.get("relay_id", 1), False)

    @staticmethod
    def _safe_list(value):
        return value if isinstance(value, list) else []

    def _local_brain_context(self):
        personal = self.personal_assistant
        todos = []
        reminders = []

        if hasattr(personal, "get_open_todos"):
            todos = self._safe_list(personal.get_open_todos())
        if hasattr(personal, "get_upcoming_reminders"):
            reminders = self._safe_list(personal.get_upcoming_reminders())

        return {
            "todos": todos,
            "reminders": reminders,
            "history": self.user_memory.get_history(limit=12),
            "preferences": self.user_memory.get_preferences(),
            "insights": self.user_memory.get_insights(),
            "session_turn": self.session_memory.last_turn(),
        }

    @staticmethod
    def _agent_tool_names(intent_data):
        intent = intent_data.get("intent", "")
        normalized = f" {ai_brain.normalize_prompt(intent_data.get('raw') or '')} "
        selected = set()

        tools_by_intent = {
            "battery": {"battery"},
            "date": {"date"},
            "git_log": {"git_log"},
            "git_status": {"git_status"},
            "iot_status": {"iot_status"},
            "iot_temperature": {"iot_temperature"},
            "list_todos": {"list_todos"},
            "open_app": {"open_app"},
            "open_folder": {"open_folder"},
            "open_project": {"open_folder", "git_log", "git_status"},
            "open_site": {"open_site"},
            "search_google": {"search_google"},
            "search_personal": {"search_personal"},
            "search_youtube": {"search_youtube"},
            "summarize_file": {"summarize_file"},
            "time": {"time"},
        }
        selected.update(tools_by_intent.get(intent, set()))

        def mentions(*phrases):
            return any(f" {phrase} " in normalized for phrase in phrases)

        if mentions("ouvre", "ouvrir", "open", "lance", "active"):
            selected.update({"open_app", "open_folder", "open_site"})
        if mentions("google"):
            selected.update({"open_site", "search_google"})
        if mentions("youtube"):
            selected.update({"open_site", "search_youtube"})
        if mentions("cherche", "recherche") and not mentions("youtube"):
            selected.add("search_google")
        if mentions("note", "notes", "todo", "todos", "tache", "taches"):
            selected.update({"list_todos", "search_personal"})
        if mentions("projet", "git", "depot", "commit"):
            selected.update({"git_log", "git_status", "open_folder"})
        if mentions("fichier", "document") and mentions("resume", "resumer", "analyse"):
            selected.add("summarize_file")
        if mentions("iot", "capteur", "sonde"):
            selected.update({"iot_status", "iot_temperature"})
        if mentions("heure"):
            selected.add("time")
        if mentions("date", "jour"):
            selected.add("date")
        if mentions("niveau batterie", "batterie du pc", "ma batterie"):
            selected.add("battery")

        return tuple(sorted(selected))

    def _agent_context(self, prompt, *, tool_names=()):
        context = self._local_brain_context()
        knowledge = ""
        library = getattr(self.local_brain, "knowledge_library", None)
        if library is not None and hasattr(library, "lookup_local"):
            knowledge = library.lookup_local(prompt)

        normalized = f" {ai_brain.normalize_prompt(prompt)} "
        personal_markers = (
            " demain ",
            " journee ",
            " mes ",
            " mon ",
            " priorite ",
            " rappel ",
            " tache ",
            " taches ",
            " todo ",
            " todos ",
        )
        selected = set(tool_names or ())
        needs_personal_context = bool(
            selected.intersection({"list_todos", "search_personal"})
            or any(marker in normalized for marker in personal_markers)
        )
        needs_project_context = bool(
            selected.intersection({"git_log", "git_status", "open_folder"})
        )

        agent_context = {
            "session": self.session_memory.recent_turns(limit=2),
        }
        if knowledge:
            agent_context["knowledge"] = knowledge
        if needs_personal_context:
            agent_context.update(
                {
                    "todos": context["todos"][:5],
                    "reminders": context["reminders"][:4],
                    "preferences": context["preferences"],
                    "insights": context["insights"],
                    "user_history": context["history"][-3:],
                }
            )
        elif needs_project_context:
            agent_context.update(
                {
                    "insights": context["insights"],
                    "user_history": context["history"][-3:],
                }
            )
        return agent_context

    @staticmethod
    def _is_fast_local_chat(prompt):
        normalized = ai_brain.normalize_prompt(prompt)
        if ai_brain._answer_math_question(prompt):
            return True
        return normalized in {
            "bonjour",
            "salut",
            "hello",
            "coucou",
            "merci",
            "bravo",
            "super",
            "ca va",
            "comment vas tu",
            "qui es tu",
            "presente toi",
            "ton nom",
        }

    @staticmethod
    def _looks_compound(prompt):
        normalized = f" {ai_brain.normalize_prompt(prompt)} "
        return any(
            marker in normalized
            for marker in (" puis ", " ensuite ", " et dis ", " et regarde ", " et verifie ", " apres cela ")
        )

    def _should_use_agent(self, intent_data):
        if not config.AI_SETTINGS.get("agent_enabled", True):
            return False
        if not ai_brain.can_answer_general_questions():
            return False

        raw = (intent_data.get("raw") or "").strip()
        if not raw:
            return False
        intent = intent_data.get("intent", "")
        if intent == "chat_fallback":
            return not self._is_fast_local_chat(raw)
        if intent in {"iot_status", "iot_temperature"}:
            return True
        if intent == "open_project" and self._looks_compound(raw):
            return True

        route = self.registry.get(intent)
        return bool(route and route.ai_enabled and self._looks_compound(raw))

    @staticmethod
    def _agent_confirmation_payload(pending_action):
        return {
            "intent": pending_action.tool_name,
            "target": pending_action.arguments.get("target", ""),
            "slots": dict(pending_action.arguments),
            "agent_pending": pending_action,
        }

    def _handle_agent_request(self, intent_data):
        prompt = (intent_data.get("raw") or intent_data.get("target", "") or "").strip()
        tool_names = self._agent_tool_names(intent_data)

        def historical_fallback():
            if intent_data.get("intent") == "chat_fallback":
                return self._handle_local_chat_fallback(intent_data, allow_ollama=False)
            return self._execute_intent(intent_data)

        result = self.agent.run(
            prompt,
            context=self._agent_context(prompt, tool_names=tool_names),
            fallback=historical_fallback,
            tool_names=tool_names,
        )
        if result.pending_action is not None:
            self.pending_confirmation = self._agent_confirmation_payload(result.pending_action)
        return result.response

    def _handle_daily_brief(self, _intent_data):
        context = self._local_brain_context()
        return self.local_brain.generate_daily_brief(
            todos=context["todos"],
            reminders=context["reminders"],
            history=context["history"],
            preferences=context["preferences"],
            insights=context["insights"],
        )

    def _handle_next_action(self, _intent_data):
        context = self._local_brain_context()
        return self.local_brain.suggest_next_action(
            todos=context["todos"],
            reminders=context["reminders"],
            history=context["history"],
            insights=context["insights"],
        )

    def _handle_prioritize_tasks(self, _intent_data):
        context = self._local_brain_context()
        return self.local_brain.summarize_priorities(
            todos=context["todos"],
            insights=context["insights"],
        )

    def _handle_focus_mode(self, intent_data):
        context = self._local_brain_context()
        project_hint = intent_data.get("target", "")
        return self.local_brain.project_focus_mode(
            todos=context["todos"],
            history=context["history"],
            insights=context["insights"],
            project_hint=project_hint,
        )

    def _handle_routine_morning(self, _intent_data):
        context = self._local_brain_context()
        return self.local_brain.build_auto_routine(
            todos=context["todos"],
            reminders=context["reminders"],
            mode="morning",
        )

    def _handle_routine_evening(self, _intent_data):
        context = self._local_brain_context()
        return self.local_brain.build_auto_routine(
            todos=context["todos"],
            reminders=context["reminders"],
            mode="evening",
        )

    def _handle_capabilities(self, _intent_data):
        return self.local_brain.explain_capabilities()

    def _handle_local_chat_fallback(self, intent_data, *, allow_ollama=True):
        target = (intent_data.get("target", "") or "").strip()
        if target.startswith("mets ") or target.startswith("joue "):
            return "Je peux lancer la musique localement. Dis par exemple: mets du ninho."
        context = self._local_brain_context()
        return self.local_brain.answer(
            target,
            todos=context["todos"],
            reminders=context["reminders"],
            history=context["history"],
            preferences=context["preferences"],
            insights=context["insights"],
            session_turn=context["session_turn"],
            allow_ollama=allow_ollama,
        )

    def _handle_open_scheduler(self, _intent_data):
        """Voice command handler — signals UI to open the scheduler window."""
        return "__OPEN_SCHEDULER__"

    def _handle_open_history(self, _intent_data):
        """Voice command handler — signals UI to open the conversation history window."""
        return "__OPEN_HISTORY__"
