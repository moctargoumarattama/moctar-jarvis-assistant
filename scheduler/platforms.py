"""
Platform connectors for the scheduler.

WhatsApp  — Playwright (WhatsApp Web, session persistante, aucune API tierce)
Facebook  — Playwright (Facebook Web, session persistante, aucune API tierce)

Aucune API de réseau social n'est utilisée.  Les deux connecteurs ouvrent le
navigateur local de l'utilisateur avec un profil persistant (les cookies de
connexion sont conservés entre les sessions) et préparent la publication.
L'utilisateur doit être connecté à chaque service dans ce profil.
"""

import logging
import sys
from pathlib import Path
from urllib.parse import quote

import config

logger = logging.getLogger("jarvis.scheduler.platforms")

# Répertoire de données utilisateur Playwright (cookies persistants)
_BROWSER_DATA_DIR = config.DATA_DIR / "browser_profiles"


def _get_playwright_context(profile_name: str):
    """
    Return a (playwright, browser_context) tuple using a persistent profile
    so the user's login session is preserved between JARVIS runs.
    Raises ImportError if playwright is not installed.
    Raises RuntimeError if the browser cannot be launched.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "playwright non installe. Lancez : pip install playwright && "
            "playwright install chromium"
        ) from exc

    user_data_dir = str(_BROWSER_DATA_DIR / profile_name)
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir,
        headless=False,
        args=["--start-maximized"],
        no_viewport=True,
    )
    return pw, context


# --------------------------------------------------------------------------- #
#  WhatsApp Web                                                                #
# --------------------------------------------------------------------------- #

def send_whatsapp(target: str, message: str) -> tuple[bool, str]:
    """
    Envoie un message WhatsApp via WhatsApp Web (Playwright, sans API tierce).
    target : numéro de téléphone international (+2236XXXXXXXX).
    Le navigateur utilise un profil persistant — l'utilisateur doit être
    connecté à WhatsApp Web dans ce profil.
    """
    target = target.strip()
    if not target:
        return False, "Numéro de téléphone WhatsApp manquant (ex: +2236XXXXXXXX)."

    # Normalise le numéro : retire espaces et tirets
    phone = target.replace(" ", "").replace("-", "")
    # URL de démarrage direct d'une conversation avec pré-remplissage du texte
    url = f"https://web.whatsapp.com/send?phone={quote(phone)}&text={quote(message)}"

    try:
        pw, context = _get_playwright_context("whatsapp")
    except ImportError as exc:
        return False, str(exc)
    except Exception as exc:
        logger.error("Playwright launch error (WhatsApp): %s", exc)
        return False, f"Impossible de lancer le navigateur : {exc}"

    try:
        page = context.new_page() if not context.pages else context.pages[0]
        page.goto(url, timeout=30_000)
        # Attend que la zone de saisie du message soit disponible
        send_box = page.locator(
            "div[data-tab='10'][contenteditable='true'], "
            "footer div[contenteditable='true']"
        ).first
        send_box.wait_for(state="visible", timeout=30_000)
        # Le texte est déjà pré-rempli via l'URL ; on appuie sur Entrée pour envoyer.
        send_box.press("Enter")
        logger.info("WhatsApp message sent to %s via WhatsApp Web", phone)
        return True, f"Message WhatsApp envoyé à {phone} via WhatsApp Web."
    except Exception as exc:
        logger.error("WhatsApp Web send error: %s", exc)
        return False, (
            f"Erreur WhatsApp Web : {exc}. "
            "Vérifiez que vous êtes connecté à WhatsApp Web dans le profil JARVIS."
        )
    finally:
        try:
            context.close()
            pw.stop()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Facebook Web                                                                #
# --------------------------------------------------------------------------- #

def send_facebook(target: str, message: str) -> tuple[bool, str]:
    """
    Prépare une publication Facebook via Facebook Web (Playwright, sans API tierce).
    target : identifiant ou slug de la page/groupe (ex: 'mon.groupe' ou
             'groups/123456789'), ou URL complète.
    Le navigateur utilise un profil persistant — l'utilisateur doit être
    connecté à Facebook dans ce profil.
    Le texte est pré-rempli dans le compositeur ; l'utilisateur clique
    « Publier » pour confirmer l'envoi final.
    """
    target = target.strip()
    if not target:
        return False, "Identifiant de page/groupe Facebook manquant."

    # Construit l'URL de destination
    if target.startswith("http://") or target.startswith("https://"):
        fb_url = target
    else:
        fb_url = f"https://www.facebook.com/{target}"

    try:
        pw, context = _get_playwright_context("facebook")
    except ImportError as exc:
        return False, str(exc)
    except Exception as exc:
        logger.error("Playwright launch error (Facebook): %s", exc)
        return False, f"Impossible de lancer le navigateur : {exc}"

    try:
        page = context.new_page() if not context.pages else context.pages[0]
        page.goto(fb_url, timeout=30_000)

        # Ouvre le compositeur de publication (zone "Qu'avez-vous en tête ?")
        composer_triggers = [
            "[data-pagelet='GroupComposer'] [role='button']",
            "[data-pagelet='FeedComposer'] [role='button']",
            "div[aria-label*='publication']",
            "div[aria-label*='post']",
            "div[aria-placeholder*='en tête']",
            "div[aria-placeholder*='mind']",
        ]
        opened = False
        for selector in composer_triggers:
            try:
                trigger = page.locator(selector).first
                trigger.wait_for(state="visible", timeout=5_000)
                trigger.click()
                opened = True
                break
            except Exception:
                continue

        if not opened:
            logger.warning("Facebook composer trigger not found for %s", target)

        # Cherche la zone de saisie du compositeur et y écrit le message
        text_selectors = [
            "div[aria-label*='publication'][contenteditable='true']",
            "div[aria-label*='post'][contenteditable='true']",
            "div[contenteditable='true'][role='textbox']",
        ]
        typed = False
        for sel in text_selectors:
            try:
                box = page.locator(sel).first
                box.wait_for(state="visible", timeout=8_000)
                box.click()
                box.type(message, delay=30)
                typed = True
                break
            except Exception:
                continue

        if typed:
            logger.info("Facebook post prepared for %s — waiting user confirmation", target)
            return True, (
                "Publication Facebook préparée dans le navigateur. "
                "Vérifiez le texte puis cliquez « Publier » pour confirmer."
            )
        else:
            logger.warning("Facebook composer text box not found for %s", target)
            return True, (
                "Facebook ouvert dans le navigateur. "
                "Le compositeur de publication n'a pas pu être rempli automatiquement — "
                "copiez votre message manuellement, puis cliquez « Publier »."
            )
    except Exception as exc:
        logger.error("Facebook Web error: %s", exc)
        return False, (
            f"Erreur Facebook Web : {exc}. "
            "Vérifiez que vous êtes connecté à Facebook dans le profil JARVIS."
        )
    # Ne ferme PAS le contexte ni le processus Playwright : le navigateur doit
    # rester ouvert pour que l'utilisateur puisse cliquer « Publier ».



# --------------------------------------------------------------------------- #
#  Dispatcher                                                                  #
# --------------------------------------------------------------------------- #

PLATFORM_HANDLERS = {
    "whatsapp": send_whatsapp,
    "facebook": send_facebook,
}

PLATFORM_LABELS = {
    "whatsapp": "WhatsApp",
    "facebook": "Facebook",
}


def dispatch(platform: str, target: str, message: str) -> tuple[bool, str]:
    """Route a message to the correct platform handler."""
    handler = PLATFORM_HANDLERS.get(platform.lower().strip())
    if handler is None:
        return False, f"Plateforme inconnue : {platform}"
    return handler(target, message)
