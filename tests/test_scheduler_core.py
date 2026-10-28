import unittest
from datetime import datetime
from unittest.mock import patch

from scheduler import scheduler_core
from scheduler.platforms import DispatchResult


class SchedulerCoreTests(unittest.TestCase):
    def test_run_once_queues_due_tasks(self):
        due_task = {"id": 7, "title": "Promo", "platform": "whatsapp", "last_sent": None}
        with patch("scheduler.scheduler_core.store.get_due_tasks", return_value=[due_task]), patch(
            "scheduler.scheduler_core.store.queue_task_occurrence", return_value=True,
        ) as queue_task:
            scheduler_core.run_once(now=datetime(2026, 8, 23, 9, 0))

        queue_task.assert_called_once_with(7, now=datetime(2026, 8, 23, 9, 0))

    def test_confirm_and_send_marks_task_active_when_delivery_is_final(self):
        task = {
            "id": 3,
            "title": "Promo",
            "platform": "whatsapp",
            "target": "+212700000000",
            "message": "Bonjour",
            "media_paths": [],
            "ai_enhanced": False,
            "execution_id": "exec-3",
        }
        with patch("scheduler.scheduler_core.store.claim_task_execution", return_value=(True, task, "")), patch(
            "scheduler.scheduler_core.store.recover_stale_execution", return_value=False,
        ), patch(
            "scheduler.scheduler_core.platforms.dispatch",
            return_value=DispatchResult("sent", "Message envoye."),
        ) as dispatch, patch("scheduler.scheduler_core.store.finish_task_execution") as finish:
            ok, detail = scheduler_core.confirm_and_send(3)

        self.assertTrue(ok)
        self.assertEqual("Message envoye.", detail)
        dispatch.assert_called_once_with(
            "whatsapp",
            "+212700000000",
            "Bonjour",
            [],
        )
        finish.assert_called_once_with(3, "exec-3", "completed", "Message envoye.")

    def test_confirm_and_send_marks_task_waiting_manual_action_when_needed(self):
        task = {
            "id": 4,
            "title": "Post FB",
            "platform": "facebook",
            "target": "mon.groupe",
            "message": "Publication",
            "media_paths": ["C:/demo/post.jpg", "C:/demo/teaser.mp4"],
            "ai_enhanced": False,
            "execution_id": "exec-4",
        }
        with patch("scheduler.scheduler_core.store.claim_task_execution", return_value=(True, task, "")), patch(
            "scheduler.scheduler_core.store.recover_stale_execution", return_value=False,
        ), patch(
            "scheduler.scheduler_core.platforms.dispatch",
            return_value=DispatchResult("prepared", "Publiez dans le navigateur."),
        ) as dispatch, patch("scheduler.scheduler_core.store.finish_task_execution") as finish:
            ok, detail = scheduler_core.confirm_and_send(4)

        self.assertTrue(ok)
        self.assertEqual("Publiez dans le navigateur.", detail)
        dispatch.assert_called_once_with(
            "facebook",
            "mon.groupe",
            "Publication",
            ["C:/demo/post.jpg", "C:/demo/teaser.mp4"],
        )
        finish.assert_called_once_with(4, "exec-4", "awaiting_manual_action", "Publiez dans le navigateur.")

    def test_confirm_and_send_marks_error_on_dispatch_failure(self):
        task = {
            "id": 5,
            "title": "Promo",
            "platform": "whatsapp",
            "target": "+212700000000",
            "message": "Bonjour",
            "ai_enhanced": False,
            "execution_id": "exec-5",
        }
        with patch("scheduler.scheduler_core.store.claim_task_execution", return_value=(True, task, "")), patch(
            "scheduler.scheduler_core.store.recover_stale_execution", return_value=False,
        ), patch(
            "scheduler.scheduler_core.platforms.dispatch",
            return_value=DispatchResult("error", "Echec."),
        ), patch("scheduler.scheduler_core.store.finish_task_execution") as finish:
            ok, detail = scheduler_core.confirm_and_send(5)

        self.assertFalse(ok)
        self.assertEqual("Echec.", detail)
        finish.assert_called_once_with(5, "exec-5", "failed", "Echec.")

    def test_mark_task_sent_returns_task_to_active(self):
        with patch(
            "scheduler.scheduler_core.store.mark_execution_manually_sent",
            return_value=(True, "Tache marquee comme envoyee."),
        ) as mark_sent:
            ok, detail = scheduler_core.mark_task_sent(8)

        self.assertTrue(ok)
        self.assertIn("envoyee", detail)
        mark_sent.assert_called_once_with(8)


if __name__ == "__main__":
    unittest.main()
