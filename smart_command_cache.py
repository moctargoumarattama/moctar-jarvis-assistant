"""SmartCommandCache : Moteur d'indexation sémantique, fréquence quotidienne et mise en cache intelligente (< 1ms).

Permet de :
1. Normaliser et indexer sémantiquement les requêtes quotidiennes de Moctar (météo, services, docker, tâches, télémétrie, briefing).
2. Fournir un cache hybride (ultra-rapide en mémoire + persistant SQLite) avec TTL adaptatif par catégorie.
3. Mesurer la récurrence quotidienne des requêtes pour détecter les commandes "chaudes" (hot queries) et préserver les quotas LLM.
4. Supporter l'invalidation chirurgicale par catégorie lors de modifications (ex: tâches, services, git).
"""

from __future__ import annotations

import json
import logging
import re
import contextlib
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import assistant_memory

LOGGER = logging.getLogger("jarvis.smart_cache")

# Durées de vie adaptatives selon la volatilité de l'information (en secondes)
DEFAULT_CATEGORY_TTLS: dict[str, float] = {
    "telemetry": 10.0,      # Télémétrie hardware / cpu / ram (très volatile)
    "services": 30.0,       # Statut des services systemd (invalidable lors d'un restart)
    "docker": 30.0,         # Statut des conteneurs docker
    "git": 30.0,            # Statut git / commits récents
    "tasks": 60.0,          # Tâches / todos (invalidable instantanément sur mutation)
    "briefing": 300.0,      # Résumé quotidien / briefing (5 minutes)
    "weather": 900.0,       # Météo (15 minutes)
    "info": 86400.0,        # Identité, profil, capacités statiques (24 heures)
    "general": 180.0,       # Requêtes générales répétées (3 minutes)
}

# Réponses dégradées, questions de clarification ou erreurs à ne JAMAIS mettre en cache
UNCACHEABLE_PATTERNS = (
    "mode dégradé",
    "mode degrade",
    "[degrade]",
    "[indisponible]",
    "moteur ia est indisponible",
    "erreur interne",
    "service unavailable",
    "pas bien compris",
    "pouvez-vous répéter",
    "pouvez vous repeter",
    "peux-tu répéter",
    "peux tu repeter",
    "tu peux répéter",
    "tu peux repeter",
    "pas bien capté",
    "pas bien capte",
    "peux me redire",
    "peux-tu me redire",
    "peux tu me redire",
    "tu peux me redire",
    "redire",
    "répéter",
    "pas compris",
    "pas saisi",
    "micro-coupure",
    "initialisation du flux audio",
    "votre playlist est lancée",
    "lecture mise en pause",
    "reprise de la lecture",
    "niveau audio ajusté",
    "capteur neurovision",
)


