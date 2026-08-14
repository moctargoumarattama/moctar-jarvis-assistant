import unittest

from intent_engine import detect_intent


class IntentEngineTests(unittest.TestCase):
    def test_detects_open_application_in_french(self):
        intent = detect_intent("Jarvis ouvre bloc note stp")

        self.assertEqual("open_app", intent["intent"])
        self.assertEqual("notepad", intent["target"])

    def test_detects_open_site_in_french(self):
        intent = detect_intent("ouvre github")

        self.assertEqual("open_site", intent["intent"])
        self.assertEqual("github", intent["target"])

    def test_detects_open_folder(self):
        intent = detect_intent("ouvre telechargements")

        self.assertEqual("open_folder", intent["intent"])
        self.assertEqual("downloads", intent["target"])

    def test_detects_open_project(self):
        intent = detect_intent("ouvre projet noor express")

        self.assertEqual("open_project", intent["intent"])
        self.assertEqual("noor_express", intent["target"])

    def test_detects_launch_server(self):
        intent = detect_intent("lance serveur flask")

        self.assertEqual("launch_project_server", intent["intent"])
        self.assertEqual("flask", intent["target"])

    def test_detects_google_search_target(self):
        intent = detect_intent("cherche sur google meteo casablanca")

        self.assertEqual("search_google", intent["intent"])
        self.assertEqual("meteo casablanca", intent["target"])

    def test_detects_create_note(self):
        intent = detect_intent("cree une note idee audit solaire")

        self.assertEqual("create_note", intent["intent"])
        self.assertEqual("idee audit solaire", intent["target"])

    def test_detects_add_todo(self):
        intent = detect_intent("ajoute todo appeler client demain")

        self.assertEqual("add_todo", intent["intent"])
        self.assertEqual("appeler client demain", intent["target"])

    def test_detects_list_todos(self):
        intent = detect_intent("liste mes todos")

        self.assertEqual("list_todos", intent["intent"])

    def test_detects_daily_brief(self):
        intent = detect_intent("resume ma journee")

        self.assertEqual("daily_brief", intent["intent"])

    def test_detects_next_action(self):
        intent = detect_intent("quoi faire maintenant")

        self.assertEqual("next_action", intent["intent"])

    def test_detects_capabilities_request(self):
        intent = detect_intent("que peux tu faire")

        self.assertEqual("assistant_capabilities", intent["intent"])

    def test_detects_summarize_file(self):
        intent = detect_intent("resume le fichier notes/audit.txt")

        self.assertEqual("summarize_file", intent["intent"])
        self.assertEqual("notes/audit.txt", intent["target"])

    def test_empty_music_command_keeps_recommendation_path_available(self):
        intent = detect_intent("mets une musique")

        self.assertEqual("play_music", intent["intent"])
        self.assertEqual("", intent["target"])

    def test_detects_energy_consumption(self):
        intent = detect_intent("calcule la consommation de 12 lampes de 60 watts pendant 8 heures")

        self.assertEqual("energy_consumption", intent["intent"])
        self.assertEqual(12.0, intent["slots"]["quantity"])
        self.assertEqual(60.0, intent["slots"]["power_watts"])
        self.assertEqual(8.0, intent["slots"]["duration_hours"])

    def test_detects_energy_cost_with_decimal_price(self):
        intent = detect_intent("calcule le cout de 4 kwh a 0,15 dirham")

        self.assertEqual("energy_cost", intent["intent"])
        self.assertEqual(4.0, intent["slots"]["energy_kwh"])
        self.assertAlmostEqual(0.15, intent["slots"]["price_per_kwh"], places=2)

    def test_detects_solar_sizing(self):
        intent = detect_intent(
            "dimensionne solaire pour 2 kwh par jour avec 5 heures de soleil et rendement 80"
        )

        self.assertEqual("solar_sizing", intent["intent"])
        self.assertEqual(2.0, intent["slots"]["daily_energy_kwh"])
        self.assertEqual(5.0, intent["slots"]["sun_hours"])
        self.assertAlmostEqual(0.8, intent["slots"]["system_efficiency"], places=2)

    def test_detects_battery_sizing(self):
        intent = detect_intent(
            "dimensionne batterie pour 3 kwh en 24 volts avec 2 jours autonomie et dod 60"
        )

        self.assertEqual("battery_sizing", intent["intent"])
        self.assertEqual(3.0, intent["slots"]["energy_kwh"])
        self.assertEqual(24.0, intent["slots"]["system_voltage"])
        self.assertEqual(2.0, intent["slots"]["autonomy_days"])
        self.assertAlmostEqual(0.6, intent["slots"]["depth_of_discharge"], places=2)

    def test_detects_inverter_sizing(self):
        intent = detect_intent("dimensionne onduleur pour 3200 watts avec marge 30")

        self.assertEqual("inverter_sizing", intent["intent"])
        self.assertEqual(3200.0, intent["slots"]["total_power_watts"])
        self.assertAlmostEqual(0.3, intent["slots"]["safety_margin"], places=2)

    def test_detects_energy_audit_template(self):
        intent = detect_intent("donne moi un template audit energetique hotel")

        self.assertEqual("energy_audit_template", intent["intent"])
        self.assertEqual("hotel", intent["target"])

    def test_detects_iot_status(self):
        intent = detect_intent("donne moi le statut iot")

        self.assertEqual("iot_status", intent["intent"])

    def test_detects_iot_temperature(self):
        intent = detect_intent("quelle est la temperature du capteur iot")

        self.assertEqual("iot_temperature", intent["intent"])

    def test_detects_iot_relay_on_with_id(self):
        intent = detect_intent("allume le relais 2")

        self.assertEqual("iot_relay_on", intent["intent"])
        self.assertEqual(2, intent["slots"]["relay_id"])

    def test_detects_iot_relay_off_with_default_id(self):
        intent = detect_intent("eteins le relais")

        self.assertEqual("iot_relay_off", intent["intent"])
        self.assertEqual(1, intent["slots"]["relay_id"])

    def test_detects_confirmation_yes(self):
        intent = detect_intent("oui")
        self.assertEqual("confirm_yes", intent["intent"])

    def test_detects_confirmation_no(self):
        intent = detect_intent("annule")
        self.assertEqual("confirm_no", intent["intent"])


if __name__ == "__main__":
    unittest.main()
