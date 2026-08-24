"""
Tests for scheduler/platforms.py

Vérifie que :
- Aucune référence à FB_ACCESS_TOKEN / Graph API n'est présente
- send_whatsapp retourne une erreur propre si la cible est vide
- send_whatsapp retourne une erreur propre si playwright n'est pas installé
- send_facebook retourne une erreur propre si la cible est vide
- send_facebook retourne une erreur propre si playwright n'est pas installé
- send_facebook retourne True avec confirmation manuelle si le compositeur est introuvable
- send_facebook navigue vers l'URL complète fournie sans modification
- dispatch route correctement vers les handlers
- dispatch retourne une erreur pour une plateforme inconnue
"""

import importlib
import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_browser_mocks(nav_raises=False):
    """
    Retourne (mock_pw, mock_context, mock_page) avec des comportements
    configurables pour les tests.
    """
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]
    mock_pw = MagicMock()

    if nav_raises:
        mock_page.goto.side_effect = Exception("network error")

    # Par défaut, les locators réussissent
    mock_page.locator.return_value.first.wait_for.return_value = None
    mock_page.locator.return_value.first.click.return_value = None
    mock_page.locator.return_value.first.press.return_value = None
    mock_page.locator.return_value.first.type.return_value = None

    return mock_pw, mock_context, mock_page


# ---------------------------------------------------------------------------
# Source-code-level checks (no API / no token references)
# ---------------------------------------------------------------------------

class TestNoBannedReferences(unittest.TestCase):
    """Vérifie l'absence de références bannies dans le code source."""

    def _source(self):
        from scheduler import platforms
        return inspect.getsource(platforms)

    def test_no_fb_access_token(self):
        self.assertNotIn("FB_ACCESS_TOKEN", self._source())

    def test_no_graph_facebook_api(self):
        self.assertNotIn("graph.facebook.com", self._source())

    def test_no_pywhatkit(self):
        self.assertNotIn("pywhatkit", self._source())

    def test_no_requests_import(self):
        self.assertNotIn("import requests", self._source())


# ---------------------------------------------------------------------------
# send_whatsapp
# ---------------------------------------------------------------------------

class TestSendWhatsapp(unittest.TestCase):

    def setUp(self):
        from scheduler import platforms as _p
        self.platforms = _p

    def test_empty_target_returns_error(self):
        ok, msg = self.platforms.send_whatsapp("", "Bonjour")
        self.assertFalse(ok)
        self.assertIn("manquant", msg.lower())

    def test_playwright_not_installed_returns_error(self):
        with patch.object(
            self.platforms,
            "_get_playwright_context",
            side_effect=ImportError("playwright non installe"),
        ):
            ok, msg = self.platforms.send_whatsapp("+2236000000", "Hello")
        self.assertFalse(ok)
        self.assertIn("playwright", msg.lower())

    def test_success_returns_true(self):
        mock_pw, mock_context, mock_page = _make_browser_mocks()
        with patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            result = self.platforms.send_whatsapp("+2236123456", "Test message")
            ok, msg = result
        self.assertTrue(ok)
        self.assertEqual("sent", result.state)
        self.assertIn("whatsapp", msg.lower())

    def test_browser_nav_error_returns_false(self):
        mock_pw, mock_context, mock_page = _make_browser_mocks(nav_raises=True)
        with patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            ok, msg = self.platforms.send_whatsapp("+2236123456", "Test")
        self.assertFalse(ok)
        self.assertIn("erreur", msg.lower())

    def test_phone_number_included_in_url(self):
        mock_pw, mock_context, mock_page = _make_browser_mocks()
        with patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            self.platforms.send_whatsapp("+2236987654", "Salut")
        called_url = mock_page.goto.call_args[0][0]
        self.assertIn("2236987654", called_url)
        self.assertIn("web.whatsapp.com", called_url)

    def test_missing_media_returns_error_before_browser_launch(self):
        ok, msg = self.platforms.send_whatsapp("+2236123456", "Salut", ["C:/introuvable/demo.jpg"])
        self.assertFalse(ok)
        self.assertIn("introuvable", msg.lower())

    def test_media_files_are_attached_on_whatsapp(self):
        mock_pw, mock_context, mock_page = _make_browser_mocks()
        with patch.object(
            self.platforms,
            "_normalize_media_paths",
            return_value=[Path("C:/demo/photo.jpg"), Path("C:/demo/video.mp4")],
        ), patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            result = self.platforms.send_whatsapp(
                "+2236123456",
                "Caption",
                ["C:/demo/photo.jpg", "C:/demo/video.mp4"],
            )
        self.assertTrue(result.ok)
        mock_page.locator.return_value.first.set_input_files.assert_called_with(
            ["C:\\demo\\photo.jpg", "C:\\demo\\video.mp4"]
        )

    def test_context_closed_after_success(self):
        mock_pw, mock_context, mock_page = _make_browser_mocks()
        with patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            self.platforms.send_whatsapp("+2236123456", "Test")
        mock_context.close.assert_called_once()


# ---------------------------------------------------------------------------
# send_facebook
# ---------------------------------------------------------------------------

