import logging
import re

import ai_brain
import config
from actions import (
    energy_actions,
    iot_actions,
    music_actions,
    personal_actions,
    project_actions,
    system_actions,
    web_actions,
)
from assistant_memory import SessionMemory, UserMemoryStore
from assistant_plugins import build_default_registry
from assistant_security import SecurityPolicy
from scheduler import scheduler_store as sched_store
from scheduler import scheduler_core as sched_core


logger = logging.getLogger("jarvis")
LOCAL_RECOMMENDATIONS = [
    "Ninho - Lettre a une femme",
    "Aya Nakamura - Djadja",
    "Wizkid - Essence",
    "Burna Boy - Last Last",
]


class AssistantCore:
    def __init__(self):
        config.ensure_runtime_directories()
        self.project_assistant = project_actions.ProjectAssistant()
        self.personal_assistant = personal_actions.PersonalAssistant()
        self.registry = build_default_registry(self)
        self.security = SecurityPolicy()
        self.session_memory = SessionMemory()
        self.user_memory = UserMemoryStore()
        self.local_brain = ai_brain.LocalBrain()
        self.pending_confirmation = None
        self.pending_music_request = False
        self.music_playing = False
        self.music_paused_for_command = False
        self._announced_scheduler_task_ids = set()

    @staticmethod
    def _format_user_facing_response(response):
        if response in {"__STOP__", "__OPEN_SCHEDULER__"}:
            return response

        text = (response or "").strip()
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

    def _resolve_confirmation(self, intent):
        if intent == "confirm_no":
            self.pending_confirmation = None
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
            return "__STOP__"

        if self.pending_music_request and intent == "chat_fallback" and target.strip():
            music_target = re.sub(
                r"^(?:je veux|je voudrais|mets|met|joue|ecoute)(?:\s+(?:la )?musique)?\s+",
                "",
                target.strip(),
            )
            music_target = re.sub(r"^(?:musique|chanson|son)\s+", "", music_target).strip()
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

        response = self._format_user_facing_response(self._execute_intent(intent_data))
        self._remember(intent, target, response)
        return response

    def _handle_open_app(self, intent_data):
        return system_actions.open_app(intent_data.get("target", ""))

    def _handle_close_app(self, intent_data):
        return system_actions.close_app(intent_data.get("target", ""))

    def _handle_open_folder(self, intent_data):
        return system_actions.open_folder(intent_data.get("target", ""))

    def _handle_time(self, _intent_data):
        return system_actions.get_time_response()

    def _handle_date(self, _intent_data):
        return system_actions.get_date_response()

    def _handle_battery(self, _intent_data):
        return system_actions.get_battery_response()

    def _handle_screenshot(self, _intent_data):
        return system_actions.capture_screenshot()

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

    def _handle_git_status(self, _intent_data):
        return self.project_assistant.git_status()

    def _handle_git_pull(self, _intent_data):
        return self.project_assistant.git_pull()

    def _handle_git_log(self, _intent_data):
        return self.project_assistant.git_log()

    def _handle_git_commit_prepare(self, _intent_data):
        return self.project_assistant.prepare_commit()

    def _handle_git_push_prepare(self, _intent_data):
        return self.project_assistant.prepare_push()

    def _handle_create_note(self, intent_data):
        return self.personal_assistant.create_note(intent_data.get("target", ""))

    def _handle_add_todo(self, intent_data):
        return self.personal_assistant.add_todo(intent_data.get("target", ""))

    def _handle_list_todos(self, _intent_data):
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

    def _handle_local_chat_fallback(self, intent_data):
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
        )

    def _handle_open_scheduler(self, _intent_data):
        """Voice command handler — signals UI to open the scheduler window."""
        return "__OPEN_SCHEDULER__"
