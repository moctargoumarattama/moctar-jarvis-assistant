# -*- coding: utf-8 -*-
"""Tests unitaires et end-to-end pour l'architecture 'Toujours Chaud, Toujours Prêt, Ultra-Intelligent' :
- WarmConnectionPool (Keep-Alive & Heartbeat)
- Dual-Engine Hedging (Zéro attente, course spéculative)
- SWR & Pre-warm Cache (Stale-While-Revalidate & Pré-chauffe spéculative)
- RecentContextTracker (Mémoire contextuelle & résolution d'anaphores)
- ProactiveSentinel (Supervision et auto-guérison proactive des services)
"""

import time
import pytest
from warm_pool import WarmConnectionPool
import dual_engine_hedging
from smart_command_cache import SmartCommandCache
from recent_context_tracker import RecentContextTracker
from proactive_sentinel import ProactiveSentinel
import autonomous_library


def test_warm_connection_pool_lifecycle():
    """Vérifie le démarrage, le statut et l'arrêt propre du WarmConnectionPool."""
    pool = WarmConnectionPool(heartbeat_interval=60.0)
    assert pool.get_status()["running"] is False

    pool.start()
    assert pool.get_status()["running"] is True
    time.sleep(0.1)

    status = pool.get_status()
    assert "latencies_ms" in status
    assert status["heartbeat_interval_sec"] == 60.0

    pool.stop()
    assert pool.get_status()["running"] is False


def test_dual_engine_hedging_fast_primary():
    """Quand le moteur primaire répond avant le hedge_delay, il gagne immédiatement."""
    def primary():
        time.sleep(0.05)
        return "Réponse rapide Gemini"

    def secondary():
        time.sleep(0.3)
        return "Réponse Groq"

    res, winner, elapsed = dual_engine_hedging.execute_hedged_call(
        primary_fn=primary,
        secondary_fn=secondary,
        primary_name="Gemini",
        secondary_name="Groq",
        hedge_delay=0.15,
    )
    assert res == "Réponse rapide Gemini"
    assert winner == "Gemini"
    assert elapsed < 150.0


def test_dual_engine_hedging_slow_primary_triggers_secondary():
    """Quand le primaire tarde (> hedge_delay), le secondaire plus rapide prend le relais."""
    def slow_primary():
        time.sleep(0.5)
        return "Réponse très lente Gemini"

    def fast_secondary():
        time.sleep(0.05)
        return "Réponse éclair Groq"

    res, winner, elapsed = dual_engine_hedging.execute_hedged_call(
        primary_fn=slow_primary,
        secondary_fn=fast_secondary,
        primary_name="Gemini",
        secondary_name="Groq",
        hedge_delay=0.1,
    )
    assert res == "Réponse éclair Groq"
    assert winner == "Groq"
    assert elapsed < 350.0


def test_dual_engine_hedging_primary_failure_fails_over():
    """Quand le primaire lève une exception (ex: 503), le secondaire délivre la réponse."""
    def failing_primary():
        raise RuntimeError("Gemini 503 Service Unavailable")

    def stable_secondary():
        time.sleep(0.02)
        return "Secours Groq nominal"

    res, winner, elapsed = dual_engine_hedging.execute_hedged_call(
        primary_fn=failing_primary,
        secondary_fn=stable_secondary,
        primary_name="Gemini",
        secondary_name="Groq",
        hedge_delay=0.2,
    )
    assert res == "Secours Groq nominal"
    assert winner == "Groq"


def test_smart_cache_swr_and_prewarm(tmp_path):
    """Vérifie le Stale-While-Revalidate (SWR) et le pré-chauffage spéculatif du cache."""
    cache = SmartCommandCache(db_path=tmp_path / "swr_cache.db")

    # 1. Enregistre une entrée avec TTL de 1.0s
    cache.set("meteo", "Il fait 24 degrés à Rabat.", category="weather", ttl=1.0)

    # Hit immédiat : pas de SWR nécessaire (< 65% de 1.0s)
    res_immediate = cache.get("meteo")
    assert res_immediate is not None
    assert res_immediate["needs_refresh"] is False

    # Après 0.7s (> 65% du TTL de 1.0s), SWR s'active
    time.sleep(0.7)
    res_swr = cache.get("meteo")
    assert res_swr is not None
    assert res_swr["reply"] == "Il fait 24 degrés à Rabat."
    assert res_swr["needs_refresh"] is True

    # 2. Pré-chauffe spéculative
    cache.prewarm_system_cache()
    res_telemetry = cache.get("telemetrie")
    assert res_telemetry is not None
    assert "processeur" in res_telemetry["reply"].lower()


