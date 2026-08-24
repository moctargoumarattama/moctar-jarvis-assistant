"""
Platform connectors for the scheduler.

WhatsApp uses WhatsApp Web through Playwright with a persistent profile.
Facebook uses Facebook Web through Playwright with a persistent profile.

No social-network API is used here. Both connectors depend on a logged-in
browser session, so they are not true headless server-side deliveries yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import config

logger = logging.getLogger("jarvis.scheduler.platforms")

_BROWSER_DATA_DIR = config.DATA_DIR / "browser_profiles"
_LIVE_MANUAL_CONTEXTS: list[tuple[object, object]] = []

_WHATSAPP_COMPOSER_SELECTORS = [
    "div[data-tab='10'][contenteditable='true']",
    "footer div[contenteditable='true']",
    "div[contenteditable='true'][role='textbox']",
]

_MEDIA_INPUT_SELECTORS = [
    "input[type='file'][accept*='image']",
    "input[type='file'][accept*='video']",
    "input[type='file']",
]


@dataclass(frozen=True)
class DispatchResult:
    state: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.state != "error"

    @property
    def requires_manual_action(self) -> bool:
        return self.state == "prepared"

    @property
    def is_final(self) -> bool:
        return self.state == "sent"

    def __iter__(self):
        yield self.ok
        yield self.detail


def _error(detail: str) -> DispatchResult:
    return DispatchResult("error", detail)


def _sent(detail: str) -> DispatchResult:
    return DispatchResult("sent", detail)


def _prepared(detail: str) -> DispatchResult:
    return DispatchResult("prepared", detail)


def _remember_manual_context(playwright, context) -> None:
    _LIVE_MANUAL_CONTEXTS.append((playwright, context))


def _normalize_media_paths(media_paths=None) -> list[Path]:
    if not media_paths:
        return []
    if isinstance(media_paths, (str, Path)):
        raw_paths = [media_paths]
    else:
        raw_paths = list(media_paths)

    normalized = []
    seen = set()
    for item in raw_paths:
        file_path = Path(str(item or "").strip()).expanduser()
        if not str(file_path) or str(file_path) in seen:
            continue
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"Fichier media introuvable : {file_path}")
        seen.add(str(file_path))
        normalized.append(file_path)
    return normalized


def _get_playwright_context(profile_name: str):
    """
    Return a (playwright, browser_context) tuple using a persistent profile
    so the user's login session is preserved between JARVIS runs.
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


def _wait_for_first_visible(page, selectors: list[str], timeout: int):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout)
            return locator
        except Exception:
            continue
    return None


def _click_first_visible(page, selectors: list[str], timeout: int = 5_000) -> bool:
    locator = _wait_for_first_visible(page, selectors, timeout)
    if locator is None:
        return False
    locator.click()
    return True


def _type_first_visible(page, selectors: list[str], text: str, timeout: int = 8_000) -> bool:
    if not (text or "").strip():
        return False
    locator = _wait_for_first_visible(page, selectors, timeout)
    if locator is None:
        return False
    locator.click()
    locator.type(text, delay=20)
    return True


def _set_first_input_files(page, selectors: list[str], file_paths) -> bool:
    payload = [str(path) for path in file_paths] if isinstance(file_paths, list) else str(file_paths)
    for selector in selectors:
        try:
            page.locator(selector).first.set_input_files(payload)
            return True
        except Exception:
            continue
    return False


def send_whatsapp(target: str, message: str, media_paths=None) -> DispatchResult:
    """
    Send a WhatsApp message through WhatsApp Web.
    """
    target = target.strip()
    if not target:
        return _error("Numero de telephone WhatsApp manquant (ex: +2236XXXXXXXX).")

    try:
        media_files = _normalize_media_paths(media_paths)
    except FileNotFoundError as exc:
        return _error(str(exc))

    if not media_files and not message.strip():
        return _error("Message ou media WhatsApp manquant.")

    phone = target.replace(" ", "").replace("-", "")
    if media_files:
        url = f"https://web.whatsapp.com/send?phone={quote(phone)}"
    else:
        url = f"https://web.whatsapp.com/send?phone={quote(phone)}&text={quote(message)}"

    try:
        pw, context = _get_playwright_context("whatsapp")
    except ImportError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Playwright launch error (WhatsApp): %s", exc)
        return _error(f"Impossible de lancer le navigateur : {exc}")

    try:
        page = context.new_page() if not context.pages else context.pages[0]
        page.goto(url, timeout=30_000)
        composer = _wait_for_first_visible(page, _WHATSAPP_COMPOSER_SELECTORS, 30_000)
        if composer is None:
            raise RuntimeError("Zone de saisie WhatsApp introuvable.")

        if media_files:
            _click_first_visible(
                page,
                [
                    "div[title='Attach']",
                    "button[aria-label*='Attach']",
                    "button[aria-label*='Joindre']",
                    "span[data-icon='plus-rounded']",
                ],
                timeout=3_000,
            )
            if not _set_first_input_files(page, _MEDIA_INPUT_SELECTORS, media_files):
                return _error("Impossible d'ajouter les medias dans WhatsApp Web.")
            page.wait_for_timeout(1_500)
            _type_first_visible(page, _WHATSAPP_COMPOSER_SELECTORS, message, timeout=20_000)
            send_button = _wait_for_first_visible(
                page,
                [
                    "span[data-icon='send']",
                    "button[aria-label*='Envoyer']",
                    "button[aria-label*='Send']",
                ],
                30_000,
            )
            if send_button is None:
                raise RuntimeError("Bouton d'envoi WhatsApp introuvable apres ajout du media.")
            send_button.click()
            logger.info("WhatsApp media message sent to %s via WhatsApp Web", phone)
            return _sent(f"Message WhatsApp avec {len(media_files)} media(s) envoye a {phone}.")

        composer.press("Enter")
        logger.info("WhatsApp message sent to %s via WhatsApp Web", phone)
        return _sent(f"Message WhatsApp envoye a {phone} via WhatsApp Web.")
    except Exception as exc:
        logger.error("WhatsApp Web send error: %s", exc)
        return _error(
            f"Erreur WhatsApp Web : {exc}. "
            "Verifiez que vous etes connecte a WhatsApp Web dans le profil JARVIS."
        )
    finally:
        try:
            context.close()
            pw.stop()
        except Exception:
            pass


