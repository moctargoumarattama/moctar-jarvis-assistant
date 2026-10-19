import re
import subprocess
import time
import webbrowser
from urllib.parse import quote_plus

try:
    import psutil
except Exception:
    class _PsutilFallback:
        @staticmethod
        def process_iter(_attrs):
            return []

    psutil = _PsutilFallback()

import config


_RECENT_URLS = {}
_BROWSER_PRIORITY = ("chrome", "brave", "edge")


def _normalize_browser_choice(browser):
    choice = (browser or "default").strip().lower()
    if choice in config.BROWSER_EXECUTABLES or choice == "default":
        return choice
    return config.WEB_SETTINGS["default_browser"]


def _get_running_browsers(process_provider=psutil.process_iter):
    running = set()
    for process in process_provider(["name"]):
        try:
            process_name = (process.info.get("name") or "").lower()
        except Exception:
            continue

        for browser_key, expected_name in config.BROWSER_PROCESS_NAMES.items():
            if process_name == expected_name.lower():
                running.add(browser_key)

    return running


def _select_browser(browser, running_browsers):
    choice = _normalize_browser_choice(browser)
    if choice != "default" and choice in running_browsers:
        return choice, True

    for candidate in _BROWSER_PRIORITY:
        if candidate in running_browsers:
            return candidate, True

    if choice in config.BROWSER_EXECUTABLES:
        return choice, False

    return "default", False


def _launch_browser_with_url(browser_key, url, launcher=subprocess.Popen):
    executable = config.BROWSER_EXECUTABLES.get(browser_key)
    if not executable:
        raise RuntimeError(f"navigateur {browser_key} non configure")

    launcher(
        ["cmd", "/c", "start", "", executable, url],
        shell=False,
    )


def _prune_recent_urls(now_value):
    cooldown = config.WEB_SETTINGS["duplicate_cooldown_seconds"]
    stale_urls = [
        url
        for url, opened_at in _RECENT_URLS.items()
        if now_value - opened_at >= cooldown
    ]
    for url in stale_urls:
        _RECENT_URLS.pop(url, None)


def _was_recently_opened(url, now_value):
    _prune_recent_urls(now_value)
    opened_at = _RECENT_URLS.get(url)
    if opened_at is None:
        return False
    return now_value - opened_at < config.WEB_SETTINGS["duplicate_cooldown_seconds"]


def _mark_opened(url, now_value):
    _RECENT_URLS[url] = now_value
    _prune_recent_urls(now_value)


def open_url_smart(
    url,
    browser="default",
    browser_module=webbrowser,
    process_provider=psutil.process_iter,
    now_provider=time.monotonic,
    launcher=subprocess.Popen,
):
    if not url:
        return "URL vide, rien a ouvrir."

    normalized_url = url.strip()
    now_value = now_provider()
    if _was_recently_opened(normalized_url, now_value):
        return "Ce site est deja en cours d'ouverture."

    running_browsers = _get_running_browsers(process_provider=process_provider)
    selected_browser, is_active_browser = _select_browser(browser, running_browsers)
    browser_label = config.BROWSER_LABELS.get(selected_browser, selected_browser)

    try:
        if selected_browser != "default":
            try:
                _launch_browser_with_url(
                    selected_browser,
                    normalized_url,
                    launcher=launcher,
                )
                _mark_opened(normalized_url, now_value)
                if is_active_browser:
                    return f"J'ouvre un nouvel onglet dans {browser_label}."
                return f"J'ouvre le site dans {browser_label}."
            except Exception:
                browser_module.open_new_tab(normalized_url)
                _mark_opened(normalized_url, now_value)
                return f"J'ouvre le site via le navigateur par defaut apres echec de {browser_label}."

        browser_module.open_new_tab(normalized_url)
        _mark_opened(normalized_url, now_value)
        if running_browsers:
            return "J'ouvre un nouvel onglet dans le navigateur actif."
        return "J'ouvre le site dans le navigateur par defaut."
    except Exception as exc:
        return f"Impossible d'ouvrir le site : {exc}"


