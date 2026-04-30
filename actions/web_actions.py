import subprocess
import time
import webbrowser
from urllib.parse import quote_plus

import psutil

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


def open_site(target):
    url = config.IMPORTANT_URLS.get(target)
    if not url:
        return "Je ne connais pas encore ce site."

    return open_url_smart(
        url,
        browser=config.WEB_SETTINGS["default_browser"],
    )


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