class TestSendFacebook(unittest.TestCase):

    def setUp(self):
        from scheduler import platforms as _p
        self.platforms = _p

    def test_empty_target_returns_error(self):
        ok, msg = self.platforms.send_facebook("", "Bonjour")
        self.assertFalse(ok)
        self.assertIn("manquant", msg.lower())

    def test_playwright_not_installed_returns_error(self):
        with patch.object(
            self.platforms,
            "_get_playwright_context",
            side_effect=ImportError("playwright non installe"),
        ):
            ok, msg = self.platforms.send_facebook("mon.groupe", "Hello")
        self.assertFalse(ok)
        self.assertIn("playwright", msg.lower())

    def test_full_url_not_prefixed(self):
        """Une URL complète n'est pas préfixée avec facebook.com."""
        mock_pw, mock_context, mock_page = _make_browser_mocks()
        with patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            full_url = "https://www.facebook.com/groups/999"
            self.platforms.send_facebook(full_url, "Test")
        called_url = mock_page.goto.call_args[0][0]
        self.assertEqual(called_url, full_url)

    def test_slug_gets_facebook_prefix(self):
        """Un slug simple est préfixé avec https://www.facebook.com/."""
        mock_pw, mock_context, mock_page = _make_browser_mocks()
        with patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            self.platforms.send_facebook("mon.groupe", "Test")
        called_url = mock_page.goto.call_args[0][0]
        self.assertEqual(called_url, "https://www.facebook.com/mon.groupe")

    def test_returns_true_when_composer_not_found(self):
        """Même si le compositeur est introuvable, retourne True avec info."""
        mock_pw, mock_context, mock_page = _make_browser_mocks()
        # Tous les locators échouent (compositeur introuvable)
        mock_page.locator.return_value.first.wait_for.side_effect = Exception("not found")
        mock_page.locator.return_value.first.click.side_effect = Exception("not found")

        with patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            result = self.platforms.send_facebook("mon.groupe", "Test")
            ok, msg = result
        self.assertTrue(ok)
        self.assertEqual("prepared", result.state)
        self.assertIn("facebook", msg.lower())

    def test_browser_nav_error_returns_false(self):
        """Erreur Playwright pendant goto → retourne False."""
        mock_pw, mock_context, mock_page = _make_browser_mocks(nav_raises=True)
        with patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            ok, msg = self.platforms.send_facebook("mon.groupe", "Test")
        self.assertFalse(ok)
        self.assertIn("erreur", msg.lower())

    def test_browser_context_not_closed_after_facebook(self):
        """Le contexte reste ouvert pour que l'utilisateur puisse cliquer Publier."""
        mock_pw, mock_context, mock_page = _make_browser_mocks()
        mock_page.locator.return_value.first.wait_for.side_effect = Exception("not found")

        with patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            self.platforms.send_facebook("mon.groupe", "Test")
        mock_context.close.assert_not_called()

    def test_media_files_are_attached_on_facebook(self):
        mock_pw, mock_context, mock_page = _make_browser_mocks()
        with patch.object(
            self.platforms,
            "_normalize_media_paths",
            return_value=[Path("C:/demo/post.mp4"), Path("C:/demo/photo.jpg")],
        ), patch.object(
            self.platforms,
            "_get_playwright_context",
            return_value=(mock_pw, mock_context),
        ):
            result = self.platforms.send_facebook(
                "mon.groupe",
                "Video",
                ["C:/demo/post.mp4", "C:/demo/photo.jpg"],
            )
        self.assertTrue(result.ok)
        mock_page.locator.return_value.first.set_input_files.assert_called_with(
            ["C:\\demo\\post.mp4", "C:\\demo\\photo.jpg"]
        )


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

class TestDispatch(unittest.TestCase):

    def setUp(self):
        from scheduler import platforms as _p
        self.platforms = _p

    def test_dispatch_unknown_platform(self):
        result = self.platforms.dispatch("instagram", "x", "y")
        ok, msg = result
        self.assertFalse(ok)
        self.assertEqual("error", result.state)
        self.assertIn("inconnue", msg.lower())

    def test_dispatch_whatsapp_calls_handler(self):
        mock_h = MagicMock(return_value=(True, "ok"))
        with patch.dict(self.platforms.PLATFORM_HANDLERS, {"whatsapp": mock_h}):
            ok, msg = self.platforms.dispatch("whatsapp", "+2236", "hi", ["C:/demo.jpg"])
        mock_h.assert_called_once_with("+2236", "hi", ["C:/demo.jpg"])
        self.assertTrue(ok)

    def test_dispatch_facebook_calls_handler(self):
        mock_h = MagicMock(return_value=(True, "ok"))
        with patch.dict(self.platforms.PLATFORM_HANDLERS, {"facebook": mock_h}):
            ok, msg = self.platforms.dispatch("facebook", "mon.groupe", "hi", ["C:/demo.mp4"])
        mock_h.assert_called_once_with("mon.groupe", "hi", ["C:/demo.mp4"])
        self.assertTrue(ok)

    def test_dispatch_case_insensitive(self):
        mock_h = MagicMock(return_value=(True, "ok"))
        with patch.dict(self.platforms.PLATFORM_HANDLERS, {"whatsapp": mock_h}):
            ok, _ = self.platforms.dispatch("WhatsApp", "+2236", "hi", "")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
