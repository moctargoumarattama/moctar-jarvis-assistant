"""Connecteur de recherche Web en temps réel et extraction de savoirs factuels sourcés.

Permet à JARVIS / M.O.C.T.A.R d'accéder au web en direct pour les questions du quotidien
(actualités, faits évolutifs, météo, cours, prix, résultats sportifs, fonctions politiques,
versions récentes) sans clé API externe requise, en conservant systématiquement :
- title (titre / nom de source)
- url (URL complète ou domaine source)
- published_date (date de publication ou date détectée)
- freshness / age_hint (indicateur d'ancienneté ou "date non spécifiée")
- retrieved_at (horodatage exact de la recherche)
"""

from __future__ import annotations

from datetime import datetime, timezone
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
_SEARCH_CACHE: dict[str, tuple[float, str, list[dict[str, str]]]] = {}

# Mots-clés déclencheurs d'un besoin de recherche web en direct ou d'intention temporelle / actualité
_WEB_QUERY_TRIGGERS = (
    "cherche sur le net",
    "cherche sur google",
    "cherche sur le web",
    "cherche des infos",
    "recherche sur le net",
    "recherche sur le web",
    "recherche google",
    "sur internet",
    "actualite",
    "actualité",
    "actualites",
    "actualités",
    "news",
    "derniere nouvelle",
    "dernière nouvelle",
    "dernieres nouvelles",
    "dernières nouvelles",
    "qui a gagne",
    "qui a gagné",
    "resultat match",
    "résultat match",
    "score du match",
    "score de",
    "classement de",
    "cours du",
    "cours de",
    "prix du",
    "prix de",
    "taux de change",
    "bitcoin",
    "ethereum",
    "bourse",
    "meteo",
    "météo",
    "quel temps",
    "temperature a",
    "température à",
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
    "premier ministre",
    "ministre de",
    "pdg de",
    "ceo de",
    "maire de",
    "roi de",
    "derniere version",
    "dernière version",
    "version actuelle",
    "sortie de",
    "aujourd'hui",
    "aujourd hui",
    "maintenant",
    "en ce moment",
    "actuellement",
    "ce mois-ci",
    "ce mois ci",
    "cette semaine",
    "cette annee",
    "cette année",
    "2025",
    "2026",
)

_TEMPORAL_REGEX = re.compile(
    r"\b(?:"
    r"aujourd['’\s]?hui|maintenant|actuellement|en\s+ce\s+moment|ce\s+mois(?:-ci)?|"
    r"cette\s+semaine|cette\s+ann[ée]e|2025|2026|derni[èe]res?\s+nouvelles?|"
    r"actualit[ée]s?|news|cours\s+d[eu]|prix\s+d[eu]|taux\s+de\s+change|"
    r"r[ée]sultat\s+(?:du\s+)?match|qui\s+a\s+gagn[ée]|score\s+d[eu]|"
    r"pr[ée]sident\s+(?:actuel|de|du)|premier\s+ministre|derni[èe]re\s+version|"
    r"m[ée]t[ée]o|quel\s+temps\s+fait"
    r")\b",
    flags=re.IGNORECASE,
)

_MONTHS_FR = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12,
    "jan": 1, "fév": 2, "fev": 2, "avr": 4, "juil": 7, "sep": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12,
}


def is_temporal_or_current_events_query(query: str) -> bool:
    """Détecte si la requête porte sur l'actualité, le temps présent ou des faits évolutifs."""
    raw = (query or "").strip()
    if not raw:
        return False
    if _TEMPORAL_REGEX.search(raw):
        return True
    return is_live_web_query(raw)


def is_live_web_query(query: str) -> bool:
    """Détecte si la requête de l'utilisateur bénéficie d'une recherche web en temps réel."""
    raw = (query or "").lower().replace("’", "'")
    if not raw:
        return False
    if _TEMPORAL_REGEX.search(raw):
        return True
    norm = re.sub(r"\s+", " ", raw).strip()
    norm_no_punct = re.sub(r"[^\w\s-]", " ", norm)
    norm_no_punct = re.sub(r"\s+", " ", norm_no_punct).strip()
    for trig in _WEB_QUERY_TRIGGERS:
        if trig in norm or trig in norm_no_punct or trig.replace("'", " ") in norm_no_punct:
            return True
    return False


