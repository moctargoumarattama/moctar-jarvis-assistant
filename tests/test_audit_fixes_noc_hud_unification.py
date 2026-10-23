"""Tests unitaires de validation des 6 correctifs de l'audit de production :
1. Réparation phonétique Franglais Whisper ('Return Arm Mode NOC', 'mode hot-trading', 'guide push', 'git commun', 'Yo Mothar').
2. Priorité GPS (locate=True) sur 'Dis-moi je suis où dans le monde, sur le globe terrestre' & retour NOC (url='/').
3. Unification des outils entre NOC (/api/jarvis/query) et Voix/HUD (/api/voice/interact) : switch_hud_view, git_status, prepare_commit, prepare_push, alias Papa Market -> Baba Market.
4. Suppression de la triplication JS dans templates/hud.html et navigation relative ('/hud', '/') dans index.html et hud_3d.js.
5. Protection Anti-Auto-Écho serveur ('Ventraler 0.2' après annonce de l'heure).
6. Nettoyage et protection du profil Moctar dans assistant_memory.py contre la pollution par phrases brutes.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import actions
from actions import project_actions
import assistant_memory
import autonomous_library
import headless_bridge
import hud_state_manager
import main
import stt_phonetic_repair
import voice_semantic_engine
import voice_transcribe


class TestAuditFixesNocHudUnification(unittest.TestCase):
    def setUp(self) -> None:
        hud_state_manager.reset_hud_state()

    def test_1_stt_phonetic_repairs_from_production_logs(self) -> None:
        # 1. "Return Arm Mode NOC." -> "retourne en mode NOC"
        r1, _ = stt_phonetic_repair.repair_stt_transcript("Return Arm Mode NOC.")
        self.assertIn("retourne en mode NOC", r1)
        self.assertTrue(main.is_direct_voice_command(r1))

        # 2. "C'est un mode hot-trading." -> "mode HUD 3D"
        r2, _ = stt_phonetic_repair.repair_stt_transcript("C'est un mode hot-trading.")
        self.assertIn("mode HUD 3D", r2)
        self.assertTrue(main.is_direct_voice_command(r2))

        # 3. "Moi, un git commun, un git push," -> "git commit" & "git push"
        r3, _ = stt_phonetic_repair.repair_stt_transcript("Moi, un git commun, un git push,")
        self.assertIn("git commit", r3)
        self.assertIn("git push", r3)
        self.assertTrue(main.is_direct_voice_command(r3))

        # 4. "C'est un guide push de mon projet Papa Market." -> "git push"
        r4, _ = stt_phonetic_repair.repair_stt_transcript("C'est un guide push de mon projet Papa Market.")
        self.assertIn("git push", r4)
        self.assertTrue(main.is_direct_voice_command(r4))

        # 5. "Yo Mothar" -> "Yo Moctar" -> wake word reconnu
        r5, _ = stt_phonetic_repair.repair_stt_transcript("Yo Mothar")
        self.assertIn("Moctar", r5)
        accepted, cmd, wake = main.extract_voice_interact_command("Yo Mothar", session_active=False)
        self.assertTrue(accepted)
        self.assertTrue(wake)
        self.assertEqual(cmd, main._WAKE_ONLY_SENTINEL)

    def test_2_gps_locate_priority_and_return_to_noc(self) -> None:
        # "Dis-moi je suis où dans le monde, sur le globe terrestre." doit activer locate=True !
        res_loc = autonomous_library.execute_autonomous_task(
            "Dis-moi je suis où dans le monde, sur le globe terrestre."
        )
        self.assertTrue(res_loc["handled"])
        self.assertEqual(res_loc["url"], "/hud")
        state = hud_state_manager.get_hud_state()
        self.assertEqual(state["view"], "globe")
        self.assertTrue(state["locate"], "locate doit être True quand Moctar demande où il est sur le globe !")

        # "Return Arm Mode NOC." -> réparé puis routé vers url="/"
        repaired_noc, _ = stt_phonetic_repair.repair_stt_transcript("Return Arm Mode NOC.")
        res_noc = autonomous_library.execute_autonomous_task(repaired_noc)
        self.assertTrue(res_noc["handled"])
        self.assertEqual(res_noc["url"], "/")
        self.assertEqual(res_noc["action"], "open_app")

    def test_3_git_and_papa_market_resolution_and_unification(self) -> None:
        # 1. Résolution de "Papa Market" vers le projet baba_market
        from actions.project_actions import _get_project_assistant
        pa = _get_project_assistant()
        proj = pa._resolve_project("Papa Market")
        self.assertIsNotNone(proj)
        self.assertEqual(proj.key, "baba_market")

        # 2. Exécution autonome d'un git push sur Papa Market
        res_git = autonomous_library.execute_autonomous_task("Fais un git push de mon projet Papa Market")
        self.assertTrue(res_git["handled"])
        self.assertEqual(res_git["task_category"], "git_dev")
        self.assertIn("Baba Market", res_git["reply"])

        # 3. Présence des outils unifiés dans VOICE_FAST_TOOLS et select_tools_for_prompt
        fast_tool_names = {t["function"]["name"] for t in voice_semantic_engine.VOICE_FAST_TOOLS}
        for expected_tool in ("switch_hud_view", "git_status", "git_pull", "prepare_commit", "prepare_push"):
            self.assertIn(expected_tool, fast_tool_names)

        noc_hud_tools = headless_bridge.select_tools_for_prompt("Dis-moi où je suis sur le globe 3D")
        self.assertIn("switch_hud_view", noc_hud_tools)

        noc_git_tools = headless_bridge.select_tools_for_prompt("Fais un git push sur Papa Market")
        self.assertIn("prepare_push", noc_git_tools)

    def test_4_hud_template_no_duplicate_inline_script_and_relative_url_navigation(self) -> None:
        hud_html = Path("templates/hud.html").read_text(encoding="utf-8")
        # templates/hud.html ne doit plus définir __MOCTAR_HUD_BOOTED__ en inline avant hud_3d.js
        self.assertNotIn("window.__MOCTAR_HUD_BOOTED__ = true;", hud_html)
        self.assertIn("/static/js/hud_3d.js", hud_html)

        index_html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("targetUrl.startsWith('/')", index_html)

        hud_js = Path("static/js/hud_3d.js").read_text(encoding="utf-8")
        self.assertIn("targetUrl === '/'", hud_js)
        self.assertIn("data.locate === true", hud_js)

    def test_5_self_echo_guard_rejects_ventraler_and_recent_tts(self) -> None:
        from types import SimpleNamespace
        import time

        fake_runtime = SimpleNamespace(
            last_tts_reply_text="Il est 23 heures 02, Moctar.",
            last_tts_timestamp=time.monotonic(),
        )
        self.assertTrue(main.is_self_echo_transcript("Ventraler 0.2", fake_runtime))
        self.assertTrue(main.is_self_echo_transcript("Il est 23 heures 02", fake_runtime))
        self.assertFalse(main.is_self_echo_transcript("Ouvre VS Code", fake_runtime))

    def test_6_moctar_profile_sanitization_and_no_raw_pollution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test_profile_clean.db")
            mem = assistant_memory.AssistantMemory(db_path=db_path)

            # Simuler un profil pollué en base SQLite (comme observé en production)
            polluted_profile = mem.get_moctar_profile()
            polluted_profile["recent_topics"] = [
                "C'est un guide push de mon projet Papa M",
                "C'est un mode hot-trading",
                "Je veux que ça soit fait par les gens de",
                "Return Arm Mode NOC",
                "Ventraler 0.2",
                "Météo",
            ]
            mem.save_fact(
                category="user_profile",
                key="moctar",
                value=json.dumps(polluted_profile, ensure_ascii=False),
                importance=5,
            )

            # Lecture : get_moctar_profile doit purger automatiquement les entrées polluées
            cleaned = mem.get_moctar_profile()
            self.assertEqual(cleaned["recent_topics"], ["Météo"])

            # Une phrase conversationnelle quelconque ne doit plus polluer recent_topics
            mem.record_conversation_turn_for_profile(
                "Je veux que ça soit fait par les gens de la maintenance demain",
                "Bien noté.",
            )
            after = mem.get_moctar_profile()
            self.assertEqual(after["recent_topics"], ["Météo"])

    def test_7_git_confirmation_oui_and_no_false_positive_pause(self) -> None:
        # 1. Réparations STT supplémentaires observées en production (23:11 -> 23:56)
        rep_bureau, _ = stt_phonetic_repair.repair_stt_transcript("Météo, mode bureau.")
        self.assertIn("mets-toi en mode bureau", rep_bureau.lower())

        rep_babam, _ = stt_phonetic_repair.repair_stt_transcript("Le paru du Babam Market.")
        self.assertIn("je parle de baba market", rep_babam.lower())

        # 2. "Réalisation, dégénération, pause." ne doit JAMAIS déclencher pause hors session
        self.assertFalse(main.is_direct_voice_command("Réalisation, dégénération, pause."))
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations("Réalisation, dégénération, pause."), "")

        # 3. "Oui." est filtré hors session sans action Git en attente, mais ACCEPTÉ en session ou après prepare_push
        project_actions._get_project_assistant().pending_git_action = None
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations("Oui.", allow_short_confirmations=False), "")
        self.assertEqual(voice_transcribe.filter_whisper_hallucinations("Oui.", allow_short_confirmations=True), "Oui.")

        acc_no_sess, _, _ = main.extract_voice_interact_command("Oui.", session_active=False)
        self.assertFalse(acc_no_sess)

        acc_sess, cmd_sess, _ = main.extract_voice_interact_command("Oui.", session_active=True)
        self.assertTrue(acc_sess)
        self.assertEqual(cmd_sess.lower(), "oui")

        # 4. Flux complet : prepare_push('baba_market') -> "Oui." exécute confirm_pending_git_action
        project_actions.prepare_push("baba_market")
        self.assertTrue(project_actions.has_pending_git_action())
        res_conf = autonomous_library.execute_autonomous_task("Oui.")
        self.assertTrue(res_conf.get("handled"))
        self.assertEqual(res_conf.get("task_category"), "git_dev")
        self.assertFalse(project_actions.has_pending_git_action())


if __name__ == "__main__":
    unittest.main()

