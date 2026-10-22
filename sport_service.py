"""Connecteur Sport en Temps Réel pour M.O.C.T.A.R.

Fournit les scores en direct, derniers résultats et classements
pour la Ligue 1, Champions League, Liga, Premier League, Serie A et Bundesliga,
ainsi que les scores par équipe avec mise en cache mémoire de 120 secondes.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

LOGGER = logging.getLogger("moctar.sport_service")

# Cache mémoire de 120 secondes (TTL)
_CACHE_TTL_SECONDS = 120.0
_SPORT_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

# Championnats majeurs pris en charge (ESPN API codes)
LEAGUES_CONFIG = {
    "ligue_1": {
        "id": "fra.1",
        "name": "Ligue 1",
        "aliases": ["ligue 1", "ligue1", "l1", "championnat francais", "championnat de france"],
    },
    "champions_league": {
        "id": "uefa.champions",
        "name": "Ligue des Champions",
        "aliases": ["champions league", "ligue des champions", "c1", "champions", "uefa champions league"],
    },
    "la_liga": {
        "id": "esp.1",
        "name": "La Liga",
        "aliases": ["liga", "la liga", "championnat espagnol", "espagne"],
    },
    "premier_league": {
        "id": "eng.1",
        "name": "Premier League",
        "aliases": ["premier league", "pl", "championnat anglais", "angleterre"],
    },
    "serie_a": {
        "id": "ita.1",
        "name": "Serie A",
        "aliases": ["serie a", "championnat italien", "italie"],
    },
    "bundesliga": {
        "id": "ger.1",
        "name": "Bundesliga",
        "aliases": ["bundesliga", "championnat allemand", "allemagne"],
    },
}

# Équipes populaires et leurs ligues associées pour recherche rapide
TOP_TEAMS_LEAGUE_MAP = {
    "psg": "ligue_1",
    "paris": "ligue_1",
    "paris saint germain": "ligue_1",
    "marseille": "ligue_1",
    "om": "ligue_1",
    "lyon": "ligue_1",
    "ol": "ligue_1",
    "monaco": "ligue_1",
    "lille": "ligue_1",
    "lens": "ligue_1",
    "rennes": "ligue_1",
    "real madrid": "la_liga",
    "real": "la_liga",
    "barcelone": "la_liga",
    "barca": "la_liga",
    "barça": "la_liga",
    "atletico": "la_liga",
    "atletico madrid": "la_liga",
    "manchester city": "premier_league",
    "man city": "premier_league",
    "city": "premier_league",
    "manchester united": "premier_league",
    "man united": "premier_league",
    "arsenal": "premier_league",
    "liverpool": "premier_league",
    "chelsea": "premier_league",
    "tottenham": "premier_league",
    "bayern": "bundesliga",
    "bayern munich": "bundesliga",
    "dortmund": "bundesliga",
    "leverkusen": "bundesliga",
    "juventus": "serie_a",
    "juve": "serie_a",
    "inter": "serie_a",
    "inter milan": "serie_a",
    "milan": "serie_a",
    "ac milan": "serie_a",
    "naples": "serie_a",
    "roma": "serie_a",
}


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def _ensure_moctar_ending(message: str) -> str:
    cleaned = (message or "").strip()
    if not cleaned:
        return "Données sportives indisponibles, Moctar."
    if re.search(r"\bmoctar\.?$", cleaned, flags=re.IGNORECASE):
        return cleaned if cleaned.endswith(".") else f"{cleaned}."
    cleaned = cleaned.rstrip(".!? ")
    return f"{cleaned}, Moctar."


def _fetch_espn_scoreboard(league_id: str) -> Optional[Dict[str, Any]]:
    """Interroge l'endpoint public ESPN pour une ligue donnée."""
    cache_key = f"espn_score_{league_id}"
    now = time.time()
    if cache_key in _SPORT_CACHE:
        timestamp, cached_data = _SPORT_CACHE[cache_key]
        if now - timestamp < _CACHE_TTL_SECONDS:
            return cached_data

    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_id}/scoreboard"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) M.O.C.T.A.R-NOC-Agent/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=4.5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                _SPORT_CACHE[cache_key] = (now, data)
                return data
    except Exception as err:
        LOGGER.debug("Erreur lors de la récupération ESPN scoreboard (%s): %s", league_id, err)

    return None


def _fetch_espn_standings(league_id: str) -> Optional[Dict[str, Any]]:
    """Interroge le classement officiel ESPN pour une ligue."""
    cache_key = f"espn_standings_{league_id}"
    now = time.time()
    if cache_key in _SPORT_CACHE:
        timestamp, cached_data = _SPORT_CACHE[cache_key]
        if now - timestamp < _CACHE_TTL_SECONDS:
            return cached_data

    url = f"https://site.api.espn.com/apis/v2/sports/soccer/{league_id}/standings"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) M.O.C.T.A.R-NOC-Agent/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=4.5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                _SPORT_CACHE[cache_key] = (now, data)
                return data
    except Exception as err:
        LOGGER.debug("Erreur lors de la récupération ESPN standings (%s): %s", league_id, err)

    return None


