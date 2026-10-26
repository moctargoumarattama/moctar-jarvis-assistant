"""Connecteur Google Workspace (Gmail, Google Calendar, Google Drive) pour M.O.C.T.A.R.

Permet l'interrogation rapide de vos e-mails non lus, l'accès à votre agenda du jour
et la recherche de documents sur Google Drive.
Gère gracieusement l'absence de credentials.json sans jamais lever d'exception.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

LOGGER = logging.getLogger("moctar.google_workspace")

_CREDS_DIR = config.APP_ROOT / "credentials"

# Chemins standards de détection des credentials Google OAuth
DEFAULT_CREDENTIALS_PATHS = [
    Path(os.getenv("GOOGLE_WORKSPACE_CREDENTIALS_PATH", "")) if os.getenv("GOOGLE_WORKSPACE_CREDENTIALS_PATH") else None,
    _CREDS_DIR / "credentials.json",
    config.APP_ROOT / "credentials.json",
    Path("./credentials/credentials.json"),
    Path("./credentials.json"),
    Path.home() / ".config" / "jarvis" / "credentials.json",
]
CREDENTIALS_PATHS = [p for p in DEFAULT_CREDENTIALS_PATHS if p is not None]

DEFAULT_TOKEN_PATHS = [
    _CREDS_DIR / "token.json",
    config.APP_ROOT / "token.json",
    Path("./credentials/token.json"),
    Path("./token.json"),
    Path.home() / ".config" / "jarvis" / "token.json",
]


def _ensure_moctar_ending(message: str) -> str:
    cleaned = (message or "").strip()
    if not cleaned:
        return "Opération Google Workspace terminée, Moctar."
    if re.search(r"\bmoctar\.?$", cleaned, flags=re.IGNORECASE):
        return cleaned if cleaned.endswith(".") else f"{cleaned}."
    cleaned = cleaned.rstrip(".!? ")
    return f"{cleaned}, Moctar."


def find_credentials_file() -> Optional[Path]:
    """Recherche l'emplacement du fichier credentials.json."""
    for p in CREDENTIALS_PATHS:
        try:
            if p and p.is_file() and p.stat().st_size > 0:
                return p
        except Exception:
            continue
    return None


def find_token_file() -> Optional[Path]:
    """Recherche l'emplacement du token d'accès OAuth token.json."""
    for p in DEFAULT_TOKEN_PATHS:
        try:
            if p and p.is_file() and p.stat().st_size > 0:
                return p
        except Exception:
            continue
    return None


def is_workspace_configured() -> bool:
    """Indique si les identifiants Google Workspace sont présents."""
    return find_credentials_file() is not None or find_token_file() is not None


def _format_missing_credentials_reply(service_name: str = "Google Workspace") -> str:
    """Message courtois et guidé en l'absence de credentials.json."""
    return _ensure_moctar_ending(
        f"Le module {service_name} est prêt mais nécessite votre clé OAuth. "
        f"Déposez votre fichier credentials.json dans le dossier {_CREDS_DIR / 'credentials.json'} "
        "pour activer la synchronisation de vos e-mails et agendas"
    )


# =============================================================================
# 1. GMAIL (E-MAILS NON LUS)
# =============================================================================
def get_unread_emails_summary(max_results: int = 5) -> Dict[str, Any]:
    """Récupère la synthèse des e-mails non lus dans la boîte de réception Gmail.

    Returns:
        Dict[str, Any] avec 'success', 'reply' (terminant par 'Moctar.'), 'emails'.
    """
    creds_path = find_credentials_file()
    token_path = find_token_file()

    if not creds_path and not token_path:
        return {
            "success": False,
            "configured": False,
            "reply": _format_missing_credentials_reply("Gmail"),
            "emails": [],
            "count": 0,
        }

    # Tentative de chargement via google-api-python-client
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = None
        if token_path and token_path.is_file():
            creds = Credentials.from_authorized_user_file(str(token_path))

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                return {
                    "success": False,
                    "configured": True,
                    "reply": _ensure_moctar_ending(
                        f"Une ré-authentification Google Workspace est requise pour Gmail. "
                        f"Veuillez renouveler token.json dans {_CREDS_DIR}/"
                    ),
                    "emails": [],
                    "count": 0,
                }

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        results = service.users().messages().list(userId="me", q="is:unread in:inbox", maxResults=max_results).execute()
        messages = results.get("messages", [])

        if not messages:
            return {
                "success": True,
                "configured": True,
                "reply": _ensure_moctar_ending("Votre boîte de réception Gmail est à jour. Aucun e-mail non lu"),
                "emails": [],
                "count": 0,
            }

        email_items = []
        for msg_meta in messages[:max_results]:
            msg = service.users().messages().get(userId="me", id=msg_meta["id"], format="metadata").execute()
            headers = msg.get("payload", {}).get("headers", [])
            subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "(Sans objet)")
            sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "Expéditeur inconnu")
            sender_clean = re.sub(r"<.*?>", "", sender).strip()
            email_items.append({"sender": sender_clean, "subject": subject, "snippet": msg.get("snippet", "")})

        count = len(messages)
        items_speech = ", ".join([f"de {e['sender']} concernant {e['subject']}" for e in email_items[:3]])
        reply = f"Vous avez {count} e-mails non lus dans votre boîte principale : {items_speech}"
        return {
            "success": True,
            "configured": True,
            "reply": _ensure_moctar_ending(reply),
            "emails": email_items,
            "count": count,
        }

    except ImportError:
        LOGGER.debug("google-api-python-client non installé")
        return {
            "success": False,
            "configured": True,
            "reply": _ensure_moctar_ending(
                "Le connecteur Gmail requiert la bibliothèque google-api-python-client. "
                "Activez-la avec pip install google-api-python-client google-auth"
            ),
            "emails": [],
            "count": 0,
        }
    except Exception as err:
        LOGGER.warning("Erreur interrogation Gmail: %s", err)
        return {
            "success": False,
            "configured": True,
            "reply": _ensure_moctar_ending(f"Impossible de synchroniser Gmail pour l'instant : {str(err)[:80]}"),
            "emails": [],
            "count": 0,
        }


