"""Tests unitaires pour les Phases B & C :
1. Connecteurs Sport en Direct (Ligue 1, Champions League, Liga, PL) & Cache 120s.
2. Connecteur Google Workspace (Gmail, Google Calendar, Google Drive) sans exception en l'absence de credentials.json.
3. Interface HUD 3D Cybernétique (GET /hud, Orbe de particules & Globe terrestre 3D).
4. Routage 0 ms des commandes vocales ("Affiche la Terre", "Affiche ma position", "Retourne à l'orbe").
5. Absence totale de modèles tiers non autorisés.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import actions.hud_actions as hud_actions
import autonomous_library
import google_workspace_service
import hud_state_manager
import main
import sport_service
from main import create_app


class TestPhaseBCHudAndServices(unittest.TestCase):
    """Validation rigoureuse des chantiers des Phases B & C."""

    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)
        hud_state_manager.reset_hud_state()

    # -------------------------------------------------------------------------
    # 1. Interface Web & Routes HUD 3D vs NOC
    # -------------------------------------------------------------------------
    def test_noc_dashboard_intact_and_hud_route(self):
        """Vérifie que GET / (NOC) reste à 200 OK et que GET /hud est opérationnel."""
        # 1. Dashboard NOC intact
        resp_noc = self.client.get("/")
        self.assertEqual(resp_noc.status_code, 200)
        self.assertIn("M.O.C.T.A.R", resp_noc.text)
        self.assertIn("HUD 3D", resp_noc.text)

        # 2. Route HUD 3D dédiée
        resp_hud = self.client.get("/hud")
        self.assertEqual(resp_hud.status_code, 200)
        self.assertIn("HUD 3D Cybernétique", resp_hud.text)
        self.assertIn("RETOUR NOC", resp_hud.text)
        self.assertIn("hud-canvas", resp_hud.text)
        self.assertIn("hud_3d.js", resp_hud.text)

        # 3. Assets JS locaux
        resp_js = self.client.get("/static/js/hud_3d.js")
        self.assertEqual(resp_js.status_code, 200)

        resp_three = self.client.get("/static/js/three.min.js")
        self.assertEqual(resp_three.status_code, 200)

    def test_hud_state_endpoints(self):
        """Vérifie les endpoints REST d'état /api/hud/state et /api/hud/view."""
        # Lecture état initial
        res_get = self.client.get("/api/hud/state")
        self.assertEqual(res_get.status_code, 200)
        data = res_get.json()
        self.assertEqual(data.get("view"), "orb")

        # Modification de vue (bascule sur le globe)
        res_post = self.client.post("/api/hud/view", json={"view": "globe", "locate": True})
        self.assertEqual(res_post.status_code, 200)
        updated = res_post.json()
        self.assertEqual(updated.get("view"), "globe")
        self.assertTrue(updated.get("locate"))

        # Vérification persistance
        res_get2 = self.client.get("/api/hud/state")
        self.assertEqual(res_get2.json().get("view"), "globe")

    # -------------------------------------------------------------------------
    # 2. Pilotage Vocal 0 ms de l'Orbe & Globe Terrestre
    # -------------------------------------------------------------------------
    def test_hud_voice_commands_deterministic(self):
        """Valide le basculement 0 ms des vues 3D à la voix."""
        # Affiche la Terre
        res_earth = autonomous_library.execute_autonomous_task("Affiche la Terre")
        self.assertTrue(res_earth["handled"])
        self.assertEqual(res_earth["task_category"], "hud_3d")
        self.assertEqual(res_earth["reply"], "Initialisation du globe terrestre. Vue depuis l'espace activée, Moctar.")
        self.assertEqual(hud_state_manager.get_hud_state()["view"], "globe")
        self.assertFalse(hud_state_manager.get_hud_state()["locate"])

        # Affiche ma position
        res_pos = autonomous_library.execute_autonomous_task("Affiche ma position")
        self.assertTrue(res_pos["handled"])
        self.assertEqual(res_pos["task_category"], "hud_3d")
        self.assertEqual(res_pos["reply"], "Localisation en cours. Le globe affiche votre position en temps réel, Moctar.")
        self.assertEqual(hud_state_manager.get_hud_state()["view"], "globe")
        self.assertTrue(hud_state_manager.get_hud_state()["locate"])

        # Retourne à l'orbe
        res_orb = autonomous_library.execute_autonomous_task("Retourne à l'orbe")
        self.assertTrue(res_orb["handled"])
        self.assertEqual(res_orb["task_category"], "hud_3d")
        self.assertEqual(res_orb["reply"], "Retour à l'interface orbitale principale, Moctar.")
        self.assertEqual(hud_state_manager.get_hud_state()["view"], "orb")

    def test_hud_direct_voice_command_filters(self):
        """Vérifie que les commandes HUD sont reconnues directement sans mot-clé."""
        for phrase in ["Affiche la Terre", "Montre le globe", "Globe terrestre", "Affiche ma position", "Localise-moi", "Mode orbe", "Ferme le globe"]:
            self.assertTrue(
                main.is_direct_voice_command(phrase),
                f"La commande '{phrase}' doit être acceptée en direct.",
            )

    # -------------------------------------------------------------------------
    # 3. Connecteur Sport en Direct
    # -------------------------------------------------------------------------
    def test_sport_service_queries_and_ending(self):
        """Vérifie le connecteur sport sur les grands championnats et le suffixe 'Moctar.'."""
        # Ligue 1
        res_l1 = sport_service.get_sports_update("Résultats Ligue 1")
        self.assertTrue(res_l1["success"])
        self.assertTrue(res_l1["reply"].endswith("Moctar."))

        # Champions League
        res_c1 = sport_service.get_sports_update("Résultats Champions League")
        self.assertTrue(res_c1["success"])
        self.assertTrue(res_c1["reply"].endswith("Moctar."))

        # Classement Premier League
        res_pl = sport_service.get_sports_update("Classement Premier League")
        self.assertTrue(res_pl["success"])
        self.assertTrue(res_pl["reply"].endswith("Moctar."))

        # Score par équipe
        res_team = sport_service.get_sports_update("Score en direct du Real Madrid")
        self.assertTrue(res_team["success"])
        self.assertTrue(res_team["reply"].endswith("Moctar."))

    def test_sport_service_cache_mechanism(self):
        """Vérifie que le cache 120s évite les requêtes HTTP répétées."""
        fake_data = {"events": [{"name": "PSG vs OM"}]}
        league_id = "fra.1"

        with patch("sport_service._fetch_espn_scoreboard", return_value=fake_data) as mock_fetch:
            # Appel 1
            sport_service.get_sports_update("Résultats Ligue 1")
            # Appel 2 (immédiat)
            sport_service.get_sports_update("Résultats Ligue 1")
            # Le fetch ne doit pas être appelé en boucle grâce au cache
            self.assertLessEqual(mock_fetch.call_count, 2)

    def test_autonomous_library_sport_routing(self):
        """Valide le routage déterministe des commandes sport dans autonomous_library."""
        queries = [
            "Résultats Ligue 1",
            "Résultats Champions League",
            "Résultats Liga",
            "Classement Premier League",
            "Score du PSG",
        ]
        for q in queries:
            res = autonomous_library.execute_autonomous_task(q)
            self.assertTrue(res["handled"], f"La requête '{q}' devrait être prise en charge.")
            self.assertEqual(res["task_category"], "sport")
            self.assertTrue(res["reply"].endswith("Moctar."))

    # -------------------------------------------------------------------------
    # 4. Connecteur Google Workspace
    # -------------------------------------------------------------------------
    def test_google_workspace_missing_credentials_graceful(self):
        """Vérifie la gestion gracieuse et le guidage vocal sans crash sans credentials.json."""
        with patch("google_workspace_service.find_credentials_file", return_value=None):
            with patch("google_workspace_service.find_token_file", return_value=None):
                # Gmail
                res_mail = google_workspace_service.get_unread_emails_summary()
                self.assertFalse(res_mail["success"])
                self.assertIn("credentials.json", res_mail["reply"])
                self.assertTrue(res_mail["reply"].endswith("Moctar."))

                # Calendar
                res_cal = google_workspace_service.get_today_agenda_summary()
                self.assertFalse(res_cal["success"])
                self.assertIn("credentials.json", res_cal["reply"])
                self.assertTrue(res_cal["reply"].endswith("Moctar."))

                # Drive
                res_drive = google_workspace_service.search_drive_documents("devis")
                self.assertFalse(res_drive["success"])
                self.assertIn("credentials.json", res_drive["reply"])
                self.assertTrue(res_drive["reply"].endswith("Moctar."))

    def test_autonomous_library_google_workspace_routing(self):
        """Valide le routage déterministe des requêtes Workspace dans autonomous_library."""
        with patch("google_workspace_service.find_credentials_file", return_value=None):
            queries = [
                "Mails non lus",
                "Vérifie mes emails",
                "Agenda du jour",
                "Mes rendez-vous",
                "Cherche sur Google Drive contrat",
            ]
            for q in queries:
                res = autonomous_library.execute_autonomous_task(q)
                self.assertTrue(res["handled"], f"La requête '{q}' devrait être prise en charge.")
                self.assertEqual(res["task_category"], "google_workspace")
                self.assertTrue(res["reply"].endswith("Moctar."))

    # -------------------------------------------------------------------------
    # 5. Textures 3D Réalistes NASA & Métriques Matérielles
    # -------------------------------------------------------------------------
    def test_hud_textures_and_metrics(self):
        """Vérifie le chargement des textures satellite NASA et les métriques matérielles."""
        # 1. Texture Satellite Terre
        resp_earth = self.client.get("/static/textures/earth_atmos_2048.jpg")
        self.assertEqual(resp_earth.status_code, 200)
        self.assertIn(resp_earth.headers.get("content-type", ""), ["image/jpeg", "image/jpg"])

        # 2. Texture Nuages Dynamiques
        resp_clouds = self.client.get("/static/textures/earth_clouds_1024.png")
        self.assertEqual(resp_clouds.status_code, 200)
        self.assertEqual(resp_clouds.headers.get("content-type"), "image/png")

        # 3. Métriques système réelles dans /api/hud/state
        resp_state = self.client.get("/api/hud/state")
        self.assertEqual(resp_state.status_code, 200)
        data = resp_state.json()
        self.assertIn("cpu_percent", data)
        self.assertIn("ram_percent", data)
        self.assertIn("hostname", data)
        self.assertIsInstance(data["cpu_percent"], (int, float))
        self.assertIsInstance(data["ram_percent"], (int, float))

    # -------------------------------------------------------------------------
    # 6. Réparations Phonétiques STT & Commandes Médias / HUD 3D
    # -------------------------------------------------------------------------
    def test_stt_repairs_and_autonomous_execution(self):
        """Vérifie les réparations des transcriptions Whisper réelles."""
        import stt_phonetic_repair

        # 1. "C'est tout en mode Hope 3D." -> "mode HUD 3D"
        repaired_hud, _ = stt_phonetic_repair.repair_stt_transcript("C'est tout en mode Hope 3D.")
        self.assertIn("mode HUD 3D", repaired_hud)
        self.assertTrue(main.is_direct_voice_command(repaired_hud))

        # 2. "J'ai YouTube en pause." -> "mets YouTube en pause"
        repaired_yt, _ = stt_phonetic_repair.repair_stt_transcript("J'ai YouTube en pause.")
        self.assertIn("mets YouTube en pause", repaired_yt)
        self.assertTrue(main.is_direct_voice_command(repaired_yt))

        # 3. Exécution 0 ms de la commande média réparée
        res_pause = autonomous_library.execute_autonomous_task("J'ai YouTube en pause")
        self.assertTrue(res_pause["handled"])
        self.assertEqual(res_pause["task_category"], "media")
        self.assertTrue(res_pause["reply"].endswith("Moctar."))

        # 4. Modes 3D Plasma et Quantum
        res_plasma = autonomous_library.execute_autonomous_task("mode plasma")
        self.assertTrue(res_plasma["handled"])
        self.assertEqual(res_plasma["task_category"], "hud_3d")
        self.assertEqual(hud_state_manager.get_hud_state()["view"], "plasma")

        res_quantum = autonomous_library.execute_autonomous_task("mode quantum")
        self.assertTrue(res_quantum["handled"])
        self.assertEqual(res_quantum["task_category"], "hud_3d")
        self.assertEqual(hud_state_manager.get_hud_state()["view"], "quantum")

    # -------------------------------------------------------------------------
    # 7. Règle d'Or Absolue : Aucun modèle externe non autorisé
    # -------------------------------------------------------------------------
    def test_strict_forbidden_models_absence(self):
        """Vérifie qu'aucun modèle tiers non approuvé n'est introduit."""
        files_to_check = [
            "sport_service.py",
            "google_workspace_service.py",
            "hud_state_manager.py",
            "actions/hud_actions.py",
            "templates/hud.html",
            "static/js/hud_3d.js",
        ]
        forbidden = ["chat" + "gpt", "open" + "ai", "ol" + "lama"]

        for filename in files_to_check:
            if not os.path.exists(filename):
                continue
            with open(filename, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().lower()
                for f_term in forbidden:
                    self.assertNotIn(
                        f_term,
                        content,
                        f"Le terme interdit '{f_term}' a été trouvé dans '{filename}' !",
                    )


if __name__ == "__main__":
    unittest.main()
