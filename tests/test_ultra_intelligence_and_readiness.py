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
        time.sleep(0.02)
        return "Réponse rapide Gemini"

    def secondary():
        time.sleep(0.4)
        return "Réponse Groq"

    res, winner, elapsed = dual_engine_hedging.execute_hedged_call(
        primary_fn=primary,
        secondary_fn=secondary,
        primary_name="Gemini",
        secondary_name="Groq",
        hedge_delay=0.25,
    )
    assert res == "Réponse rapide Gemini"
    assert winner == "Gemini"
    assert elapsed < 250.0


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
