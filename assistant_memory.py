"""Système de mémoire duale pour JARVIS (Court Terme + Sémantique Long Terme).

Fournit :
1. Mémoire de Travail (Court Terme / Sliding Window) : Gestion fluide d'un historique
   glissant token-aware pour éviter tout débordement de contexte.
2. Mémoire Sémantique Long Terme (Persistante locale SQLite) : Stockage et recherche
   sémantique par mots-clés pondérés (BM25 / Token Overlap) des faits techniques et préférences.
3. Rétrocompatibilité totale avec les composants historiques (SessionMemory, UserMemoryStore).
"""

from collections import Counter
from datetime import datetime
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import config

logger = logging.getLogger("jarvis.memory")

_DEFAULT_DB_PATH = config.DATA_DIR / "jarvis_memory.db"

_STOPWORDS: Set[str] = {
    "le", "la", "les", "un", "une", "des", "du", "de", "d", "l", "et", "ou",
    "en", "a", "au", "aux", "pour", "dans", "sur", "par", "avec", "sans",
    "sous", "ce", "cet", "cette", "ces", "mon", "ton", "son", "ma", "ta", "sa",
    "mes", "tes", "ses", "notre", "votre", "leur", "nos", "vos", "leurs",
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
    "qui", "que", "quoi", "dont", "ou", "est", "sont", "ete", "etre", "avoir",
    "fait", "faire", "plus", "moins", "tres", "bien", "aussi", "the", "a", "an",
    "and", "or", "in", "on", "at", "to", "for", "of", "with", "is", "are", "was",
}


def _tokenize(text: str) -> List[str]:
    """Tokenise, normalise et filtre les stopwords d'un texte."""
    if not text:
        return []
    cleaned = re.sub(r"[^\w\s-]", " ", text.lower())
    tokens = [t for t in re.split(r"[\s_-]+", cleaned) if len(t) > 1 and t not in _STOPWORDS]
    return tokens


from contextlib import contextmanager

DEFAULT_MOCTAR_PROFILE: Dict[str, Any] = {
    "name": "Moctar",
    "hardware": "Dell Precision M4800 (Linux Ubuntu)",
    "role": "Ingénieur & Pilote des systèmes",
    "tone_preference": "Naturel, chaleureux, direct, concis et complice",
    "preferred_editor": "VS Code",
    "preferred_shell": "bash / zsh",
    "favorite_sports_teams": ["Real Madrid"],
    "favorite_competitions": ["Ligue 1", "Champions League", "Liga", "Premier League"],
    "recent_topics": [],
    "known_preferences": {},
    "last_interaction": None,
    "total_turns": 0,
}


