"""Extensible knowledge layer for everyday questions.

It first provides a few safe offline replies, then consults French Wikipedia
when the computer has internet access. Results are cached for the session.
"""
from functools import lru_cache
import re
import unicodedata
from urllib.parse import quote

import config

try:
    import requests
except Exception:
    requests = None


_OFFLINE_GUIDES = (
    (
        ("urgence", "danger", "saigne", "saignement", "malaise"),
        "En cas de danger ou de symptome grave, appelle les secours locaux immediatement. "
        "Je peux aider a trouver des gestes de base, mais je ne remplace pas un professionnel de sante.",
    ),
    (
        ("mot de passe", "pirate", "piratage", "compte vole"),
        "Change le mot de passe depuis un appareil fiable, active la double authentification, "
        "deconnecte les sessions inconnues et contacte le service concerne.",
    ),
    (
        ("budget", "economiser", "economies"),
        "Commence par noter tes revenus et tes depenses fixes, fixe un plafond hebdomadaire, "
        "puis mets automatiquement une petite somme de cote chaque mois.",
    ),
)


class KnowledgeLibrary:
    def __init__(self, requester=None):
        self.requester = requester or (requests.get if requests else None)

    @staticmethod
    def _normalize_text(text):
        normalized = unicodedata.normalize("NFD", (text or "").lower().strip())
        return "".join(char for char in normalized if unicodedata.category(char) != "Mn")

    @classmethod
    def _offline_answer(cls, query):
        normalized = cls._normalize_text(query)
        for keywords, answer in _OFFLINE_GUIDES:
            if any(keyword in normalized for keyword in keywords):
                return answer
        return ""

    @classmethod
    def _is_math_like_query(cls, query):
        normalized = cls._normalize_text(query)
        if not re.search(r"\d", normalized):
            return False
        operators = ("+", "-", "*", "/", " x ", " fois ", " calcule ", " combien font ", " combien fait ")
        return any(operator in f" {normalized} " for operator in operators)

    @staticmethod
    def _trim_extract(extract):
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", (extract or "").strip())
            if sentence.strip()
        ]
        if not sentences:
            return ""
        return sentences[0]

    @lru_cache(maxsize=128)
    def lookup(self, query):
        query = (query or "").strip()
        if not query:
            return ""

        offline = self._offline_answer(query)
        if offline:
            return offline
        if self._is_math_like_query(query):
            return ""
        if not config.KNOWLEDGE_SETTINGS["enable_wikipedia"] or not self.requester:
            return ""

        try:
            search = self.requester(
                "https://fr.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 1},
                timeout=config.KNOWLEDGE_SETTINGS["timeout_seconds"],
                headers={"User-Agent": "MOCTAR-Personal-Assistant/1.0"},
            ).json()
            results = search.get("query", {}).get("search", [])
            if not results:
                return ""

            title = results[0].get("title", "").strip()
            if not title:
                return ""
            summary = self.requester(
                f"https://fr.wikipedia.org/api/rest_v1/page/summary/{quote(title)}",
                timeout=config.KNOWLEDGE_SETTINGS["timeout_seconds"],
                headers={"User-Agent": "MOCTAR-Personal-Assistant/1.0"},
            ).json()
            extract = self._trim_extract(summary.get("extract") or "")
            if not extract:
                return ""
            return f"D'apres Wikipedia : {extract}"
        except Exception:
            return ""
