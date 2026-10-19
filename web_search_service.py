"""Connecteur de recherche Web en temps réel et extraction de savoirs factuels.

Permet à JARVIS / M.O.C.T.A.R d'accéder au web en direct pour les questions du quotidien
(actualités, faits, définitions, culture, scores, géographie) sans clé API externe requise,
avec cache en mémoire (TTL 15 min) et injection directe de contexte synthétisé.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger("jarvis.web_search")

_SEARCH_CACHE_TTL_SECONDS = 900.0  # 15 minutes
_SEARCH_CACHE: dict[str, tuple[float, list[str]]] = {}

# Mots-clés déclencheurs d'un besoin de recherche web en direct
_WEB_QUERY_TRIGGERS = (
    "cherche sur le net",
    "cherche sur google",
    "cherche sur le web",
    "cherche des infos",
    "recherche sur le net",
    "recherche sur le web",
    "recherche google",
    "actualite",
    "actualité",
    "news",
    "dernieres nouvelles",
    "dernières nouvelles",
    "qui a gagne",
    "qui a gagné",
    "resultat match",
    "résultat match",
    "cours du",
    "prix du",
    "qui est",
    "qui etait",
    "qui était",
    "qu'est-ce que",
    "qu est ce que",
    "c'est quoi",
    "c est quoi",
    "definition de",
    "définition de",
    "population de",
    "capitale de",
    "president de",
    "président de",
)


def is_live_web_query(query: str) -> bool:
    """Détecte si la requête de l'utilisateur bénéficie d'une recherche web en temps réel."""
    raw = (query or "").lower().replace("’", "'")
    norm = re.sub(r"\s+", " ", raw).strip()
    norm_no_punct = re.sub(r"[^\w\s-]", " ", norm)
    norm_no_punct = re.sub(r"\s+", " ", norm_no_punct).strip()
    for trig in _WEB_QUERY_TRIGGERS:
        if trig in norm or trig in norm_no_punct or trig.replace("'", " ") in norm_no_punct:
            return True
    return False


def search_duckduckgo_snippets(query: str, max_results: int = 3, timeout: float = 3.5) -> list[str]:
    """Extrait les extraits textuels factuels depuis DuckDuckGo sans dépendances externes."""
    clean_q = query.strip()
    if not clean_q:
        return []

    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(clean_q)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_html = resp.read().decode("utf-8", errors="ignore")
            # Extraction des balises result__snippet
            raw_snippets = re.findall(
                r'<a class="result__snippet[^"]*"[^>]*>(.*?)</a>',
                raw_html,
                flags=re.DOTALL,
            )
            cleaned_snippets = []
            for snip in raw_snippets[:max_results]:
                # Suppression des balises HTML intérieures (ex: <b>...</b>)
                no_tags = re.sub(r"<[^>]+>", "", snip)
                unescaped = html.unescape(no_tags).strip()
                if unescaped and len(unescaped) > 20:
                    cleaned_snippets.append(unescaped)
            return cleaned_snippets
    except Exception as exc:
        logger.debug("Erreur DuckDuckGo HTML pour '%s': %s", clean_q, exc)
        return []


def search_wikipedia_summary(query: str, timeout: float = 2.5) -> str | None:
    """Interroge l'API REST de Wikipédia France pour les concepts, personnes ou lieux."""
    clean_q = re.sub(
        r"^(?:qui\s+est|qui\s+[ée]tait|c['’]\s*est\s+quoi|qu['’]\s*est[- ]ce\s+que|d[ée]finition\s+de|parle[- ]moi\s+de|info\s+sur)\s+",
        "",
        query,
        flags=re.IGNORECASE,
    ).strip(" ?.")
    if not clean_q or len(clean_q) < 2:
        return None

    try:
        url = f"https://fr.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(clean_q)}"
        headers = {"User-Agent": "JarvisAssistant/1.0 (moctar@jarvis)"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            extract = data.get("extract")
            if extract and len(extract) > 25:
                return str(extract).strip()
    except Exception as exc:
        logger.debug("Wikipedia summary pour '%s' indisponible: %s", clean_q, exc)

    return None


def fetch_live_web_context(query: str, max_chars: int = 600) -> str:
    """Récupère une synthèse textuelle compacte pour enrichir le prompt LLM de M.O.C.T.A.R.

    Met en cache le résultat pendant 15 minutes pour garantir une latence de 0 ms
    sur les questions récurrentes.
    """
    cleaned_query = query.strip()
    cache_key = cleaned_query.lower()
    now_t = time.monotonic()

    cached = _SEARCH_CACHE.get(cache_key)
    if cached and (now_t - cached[0]) < _SEARCH_CACHE_TTL_SECONDS:
        snippets = cached[1]
    else:
        snippets: list[str] = []
        # 1. Tentative Wikipédia si pertinent (rapide et propre)
        wiki_extract = search_wikipedia_summary(cleaned_query)
        if wiki_extract:
            snippets.append(f"Wikipédia : {wiki_extract}")

        # 2. Complément DuckDuckGo pour actualités récentes ou précision
        ddg_snippets = search_duckduckgo_snippets(cleaned_query, max_results=2)
        snippets.extend(ddg_snippets)

        if snippets:
            _SEARCH_CACHE[cache_key] = (now_t, snippets)

    if not snippets:
        return ""

    joined = "\n".join(f"- {s}" for s in snippets)
    if len(joined) > max_chars:
        joined = joined[:max_chars].rstrip() + "..."

    return f"[INFORMATIONS WEB ET ACTUALITÉS EN DIRECT]\n{joined}"
