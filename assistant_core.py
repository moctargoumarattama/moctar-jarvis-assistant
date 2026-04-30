import logging

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
from ai_brain import safe_ask_gpt


logger = logging.getLogger("jarvis")


class AssistantCore:
    def __init__(self):
        config.ensure_runtime_directories()
        self.project_assistant = project_actions.ProjectAssistant()
        self.personal_assistant = personal_actions.PersonalAssistant()

    def recommend_music(self, mood="musique populaire du moment"):
        prompt = (
            "Propose une seule musique a ecouter maintenant. "
            f"Contexte ou humeur: {mood}. "
            "Reponds uniquement avec: titre - artiste."
        )
        return safe_ask_gpt(prompt, fallback="Ninho - Lettre a une femme")

    def poll_background_messages(self):
        return self.personal_assistant.poll_due_reminders()

    @staticmethod
    def _format_number(value, decimals=2):
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        formatted = f"{value:.{decimals}f}"
        return formatted.rstrip("0").rstrip(".")

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

    def handle_intent(self, intent_data):
        intent = intent_data["intent"]
        target = intent_data.get("target", "")
        slots = intent_data.get("slots", {})

        logger.info("Intent detected: %s", intent_data)

        if intent == "empty":
            return "Je n'ai rien entendu."
        if intent == "stop":
            return "__STOP__"
        if intent == "open_app":
            return system_actions.open_app(target)
        if intent == "close_app":
            return system_actions.close_app(target)
        if intent == "open_site":
            return web_actions.open_site(target)
        if intent == "open_folder":
            return system_actions.open_folder(target)
        if intent == "play_music":
            return music_actions.play_music(target, recommender=self.recommend_music)
        if intent == "playlist":
            return music_actions.open_playlist(target)
        if intent == "search_google":
            return web_actions.search_google(target)
        if intent == "search_youtube":
            return web_actions.search_youtube(target)
        if intent == "time":
            return system_actions.get_time_response()
        if intent == "battery":
            return system_actions.get_battery_response()
        if intent == "screenshot":
            return system_actions.capture_screenshot()
        if intent == "volume_up":
            return system_actions.change_volume("volume_up")
        if intent == "volume_down":
            return system_actions.change_volume("volume_down")
        if intent == "mute":
            return system_actions.change_volume("mute")
        if intent == "open_project":
            open_in_vscode = slots.get("editor") == "vscode"
            return self.project_assistant.open_project(target, open_in_vscode=open_in_vscode)
        if intent == "launch_project_server":
            return self.project_assistant.launch_project_server(target or None)
        if intent == "git_status":
            return self.project_assistant.git_status()
        if intent == "git_pull":
            return self.project_assistant.git_pull()
        if intent == "git_log":
            return self.project_assistant.git_log()
        if intent == "git_commit_prepare":
            return self.project_assistant.prepare_commit()
        if intent == "git_push_prepare":
            return self.project_assistant.prepare_push()
        if intent == "create_note":
            return self.personal_assistant.create_note(target)
        if intent == "add_todo":
            return self.personal_assistant.add_todo(target)
        if intent == "list_todos":
            return self.personal_assistant.list_todos()
        if intent == "remind_me":
            return self.personal_assistant.schedule_reminder(
                slots.get("message", target),
                slots.get("when_text", ""),
            )
        if intent == "summarize_file":
            lower = target.lower()
            if lower.endswith(".csv") or lower.endswith(".xlsx"):
                return self.project_assistant.summarize_tabular_file(target)
            return self.personal_assistant.summarize_file(target)
        if intent == "search_personal":
            return self.personal_assistant.search(target)
        if intent in {
            "energy_consumption",
            "energy_cost",
            "solar_sizing",
            "battery_sizing",
            "inverter_sizing",
            "energy_audit_template",
        }:
            return self._handle_energy_intent(intent, target, slots)
        if intent == "iot_status":
            return iot_actions.get_iot_status()
        if intent == "iot_temperature":
            return iot_actions.get_iot_temperature()
        if intent == "iot_relay_on":
            return iot_actions.set_relay_state(slots.get("relay_id", 1), True)
        if intent == "iot_relay_off":
            return iot_actions.set_relay_state(slots.get("relay_id", 1), False)
        if intent == "chat_fallback":
            return safe_ask_gpt(target, fallback=config.AI_SETTINGS["chat_fallback"])

        return "Je n'ai pas compris cette commande."
