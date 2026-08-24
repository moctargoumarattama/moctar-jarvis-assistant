import unittest
from unittest.mock import Mock, patch

import ai_brain
from knowledge_library import KnowledgeLibrary


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class KnowledgeLibraryTests(unittest.TestCase):
    def test_wikipedia_lookup_returns_summary(self):
        requester = Mock(side_effect=[
            _Response({"query": {"search": [{"title": "Troom Troom"}]}}),
            _Response({"extract": "Troom Troom est une chaine video. La chaine publie aussi des tutoriels."}),
        ])
        library = KnowledgeLibrary(requester=requester)

        answer = library.lookup("troom")

        self.assertEqual("D'apres Wikipedia : Troom Troom est une chaine video.", answer)
        self.assertEqual(2, requester.call_count)

    def test_math_like_query_skips_wikipedia_lookup(self):
        requester = Mock()
        library = KnowledgeLibrary(requester=requester)

        answer = library.lookup("combien font 0 x 0")

        self.assertEqual("", answer)
        requester.assert_not_called()

    def test_unknown_question_does_not_repeat_last_subject(self):
        library = Mock()
        library.lookup.return_value = "Reponse encyclopedique."
        brain = ai_brain.LocalBrain(knowledge_library=library)

        with patch("ai_brain.can_answer_general_questions", return_value=False):
            answer = brain.answer("troom", session_turn={"target": "booba"})

        self.assertEqual("Reponse encyclopedique.", answer)


if __name__ == "__main__":
    unittest.main()
