"""Durable occurrence reservations, using isolated SQLite and no real dispatch."""

import sqlite3
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scheduler import scheduler_core as core
from scheduler import scheduler_store as store
from scheduler.platforms import DispatchResult


class SchedulerOccurrenceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.database = Path(directory) / "scheduler.db"
        self.stack.enter_context(patch.object(store, "DB_PATH", self.database))
        self.stack.enter_context(patch.object(store.config, "ensure_runtime_directories"))
        self.slot = datetime(2026, 10, 26, 9, 0)  # Monday
        with core._dispatch_lock:
            core._in_flight_tasks.clear()
            core._recent_dispatches.clear()

    @contextmanager
    def at(self, instant):
        with patch.object(store, "datetime", wraps=datetime) as clock:
            clock.now.return_value = instant
            yield

    def task(self, **kwargs):
        values = dict(title="Publication", platform="whatsapp", target="test-recipient",
                      message="Message de test", send_time="09:00")
        values.update(kwargs)
        return store.create_task(**values)

    def ledger(self, task_id):
        with store._conn_scope() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM scheduler_executions WHERE task_id=? ORDER BY started_at", (task_id,),
            )]

    def deliver(self, task, instant):
        with self.at(instant), patch.object(core.platforms, "dispatch", return_value=DispatchResult("sent", "Envoye")):
            core.run_once(now=instant)
            return core.confirm_and_send(task["id"])

    def test_migration_keeps_legacy_task_and_adds_nullable_execution_columns(self):
        with sqlite3.connect(self.database) as connection:
            connection.executescript("""
                CREATE TABLE scheduled_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                    platform TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL, frequency TEXT NOT NULL DEFAULT 'daily',
                    weekday INTEGER, send_time TEXT NOT NULL DEFAULT '09:00',
                    status TEXT NOT NULL DEFAULT 'active', ai_enhanced INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, last_sent TEXT
                );
                INSERT INTO scheduled_tasks
                    (id, title, platform, target, message, created_at)
                VALUES (17, 'A conserver', 'whatsapp', 'destinataire', 'Texte intact', '2026-01-01');
            """)
        connection.close()  # sqlite3's context manager commits but does not close.
        first = store.get_task(17)
        second = store.get_task(17)  # Migration must be safe to repeat.
        self.assertEqual(first, second)
        self.assertEqual(first["title"], "A conserver")
        self.assertEqual(first["message"], "Texte intact")
        self.assertIsNone(first["occurrence_key"])
        self.assertIsNone(first["execution_id"])
        self.assertEqual(self.ledger(17), [])

    def test_marker_is_committed_before_dispatch_and_replay_survives_restart(self):
        task = self.task()
        committed_markers = []

        def dispatch(*_args):
            # A distinct connection observes the marker before the side effect.
            current = store.get_task(task["id"])
            rows = self.ledger(task["id"])
            self.assertEqual(current["status"], "processing")
            self.assertEqual(rows[0]["status"], "processing")
            self.assertEqual(str(uuid.UUID(current["execution_id"])), current["execution_id"])
            committed_markers.append(current["execution_id"])
            return DispatchResult("sent", "Envoye")

        with self.at(self.slot), patch.object(core.platforms, "dispatch", side_effect=dispatch) as sender:
            core.run_once(now=self.slot)
            self.assertTrue(core.confirm_and_send(task["id"])[0])
            core._recent_dispatches.clear()  # Simulated process-memory loss.
            store.update_task(task["id"], status="pending_confirmation")  # Stale HTTP replay.
            self.assertTrue(core.confirm_and_send(task["id"])[0])
            sender.assert_called_once()
        rows = self.ledger(task["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["execution_id"], committed_markers[0])
        self.assertEqual(rows[0]["occurrence_key"], f"{task['id']}:2026-10-26T09:00")
        self.assertEqual(rows[0]["status"], "completed")

    def test_same_day_after_old_memory_ttl_never_dispatches_twice(self):
        task = self.task()
        self.assertTrue(self.deliver(task, self.slot)[0])
        with self.at(self.slot + timedelta(hours=6)), patch.object(core.platforms, "dispatch") as sender:
            core._recent_dispatches.clear()
            ok, detail = core.confirm_and_send(task["id"])
            self.assertTrue(ok)
            self.assertIn("idempotent", detail)
            sender.assert_not_called()

    def test_daily_next_slot_has_new_uuid_and_preserves_previous_history(self):
        task = self.task()
        self.assertTrue(self.deliver(task, self.slot)[0])
        first_uuid = store.get_task(task["id"])["execution_id"]
        self.assertTrue(self.deliver(task, self.slot + timedelta(days=1))[0])
        current = store.get_task(task["id"])
        self.assertNotEqual(first_uuid, current["execution_id"])
        self.assertEqual(current["occurrence_key"], f"{task['id']}:2026-10-27T09:00")
        self.assertEqual(len(self.ledger(task["id"])), 2)

    def test_weekly_next_slot_works_but_other_weekdays_do_not_queue(self):
        task = self.task(frequency="weekly", weekday=0)
        self.assertTrue(self.deliver(task, self.slot)[0])
        next_day = self.slot + timedelta(days=1)
        core.run_once(now=next_day)
        self.assertEqual(store.get_task(task["id"])["status"], "active")
        self.assertEqual(len(self.ledger(task["id"])), 1)
        self.assertTrue(self.deliver(task, self.slot + timedelta(days=7))[0])
        self.assertEqual(store.get_task(task["id"])["occurrence_key"], f"{task['id']}:2026-11-02T09:00")
        self.assertEqual(len(self.ledger(task["id"])), 2)

    def test_late_confirmation_retains_queued_day(self):
        task = self.task()
        core.run_once(now=self.slot)
        late = self.slot + timedelta(days=1, hours=2)
        with self.at(late), patch.object(core.platforms, "dispatch", return_value=DispatchResult("sent", "Envoye")):
            self.assertTrue(core.confirm_and_send(task["id"])[0])
        self.assertEqual(store.get_task(task["id"])["occurrence_key"], f"{task['id']}:2026-10-26T09:00")

    def test_late_previous_day_delivery_does_not_consume_todays_slot(self):
        task = self.task()
        core.run_once(now=self.slot)
        late = self.slot + timedelta(days=1, hours=-1)
        with self.at(late), patch.object(core.platforms, "dispatch", return_value=DispatchResult("sent", "Envoye")):
            self.assertTrue(core.confirm_and_send(task["id"])[0])
        self.assertTrue(self.deliver(task, self.slot + timedelta(days=1))[0])
        rows = self.ledger(task["id"])
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["occurrence_key"] for row in rows}, {
            f"{task['id']}:2026-10-26T09:00", f"{task['id']}:2026-10-27T09:00",
        })

    def test_crashed_lease_is_uncertain_and_cannot_be_replayed_after_recovery(self):
        task = self.task()
        claimed, execution, _ = store.claim_task_execution(task["id"], now=self.slot)
        self.assertTrue(claimed)
        with self.at(self.slot + timedelta(minutes=6)), patch.object(core.platforms, "dispatch") as sender:
            core._reclaim_stale_processing_tasks()
            recovered = store.get_task(task["id"])
            self.assertEqual(recovered["status"], "awaiting_confirmation")
            self.assertEqual(recovered["execution_id"], execution["execution_id"])
            self.assertIn("Verification", recovered["execution_note"])
            self.assertEqual(self.ledger(task["id"])[0]["status"], "interrupted_uncertain")
            ok, detail = core.confirm_and_send(task["id"])
            self.assertFalse(ok)
            self.assertIn("Verification", detail)
            # Even toggling active cannot bypass the occurrence ledger.
            store.update_task(task["id"], status="active")
            self.assertFalse(core.confirm_and_send(task["id"])[0])
            sender.assert_not_called()

    def test_active_inflight_execution_is_never_reclaimed(self):
        task = self.task()
        store.claim_task_execution(task["id"], now=self.slot)
        with core._dispatch_lock:
            core._in_flight_tasks.add(task["id"])
        try:
            with self.at(self.slot + timedelta(hours=1)):
                core._reclaim_stale_processing_tasks()
            self.assertEqual(store.get_task(task["id"])["status"], "processing")
        finally:
            with core._dispatch_lock:
                core._in_flight_tasks.discard(task["id"])

    def test_manual_finalization_remains_possible_and_prevents_reprepare(self):
        task = self.task(platform="facebook")
        with self.at(self.slot), patch.object(core.platforms, "dispatch", return_value=DispatchResult("prepared", "Cliquer publier")) as sender:
            self.assertTrue(core.confirm_and_send(task["id"])[0])
            self.assertEqual(store.get_task(task["id"])["status"], "awaiting_manual_action")
            self.assertTrue(core.confirm_and_send(task["id"])[0])
            self.assertTrue(core.mark_task_sent(task["id"])[0])
            self.assertTrue(core.confirm_and_send(task["id"])[0])
            sender.assert_called_once()
        self.assertEqual(self.ledger(task["id"])[0]["status"], "completed")
        self.assertEqual(store.get_task(task["id"])["status"], "active")

    def test_verified_uncertain_delivery_can_be_marked_sent_then_next_day_runs(self):
        task = self.task()
        store.claim_task_execution(task["id"], now=self.slot)
        store.recover_stale_execution(task["id"], now=self.slot + timedelta(minutes=6))
        with self.at(self.slot + timedelta(minutes=7)):
            self.assertTrue(core.mark_task_sent(task["id"])[0])
        self.assertTrue(self.deliver(task, self.slot + timedelta(days=1))[0])
        self.assertEqual(len(self.ledger(task["id"])), 2)

    def test_legacy_completed_occurrence_is_not_resent_after_migration(self):
        task = self.task()
        store.update_task(task["id"], last_sent=self.slot.isoformat())
        with self.at(self.slot + timedelta(hours=1)), patch.object(core.platforms, "dispatch") as sender:
            self.assertTrue(core.confirm_and_send(task["id"])[0])
            sender.assert_not_called()
        self.assertEqual(self.ledger(task["id"])[0]["status"], "completed")

    def test_pre_migration_interruption_is_imported_without_resending(self):
        task = self.task()
        store.update_task(task["id"], status="awaiting_confirmation",
                          last_attempt_at=self.slot.isoformat(), execution_note="Interrompue")
        with self.at(self.slot + timedelta(days=1)), patch.object(core.platforms, "dispatch") as sender:
            self.assertFalse(core.confirm_and_send(task["id"])[0])
            sender.assert_not_called()
        rows = self.ledger(task["id"])
        self.assertEqual(rows[0]["occurrence_key"], f"{task['id']}:2026-10-26T09:00")
        self.assertEqual(rows[0]["status"], "interrupted_uncertain")

    def test_two_concurrent_sqlite_claims_only_one_wins(self):
        task = self.task()
        barrier = threading.Barrier(2)

        def reserve():
            barrier.wait(timeout=5)
            return store.claim_task_execution(task["id"], now=self.slot)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: reserve(), range(2)))
        self.assertEqual(sum(claimed for claimed, _, _ in results), 1)
        self.assertEqual(len(self.ledger(task["id"])), 1)

    def test_sqlite_failure_never_falls_back_to_unprotected_dispatch(self):
        task = self.task()
        with patch.object(store, "claim_task_execution", side_effect=sqlite3.OperationalError("disk full")), patch.object(core.platforms, "dispatch") as sender:
            ok, detail = core.confirm_and_send(task["id"])
        self.assertFalse(ok)
        self.assertIn("disk full", detail)
        sender.assert_not_called()

    def test_exception_after_external_dispatch_stays_uncertain_and_blocks_retry(self):
        task = self.task()
        with self.at(self.slot), patch.object(core.platforms, "dispatch", side_effect=TimeoutError("resultat inconnu")) as sender:
            ok, detail = core.confirm_and_send(task["id"])
            self.assertFalse(ok)
            self.assertIn("resultat inconnu", detail)
            self.assertFalse(core.confirm_and_send(task["id"])[0])
            sender.assert_called_once()
        self.assertEqual(store.get_task(task["id"])["status"], "awaiting_confirmation")
        self.assertEqual(self.ledger(task["id"])[0]["status"], "interrupted_uncertain")


if __name__ == "__main__":
    unittest.main()
