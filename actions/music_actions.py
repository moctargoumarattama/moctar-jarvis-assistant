import webbrowser
from urllib.parse import quote_plus

import config


def play_music(query, recommender=None):
    try:
        import pywhatkit
    except Exception as exc:
        return f"Lecture YouTube indisponible pour le moment : {exc}"

    final_query = query.strip() if query else ""
    if not final_query and recommender:
        final_query = recommender().strip()

    if not final_query:
        final_query = "musique populaire"

    try:
        pywhatkit.playonyt(final_query)
        return f"Lecture de {final_query}."
    except Exception as exc:
        return f"Impossible de lancer la musique : {exc}"


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
