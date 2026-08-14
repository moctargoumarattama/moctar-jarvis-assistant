"""
Platform connectors for the scheduler.

WhatsApp  — pywhatkit.sendwhatmsg_instantly  (free, uses WhatsApp Web)
Facebook  — Graph API (free token, post to page/group)
TikTok    — deep-link + clipboard (semi-manual confirmation)
"""

import logging
import os
import webbrowser
from urllib.parse import quote

import config

logger = logging.getLogger("jarvis.scheduler.platforms")

# --------------------------------------------------------------------------- #
#  WhatsApp                                                                    #
# --------------------------------------------------------------------------- #

def send_whatsapp(target: str, message: str) -> tuple[bool, str]:
    """
    Send a WhatsApp message via pywhatkit.
    target: phone number (+2236XXXXXXXX) or group invite link/name.
    Returns (success, detail_message).
    """
    try:
        import pywhatkit  # already in requirements.txt
    except ImportError:
        return False, "pywhatkit non installe. Lancez: pip install pywhatkit"

    target = target.strip()
    if not target:
        return False, "Numero ou lien de groupe WhatsApp manquant."

    try:
        # sendwhatmsg_instantly opens WhatsApp Web and sends without waiting
        wait_time = config.SCHEDULER_SETTINGS.get("whatsapp_wait_time", 15)
        pywhatkit.sendwhatmsg_instantly(
            target,
            message,
            wait_time=wait_time,
            tab_close=True,
            close_time=5,
        )
        logger.info("WhatsApp message sent to %s", target)
        return True, f"Message WhatsApp envoye a {target}"
    except Exception as exc:
        logger.error("WhatsApp send error: %s", exc)
        return False, f"Erreur WhatsApp : {exc}"


# --------------------------------------------------------------------------- #
#  Facebook                                                                    #
# --------------------------------------------------------------------------- #

def send_facebook(target: str, message: str) -> tuple[bool, str]:
    """
    Post a message to a Facebook page or group via the free Graph API.
    target: page_id or group_id.
    Requires FB_ACCESS_TOKEN env var.
    """
    try:
        import requests
    except ImportError:
        return False, "requests non disponible."

    access_token = os.getenv("FB_ACCESS_TOKEN", "").strip()
    if not access_token:
        return False, (
            "Token Facebook manquant. "
            "Ajoutez FB_ACCESS_TOKEN dans votre .env "
            "(token gratuit depuis developers.facebook.com)."
        )

    target = target.strip()
    if not target:
        return False, "ID de page/groupe Facebook manquant."

    url = f"https://graph.facebook.com/v19.0/{target}/feed"
    payload = {"message": message, "access_token": access_token}
    try:
        resp = requests.post(url, data=payload, timeout=15)
        data = resp.json()
        if "id" in data:
            logger.info("Facebook post published to %s: %s", target, data["id"])
            return True, f"Publication Facebook envoyee (id: {data['id']})"
        error_msg = data.get("error", {}).get("message", str(data))
        logger.error("Facebook API error: %s", error_msg)
        return False, f"Erreur Facebook API : {error_msg}"
    except Exception as exc:
        logger.error("Facebook send error: %s", exc)
        return False, f"Erreur Facebook : {exc}"


# --------------------------------------------------------------------------- #
#  TikTok  (semi-manuel — deep link + clipboard)                               #
# --------------------------------------------------------------------------- #

def send_tiktok(target: str, message: str) -> tuple[bool, str]:
    """
    TikTok does not allow direct text posting via free API.
    We open the TikTok upload page in the browser and copy the message
    to the clipboard so the user just has to paste & confirm.
    Returns (True, info_message) since this is always user-confirmed.
    """
    try:
        import pyperclip  # optional, best-effort
        pyperclip.copy(message)
        clipboard_ok = True
    except Exception:
        clipboard_ok = False

    tiktok_url = "https://www.tiktok.com/upload"
    webbrowser.open(tiktok_url)

    detail = (
        "TikTok ouvert dans le navigateur. "
        + ("Message copie dans le presse-papier — collez-le dans la description." if clipboard_ok
           else "Copiez votre message manuellement dans la description TikTok.")
    )
    logger.info("TikTok deep-link opened for target=%s", target)
    return True, detail


# --------------------------------------------------------------------------- #
#  Dispatcher                                                                  #
# --------------------------------------------------------------------------- #

PLATFORM_HANDLERS = {
    "whatsapp": send_whatsapp,
    "facebook": send_facebook,
    "tiktok": send_tiktok,
}

PLATFORM_LABELS = {
    "whatsapp": "WhatsApp",
    "facebook": "Facebook",
    "tiktok": "TikTok",
}


def dispatch(platform: str, target: str, message: str) -> tuple[bool, str]:
    """Route a message to the correct platform handler."""
    handler = PLATFORM_HANDLERS.get(platform.lower().strip())
    if handler is None:
        return False, f"Plateforme inconnue : {platform}"
    return handler(target, message)