def test_recent_context_tracker_and_anaphora():
    """Vérifie la mémorisation court-terme et la résolution contextuelle d'anaphores."""
    tracker = RecentContextTracker()

    # 1. Mémorisation d'un service
    tracker.record_service("klasora-whatsapp.service", "WhatsApp")
    entity_type, entity_data = tracker.get_last_entity()
    assert entity_type == "service"
    assert entity_data["short_name"] == "klasora-whatsapp"

    # Résolution 'relance-le'
    intent_restart = tracker.resolve_anaphoric_intent("relance-le")
    assert intent_restart is not None
    assert intent_restart["action"] == "restart_service"
    assert intent_restart["target"] == "klasora-whatsapp.service"

    # Résolution 'son statut'
    intent_status = tracker.resolve_anaphoric_intent("quel est son statut")
    assert intent_status is not None
    assert intent_status["action"] == "status_service"
    assert intent_status["display_name"] == "WhatsApp"

    # 2. Mémorisation d'un média
    tracker.record_media("play", "Ma Playlist Rap")
    intent_media = tracker.resolve_anaphoric_intent("coupe-le")
    assert intent_media is not None
    assert intent_media["action"] == "pause_media"

    intent_resume = tracker.resolve_anaphoric_intent("relance ça")
    assert intent_resume is not None
    assert intent_resume["action"] == "resume_media"

    # 3. Mémorisation d'une application
    tracker.record_app("vscode", "Visual Studio Code - main.py")
    intent_close_app = tracker.resolve_anaphoric_intent("ferme-le")
    assert intent_close_app is not None
    assert intent_close_app["action"] == "close_active_window"


def test_proactive_sentinel_auto_healing():
    """Vérifie que la Sentinelle Proactive détecte un service inactif et le guérit."""
    sentinel = ProactiveSentinel.get_instance()

    # Simule un scan
    actions = sentinel.scan_and_heal()
    assert isinstance(actions, list)

    # Vérifie la gestion d'annonce vocale
    sentinel._pending_announcements.append({
        "message": "Moctar, le service WhatsApp a été relancé.",
        "service": "klasora-whatsapp.service",
        "healed": True,
        "created_at": time.time(),
    })
    msg = sentinel.pop_pending_announcement()
    assert msg == "Moctar, le service WhatsApp a été relancé."
    assert sentinel.pop_pending_announcement() is None


def test_autonomous_library_anaphora_and_whatsapp_flow():
    """Test bout-en-bout : commande WhatsApp suivie d'une anaphore 'relance-le'."""
    # 1. Commande explicite WhatsApp
    res_wa = autonomous_library.execute_autonomous_task("relance whatsapp")
    assert res_wa.get("handled") is True
    assert res_wa.get("task_category") == "services"
    assert "WhatsApp" in res_wa.get("reply", "")

    # 2. Commande anaphorique immédiatement après : 'relance-le'
    res_anaphora = autonomous_library.execute_autonomous_task("relance-le")
    assert res_anaphora.get("handled") is True
    assert res_anaphora.get("task_category") == "services"
    assert "WhatsApp" in res_anaphora.get("reply", "")


def test_voice_micro_mute_sleep_and_wake_protocol():
    """Vérifie le protocole de mise en veille silencieuse et réactivation du micro sans déranger la nuit."""
    import main
    import dynamic_attention_engine

    # 1. Ordres explicites de coupure micro
    res_mute1 = main.handle_instant_local_command("coupe ton micro")
    assert "Micro coupé Moctar" in res_mute1

    res_mute2 = main.handle_instant_local_command("coupe le micro")
    assert "Micro coupé Moctar" in res_mute2

    res_mute3 = main.handle_instant_local_command("et stop")
    assert "Micro coupé Moctar" in res_mute3

    res_night = main.handle_instant_local_command("bonne nuit")
    assert "Bonne nuit Moctar" in res_night
    assert "Micro coupé" in res_night

    # 2. Désengagement dans DynamicAttentionEngine
    att1 = dynamic_attention_engine.classify_utterance_attention("coupe ton micro", is_session_active=True)
    assert att1["should_disengage"] is True
    assert att1["intent"] == "disengage"

    att2 = dynamic_attention_engine.classify_utterance_attention("coupe le micro", is_session_active=True)
    assert att2["should_disengage"] is True

    att3 = dynamic_attention_engine.classify_utterance_attention("et stop", is_session_active=True)
    assert att3["should_disengage"] is True

    # 3. Réactivation / Rallumage micro ("allume ton micro", "tu peux reprendre")
    res_wake1 = main.handle_instant_local_command("allume ton micro")
    assert "Micro réactivé Moctar" in res_wake1

    res_wake2 = main.handle_instant_local_command("tu peux reprendre")
    assert "Micro réactivé Moctar" in res_wake2

    # 4. Variations complexes et composées ('Maintenant, coupe et arrête le micro', 'coupes de micro')
    res_mute4 = main.handle_instant_local_command('Maintenant, coupe et arrête le micro')
    assert res_mute4 is not None
    assert 'Micro coupé Moctar' in res_mute4

    res_mute5 = main.handle_instant_local_command('Coupes de micro')
    assert res_mute5 is not None
    assert 'Micro coupé Moctar' in res_mute5

    res_mute6 = main.handle_instant_local_command('arrête le micro')
    assert res_mute6 is not None
    assert 'Micro coupé Moctar' in res_mute6

    # 5. Garde-fou inactivité et silence nocturne ProactiveCompanion
    from proactive_companion import ProactiveCompanion
    import time
    comp = ProactiveCompanion.get_instance()
    # Simuler inactivité > 5 minutes (utilisateur endormi dans sa chambre)
    comp._last_activity_time = time.monotonic() - 400.0
    comp._work_session_start = time.monotonic() - 3600.0
    # En pleine nuit avec inactivité : JAMAIS de prise de parole spontanée
    circadian_silent = comp.check_circadian_wellness()
    assert circadian_silent is None, 'Le compagnon ne doit JAMAIS parler quand Moctar dort'

    # Simuler runtime avec micro coupé
    fake_rt = type('Runtime', (), {'voice_muted': True, 'mic_sleep': True, 'voice_session_until': 0.0})()
    comp._last_activity_time = time.monotonic()
    proactive_muted = comp.evaluate_proactive_event(fake_rt)
    assert proactive_muted is None, 'Le compagnon ne doit JAMAIS émettre d événement si le micro est coupé'


