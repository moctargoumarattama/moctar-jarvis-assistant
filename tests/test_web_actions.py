import unittest
from unittest.mock import Mock, patch

from actions import web_actions


def fake_process_provider(*process_names):
    def _provider(_attrs):
        return [Mock(info={"name": name}) for name in process_names]

    return _provider


class WebActionsTests(unittest.TestCase):
    def setUp(self):
        web_actions._RECENT_URLS.clear()

    def test_open_url_smart_reuses_active_browser_tab_with_browser_command(self):
        launcher = Mock()
        browser_module = Mock()

        response = web_actions.open_url_smart(
            "https://www.youtube.com",
            browser="default",
            browser_module=browser_module,
            process_provider=fake_process_provider("msedge.exe"),
            now_provider=lambda: 100.0,
            launcher=launcher,
        )

        launcher.assert_called_once()
        browser_module.open_new_tab.assert_not_called()
        self.assertIn("nouvel onglet", response.lower())
        self.assertIn("edge", response.lower())

    def test_open_url_smart_avoids_duplicate_open_in_short_window(self):
        browser_module = Mock()

        first_response = web_actions.open_url_smart(
            "https://mail.google.com",
            browser="default",
            browser_module=browser_module,
            process_provider=fake_process_provider(),
            now_provider=lambda: 200.0,
        )
        second_response = web_actions.open_url_smart(
            "https://mail.google.com",
            browser="default",
            browser_module=browser_module,
            process_provider=fake_process_provider(),
            now_provider=lambda: 201.0,
        )

        browser_module.open_new_tab.assert_called_once_with("https://mail.google.com")
        self.assertIn("j'ouvre", first_response.lower())
        self.assertIn("deja", second_response.lower())

    def test_open_site_delegates_to_smart_opening(self):
        with patch("actions.web_actions.open_url_smart", return_value="J'ouvre github.") as open_url_smart:
            response = web_actions.open_site("github")

        open_url_smart.assert_called_once_with(
            "https://github.com",
            browser=web_actions.config.WEB_SETTINGS["default_browser"],
        )
        self.assertEqual("J'ouvre github.", response)


if __name__ == "__main__":
    unittest.main()