def _strip_accents(text: str) -> str:
    """Retire les accents et signes diacritiques."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFD", text)
    return "".join(c for c in norm if unicodedata.category(c) != "Mn")


def canonicalize_query(query: str) -> Tuple[str, str, Dict[str, Any]]:
    """Normalise et indexe une requête pour en extraire la clé canonique et la catégorie.

    Returns:
        (canonical_key, category, metadata)
    """
    if not query:
        return ("empty", "general", {})

    raw = str(query).strip()
    norm = _strip_accents(raw).lower()
    # Supprime la ponctuation
    clean = re.sub(r"[^\w\s]", " ", norm)
    clean = re.sub(r"\s+", " ", clean).strip()

    # Retrait des formules de politesse, wake words et verbes introductifs superflus
    prefixes_to_strip = [
        r"^(?:yo|salut|bonjour|bonsoir|hey|hello)\s+(?:jarvis|moctar|l['’]ami|mon pote|copilote)\b",
        r"^(?:jarvis|moctar)\b",
        r"^(?:dis[- ]moi|peux[- ]tu\s+me\s+dire|peux[- ]tu\s+me\s+donner|donne[- ]moi|donne moi|fais[- ]moi|fais moi)\b",
        r"^(?:affiche[- ]moi|montre[- ]moi|affiche|montre|cherche[- ]moi|cherche)\b",
        r"^(?:s['’]il\s+te\s+pla[iî]t|s['’]il\s+vous\s+pla[iî]t|stp)\b",
        r"^(?:quel\s+est|quelle\s+est|quels\s+sont|quelles\s+sont|c['’]est\s+quoi|est[- ]ce\s+que)\b",
    ]
    for pat in prefixes_to_strip:
        clean = re.sub(pat, "", clean).strip()

    # Nettoyage final
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        clean = norm

    metadata: Dict[str, Any] = {}

    # 1. Catégorie MÉTÉO
    if any(k in clean for k in ("meteo", "temps fait il", "temps qu il fait", "temperature", "quel temps", "prevision")):
        city = "rabat"  # Ville par défaut de référence
        known_cities = ("rabat", "niamey", "casablanca", "marrakech", "tanger", "paris", "dakar", "lyon", "agadir", "abidjan")
        for c in known_cities:
            if c in clean:
                city = c
                break
        metadata["city"] = city
        return (f"weather:{city}", "weather", metadata)

    # 2. Catégorie SERVICES DEVOPS / SYSTEMCTL
    if any(k in clean for k in ("services", "systemctl", "service jarvis", "services actifs", "etat des services", "statut des services")):
        if "restart" not in clean and "redemarre" not in clean and "relance" not in clean and "stop" not in clean:
            return ("system:services", "services", metadata)

    # 3. Catégorie DOCKER / CONTENEURS
    if any(k in clean for k in ("docker", "conteneur", "conteneurs", "container", "containers", "docker ps")):
        if "stop" not in clean and "kill" not in clean and "restart" not in clean and "rm" not in clean:
            return ("docker:status", "docker", metadata)

    # 4. Catégorie TÉLÉMÉTRIE / HARDWARE / PERFORMANCES
    if any(k in clean for k in (
        "telemetrie", "charge cpu", "utilisation memoire", "ram libre", "ram utilisee",
        "charge systeme", "sante machine", "sante du pc", "temperature cpu", "etat de la machine",
        "cpu ram", "memoire vive"
    )):
        return ("system:telemetry", "telemetry", metadata)

    # 5. Catégorie TÂCHES / TODOS
    if any(k in clean for k in (
        "taches", "todos", "todo", "qu est ce que j ai a faire", "choses a faire",
        "todo list", "mes todos", "mes taches", "liste des taches"
    )):
        if "ajoute" not in clean and "cree" not in clean and "supprime" not in clean and "termine" not in clean:
            return ("tasks:list", "tasks", metadata)

    # 6. Catégorie BRIEFING / RÉSUMÉ QUOTIDIEN
    if any(k in clean for k in ("briefing", "point du jour", "resume du jour", "briefing quotidien", "briefing matinal")):
        return ("briefing:daily", "briefing", metadata)

    # 7. Catégorie GIT / ÉTAT DU REPOSITORY
    if any(k in clean for k in ("statut git", "git status", "dernier commit", "branche courante", "git log")):
        if "commit" not in clean and "push" not in clean and "pull" not in clean:
            return ("git:status", "git", metadata)

    # 8. Catégorie IDENTITÉ / CAPACITÉS
    if any(k in clean for k in (
        "qui es tu", "comment tu t appelles", "qui t a cree", "qui est ton createur",
        "presente toi", "ta version", "qui est moctar", "tu es capable de faire quoi", "tes capacites"
    )):
        return (f"info:{clean.replace(' ', '_')[:32]}", "info", metadata)

    # Catégorie générale avec tokenisation compacte
    tokens = [t for t in clean.split() if len(t) > 2]
    compact_key = "_".join(tokens[:6]) if tokens else clean[:32]
    return (f"query:{compact_key}", "general", metadata)


class SmartCommandCache:
    """Gestionnaire de cache intelligent pour commandes fréquentes et indexation quotidienne."""

    _instance: Optional[SmartCommandCache] = None
    _singleton_lock = threading.Lock()

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = Path(db_path or assistant_memory._DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        # Cache en mémoire : {canonical_key: dict}
        self._cache: Dict[str, Dict[str, Any]] = {}

        # Fréquence en mémoire : {(date_str, canonical_key): count}
        self._frequencies: Dict[Tuple[str, str], int] = {}

        # Métriques
        self._hits = 0
        self._misses = 0

        self._init_db()
        self._load_persisted_cache()
        LOGGER.info("SmartCommandCache initialisé avec succès sur %s", self.db_path)

    @classmethod
    def get_instance(cls, db_path: Optional[str | Path] = None) -> SmartCommandCache:
        """Retourne l'instance singleton du SmartCommandCache."""
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(db_path=db_path)
            return cls._instance

    @contextlib.contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _init_db(self) -> None:
        """Crée les tables SQLite de persistance et d'indexation."""
        with self._lock, self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS command_cache (
                    canonical_key TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    query_pattern TEXT NOT NULL,
                    reply_text TEXT NOT NULL,
                    agent_result_json TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    last_accessed REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_command_cache_category
                ON command_cache(category)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_frequency (
                    date_str TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    raw_query TEXT NOT NULL,
                    count INTEGER DEFAULT 1,
                    last_seen REAL NOT NULL,
                    PRIMARY KEY (date_str, canonical_key)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_freq_date
                ON query_frequency(date_str)
            """)
            conn.commit()

    def _load_persisted_cache(self) -> None:
        """Charge en mémoire les entrées valides non expirées depuis SQLite."""
        now = time.monotonic()
        wall_now = time.time()
        with self._lock, self._connection() as conn:
            rows = conn.execute("""
                SELECT canonical_key, category, query_pattern, reply_text,
                       agent_result_json, created_at, expires_at, hit_count
                FROM command_cache
                WHERE expires_at > ?
            """, (wall_now,)).fetchall()

            for r in rows:
                agent_res = None
                if r["agent_result_json"]:
                    try:
                        agent_res = json.loads(r["agent_result_json"])
                    except Exception:
                        pass
                remaining_ttl = max(1.0, r["expires_at"] - wall_now)
                self._cache[r["canonical_key"]] = {
                    "category": r["category"],
                    "query_pattern": r["query_pattern"],
                    "reply": r["reply_text"],
                    "agent_result": agent_res,
                    "created_at": now,
                    "expires_at": now + remaining_ttl,
                    "hit_count": r["hit_count"],
                    "last_accessed": now,
                }
            LOGGER.info("SmartCommandCache : %d entrées actives restaurées depuis SQLite", len(self._cache))

    def record_query(self, query: str) -> int:
        """Enregistre l'occurrence d'une requête et retourne la fréquence du jour."""
        canonical_key, _, _ = canonicalize_query(query)
        date_str = datetime.now().strftime("%Y-%m-%d")
        now_ts = time.time()

        with self._lock:
            # 1. Mise à jour mémoire
            freq_key = (date_str, canonical_key)
            self._frequencies[freq_key] = self._frequencies.get(freq_key, 0) + 1
            new_count = self._frequencies[freq_key]

            # 2. Persistance SQLite
            try:
                with self._connection() as conn:
                    conn.execute("""
                        INSERT INTO query_frequency (date_str, canonical_key, raw_query, count, last_seen)
                        VALUES (?, ?, ?, 1, ?)
                        ON CONFLICT(date_str, canonical_key) DO UPDATE SET
                            count = count + 1,
                            raw_query = excluded.raw_query,
                            last_seen = excluded.last_seen
                    """, (date_str, canonical_key, query[:150], now_ts))
                    conn.commit()
            except Exception as exc:
                LOGGER.debug("Échec enregistrement fréquence requête SQLite: %s", exc)

            return new_count

    def is_hot(self, query: str) -> bool:
        """Détermine si une commande est fréquemment demandée aujourd'hui (count >= 2)."""
        canonical_key, _, _ = canonicalize_query(query)
        date_str = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            freq_key = (date_str, canonical_key)
            if freq_key in self._frequencies:
                return self._frequencies[freq_key] >= 2
            try:
                with self._connection() as conn:
                    row = conn.execute("""
                        SELECT count FROM query_frequency
                        WHERE date_str = ? AND canonical_key = ?
                    """, (date_str, canonical_key)).fetchone()
                    if row:
                        cnt = int(row["count"])
                        self._frequencies[freq_key] = cnt
                        return cnt >= 2
            except Exception:
                pass
            return False

    def get(self, query: str) -> Optional[Dict[str, Any]]:
        """Récupère une réponse en cache (< 1ms). Retourne None si expiré ou absent."""
        canonical_key, category, _ = canonicalize_query(query)
        now = time.monotonic()

        with self._lock:
            entry = self._cache.get(canonical_key)
            if not entry:
                self._misses += 1
                return None

            # Vérification expiration
            if now >= entry["expires_at"]:
                # Expiré : purge propre
                del self._cache[canonical_key]
                self._misses += 1
                try:
                    with self._connection() as conn:
                        conn.execute("DELETE FROM command_cache WHERE canonical_key = ?", (canonical_key,))
                        conn.commit()
                except Exception:
                    pass
                return None

            # Hit valide !
            entry["hit_count"] += 1
            entry["last_accessed"] = now
            self._hits += 1

            # Mise à jour asynchrone / discrète du hit_count dans SQLite
            try:
                with self._connection() as conn:
                    conn.execute("""
                        UPDATE command_cache
                        SET hit_count = hit_count + 1, last_accessed = ?
                        WHERE canonical_key = ?
                    """, (time.time(), canonical_key))
                    conn.commit()
            except Exception:
                pass

            LOGGER.debug(
                "⚡ [SMART CACHE HIT] '%s' (cle=%s, cat=%s, hits=%d)",
                query, canonical_key, category, entry["hit_count"],
            )

            return {
                "reply": entry["reply"],
                "agent_result": entry.get("agent_result"),
                "category": entry["category"],
                "canonical_key": canonical_key,
                "hit_count": entry["hit_count"],
                "from_cache": True,
            }

    def set(
        self,
        query: str,
        reply: str,
        category: Optional[str] = None,
        agent_result: Optional[Dict[str, Any]] = None,
        ttl: Optional[float] = None,
    ) -> bool:
        """Enregistre ou rafraîchit une réponse dans le cache intelligent."""
        if not query or not reply:
            return False

        reply_clean = reply.strip()
        low_reply = reply_clean.lower()

        # Ne jamais mettre en cache les réponses d'erreur, indisponibilité ou incompréhension
        if any(bad in low_reply for bad in UNCACHEABLE_PATTERNS):
            return False

        canonical_key, deduced_cat, _ = canonicalize_query(query)
        cat = category or deduced_cat

        # Ne jamais mettre en cache les actions de contrôle ou d'exécution (media, snap, volume, system tools)
        NON_CACHEABLE_CATEGORIES = {"media", "system_tools", "workspace_layout", "window", "apps", "neurovision"}
        if cat in NON_CACHEABLE_CATEGORIES or (category and category in NON_CACHEABLE_CATEGORIES):
            return False

        # Ne jamais mettre en cache un résultat contenant une action ou des outils de mutation
        if agent_result and isinstance(agent_result, dict):
            if agent_result.get("action"):
                return False
            tools = agent_result.get("tools_used") or []
            mutating_tools = {
                "control_playback", "launch_playlist", "snap_active_window",
                "close_active_window", "close_app", "adjust_system_volume",
            }
            if any(isinstance(t, dict) and t.get("tool") in mutating_tools for t in tools):
                return False

        # Détermination du TTL
        if ttl is not None:
            chosen_ttl = max(0.01, float(ttl))
        else:
            chosen_ttl = DEFAULT_CATEGORY_TTLS.get(cat, DEFAULT_CATEGORY_TTLS["general"])

        now = time.monotonic()
        expires_at = now + chosen_ttl
        wall_now = time.time()
        wall_expires_at = wall_now + chosen_ttl

        agent_json = None
        if agent_result and isinstance(agent_result, dict):
            try:
                # Éviter de sérialiser des objets non JSON
                clean_dict = {
                    k: v for k, v in agent_result.items()
                    if k not in ("thought_chain", "raw_response") and isinstance(v, (str, int, float, bool, list, dict, type(None)))
                }
                agent_json = json.dumps(clean_dict)
            except Exception:
                agent_json = None

        with self._lock:
            existing_hits = self._cache.get(canonical_key, {}).get("hit_count", 0)
            self._cache[canonical_key] = {
                "category": cat,
                "query_pattern": query[:150],
                "reply": reply_clean,
                "agent_result": agent_result,
                "created_at": now,
                "expires_at": expires_at,
                "hit_count": existing_hits,
                "last_accessed": now,
            }

            try:
                with self._connection() as conn:
                    conn.execute("""
                        INSERT INTO command_cache (
                            canonical_key, category, query_pattern, reply_text,
                            agent_result_json, created_at, expires_at, hit_count, last_accessed
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(canonical_key) DO UPDATE SET
                            category = excluded.category,
                            query_pattern = excluded.query_pattern,
                            reply_text = excluded.reply_text,
                            agent_result_json = excluded.agent_result_json,
                            expires_at = excluded.expires_at,
                            last_accessed = excluded.last_accessed
                    """, (
                        canonical_key, cat, query[:150], reply_clean,
                        agent_json, wall_now, wall_expires_at, existing_hits, wall_now,
                    ))
                    conn.commit()
            except Exception as exc:
                LOGGER.debug("Échec persistance cache SQLite: %s", exc)

        LOGGER.info(
            "⚡ [SMART CACHE SET] Clé '%s' (cat=%s, ttl=%.0fs): '%s'",
            canonical_key, cat, chosen_ttl, reply_clean[:60],
        )
        return True

    def invalidate_category(self, category: str) -> int:
        """Invalide chirurgicalement toutes les entrées d'une catégorie donnée."""
        cat_lower = str(category).strip().lower()
        purged = 0
        with self._lock:
            keys_to_del = [k for k, v in self._cache.items() if v.get("category") == cat_lower]
            for k in keys_to_del:
                del self._cache[k]
                purged += 1

            try:
                with self._connection() as conn:
                    cur = conn.execute("DELETE FROM command_cache WHERE category = ?", (cat_lower,))
                    conn.commit()
                    purged = max(purged, cur.rowcount)
            except Exception as exc:
                LOGGER.debug("Échec invalidation catégorie SQLite: %s", exc)

        LOGGER.info("⚡ [SMART CACHE INVALIDATE] Catégorie '%s' : %d entrées purgées", cat_lower, purged)
        return purged

    def invalidate_key(self, canonical_key: str) -> bool:
        """Invalide une clé canonique spécifique."""
        with self._lock:
            deleted = self._cache.pop(canonical_key, None) is not None
            try:
                with self._connection() as conn:
                    conn.execute("DELETE FROM command_cache WHERE canonical_key = ?", (canonical_key,))
                    conn.commit()
                    deleted = True
            except Exception:
                pass
        return deleted

    def clear(self) -> int:
        """Vide l'intégralité du cache."""
        with self._lock:
            total = len(self._cache)
            self._cache.clear()
            try:
                with self._connection() as conn:
                    conn.execute("DELETE FROM command_cache")
                    conn.commit()
            except Exception:
                pass
            return total

    def get_stats(self) -> Dict[str, Any]:
        """Retourne les métriques de performance du cache."""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100.0) if total_requests > 0 else 0.0
            categories = {}
            for v in self._cache.values():
                cat = v.get("category", "general")
                categories[cat] = categories.get(cat, 0) + 1

            return {
                "active_cached_entries": len(self._cache),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate_percent": round(hit_rate, 2),
                "categories": categories,
            }

    def get_daily_frequencies(self, date_str: Optional[str] = None, limit: int = 15) -> List[Dict[str, Any]]:
        """Liste les requêtes les plus posées du jour pour analyse d'optimisation."""
        target_date = date_str or datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            try:
                with self._connection() as conn:
                    rows = conn.execute("""
                        SELECT canonical_key, raw_query, count, last_seen
                        FROM query_frequency
                        WHERE date_str = ?
                        ORDER BY count DESC
                        LIMIT ?
                    """, (target_date, limit)).fetchall()
                    return [
                        {
                            "canonical_key": r["canonical_key"],
                            "raw_query": r["raw_query"],
                            "count": r["count"],
                            "last_seen": r["last_seen"],
                        }
                        for r in rows
                    ]
            except Exception as exc:
                LOGGER.debug("Échec lecture fréquences quotidiennes : %s", exc)
                return []


def get_command_cache(db_path: Optional[str | Path] = None) -> SmartCommandCache:
    """Accesseur standard pour le singleton SmartCommandCache."""
    return SmartCommandCache.get_instance(db_path=db_path)