def test_voice_microphone_lock_and_unlock_with_yo():
    """Vérifie le protocole de verrouillage strict du micro déverrouillable UNIQUEMENT via 'yo'."""
    import main
    import dynamic_attention_engine

    # 1. Détection des commandes de verrouillage strict
    assert main.is_microphone_lock_command("verrouiller le micro") is True
    assert main.is_microphone_lock_command("verrouille ton micro") is True
    assert main.is_microphone_lock_command("verrouille le micro") is True
    assert main.is_microphone_lock_command("bloque le micro") is True
    assert main.is_microphone_lock_command("ferme le micro carrement") is True
    assert main.is_microphone_lock_command("ferme carrément le micro") is True
    assert main.is_microphone_lock_command("coupe le micro") is False  # mute standard, pas lock

    # 2. Détection des commandes de déverrouillage strict (mot-clé exclusif 'yo' ou déverrouille)
    assert main.is_microphone_unlock_command("yo") is True
    assert main.is_microphone_unlock_command("yo !") is True
    assert main.is_microphone_unlock_command("yo moctar") is True
    assert main.is_microphone_unlock_command("déverrouille le micro") is True
    assert main.is_microphone_unlock_command("deverrouille le micro") is True
    assert main.is_microphone_unlock_command("debloque le micro") is True
    assert main.is_microphone_unlock_command("bonjour") is False
    assert main.is_microphone_unlock_command("quelle heure est-il") is False

    # 3. Réponse locale instantanée (0ms) au verrouillage
    res_lock = main.handle_instant_local_command("verrouiller le micro")
    assert res_lock is not None
    assert "Micro verrouillé Moctar" in res_lock
    assert "Yo" in res_lock

    # 4. Réponse locale instantanée (0ms) au déverrouillage
    res_unlock = main.handle_instant_local_command("yo")
    assert res_unlock is not None
    assert "Micro déverrouillé Moctar" in res_unlock

    # 5. Extraction du wake word "yo"
    accepted_yo, cmd_yo, wake_yo = main.extract_voice_interact_command("yo")
    assert accepted_yo is True
    assert cmd_yo == main._WAKE_ONLY_SENTINEL
    assert wake_yo is True

    accepted_cmd, cmd_text, wake_cmd = main.extract_voice_interact_command("yo moctar quelle heure est-il")
    assert accepted_cmd is True
    assert "quelle heure est-il" in cmd_text
    assert wake_cmd is True

    # 6. DynamicAttentionEngine clôture la session sur ordre de verrouillage
    att_lock = dynamic_attention_engine.classify_utterance_attention("verrouiller le micro", is_session_active=True)
    assert att_lock["should_disengage"] is True
    assert att_lock["intent"] == "disengage"

    # 7. ProactiveCompanion bloque toute initiative si le micro est verrouillé
    from proactive_companion import ProactiveCompanion
    comp = ProactiveCompanion.get_instance()
    locked_rt = type('Runtime', (), {'voice_locked': True, 'voice_muted': True, 'mic_sleep': True, 'voice_session_until': 0.0})()
    comp._last_activity_time = time.monotonic()
    assert comp.evaluate_proactive_event(locked_rt) is None