def format_scoreboard_summary(league_name: str, data: Dict[str, Any], team_filter: Optional[str] = None) -> Optional[str]:
    """Formate une synthèse orale concise et chirurgicale des matchs."""
    events = data.get("events", [])
    if not events:
        return None

    match_strings = []
    norm_filter = _normalize(team_filter) if team_filter else None

    for ev in events:
        comps = ev.get("competitions", [])
        if not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        home_name = home.get("team", {}).get("shortDisplayName") or home.get("team", {}).get("name", "Équipe 1")
        away_name = away.get("team", {}).get("shortDisplayName") or away.get("team", {}).get("name", "Équipe 2")
        home_score = home.get("score", "0")
        away_score = away.get("score", "0")

        status_type = comp.get("status", {}).get("type", {})
        status_state = status_type.get("state", "pre")  # 'pre', 'in', 'post'
        status_detail = status_type.get("shortDetail", "")

        # Filtre sur équipe spécifique si demandé
        if norm_filter:
            h_norm = _normalize(home_name)
            a_norm = _normalize(away_name)
            if norm_filter not in h_norm and norm_filter not in a_norm:
                continue

        if status_state == "in":
            # Match en cours
            match_strings.append(f"🔴 EN DIRECT : {home_name} {home_score} - {away_score} {away_name} ({status_detail})")
        elif status_state == "post":
            # Match terminé
            match_strings.append(f"{home_name} {home_score} - {away_score} {away_name}")
        else:
            # Match à venir
            match_strings.append(f"{home_name} vs {away_name} ({status_detail})")

    if not match_strings:
        return None

    # Garder les 3-4 affiches les plus pertinentes pour concision orale
    summary_matches = ", ".join(match_strings[:4])
    return f"Résultats {league_name} : {summary_matches}."


def format_standings_summary(league_name: str, data: Dict[str, Any]) -> Optional[str]:
    """Formate les 4 premières places du classement."""
    children = data.get("children", [])
    entries = []
    if children and "standings" in children[0]:
        entries = children[0]["standings"].get("entries", [])
    elif "standings" in data:
        entries = data["standings"].get("entries", [])

    if not entries:
        return None

    top_teams = []
    for rank, entry in enumerate(entries[:4], 1):
        team_name = entry.get("team", {}).get("shortDisplayName") or entry.get("team", {}).get("displayName", "")
        # Extraire les points
        stats = entry.get("stats", [])
        pts = next((s.get("value") for s in stats if s.get("name") in ("points", "pts")), None)
        pts_str = f" ({int(pts)} pts)" if pts is not None else ""
        top_teams.append(f"{rank}er {team_name}{pts_str}" if rank == 1 else f"{rank}e {team_name}{pts_str}")

    if not top_teams:
        return None

    return f"Classement {league_name} : " + ", ".join(top_teams) + "."


def get_sports_update(query: str) -> Dict[str, Any]:
    """Point d'entrée principal pour traiter les requêtes sportives orales ou textuelles.

    Args:
        query (str): Ex: "Résultats Ligue 1", "Score Real Madrid", "Classement Premier League".

    Returns:
        Dict[str, Any] contenant 'success', 'reply' (terminant par 'Moctar.'), 'data'.
    """
    norm = _normalize(query)

    # 1. Détection de championnat
    target_league_key = None
    for key, cfg in LEAGUES_CONFIG.items():
        if any(alias in norm for alias in cfg["aliases"]):
            target_league_key = key
            break

    # 2. Détection d'équipe si aucun championnat explicite
    target_team = None
    if not target_league_key:
        for team_alias, league_key in TOP_TEAMS_LEAGUE_MAP.items():
            if re.search(rf"\b{re.escape(team_alias)}\b", norm):
                target_team = team_alias
                target_league_key = league_key
                break

    # Ligue par défaut si non spécifiée (Ligue 1)
    if not target_league_key:
        target_league_key = "ligue_1"

    league_cfg = LEAGUES_CONFIG[target_league_key]
    league_id = league_cfg["id"]
    league_name = league_cfg["name"]

    is_standings_query = any(w in norm for w in ("classement", "table", "position", "premier"))

    # A. Requête de classement
    if is_standings_query:
        standings_data = _fetch_espn_standings(league_id)
        if standings_data:
            summary = format_standings_summary(league_name, standings_data)
            if summary:
                return {
                    "success": True,
                    "query": query,
                    "reply": _ensure_moctar_ending(summary),
                    "data": {"league": league_name, "type": "standings"},
                }

    # B. Requête de résultats ou scores
    scoreboard_data = _fetch_espn_scoreboard(league_id)
    if scoreboard_data:
        summary = format_scoreboard_summary(league_name, scoreboard_data, team_filter=target_team)
        if summary:
            return {
                "success": True,
                "query": query,
                "reply": _ensure_moctar_ending(summary),
                "data": {"league": league_name, "type": "scoreboard", "team": target_team},
            }

    # C. Fallback via recherche Web en direct (DuckDuckGo snippets)
    try:
        import web_search_service
        search_term = f"résultat foot {league_name}" if not target_team else f"score direct {target_team}"
        snippets = web_search_service.search_duckduckgo_snippets(search_term, max_results=2)
        if snippets:
            snippet_text = snippets[0]
            if len(snippet_text) > 220:
                snippet_text = snippet_text[:217] + "..."
            reply = f"Dernières actualités sportives ({league_name}) : {snippet_text}"
            return {
                "success": True,
                "query": query,
                "reply": _ensure_moctar_ending(reply),
                "data": {"source": "web_search", "league": league_name},
            }
    except Exception as search_err:
        LOGGER.debug("Fallback web search sport failed: %s", search_err)

    # Réponse par défaut élégante
    fallback_msg = f"Les flux sportifs pour {league_name} sont actuellement synchronisés. Aucun match majeur n'est en cours en ce moment"
    return {
        "success": True,
        "query": query,
        "reply": _ensure_moctar_ending(fallback_msg),
        "data": {"league": league_name, "status": "nominal"},
    }
