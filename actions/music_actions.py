import webbrowser
from urllib.parse import quote_plus

import config


def play_music(query, recommender=None, browser_opener=webbrowser.open_new_tab):
    final_query = query.strip() if query else ""
    if not final_query:
        final_query = recommender().strip() if recommender else ""
    if not final_query:
        return "Quelle musique veux-tu que je lance ? Donne-moi le titre ou l'artiste."

    try:
        # Opening an explicit YouTube Music search is much more dependable
        # than automating a browser page through pywhatkit.
        url = f"{config.IMPORTANT_URLS['youtube_music']}/search?q={quote_plus(final_query)}"
        if browser_opener(url) is False:
            return "Impossible d'ouvrir YouTube Music."
        return f"J'ouvre {final_query} dans YouTube Music."
    except Exception as exc:
        return f"Impossible de lancer la musique : {exc}"


def control_music(action, pyautogui_module=None):
    if pyautogui_module is None:
        from actions import system_actions
        pyautogui_module = system_actions.pyautogui

    mapping = {
        "music_pause": ("playpause", "Je mets la musique en pause."),
        "music_resume": ("playpause", "Je reprends la musique."),
        "music_next": ("nexttrack", "Je passe au morceau suivant."),
        "music_previous": ("prevtrack", "Je reviens au morceau precedent."),
    }
    key, response = mapping[action]
    try:
        pyautogui_module.press(key)
        return response
    except Exception as exc:
        return f"Impossible de controler la musique : {exc}"


def open_playlist(query):
    final_query = query.strip() if query else config.DEFAULT_SITE_SEARCH_QUERY
    if "playlist" not in final_query:
        final_query = f"playlist {final_query}".strip()

    try:
        webbrowser.open(
            f"{config.IMPORTANT_URLS['youtube']}/results?search_query={quote_plus(final_query)}"
        )
        return f"Je cherche {final_query}."
    except Exception as exc:
        return f"Impossible d'ouvrir la playlist : {exc}"