def resolve_site_url(target: str = "") -> tuple[str, str]:
    """Résout une cible en URL absolue et titre de page avec table intelligente."""
    raw = (target or "").strip()
    if not raw:
        return "https://www.google.com", "Google"

    cleaned = raw.strip("\"'").strip()
    # Nettoyage des tournures de commande "ouvre youtube", "cherche...", etc.
    cleaned = re.sub(r"^(?:ouvre|ouvrez|lance|lancez|cherche|cherchez|va sur|navigue vers|go to)\s+(?:le site\s+|l'application\s+|l'app\s+|la page\s+)?", "", cleaned, flags=re.IGNORECASE).strip()
    if not cleaned:
        cleaned = raw.strip()
    lowered = cleaned.lower()

    if lowered.startswith("http://") or lowered.startswith("https://"):
        return cleaned, cleaned

    direct_map = {
        "youtube": ("https://www.youtube.com", "YouTube"),
        "google": ("https://www.google.com", "Google"),
        "github": ("https://github.com", "GitHub"),
        "wikipedia": ("https://fr.wikipedia.org", "Wikipédia"),
        "gmail": ("https://mail.google.com", "Gmail"),
        "whatsapp": ("https://web.whatsapp.com", "WhatsApp Web"),
        "pythonanywhere": ("https://www.pythonanywhere.com", "PythonAnywhere"),
    }
    for k, v in direct_map.items():
        if lowered == k or lowered.startswith(k + " ") or lowered.endswith(" " + k):
            return v

    if lowered in config.IMPORTANT_URLS:
        return config.IMPORTANT_URLS[lowered], cleaned.capitalize()

    # Détection de domaine avec extension (ex: babamarket.ma, facebook.com, site.fr)
    if re.match(r"^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(/.*)?$", cleaned) or re.match(r"^localhost(:\d+)?(/.*)?$", cleaned):
        return f"https://{cleaned}", cleaned

    # Terme générique ou recherche Web
    return f"https://www.google.com/search?q={quote_plus(cleaned)}", f"Recherche Google: {cleaned}"


def open_site(target: str = "", url: str = "", site: str = "", query: str = "", name: str = "", as_dict: bool = False, **kwargs):
    """Ouvre un site web de manière universelle et flexible."""
    chosen_target = target or url or site or query or name or ""
    if not chosen_target and kwargs.get("_raw_prompt"):
        chosen_target = kwargs["_raw_prompt"]

    target_url, title = resolve_site_url(chosen_target)

    status = open_url_smart(
        target_url,
        browser=config.WEB_SETTINGS["default_browser"],
    )

    if as_dict:
        return {
            "action": "open_browser",
            "url": target_url,
            "title": title,
            "status": status,
        }

    return status


def search_google(query):
    if not query:
        return "Que veux-tu chercher sur Google ?"

    url = f"{config.IMPORTANT_URLS['google']}/search?q={quote_plus(query)}"
    status = open_url_smart(url, browser=config.WEB_SETTINGS["default_browser"])
    if status.startswith("Impossible"):
        return f"Impossible de lancer la recherche Google : {status.split(': ', 1)[-1]}"
    return f"Recherche Google pour {query}. {status}"


def search_youtube(query):
    if not query:
        return "Que veux-tu chercher sur YouTube ?"

    url = f"{config.IMPORTANT_URLS['youtube']}/results?search_query={quote_plus(query)}"
    status = open_url_smart(url, browser=config.WEB_SETTINGS["default_browser"])
    if status.startswith("Impossible"):
        return f"Impossible de lancer la recherche YouTube : {status.split(': ', 1)[-1]}"
    return f"Recherche YouTube pour {query}. {status}"


def get_weather(city: str = "Niamey", target_day: str = "today") -> str:
    """Récupère les informations météorologiques en direct et prévisions pour une ville via Open-Meteo."""
    import weather_service
    res = weather_service.get_weather_report(city=city, target_day=target_day)
    return str(res.get("oral_text") or f"Données météo indisponibles pour {city}.")


def search_web_instant(query: str) -> str:
    """Recherche des faits ou des informations récentes sur le web en direct."""
    import web_search_service
    ctx = web_search_service.fetch_live_web_context(query)
    return ctx if ctx else f"Aucune information récente trouvée pour '{query}'."