def _unwrap_ddg_url(raw_href: str) -> tuple[str, str]:
    """Extrait l'URL finale propre et le domaine depuis un lien DuckDuckGo HTML."""
    href = html.unescape((raw_href or "").strip())
    if not href:
        return ("URL non spécifiée", "web")
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        uddg = qs.get("uddg", [None])[0]
        if uddg:
            href = urllib.parse.unquote(uddg)
            parsed = urllib.parse.urlparse(href)
    domain = (parsed.netloc or "").lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return (href or "URL non spécifiée", domain or "source-web")


def _compute_freshness_from_date(dt: datetime) -> str:
    """Calcule un indicateur d'ancienneté lisible par rapport à la date courante."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    days = delta.days
    if days < 0:
        return "récent (date future/courante)"
    if days == 0:
        hours = max(1, int(delta.total_seconds() // 3600))
        return f"aujourd'hui (il y a ~{hours}h)" if hours > 0 else "aujourd'hui"
    if days == 1:
        return "hier (il y a 1 jour)"
    if days < 7:
        return f"il y a {days} jours"
    if days < 31:
        weeks = max(1, days // 7)
        return f"il y a {weeks} semaine(s)"
    if days < 365:
        months = max(1, days // 30)
        return f"il y a {months} mois"
    years = max(1, days // 365)
    return f"il y a ~{years} an(s)"


def _extract_date_and_freshness(text: str, raw_timestamp: str | None = None) -> tuple[str, str]:
    """Détecte une date de publication et son ancienneté depuis une métadonnée ISO ou un extrait."""
    # 1. Métadonnée ISO explicite (ex: Wikipédia timestamp)
    if raw_timestamp:
        try:
            iso_clean = str(raw_timestamp).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_clean)
            pub_date = dt.strftime("%Y-%m-%d")
            return (pub_date, _compute_freshness_from_date(dt))
        except Exception:
            pass

    body = (text or "").strip()
    if not body:
        return ("date non spécifiée", "date non spécifiée")

    # 2. Marqueurs relatifs dans l'extrait ("il y a 3 heures", "2 days ago", "hier", "aujourd'hui")
    rel_fr = re.search(
        r"\b(il y a\s+\d+\s*(?:minute|heure|jour|semaine|mois|an)s?|aujourd['’]?hui|hier)\b",
        body,
        flags=re.IGNORECASE,
    )
    if rel_fr:
        marker = rel_fr.group(1).strip()
        return (marker, marker.lower())

    rel_en = re.search(
        r"\b(\d+\s+(?:minute|hour|day|week|month|year)s?\s+ago|yesterday|today)\b",
        body,
        flags=re.IGNORECASE,
    )
    if rel_en:
        marker = rel_en.group(1).strip()
        return (marker, marker.lower())

    # 3. Date ISO YYYY-MM-DD dans l'extrait
    iso_match = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", body)
    if iso_match:
        y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        try:
            dt = datetime(y, m, d, tzinfo=timezone.utc)
            return (iso_match.group(0), _compute_freshness_from_date(dt))
        except Exception:
            return (iso_match.group(0), f"année {y}")

    # 4. Date française (ex: "24 octobre 2026")
    fr_date_match = re.search(
        r"\b(\d{1,2})\s+(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|septembre|octobre|novembre|d[ée]cembre|"
        r"jan|f[ée]v|avr|juil|sep|oct|nov|d[ée]c)\.?\s+(20\d{2})\b",
        body,
        flags=re.IGNORECASE,
    )
    if fr_date_match:
        day_s, month_s, year_s = fr_date_match.group(1), fr_date_match.group(2).lower(), fr_date_match.group(3)
        month_num = _MONTHS_FR.get(month_s.rstrip("."))
        if month_num:
            try:
                dt = datetime(int(year_s), month_num, int(day_s), tzinfo=timezone.utc)
                return (f"{int(day_s):02d}/{month_num:02d}/{year_s}", _compute_freshness_from_date(dt))
            except Exception:
                pass
        return (fr_date_match.group(0), f"année {year_s}")

    # 5. Année explicite récente dans l'extrait
    year_match = re.search(r"\b(202[0-9])\b", body)
    if year_match:
        yr = int(year_match.group(1))
        now_yr = datetime.now(timezone.utc).year
        diff_yr = now_yr - yr
        fresh = "cette année" if diff_yr == 0 else (f"il y a ~{diff_yr} an(s)" if diff_yr > 0 else f"horizon {yr}")
        return (str(yr), fresh)

    return ("date non spécifiée", "date non spécifiée")


def _format_sourced_entry(item: dict[str, str]) -> str:
    """Formate un résultat structuré avec ses métadonnées obligatoires pour le LLM."""
    title = item.get("title") or item.get("domain") or "Source Web"
    url = item.get("url") or item.get("domain") or "URL non spécifiée"
    pub_date = item.get("published_date") or "date non spécifiée"
    freshness = item.get("freshness") or item.get("age_hint") or "date non spécifiée"
    snippet = item.get("snippet") or ""
    return (
        f"[Source: {title} | URL: {url} | Date: {pub_date} | Ancienneté: {freshness}] "
        f"{snippet}"
    ).strip()


def search_duckduckgo_structured(
    query: str,
    max_results: int = 3,
    timeout: float = 3.5,
) -> list[dict[str, str]]:
    """Extrait les résultats structurés (title, url, published_date, freshness, snippet) depuis DuckDuckGo."""
    clean_q = (query or "").strip()
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

        results: list[dict[str, str]] = []
        # Découpage par blocs de résultats DuckDuckGo HTML
        blocks = re.findall(
            r'<div class="[^"]*result__body[^"]*">(.*?)</div>\s*</div>',
            raw_html,
            flags=re.DOTALL,
        )
        if not blocks:
            # Repli sur appariement direct titre + snippet
            title_matches = re.findall(
                r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                raw_html,
                flags=re.DOTALL,
            )
            snippet_matches = re.findall(
                r'<a[^>]*class="result__snippet[^"]*"[^>]*>(.*?)</a>',
                raw_html,
                flags=re.DOTALL,
            )
            for idx, raw_snip in enumerate(snippet_matches[:max_results]):
                clean_snip = html.unescape(re.sub(r"<[^>]+>", "", raw_snip)).strip()
                if not clean_snip or len(clean_snip) <= 20:
                    continue
                if idx < len(title_matches):
                    raw_href, raw_title = title_matches[idx]
                    clean_url, domain = _unwrap_ddg_url(raw_href)
                    clean_title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip() or domain
                else:
                    clean_url, domain, clean_title = ("URL non spécifiée", "duckduckgo.com", "DuckDuckGo")
                pub_date, freshness = _extract_date_and_freshness(clean_snip)
                results.append({
                    "title": clean_title,
                    "url": clean_url,
                    "domain": domain,
                    "published_date": pub_date,
                    "freshness": freshness,
                    "age_hint": freshness,
                    "snippet": clean_snip,
                })
            return results

        for blk in blocks:
            if len(results) >= max_results:
                break
            snip_m = re.search(
                r'<a[^>]*class="result__snippet[^"]*"[^>]*>(.*?)</a>',
                blk,
                flags=re.DOTALL,
            )
            if not snip_m:
                continue
            clean_snip = html.unescape(re.sub(r"<[^>]+>", "", snip_m.group(1))).strip()
            if not clean_snip or len(clean_snip) <= 20:
                continue

            title_m = re.search(
                r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                blk,
                flags=re.DOTALL,
            )
            if title_m:
                clean_url, domain = _unwrap_ddg_url(title_m.group(1))
                clean_title = html.unescape(re.sub(r"<[^>]+>", "", title_m.group(2))).strip() or domain
            else:
                url_m = re.search(r'<a[^>]*class="[^"]*result__url[^"]*"[^>]*href="([^"]*)"', blk, flags=re.DOTALL)
                clean_url, domain = _unwrap_ddg_url(url_m.group(1) if url_m else "")
                clean_title = domain

            # Vérifie également si une date figure dans les extras du bloc
            extra_text = html.unescape(re.sub(r"<[^>]+>", " ", blk))
            pub_date, freshness = _extract_date_and_freshness(f"{clean_snip} {extra_text}")
            results.append({
                "title": clean_title,
                "url": clean_url,
                "domain": domain,
                "published_date": pub_date,
                "freshness": freshness,
                "age_hint": freshness,
                "snippet": clean_snip,
            })

        return results
    except Exception as exc:
        logger.debug("Erreur DuckDuckGo HTML pour '%s': %s", clean_q, exc)
        return []


def search_duckduckgo_snippets(query: str, max_results: int = 3, timeout: float = 3.5) -> list[str]:
    """Extrait les extraits textuels factuels sourcés (URL, date, ancienneté) depuis DuckDuckGo."""
    structured = search_duckduckgo_structured(query, max_results=max_results, timeout=timeout)
    return [_format_sourced_entry(item) for item in structured]


def search_wikipedia_structured(query: str, timeout: float = 2.5) -> dict[str, str] | None:
    """Interroge l'API REST de Wikipédia France en conservant titre, URL, date de révision et ancienneté."""
    clean_q = re.sub(
        r"^(?:qui\s+est|qui\s+[ée]tait|c['’]\s*est\s+quoi|qu['’]\s*est[- ]ce\s+que|d[ée]finition\s+de|parle[- ]moi\s+de|info\s+sur)\s+",
        "",
        query or "",
        flags=re.IGNORECASE,
    ).strip(" ?.")
    if not clean_q or len(clean_q) < 2:
        return None

    try:
        api_url = f"https://fr.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(clean_q)}"
        headers = {"User-Agent": "JarvisAssistant/1.0 (moctar@jarvis)"}
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            extract = str(data.get("extract") or "").strip()
            if not extract or len(extract) <= 25:
                return None
            title = str(data.get("title") or clean_q).strip()
            page_url = (
                data.get("content_urls", {}).get("desktop", {}).get("page")
                or f"https://fr.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
            )
            raw_ts = data.get("timestamp")
            pub_date, freshness = _extract_date_and_freshness(extract, raw_timestamp=raw_ts)
            return {
                "title": f"Wikipédia ({title})",
                "url": str(page_url),
                "domain": "fr.wikipedia.org",
                "published_date": pub_date,
                "freshness": freshness,
                "age_hint": freshness,
                "snippet": extract,
            }
    except Exception as exc:
        logger.debug("Wikipedia summary pour '%s' indisponible: %s", clean_q, exc)

    return None


