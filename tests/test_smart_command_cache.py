"""Tests unitaires complets pour le module SmartCommandCache."""

import time
from pathlib import Path
import pytest

from smart_command_cache import (
    SmartCommandCache,
    canonicalize_query,
    get_command_cache,
)


@pytest.fixture
def temp_cache(tmp_path: Path) -> SmartCommandCache:
    """Fixture créant un cache isolé avec une base SQLite temporaire."""
    db_file = tmp_path / "test_memory.db"
    return SmartCommandCache(db_path=db_file)


def test_canonicalize_query_categories():
    """Vérifie l'indexation sémantique et la catégorisation des commandes."""
    # Météo
    key, cat, meta = canonicalize_query("Jarvis, donne-moi la météo à Rabat s'il te plaît")
    assert cat == "weather"
    assert key == "weather:rabat"
    assert meta.get("city") == "rabat"

    key2, cat2, _ = canonicalize_query("quel temps fait-il à Niamey ?")
    assert cat2 == "weather"
    assert key2 == "weather:niamey"

    # Services
    key3, cat3, _ = canonicalize_query("affiche-moi l'état des services")
    assert cat3 == "services"
    assert key3 == "system:services"

    # Docker
    key4, cat4, _ = canonicalize_query("statut des conteneurs docker")
    assert cat4 == "docker"
    assert key4 == "docker:status"

    # Télémétrie
    key5, cat5, _ = canonicalize_query("donne-moi la télémétrie de la machine")
    assert cat5 == "telemetry"
    assert key5 == "system:telemetry"

    # Tâches
    key6, cat6, _ = canonicalize_query("qu'est-ce que j'ai à faire aujourd'hui ?")
    assert cat6 == "tasks"
    assert key6 == "tasks:list"

    # Briefing
    key7, cat7, _ = canonicalize_query("fais-moi le briefing quotidien")
    assert cat7 == "briefing"
    assert key7 == "briefing:daily"

    # Git
    key8, cat8, _ = canonicalize_query("statut git du projet")
    assert cat8 == "git"
    assert key8 == "git:status"

    # Identité
    key9, cat9, _ = canonicalize_query("qui es-tu ?")
    assert cat9 == "info"
    assert "qui_es_tu" in key9


def test_cache_set_and_get(temp_cache: SmartCommandCache):
    """Vérifie le cycle d'enregistrement et de restitution ultra-rapide."""
    query = "quelle est la météo à Rabat ?"
    reply = "Il fait 22°C et grand soleil à Rabat, Moctar."
    agent_result = {"status": "success", "reply": reply, "fast_path": True}

    assert temp_cache.get(query) is None

    # Enregistrement
    saved = temp_cache.set(query, reply, agent_result=agent_result, ttl=10.0)
    assert saved is True

    # Récupération immédiate
    cached = temp_cache.get(query)
    assert cached is not None
    assert cached["reply"] == reply
    assert cached["category"] == "weather"
    assert cached["hit_count"] == 1
    assert cached["from_cache"] is True

    # Deuxième hit -> compteur incrémenté
    cached2 = temp_cache.get("donne-moi la météo à Rabat")
    assert cached2 is not None
    assert cached2["hit_count"] == 2


def test_cache_expiration(temp_cache: SmartCommandCache):
    """Vérifie l'expiration naturelle du TTL."""
    query = "télémétrie CPU"
    reply = "CPU à 12%, température 45°C."

    temp_cache.set(query, reply, ttl=0.1)
    # Immédiat -> présent
    assert temp_cache.get(query) is not None

    time.sleep(0.15)
    # Après expiration -> None
    assert temp_cache.get(query) is None


def test_cache_invalidation_by_category(temp_cache: SmartCommandCache):
    """Vérifie l'invalidation chirurgicale par catégorie."""
    temp_cache.set("liste des tâches", "3 tâches en cours pour Klasora.", category="tasks")
    temp_cache.set("statut docker", "Tous les conteneurs sont actifs.", category="docker")

    assert temp_cache.get("mes tâches") is not None
    assert temp_cache.get("statut docker") is not None

    # Invalidation des tâches uniquement
    purged = temp_cache.invalidate_category("tasks")
    assert purged >= 1

    # Les tâches ne sont plus en cache, mais docker est toujours là
    assert temp_cache.get("mes tâches") is None
    assert temp_cache.get("statut docker") is not None


def test_uncacheable_responses(temp_cache: SmartCommandCache):
    """Vérifie que les réponses dégradées ou d'erreur ne sont jamais mises en cache."""
    assert temp_cache.set("test", "Mode dégradé : API primaire et secours injoignables") is False
    assert temp_cache.set("test", "[DEGRADE] Indisponible") is False
    assert temp_cache.set("test", "Pardon Moctar, je n'ai pas bien compris votre phrase.") is False
    assert temp_cache.set("test", "Le moteur IA est indisponible pour le moment.") is False
    assert temp_cache.get("test") is None


def test_daily_frequencies_and_hot_queries(temp_cache: SmartCommandCache):
    """Vérifie l'indexation quotidienne et la détection des hot queries."""
    query = "météo rabat"
    assert temp_cache.is_hot(query) is False

    # Première occurrence
    c1 = temp_cache.record_query(query)
    assert c1 == 1
    assert temp_cache.is_hot(query) is False

    # Deuxième occurrence -> devient "hot"
    c2 = temp_cache.record_query("donne-moi la météo rabat")
    assert c2 == 2
    assert temp_cache.is_hot(query) is True

    freqs = temp_cache.get_daily_frequencies()
    assert len(freqs) >= 1
    assert freqs[0]["canonical_key"] == "weather:rabat"
    assert freqs[0]["count"] >= 2


def test_cache_stats(temp_cache: SmartCommandCache):
    """Vérifie le calcul des métriques de hits/misses."""
    temp_cache.set("statut git", "Sur branche main, working tree clean.", category="git")
    temp_cache.get("statut git")  # Hit
    temp_cache.get("statut git")  # Hit
    temp_cache.get("inconnu")     # Miss

    stats = temp_cache.get_stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["hit_rate_percent"] == 66.67
    assert stats["categories"].get("git") == 1
