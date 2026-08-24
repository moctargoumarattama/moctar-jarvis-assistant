import unittest
from unittest.mock import Mock

from actions.music_actions import control_music, play_music


class MusicActionsTests(unittest.TestCase):
    def test_empty_request_uses_recommender_and_opens_youtube_music(self):
        opened = []
        response = play_music("", recommender=lambda: "Ninho", browser_opener=opened.append)
        self.assertIn("Ninho", response)
        self.assertIn("music.youtube.com", opened[0])

    def test_control_music_uses_media_key(self):
        keyboard = Mock()
        response = control_music("music_next", pyautogui_module=keyboard)
        keyboard.press.assert_called_once_with("nexttrack")
        self.assertIn("suivant", response)


if __name__ == "__main__":
    unittest.main()