def search_wikipedia_summary(query: str, timeout: float = 2.5) -> str | None:
    """Interroge l'API REST de Wikipédia France et retourne un extrait sourcé."""
    info = search_wikipedia_structured(query, timeout=timeout)
    if not info:
        return None
    return _format_sourced_entry(info)


def search_web_sourced(query: str, max_results: int = 3) -> dict[str, Any]:
    """Exécute une recherche web sourcée et retourne la structure complète + le contexte LLM."""
    cleaned_query = (query or "").strip()
    retrieved_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if not cleaned_query:
        return {
            "success": False,
            "query": "",
            "retrieved_at": retrieved_at,
            "results": [],
            "formatted_context": "",
            "message": "Requête de recherche web vide.",
        }

    cache_key = cleaned_query.lower()
    now_t = time.monotonic()
    cached = _SEARCH_CACHE.get(cache_key)
    if cached and (now_t - cached[0]) < _SEARCH_CACHE_TTL_SECONDS:
        _, cached_retrieved_at, results = cached
        retrieved_at = cached_retrieved_at
    else:
        results = []
        wiki_item = search_wikipedia_structured(cleaned_query)
        if wiki_item:
            results.append(wiki_item)
        ddg_items = search_duckduckgo_structured(cleaned_query, max_results=max_results)
        results.extend(ddg_items)
        if results:
            _SEARCH_CACHE[cache_key] = (now_t, retrieved_at, results)

    if not results:
        return {
            "success": False,
            "query": cleaned_query,
            "retrieved_at": retrieved_at,
            "results": [],
            "formatted_context": "",
            "message": (
                f"Aucun résultat web vérifiable n'a pu être récupéré en direct pour '{cleaned_query}' "
                f"(recherche tentée à {retrieved_at})."
            ),
        }

    lines = [f"- {_format_sourced_entry(r)}" for r in results[: max(1, max_results)]]
    formatted_block = (
        f"[INFORMATIONS WEB ET ACTUALITÉS EN DIRECT — retrieved_at: {retrieved_at}]\n"
        + "\n".join(lines)
    )
    return {
        "success": True,
        "query": cleaned_query,
        "retrieved_at": retrieved_at,
        "results": results[: max(1, max_results)],
        "formatted_context": formatted_block,
        "message": formatted_block,
    }


def fetch_live_web_context(query: str, max_chars: int = 1200) -> str:
    """Récupère une synthèse textuelle sourcée (URL, date, ancienneté, retrieved_at) pour le prompt LLM.

    Met en cache le résultat pendant 15 minutes pour garantir une latence de 0 ms
    sur les questions récurrentes.
    """
    res = search_web_sourced(query, max_results=3)
    if not res.get("success") or not res.get("results"):
        return ""

    retrieved_at = res["retrieved_at"]
    entries = [f"- {_format_sourced_entry(r)}" for r in res["results"]]
    joined = "\n".join(entries)
    if len(joined) > max_chars:
        joined = joined[:max_chars].rstrip() + "..."

    return (
        f"[INFORMATIONS WEB ET ACTUALITÉS EN DIRECT — retrieved_at: {retrieved_at}]\n"
        f"{joined}"
    )