class AssistantMemory:
    """Gestionnaire de mémoire duale industrielle (SQLite locale + historique glissant)."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        self.db_path = Path(db_path or _DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info("AssistantMemory connectée à %s", self.db_path)

    @contextmanager
    def _get_connection(self):
        """Crée une connexion SQLite fermée automatiquement à la fin du bloc."""
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        try:
            conn.execute("PRAGMA busy_timeout = 5000;")
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialise le schéma de base de données SQLite de façon idempotente."""
        with self._get_connection() as conn:
            try:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
            except Exception as pragma_err:
                logger.debug("Pragma journal_mode ignore: %s", pragma_err)
            cursor = conn.cursor()
            # Table 1 : Faits et connaissances persistantes à long terme
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding_or_keywords TEXT,
                    importance_score INTEGER DEFAULT 1,
                    UNIQUE(category, key)
                )
                """
            )

            # Table 2 : Historique de conversations multi-tours pour la mémoire de travail
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT
                )
                """
            )

            # Table 3 : Métadonnées et thèmes des sessions de conversation
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    summary TEXT
                )
                """
            )

            # Index d'optimisation pour requêtes rapides
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_cat_key ON memories(category, key)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_session ON conversation_history(session_id, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_sessions_updated ON conversation_sessions(updated_at DESC)")

            # Synchronisation automatique idempotente pour sessions existantes
            cursor.execute(
                """
                INSERT OR IGNORE INTO conversation_sessions (session_id, title, created_at, updated_at, message_count)
                SELECT
                    session_id,
                    COALESCE(
                        NULLIF(
                            (SELECT substr(content, 1, 60) FROM conversation_history WHERE session_id = h.session_id AND role = 'user' ORDER BY id ASC LIMIT 1),
                            ''
                        ),
                        'Session ' || substr(session_id, 1, 8)
                    ) AS title,
                    MIN(timestamp) AS created_at,
                    MAX(timestamp) AS updated_at,
                    COUNT(*) AS message_count
                FROM conversation_history h
                GROUP BY session_id
                """
            )
            conn.commit()

    # ==========================================================================
    # 1. MÉMOIRE SÉMANTIQUE LONG TERME (FAITS, PRÉFÉRENCES, CONFIGURATIONS)
    # ==========================================================================

    def save_fact(
        self,
        category: str,
        key: str,
        value: str,
        importance: int = 1,
    ) -> bool:
        """Enregistre ou met à jour un fait dans la mémoire permanente (upsert).

        Args:
            category: Domaine du fait (ex: 'system', 'preferences', 'iot', 'network').
            key: Identifiant concis du fait (ex: 'ip_vps', 'ssh_port', 'favorite_theme').
            value: Contenu informatif détaillé du fait.
            importance: Niveau de priorité de 1 (standard) à 5 (critique).

        Returns:
            bool: True si l'opération a réussi, False sinon.
        """
        normalized_cat = category.strip().lower() or "general"
        normalized_key = key.strip().lower()
        if not normalized_key or not value:
            logger.warning("Tentative d'enregistrement d'un fait invalide (clé ou valeur vide).")
            return False

        # Extraction des mots-clés d'indexation
        keywords = " ".join(_tokenize(f"{normalized_cat} {normalized_key} {value}"))
        now_iso = datetime.now().isoformat(timespec="seconds")

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO memories (timestamp, category, key, content, embedding_or_keywords, importance_score)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(category, key) DO UPDATE SET
                        content = excluded.content,
                        timestamp = excluded.timestamp,
                        embedding_or_keywords = excluded.embedding_or_keywords,
                        importance_score = excluded.importance_score
                    """,
                    (now_iso, normalized_cat, normalized_key, value.strip(), keywords, int(importance)),
                )
                conn.commit()
            logger.info("Fait mémorisé avec succès : [%s] %s = %s", normalized_cat, normalized_key, value.strip())
            return True
        except Exception as exc:
            logger.exception("Erreur lors de l'enregistrement du fait dans memories : %s", exc)
            return False

    def save_daily_episode(
        self,
        topic: str,
        summary: str,
        source: str = "desktop",
        tags: Optional[List[str]] = None,
        importance: int = 2,
    ) -> bool:
        """Enregistre un épisode quotidien de travail ou de vie pour la mémoire vivante.
        
        Permet à M.O.C.T.A.R de se souvenir des sujets de la veille, des échanges sur WhatsApp,
        ou du travail en cours pour accueillir Moctar de façon personnalisée et proactive.
        """
        clean_topic = (topic or "").strip()
        clean_summary = (summary or "").strip()
        if not clean_topic:
            return False
        now_dt = datetime.now()
        timestamp_key = now_dt.strftime("%Y%m%d_%H%M%S")
        key = f"ep_{timestamp_key}"
        data = {
            "topic": clean_topic,
            "summary": clean_summary or clean_topic,
            "source": source,
            "tags": tags or [],
            "timestamp": now_dt.isoformat(timespec="seconds"),
        }
        val_str = json.dumps(data, ensure_ascii=False)
        return self.save_fact(category="daily_episode", key=key, value=val_str, importance=importance)

    def get_latest_daily_episode(self) -> Optional[Dict[str, Any]]:
        """Récupère le dernier épisode quotidien enregistré."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT timestamp, key, content, importance_score FROM memories "
                    "WHERE category = 'daily_episode' ORDER BY timestamp DESC LIMIT 1"
                )
                row = cursor.fetchone()
                if not row:
                    return None
                try:
                    data = json.loads(row[2])
                    if isinstance(data, dict):
                        return data
                except Exception:
                    return {"topic": row[2], "summary": row[2], "source": "desktop", "timestamp": row[0]}
        except Exception as exc:
            logger.error("Erreur lors de la lecture du dernier épisode quotidien : %s", exc)
        return None

    def get_recent_daily_episodes(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Récupère la liste des épisodes récents ordonnés chronologiquement inversé."""
        episodes = []
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT timestamp, key, content FROM memories "
                    "WHERE category = 'daily_episode' ORDER BY timestamp DESC LIMIT ?",
                    (int(limit),)
                )
                rows = cursor.fetchall()
                for r in rows:
                    try:
                        d = json.loads(r[2])
                        if isinstance(d, dict):
                            episodes.append(d)
                    except Exception:
                        episodes.append({"topic": r[2], "summary": r[2], "timestamp": r[0]})
        except Exception as exc:
            logger.error("Erreur lors de la lecture des épisodes récents : %s", exc)
        return episodes

    def build_episodic_greeting(self, hour: Optional[int] = None) -> Optional[str]:
        """Formule une salutation contextuelle et vivante basée sur le dernier épisode."""
        latest = self.get_latest_daily_episode()
        if not latest or not latest.get("topic"):
            return None
        topic = latest.get("topic", "").strip()
        source = latest.get("source", "desktop")
        ts_str = latest.get("timestamp")
        diff_hours = 0.0
        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str)
                diff_hours = (datetime.now() - dt).total_seconds() / 3600.0
            except Exception:
                diff_hours = 12.0

        if hour is None:
            try:
                hour = datetime.now().hour
            except Exception:
                hour = 12

        # 1. Continuité WhatsApp récente (< 4 heures)
        if source == "whatsapp" and diff_hours < 4.0:
            return f"Sur WhatsApp tout à l'heure, on parlait de {topic}. Tu veux qu'on reprenne ça, Moctar ?"

        # 2. Continuité matinale de la veille (travail d'hier soir)
        if 6 <= hour < 12 and 6.0 <= diff_hours <= 24.0:
            return f"Hier soir on travaillait sur {topic}. On continue là-dessus ce matin, Moctar ?"

        # 3. Épisode récent (< 6h)
        if diff_hours < 6.0:
            return f"On avançait sur {topic}. On continue là-dessus, Moctar ?"

        return None

    # ==========================================================================
    # 1b. PROFIL CONTINU DE MOCTAR (APPRENTISSAGE AUTONOME MULTI-CONVERSATIONS)
    # ==========================================================================

    def get_moctar_profile(self) -> Dict[str, Any]:
        """Récupère le profil vivant de Moctar ou initialise le profil par défaut."""
        default_profile = dict(DEFAULT_MOCTAR_PROFILE)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT content FROM memories WHERE category = 'user_profile' AND key = 'moctar' LIMIT 1"
                )
                row = cursor.fetchone()
                if row and row[0]:
                    try:
                        data = json.loads(row[0])
                        if isinstance(data, dict):
                            # Fusion sur les valeurs par défaut pour garantir la présence de toutes les clés
                            for k, v in default_profile.items():
                                if k not in data:
                                    data[k] = v
                            # Purge automatique des topics pollués par d'anciennes transcriptions brutes Whisper
                            raw_topics = data.get("recent_topics")
                            if isinstance(raw_topics, list):
                                polluted_markers = (
                                    "ventraler", "return arm", "hot-trading", "hot trading",
                                    "guide push", "gens de la", "gens de", "ça va", "ca va",
                                    "git commun", "yo ", "pardon moctar",
                                )
                                cleaned_topics = [
                                    t for t in raw_topics
                                    if isinstance(t, str)
                                    and not any(pm in t.lower() for pm in polluted_markers)
                                    and len(t) <= 35
                                ]
                                if len(cleaned_topics) != len(raw_topics):
                                    data["recent_topics"] = cleaned_topics
                                    try:
                                        self.save_fact(
                                            category="user_profile",
                                            key="moctar",
                                            value=json.dumps(data, ensure_ascii=False),
                                            importance=5,
                                        )
                                    except Exception:
                                        pass
                            return data
                    except Exception:
                        pass
        except Exception as exc:
            logger.error("Erreur lors de la lecture du profil Moctar : %s", exc)

        # Initialisation idempotente si absent
        self.save_fact(
            category="user_profile",
            key="moctar",
            value=json.dumps(default_profile, ensure_ascii=False),
            importance=5,
        )
        return default_profile

    def update_moctar_profile(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Met à jour le profil persistant de Moctar avec fusion incrémentale."""
        current = self.get_moctar_profile()
        for k, v in updates.items():
            current[k] = v
        current["last_interaction"] = datetime.now().isoformat(timespec="seconds")
        val_str = json.dumps(current, ensure_ascii=False)
        self.save_fact(category="user_profile", key="moctar", value=val_str, importance=5)
        return current

    def record_conversation_turn_for_profile(self, user_text: str, assistant_reply: str) -> None:
        """Met à jour le profil vivant de Moctar de façon autonome après chaque échange."""
        if not user_text or not user_text.strip():
            return

        profile = self.get_moctar_profile()
        profile["total_turns"] = int(profile.get("total_turns", 0)) + 1
        profile["last_interaction"] = datetime.now().isoformat(timespec="seconds")

        clean_user = user_text.strip()
        low_user = clean_user.lower()

        # 1. Détection du sujet principal canonique pour l'historique récent des thèmes (jamais de texte brut arbitraire)
        topic = None
        if any(w in low_user for w in ("foot", "match", "ligue 1", "champions league", "liga", "score", "real madrid", "classement")):
            topic = "Résultats sportifs"
        elif any(w in low_user for w in ("papa market", "baba market", "babamarket", "papamarket")):
            topic = "Projet Baba Market"
        elif any(w in low_user for w in ("code", "vscode", "dev", "python", "script", "terminal", "bureau", "git", "commit", "push", "pull", "docker")):
            topic = "Développement & VS Code"
        elif any(w in low_user for w in ("meteo", "météo", "temperature", "température", "niamey", "casablanca")):
            topic = "Météo"
        elif any(w in low_user for w in ("hud", "globe", "orbe", "terre", "3d", "plasma", "quantum", "localise", "position")):
            topic = "Interface HUD 3D"
        elif any(w in low_user for w in ("cpu", "ram", "service", "systemctl", "vps", "processus", "disque", "noc", "dashboard")):
            topic = "Surveillance système NOC"
        elif any(w in low_user for w in ("musique", "chanson", "volume", "pause", "zik", "audio")):
            topic = "Multimédia & Audio"
        elif any(w in low_user for w in ("solaire", "batterie", "onduleur", "energie", "énergie", "audit")):
            topic = "Génie énergétique & Solaire"

        if topic:
            recent = profile.get("recent_topics", [])
            if not isinstance(recent, list):
                recent = []
            if topic in recent:
                recent.remove(topic)
            recent.insert(0, topic)
            profile["recent_topics"] = recent[:5]

        # 2. Détection de préférences ou clubs sportifs mentionnés
        fav_teams = set(profile.get("favorite_sports_teams", ["Real Madrid"]))
        team_patterns = {
            "real madrid": "Real Madrid",
            "barça": "FC Barcelone",
            "barcelone": "FC Barcelone",
            "psg": "PSG",
            "paris": "PSG",
            "marseille": "OM",
            "om": "OM",
            "arsenal": "Arsenal",
            "manchester city": "Manchester City",
            "liverpool": "Liverpool",
            "bayern": "Bayern Munich",
        }
        for pat, canonical in team_patterns.items():
            if re.search(rf"\b(?:mon\s+club|mon\s+[ée]quipe|je\s+supporte|j['’]aime|fan\s+du)\s+.*{pat}\b", low_user) or (
                pat in low_user and any(w in low_user for w in ("préféré", "prefere", "supporte"))
            ):
                fav_teams.add(canonical)
        profile["favorite_sports_teams"] = list(fav_teams)

        # 3. Préférences d'outils et de villes
        known_prefs = profile.get("known_preferences", {})
        if "vscode" in low_user or "visual studio code" in low_user:
            known_prefs["editor"] = "VS Code"
        if "chrome" in low_user:
            known_prefs["browser"] = "Chrome"
        if any(w in low_user for w in ("niamey", "niger")):
            known_prefs["primary_city"] = "Niamey"
        elif any(w in low_user for w in ("casablanca", "casa", "maroc")):
            known_prefs["secondary_city"] = "Casablanca"
        profile["known_preferences"] = known_prefs

        # Enregistrement persistant autonome
        self.update_moctar_profile(profile)

    def get_moctar_profile_prompt_snippet(self) -> str:
        """Génère un bloc textuel contextuel et vivant du profil de Moctar pour injection LLM."""
        profile = self.get_moctar_profile()
        recent_topics = profile.get("recent_topics", [])
        topics_str = ", ".join(recent_topics) if recent_topics else "Général"
        teams = ", ".join(profile.get("favorite_sports_teams", ["Real Madrid"]))
        turn_count = profile.get("total_turns", 0)

        lines = [
            "[PROFIL CONTINU DE MOCTAR (PERSONNALISATION VIVANTE)]",
            f"- Utilisateur : {profile.get('name', 'Moctar')} (Ingénieur & Architecte sur {profile.get('hardware', 'Dell Precision M4800')})",
            f"- Ton souhaité : {profile.get('tone_preference', 'Humain, chaleureux, direct et complice')}",
            f"- Équipes & Sports suivis : {teams}",
            f"- Sujets récents abordés : {topics_str}",
            f"- Volume d'échanges partagés : {turn_count} tours de conversation",
            "- Directive de posture : Ne termine PAS tes phrases par 'Moctar.' à chaque fois. Parle-lui avec naturel et fluidité, et pose de courtes questions de clarification dès que nécessaire.",
        ]
        return "\n".join(lines)

    def search_facts(
        self,
        query: str,
        limit: int = 3,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Recherche les faits les plus pertinents par similarité lexicale et pondération.

        Args:
            query: Question ou termes de recherche.
            limit: Nombre maximal de faits à retourner.
            category: Catégorie optionnelle pour restreindre le périmètre (ex: 'network', 'iot').

        Returns:
            List[Dict[str, Any]]: Faits triés par score de pertinence décroissant.
        """
        query_tokens = _tokenize(query)
        raw_query_lower = query.lower().strip()
        normalized_cat = category.lower().strip() if category else None

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if normalized_cat:
                    cursor.execute(
                        "SELECT id, timestamp, category, key, content, embedding_or_keywords, importance_score FROM memories WHERE LOWER(category) = ?",
                        (normalized_cat,),
                    )
                else:
                    cursor.execute(
                        "SELECT id, timestamp, category, key, content, embedding_or_keywords, importance_score FROM memories"
                    )
                rows = cursor.fetchall()
        except Exception as exc:
            logger.error("Erreur lors de la lecture des mémoires : %s", exc)
            return []

        if not query_tokens and not raw_query_lower:
            return [
                {
                    "id": r[0],
                    "timestamp": r[1],
                    "category": r[2],
                    "key": r[3],
                    "content": r[4],
                    "importance": r[6],
                    "score": 1.0,
                }
                for r in sorted(rows, key=lambda x: x[6], reverse=True)[:limit]
            ]

        scored_results: List[Tuple[float, Dict[str, Any]]] = []

        for row in rows:
            _id, ts, cat, key, content, kw, importance = row
            score = 0.0
            key_lower = key.lower()
            cat_lower = cat.lower()
            content_lower = content.lower()

            # 1. Correspondance exacte ou partielle sur la clé
            if key_lower == raw_query_lower:
                score += 15.0
            elif key_lower in raw_query_lower or raw_query_lower in key_lower:
                score += 8.0
            elif any(token in key_lower for token in query_tokens):
                score += 4.0

            # 2. Correspondance sur la catégorie
            if cat_lower in raw_query_lower:
                score += 3.0
            if normalized_cat and cat_lower == normalized_cat:
                score += 2.0

            # 3. Fréquence des tokens dans le contenu
            doc_tokens = Counter(_tokenize(f"{key} {content} {kw}"))
            for q_tok in query_tokens:
                if q_tok in doc_tokens:
                    # Score TF logarithmique
                    score += 1.5 * (1.0 + math.log(doc_tokens[q_tok]))

            # 4. Bonus d'importance déclarée
            if score > 0:
                score *= (1.0 + 0.2 * min(int(importance or 1), 5))
                scored_results.append(
                    (
                        score,
                        {
                            "id": _id,
                            "timestamp": ts,
                            "category": cat,
                            "key": key,
                            "content": content,
                            "importance": importance,
                            "score": round(score, 3),
                        },
                    )
                )

        scored_results.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored_results[:limit]]

    def get_system_context_snippet(self, user_query: str) -> str:
        """Génère un bloc de contexte compact récapitulant les faits pertinents pour le prompt système.

        Args:
            user_query: Requête courante de l'utilisateur.

        Returns:
            str: Bloc textuel à injecter dynamiquement dans le SYSTEM_PROMPT.
        """
        relevant_facts = self.search_facts(user_query, limit=4)

        # Récupération supplémentaire des faits hautement critiques (importance >= 4)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT category, key, content FROM memories WHERE importance_score >= 4 ORDER BY timestamp DESC LIMIT 3"
                )
                critical_rows = cursor.fetchall()
        except Exception:
            critical_rows = []

        seen_keys = set()
        fact_lines: List[str] = []

        # Ajout des faits trouvés par recherche sémantique
        for fact in relevant_facts:
            compound_key = f"{fact['category']}:{fact['key']}"
            if compound_key not in seen_keys:
                seen_keys.add(compound_key)
                fact_lines.append(f"- [{fact['category']}] {fact['key']} = {fact['content']}")

        # Ajout des faits critiques
        for cat, k, val in critical_rows:
            compound_key = f"{cat}:{k}"
            if compound_key not in seen_keys:
                seen_keys.add(compound_key)
                fact_lines.append(f"- [{cat}] {k} = {val}")

        if not fact_lines:
            return ""

        return (
            "FAITS MÉMORISÉS ET PRÉFÉRENCES (MÉMOIRE PERMANENTE JARVIS) :\n"
            + "\n".join(fact_lines)
        )

    def delete_fact(self, key: str, category: Optional[str] = None) -> bool:
        """Supprime un fait de la mémoire permanente."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if category:
                    cursor.execute("DELETE FROM memories WHERE category = ? AND key = ?", (category.strip().lower(), key.strip().lower()))
                else:
                    cursor.execute("DELETE FROM memories WHERE key = ?", (key.strip().lower(),))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            logger.error("Erreur lors de la suppression du fait '%s' : %s", key, exc)
            return False

    # ==========================================================================
    # 2. MÉMOIRE DE TRAVAIL (COURT TERME / SLIDING WINDOW TOKEN-AWARE)
    # ==========================================================================

    def add_message(
        self,
        session_id: str,
        role: str,
        content: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_call_id: Optional[str] = None,
    ) -> None:
        """Enregistre un message dans l'historique de la session avec protection anti-doublon.

        Args:
            session_id: Identifiant de la session de conversation.
            role: Rôle de l'émetteur ('user', 'assistant', 'system', 'tool').
            content: Corps textuel du message.
            tool_calls: Appels d'outils émis par l'assistant le cas échéant.
            tool_call_id: ID du tool call si le message est une réponse d'outil.
        """
        now_iso = datetime.now().isoformat(timespec="seconds")
        tool_calls_str = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None
        clean_content = (content or "").strip()

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Anti-doublon (Idempotence) : Évite d'enregistrer deux fois le même tour dans la session
                cursor.execute(
                    """
                    SELECT role, content, tool_calls, timestamp 
                    FROM conversation_history 
                    WHERE session_id = ? 
                    ORDER BY id DESC LIMIT 1
                    """,
                    (session_id,)
                )
                last_row = cursor.fetchone()
                if last_row:
                    last_role, last_content, last_tc, last_ts = last_row
                    if (
                        last_role == role
                        and (last_content or "").strip() == clean_content
                        and (last_tc or None) == tool_calls_str
                    ):
                        try:
                            t_now = datetime.fromisoformat(now_iso)
                            t_last = datetime.fromisoformat(last_ts)
                            diff_sec = abs((t_now - t_last).total_seconds())
                        except Exception:
                            diff_sec = 0.0

                        if diff_sec <= 15.0:
                            logger.debug(
                                "Anti-doublon mémoire actif : message '%s' [%s] déjà mémorisé il y a %.1fs (session=%s). Doublon ignoré.",
                                clean_content[:40],
                                role,
                                diff_sec,
                                session_id,
                            )
                            return

                cursor.execute(
                    """
                    INSERT INTO conversation_history (session_id, timestamp, role, content, tool_calls, tool_call_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, now_iso, role, content or "", tool_calls_str, tool_call_id),
                )

                # Mise à jour ou création de la session associée
                title_candidate = (content or "").strip().replace("\n", " ")[:60] if role == "user" and content else ""
                default_title = title_candidate or f"Discussion du {now_iso[:10]}"

                cursor.execute(
                    """
                    INSERT INTO conversation_sessions (session_id, title, created_at, updated_at, message_count)
                    VALUES (?, ?, ?, ?, 1)
                    ON CONFLICT(session_id) DO UPDATE SET
                        updated_at = excluded.updated_at,
                        message_count = conversation_sessions.message_count + 1,
                        title = CASE 
                            WHEN (conversation_sessions.title LIKE 'Discussion du%' OR conversation_sessions.title LIKE 'Session %' OR conversation_sessions.title LIKE 'Nouvelle session%') 
                                 AND excluded.title != '' AND excluded.title NOT LIKE 'Discussion du%'
                            THEN excluded.title 
                            ELSE conversation_sessions.title 
                        END
                    """,
                    (session_id, default_title, now_iso, now_iso),
                )
                conn.commit()
        except Exception as exc:
            logger.error("Erreur lors de l'enregistrement dans conversation_history : %s", exc)

    def get_context_window(
        self,
        session_id: str,
        max_turns: int = 8,
        max_chars: int = 14000,
    ) -> List[Dict[str, Any]]:
        """Récupère la fenêtre glissante des derniers tours optimisée pour injection LLM.

        Garantit que le protocole Function Calling reste valide en évitant d'isoler
        un message 'tool' de son message 'assistant' déclencheur.

        Args:
            session_id: Identifiant de session cible.
            max_turns: Nombre maximal d'échanges à remonter.
            max_chars: Limite indicative de caractères pour éviter l'explosion du contexte.

        Returns:
            List[Dict[str, Any]]: Liste ordonnée chronologiquement des messages récents.
        """
        fetch_limit = max(10, max_turns * 3)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT role, content, tool_calls, tool_call_id
                    FROM conversation_history
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (session_id, fetch_limit),
                )
                rows = cursor.fetchall()
        except Exception as exc:
            logger.error("Erreur lors de la lecture de la fenêtre de contexte : %s", exc)
            return []

        if not rows:
            return []

        # Remise dans l'ordre chronologique
        rows.reverse()

        messages: List[Dict[str, Any]] = []
        total_chars = 0

        for role, content, tool_calls_raw, tool_call_id in rows:
            msg: Dict[str, Any] = {"role": role, "content": content or ""}
            if tool_calls_raw:
                try:
                    msg["tool_calls"] = json.loads(tool_calls_raw)
                except Exception:
                    pass
            if tool_call_id:
                msg["tool_call_id"] = tool_call_id

            msg_len = len(content or "") + len(tool_calls_raw or "")
            if total_chars + msg_len > max_chars and len(messages) >= 2:
                # Éviter de couper au milieu si le message suivant est un tool
                break

            total_chars += msg_len
            messages.append(msg)

        # Nettoyage de tête : ne jamais commencer la liste de contexte par un message 'tool'
        while messages and messages[0].get("role") == "tool":
            messages.pop(0)

        # Déduplication de sécurité : élimine les doublons consécutifs résiduels (même rôle et même contenu)
        deduped: List[Dict[str, Any]] = []
        for m in messages:
            if (
                deduped
                and deduped[-1].get("role") == m.get("role")
                and (deduped[-1].get("content") or "").strip() == (m.get("content") or "").strip()
            ):
                continue
            deduped.append(m)
        messages = deduped

        # Tronquer aux max_turns derniers messages si nécessaire
        if len(messages) > max_turns * 2:
            messages = messages[-(max_turns * 2):]
            while messages and messages[0].get("role") == "tool":
                messages.pop(0)

        return messages

    def get_last_assistant_message(self, session_id: Optional[str] = None) -> Optional[str]:
        """Récupère le dernier message prononcé par l'assistant pour pouvoir le répéter à la demande."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if session_id:
                    cursor.execute(
                        """
                        SELECT content FROM conversation_history 
                        WHERE session_id = ? AND role = 'assistant' AND content IS NOT NULL AND content != '' 
                        ORDER BY id DESC LIMIT 1
                        """,
                        (session_id,)
                    )
                else:
                    cursor.execute(
                        """
                        SELECT content FROM conversation_history 
                        WHERE role = 'assistant' AND content IS NOT NULL AND content != '' 
                        ORDER BY id DESC LIMIT 1
                        """
                    )
                row = cursor.fetchone()
                if row and row[0]:
                    return str(row[0]).strip()
        except Exception as exc:
            logger.error("Erreur lors de la récupération du dernier message assistant : %s", exc)
        return None

    def clear_session(self, session_id: str) -> None:
        """Efface l'historique court terme d'une session."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM conversation_history WHERE session_id = ?", (session_id,))
                cursor.execute("DELETE FROM conversation_sessions WHERE session_id = ?", (session_id,))
                conn.commit()
                logger.info("Historique de session '%s' réinitialisé.", session_id)
        except Exception as exc:
            logger.error("Erreur lors de l'effacement de session : %s", exc)

    def create_or_update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Crée explicitement ou met à jour les métadonnées d'une session de conversation."""
        now_iso = datetime.now().isoformat(timespec="seconds")
        clean_title = (title or "").strip() or f"Session {session_id[:8]}"
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO conversation_sessions (session_id, title, created_at, updated_at, message_count, summary)
                    VALUES (?, ?, ?, ?, 0, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        title = COALESCE(NULLIF(excluded.title, ''), conversation_sessions.title),
                        updated_at = excluded.updated_at,
                        summary = COALESCE(excluded.summary, conversation_sessions.summary)
                    """,
                    (session_id, clean_title, now_iso, now_iso, summary),
                )
                conn.commit()
                cursor.execute(
                    "SELECT session_id, title, created_at, updated_at, message_count, summary FROM conversation_sessions WHERE session_id = ?",
                    (session_id,),
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "session_id": row[0],
                        "title": row[1],
                        "created_at": row[2],
                        "updated_at": row[3],
                        "message_count": row[4],
                        "summary": row[5],
                    }
        except Exception as exc:
            logger.error("Erreur lors de la création/mise à jour de session '%s' : %s", session_id, exc)
        return {
            "session_id": session_id,
            "title": clean_title,
            "created_at": now_iso,
            "updated_at": now_iso,
            "message_count": 0,
            "summary": summary,
        }

    def list_sessions(
        self,
        limit: int = 50,
        search_query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Liste les sessions de conversation récentes avec filtrage optionnel par recherche."""
        limit = max(1, min(int(limit), 200))
        sessions: List[Dict[str, Any]] = []
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if search_query and search_query.strip():
                    term = f"%{search_query.strip().lower()}%"
                    cursor.execute(
                        """
                        SELECT s.session_id, s.title, s.created_at, s.updated_at, s.message_count, s.summary,
                               (SELECT content FROM conversation_history WHERE session_id = s.session_id AND role IN ('user', 'assistant') ORDER BY id DESC LIMIT 1) AS last_message
                        FROM conversation_sessions s
                        WHERE LOWER(s.title) LIKE ? 
                           OR s.session_id IN (
                               SELECT DISTINCT session_id FROM conversation_history 
                               WHERE LOWER(content) LIKE ?
                           )
                        ORDER BY s.updated_at DESC
                        LIMIT ?
                        """,
                        (term, term, limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT s.session_id, s.title, s.created_at, s.updated_at, s.message_count, s.summary,
                               (SELECT content FROM conversation_history WHERE session_id = s.session_id AND role IN ('user', 'assistant') ORDER BY id DESC LIMIT 1) AS last_message
                        FROM conversation_sessions s
                        ORDER BY s.updated_at DESC
                        LIMIT ?
                        """,
                        (limit,),
                    )
                for row in cursor.fetchall():
                    sessions.append({
                        "session_id": row[0],
                        "title": row[1],
                        "created_at": row[2],
                        "updated_at": row[3],
                        "message_count": row[4],
                        "summary": row[5],
                        "last_message": row[6] or "",
                    })
        except Exception as exc:
            logger.error("Erreur lors de la récupération des sessions : %s", exc)
        return sessions

    def get_session_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Récupère l'intégralité chronologique des échanges pour une session donnée."""
        messages: List[Dict[str, Any]] = []
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if limit:
                    cursor.execute(
                        """
                        SELECT id, session_id, timestamp, role, content, tool_calls, tool_call_id
                        FROM conversation_history
                        WHERE session_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (session_id, int(limit)),
                    )
                    rows = cursor.fetchall()
                    rows.reverse()
                else:
                    cursor.execute(
                        """
                        SELECT id, session_id, timestamp, role, content, tool_calls, tool_call_id
                        FROM conversation_history
                        WHERE session_id = ?
                        ORDER BY id ASC
                        """,
                        (session_id,),
                    )
                    rows = cursor.fetchall()

                for r in rows:
                    tool_calls = None
                    if r[5]:
                        try:
                            tool_calls = json.loads(r[5])
                        except Exception:
                            tool_calls = r[5]
                    messages.append({
                        "id": r[0],
                        "session_id": r[1],
                        "timestamp": r[2],
                        "role": r[3],
                        "content": r[4] or "",
                        "tool_calls": tool_calls,
                        "tool_call_id": r[6],
                    })
        except Exception as exc:
            logger.error("Erreur lors de la lecture des messages de la session '%s' : %s", session_id, exc)
        return messages

    def update_session_title(self, session_id: str, title: str) -> bool:
        """Modifie manuellement le titre ou sujet d'une session."""
        clean_title = title.strip()
        if not clean_title:
            return False
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE conversation_sessions SET title = ? WHERE session_id = ?",
                    (clean_title, session_id),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            logger.error("Erreur mise à jour titre de session '%s' : %s", session_id, exc)
            return False

    def delete_session(self, session_id: str) -> bool:
        """Supprime définitivement une session et tous les messages associés."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM conversation_history WHERE session_id = ?", (session_id,))
                cursor.execute("DELETE FROM conversation_sessions WHERE session_id = ?", (session_id,))
                conn.commit()
                logger.info("Session '%s' et son historique supprimés.", session_id)
                return True
        except Exception as exc:
            logger.error("Erreur suppression session '%s' : %s", session_id, exc)
            return False

    def search_conversation_history(
        self,
        query: str,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """Recherche dans tout l'historique des messages pour retrouver des échanges passés."""
        clean_query = query.strip()
        if not clean_query:
            return []
        limit = max(1, min(int(limit), 100))
        results: List[Dict[str, Any]] = []
        term = f"%{clean_query.lower()}%"
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT h.id, h.session_id, h.timestamp, h.role, h.content, s.title
                    FROM conversation_history h
                    LEFT JOIN conversation_sessions s ON h.session_id = s.session_id
                    WHERE LOWER(h.content) LIKE ?
                    ORDER BY h.id DESC
                    LIMIT ?
                    """,
                    (term, limit),
                )
                for r in cursor.fetchall():
                    results.append({
                        "id": r[0],
                        "session_id": r[1],
                        "timestamp": r[2],
                        "role": r[3],
                        "content": r[4] or "",
                        "session_title": r[5] or f"Session {r[1][:8]}",
                    })
        except Exception as exc:
            logger.error("Erreur recherche historique de conversation : %s", exc)
        return results


# ==============================================================================
# RÉTROCOMPATIBILITÉ AVEC L'ANCIENNE ARCHITECTURE JARVIS
# ==============================================================================

class SessionMemory:
    """Mémoire de session en mémoire vive (pour compatibilité avec assistant_core)."""

    def __init__(self, max_turns: int = 20) -> None:
        self.max_turns = max_turns
        self.turns: List[Dict[str, Any]] = []

    def add_turn(self, intent: str, target: str, response: str) -> None:
        self.turns.append(
            {
                "intent": intent,
                "target": target,
                "response": response,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def add(self, role: str, content: str) -> None:
        """Alias pour ajouter un tour de dialogue rôle/contenu."""
        self.turns.append(
            {
                "role": role,
                "content": content,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def get_history(self) -> List[Dict[str, Any]]:
        """Renvoie la liste des tours enregistrés."""
        return list(self.turns)

    def last_turn(self) -> Optional[Dict[str, Any]]:
        if not self.turns:
            return None
        return self.turns[-1]

    def recent_turns(self, limit: int = 4) -> List[Dict[str, Any]]:
        limit = max(0, int(limit))
        if limit == 0:
            return []
        return list(self.turns[-limit:])

    def clear(self) -> None:
        self.turns.clear()


class UserMemoryStore:
    """Gestionnaire de mémoire utilisateur JSON (pour compatibilité avec assistant_core)."""

    MAX_RESPONSE_CHARS = 220

    def __init__(self, path: Optional[Union[str, Path]] = None) -> None:
        target = Path(path or config.USER_MEMORY_FILE)
        if target.is_dir():
            self.path = target / "user_memory.json"
        else:
            self.path = target
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: Optional[Dict[str, Any]] = None
        if not self.path.exists():
            self.path.write_text(
                json.dumps(
                    {
                        "preferences": {},
                        "history": [],
                        "insights": {
                            "top_intents": {},
                            "active_project": "",
                            "recent_files": [],
                            "last_focus_topic": "",
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    def _load(self) -> Dict[str, Any]:
        if self._cache is not None:
            return self._cache
        try:
            self._cache = json.loads(self.path.read_text(encoding="utf-8"))
            return self._cache
        except Exception:
            self._cache = {
                "preferences": {},
                "history": [],
                "insights": {
                    "top_intents": {},
                    "active_project": "",
                    "recent_files": [],
                    "last_focus_topic": "",
                },
            }
            return self._cache

    def _save(self, payload: Dict[str, Any]) -> None:
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        self.path.write_text(serialized, encoding="utf-8")
        self._cache = payload

    def set_preference(self, key: str, value: Any) -> None:
        payload = self._load()
        payload.setdefault("preferences", {})[key] = value
        self._save(payload)

    def get_preference(self, key: str, default: Any = None) -> Any:
        payload = self._load()
        return payload.get("preferences", {}).get(key, default)

    def remember(self, key: str, value: Any) -> bool:
        """Alias de mémorisation directe pour compatibilité."""
        self.set_preference(key, value)
        return True

    def recall(self, key: str, default: Any = None) -> Any:
        """Alias de rappel direct pour compatibilité."""
        return self.get_preference(key, default)

    def get_preferences(self) -> Dict[str, Any]:
        payload = self._load()
        return dict(payload.get("preferences", {}))

    def record_interaction(self, intent: str, target: str, response: Any) -> None:
        payload = self._load()
        history = payload.setdefault("history", [])
        insights = payload.setdefault("insights", {})
        top_intents = insights.setdefault("top_intents", {})
        response_text = str(response or "")
        response_preview = response_text[: self.MAX_RESPONSE_CHARS]
        if len(response_text) > self.MAX_RESPONSE_CHARS:
            response_preview += "..."
        history.append(
            {
                "intent": intent,
                "target": target,
                "response": response_preview,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        payload["history"] = history[-100:]

        if intent not in {"confirm_yes", "confirm_no", "empty"}:
            top_intents[intent] = int(top_intents.get(intent, 0)) + 1
        normalized_target = (target or "").strip()
        if normalized_target:
            if intent in {"open_project", "launch_project_server"}:
                insights["active_project"] = normalized_target
            if intent in {"summarize_file", "search_personal", "create_note"}:
                recent_files = insights.setdefault("recent_files", [])
                recent_files.append(normalized_target)
                insights["recent_files"] = recent_files[-6:]
            if intent in {"next_action", "daily_brief", "search_personal", "summarize_file"}:
                insights["last_focus_topic"] = normalized_target

        self._save(payload)

    def get_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        payload = self._load()
        history = list(payload.get("history", []))
        if limit is None:
            return history
        return history[-limit:]

    def get_insights(self) -> Dict[str, Any]:
        payload = self._load()
        insights = payload.get("insights", {})
        return {
            "top_intents": dict(insights.get("top_intents", {})),
            "active_project": insights.get("active_project", ""),
            "recent_files": list(insights.get("recent_files", [])),
            "last_focus_topic": insights.get("last_focus_topic", ""),
        }
