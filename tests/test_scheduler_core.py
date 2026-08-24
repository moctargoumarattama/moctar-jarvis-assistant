import unittest
from datetime import datetime
from unittest.mock import patch

from scheduler import scheduler_core
from scheduler.platforms import DispatchResult


class SchedulerCoreTests(unittest.TestCase):
    def test_run_once_queues_due_tasks(self):
        due_task = {"id": 7, "title": "Promo", "platform": "whatsapp", "last_sent": None}
        with patch("scheduler.scheduler_core.store.get_due_tasks", return_value=[due_task]), patch(
            "scheduler.scheduler_core.store.update_task"
        ) as update_task:
            scheduler_core.run_once(now=datetime(2026, 8, 23, 9, 0))

        update_task.assert_called_once_with(7, status="pending_confirmation")

    def test_confirm_and_send_marks_task_active_when_delivery_is_final(self):
        task = {
            "id": 3,
            "title": "Promo",
            "platform": "whatsapp",
            "target": "+212700000000",
            "message": "Bonjour",
            "media_paths": [],
            "ai_enhanced": False,
        }
        with patch("scheduler.scheduler_core.store.get_task", return_value=task), patch(
            "scheduler.scheduler_core.platforms.dispatch",
            return_value=DispatchResult("sent", "Message envoye."),
        ) as dispatch, patch("scheduler.scheduler_core.store.update_task") as update_task:
            ok, detail = scheduler_core.confirm_and_send(3)

        self.assertTrue(ok)
        self.assertEqual("Message envoye.", detail)
        dispatch.assert_called_once_with(
            "whatsapp",
            "+212700000000",
            "Bonjour",
            [],
        )
        self.assertEqual("active", update_task.call_args.kwargs["status"])
        self.assertIn("last_sent", update_task.call_args.kwargs)

    def test_confirm_and_send_marks_task_waiting_manual_action_when_needed(self):
        task = {
            "id": 4,
            "title": "Post FB",
            "platform": "facebook",
            "target": "mon.groupe",
            "message": "Publication",
            "media_paths": ["C:/demo/post.jpg", "C:/demo/teaser.mp4"],
            "ai_enhanced": False,
        }
        with patch("scheduler.scheduler_core.store.get_task", return_value=task), patch(
            "scheduler.scheduler_core.platforms.dispatch",
            return_value=DispatchResult("prepared", "Publiez dans le navigateur."),
        ) as dispatch, patch("scheduler.scheduler_core.store.update_task") as update_task:
            ok, detail = scheduler_core.confirm_and_send(4)

        self.assertTrue(ok)
        self.assertEqual("Publiez dans le navigateur.", detail)
        dispatch.assert_called_once_with(
            "facebook",
            "mon.groupe",
            "Publication",
            ["C:/demo/post.jpg", "C:/demo/teaser.mp4"],
        )
        self.assertEqual({"status": "awaiting_manual_action"}, update_task.call_args.kwargs)

    def test_confirm_and_send_marks_error_on_dispatch_failure(self):
        task = {
            "id": 5,
            "title": "Promo",
            "platform": "whatsapp",
            "target": "+212700000000",
            "message": "Bonjour",
            "ai_enhanced": False,
        }
        with patch("scheduler.scheduler_core.store.get_task", return_value=task), patch(
            "scheduler.scheduler_core.platforms.dispatch",
            return_value=DispatchResult("error", "Echec."),
        ), patch("scheduler.scheduler_core.store.update_task") as update_task:
            ok, detail = scheduler_core.confirm_and_send(5)

        self.assertFalse(ok)
        self.assertEqual("Echec.", detail)
        self.assertEqual({"status": "error"}, update_task.call_args.kwargs)

    def test_mark_task_sent_returns_task_to_active(self):
        task = {"id": 8, "status": "awaiting_manual_action"}
        with patch("scheduler.scheduler_core.store.get_task", return_value=task), patch(
            "scheduler.scheduler_core.store.update_task"
        ) as update_task:
            ok, detail = scheduler_core.mark_task_sent(8)

        self.assertTrue(ok)
        self.assertIn("envoyee", detail)
        self.assertEqual("active", update_task.call_args.kwargs["status"])
        self.assertIn("last_sent", update_task.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