def send_facebook(target: str, message: str, media_paths=None) -> DispatchResult:
    """
    Prepare a Facebook publication through Facebook Web.

    The browser stays open because the final click on "Publier" is still a
    manual step with the current web automation approach.
    """
    target = target.strip()
    if not target:
        return _error("Identifiant de page/groupe Facebook manquant.")

    try:
        media_files = _normalize_media_paths(media_paths)
    except FileNotFoundError as exc:
        return _error(str(exc))

    if target.startswith("http://") or target.startswith("https://"):
        fb_url = target
    else:
        fb_url = f"https://www.facebook.com/{target}"

    try:
        pw, context = _get_playwright_context("facebook")
    except ImportError as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.error("Playwright launch error (Facebook): %s", exc)
        return _error(f"Impossible de lancer le navigateur : {exc}")

    try:
        page = context.new_page() if not context.pages else context.pages[0]
        page.goto(fb_url, timeout=30_000)

        _click_first_visible(
            page,
            [
                "[data-pagelet='GroupComposer'] [role='button']",
                "[data-pagelet='FeedComposer'] [role='button']",
                "div[aria-label*='publication']",
                "div[aria-label*='post']",
                "div[aria-placeholder*='en tete']",
                "div[aria-placeholder*='mind']",
            ],
            timeout=5_000,
        )

        typed = _type_first_visible(
            page,
            [
                "div[aria-label*='publication'][contenteditable='true']",
                "div[aria-label*='post'][contenteditable='true']",
                "div[contenteditable='true'][role='textbox']",
            ],
            message,
            timeout=8_000,
        )

        media_attached = False
        if media_files:
            _click_first_visible(
                page,
                [
                    "div[aria-label*='Photo/video']",
                    "div[aria-label*='Photo']",
                    "div[role='button'][aria-label*='video']",
                ],
                timeout=3_000,
            )
            media_attached = _set_first_input_files(page, _MEDIA_INPUT_SELECTORS, media_files)
            if not media_attached:
                return _error("Impossible d'ajouter les medias dans Facebook.")
            page.wait_for_timeout(1_500)

        _remember_manual_context(pw, context)
        if typed and media_attached:
            return _prepared(
                f"Publication Facebook preparee avec texte et {len(media_files)} media(s). "
                "Verifiez le contenu puis cliquez sur Publier."
            )
        if media_attached:
            return _prepared(
                f"Publication Facebook preparee avec {len(media_files)} media(s). "
                "Ajoutez ou verifiez le texte puis cliquez sur Publier."
            )
        if typed:
            return _prepared(
                "Publication Facebook preparee dans le navigateur. "
                "Verifiez le texte puis cliquez sur Publier pour terminer l'envoi."
            )
        return _prepared(
            "Facebook est ouvert dans le navigateur. "
            "Le compositeur n'a pas pu etre rempli automatiquement : "
            "completez la publication puis cliquez sur Publier."
        )
    except Exception as exc:
        logger.error("Facebook Web error: %s", exc)
        return _error(
            f"Erreur Facebook Web : {exc}. "
            "Verifiez que vous etes connecte a Facebook dans le profil JARVIS."
        )


PLATFORM_HANDLERS = {
    "whatsapp": send_whatsapp,
    "facebook": send_facebook,
}

PLATFORM_LABELS = {
    "whatsapp": "WhatsApp",
    "facebook": "Facebook",
}


def dispatch(
    platform: str,
    target: str,
    message: str,
    media_paths=None,
) -> DispatchResult:
    """Route a message to the correct platform handler."""
    handler = PLATFORM_HANDLERS.get(platform.lower().strip())
    if handler is None:
        return _error(f"Plateforme inconnue : {platform}")
    return handler(target, message, media_paths)