# =============================================================================
# 2. GOOGLE CALENDAR (AGENDA DU JOUR & RENDEZ-VOUS)
# =============================================================================
def get_today_agenda_summary() -> Dict[str, Any]:
    """Récupère les prochains événements de l'agenda Google Calendar pour la journée.

    Returns:
        Dict[str, Any] avec 'success', 'reply' (terminant par 'Moctar.'), 'events'.
    """
    creds_path = find_credentials_file()
    token_path = find_token_file()

    if not creds_path and not token_path:
        return {
            "success": False,
            "configured": False,
            "reply": _format_missing_credentials_reply("Google Calendar"),
            "events": [],
            "count": 0,
        }

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = None
        if token_path and token_path.is_file():
            creds = Credentials.from_authorized_user_file(str(token_path))

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                return {
                    "success": False,
                    "configured": True,
                    "reply": _ensure_moctar_ending(
                        f"Une ré-authentification est requise pour Google Calendar dans {_CREDS_DIR}/"
                    ),
                    "events": [],
                    "count": 0,
                }

        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        start_of_day = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_of_day = (now_dt + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        events_result = service.events().list(
            calendarId="primary",
            timeMin=start_of_day,
            timeMax=end_of_day,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])
        if not events:
            return {
                "success": True,
                "configured": True,
                "reply": _ensure_moctar_ending("Votre agenda Google Calendar est totalement libre pour aujourd'hui"),
                "events": [],
                "count": 0,
            }

        event_items = []
        speech_parts = []
        for event in events[:5]:
            summary = event.get("summary", "Événement sans titre")
            start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
            time_label = ""
            if "T" in start:
                try:
                    time_part = start.split("T")[1][:5]
                    time_label = f"à {time_part}"
                except Exception:
                    pass
            event_items.append({"summary": summary, "start": start, "time": time_label})
            speech_parts.append(f"{summary} {time_label}".strip())

        count = len(events)
        reply = f"Vous avez {count} rendez-vous programmés aujourd'hui : " + ", ".join(speech_parts)
        return {
            "success": True,
            "configured": True,
            "reply": _ensure_moctar_ending(reply),
            "events": event_items,
            "count": count,
        }

    except ImportError:
        LOGGER.debug("google-api-python-client non installé")
        return {
            "success": False,
            "configured": True,
            "reply": _ensure_moctar_ending(
                "Le connecteur Google Calendar requiert la bibliothèque google-api-python-client"
            ),
            "events": [],
            "count": 0,
        }
    except Exception as err:
        LOGGER.warning("Erreur interrogation Calendar: %s", err)
        return {
            "success": False,
            "configured": True,
            "reply": _ensure_moctar_ending(f"Impossible de charger Google Calendar pour l'instant : {str(err)[:80]}"),
            "events": [],
            "count": 0,
        }


# =============================================================================
# 3. GOOGLE DRIVE (RECHERCHE DE DOCUMENTS)
# =============================================================================
def search_drive_documents(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Recherche des fichiers ou documents dans Google Drive.

    Args:
        query (str): Nom ou terme de recherche.

    Returns:
        Dict[str, Any] avec 'success', 'reply' (terminant par 'Moctar.'), 'files'.
    """
    creds_path = find_credentials_file()
    token_path = find_token_file()

    if not creds_path and not token_path:
        return {
            "success": False,
            "configured": False,
            "reply": _format_missing_credentials_reply("Google Drive"),
            "files": [],
            "count": 0,
        }

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = None
        if token_path and token_path.is_file():
            creds = Credentials.from_authorized_user_file(str(token_path))

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                return {
                    "success": False,
                    "configured": True,
                    "reply": _ensure_moctar_ending(
                        f"Une ré-authentification est requise pour Google Drive dans {_CREDS_DIR}/"
                    ),
                    "files": [],
                    "count": 0,
                }

        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        escaped_query = query.replace("'", "\\'")
        q_filter = f"name contains '{escaped_query}' and trashed = false" if query else "trashed = false"

        results = service.files().list(
            q=q_filter,
            pageSize=max_results,
            fields="files(id, name, mimeType, webViewLink)",
        ).execute()

        files = results.get("files", [])
        if not files:
            return {
                "success": True,
                "configured": True,
                "reply": _ensure_moctar_ending(f"Aucun document trouvé sur Google Drive correspondant à '{query}'"),
                "files": [],
                "count": 0,
            }

        file_names = [f.get("name", "Document sans nom") for f in files]
        count = len(files)
        files_speech = ", ".join(file_names[:3])
        reply = f"J'ai trouvé {count} document(s) sur Google Drive pour '{query}' : {files_speech}"
        return {
            "success": True,
            "configured": True,
            "reply": _ensure_moctar_ending(reply),
            "files": files,
            "count": count,
        }

    except ImportError:
        return {
            "success": False,
            "configured": True,
            "reply": _ensure_moctar_ending("Le connecteur Google Drive requiert la bibliothèque google-api-python-client"),
            "files": [],
            "count": 0,
        }
    except Exception as err:
        LOGGER.warning("Erreur interrogation Drive: %s", err)
        return {
            "success": False,
            "configured": True,
            "reply": _ensure_moctar_ending(f"Recherche Google Drive indisponible : {str(err)[:80]}"),
            "files": [],
            "count": 0,
        }

