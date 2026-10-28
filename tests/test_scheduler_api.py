"""Tests pour les endpoints de l'API Planificateur / Automatisations (main.py)."""
from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import create_app
from scheduler import scheduler_store


class TestSchedulerAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = TestClient(cls.app)
        cls.addClassCleanup(cls.client.close)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = patch.object(scheduler_store, "DB_PATH", Path(directory.name) / "scheduler.db")
        database.start()
        self.addCleanup(database.stop)

    def test_list_tasks_endpoint(self):
        resp = self.client.get("/api/scheduler/tasks")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("tasks", data)
        self.assertIsInstance(data["tasks"], list)

    def test_crud_task_flow(self):
        # 1. Create
        payload = {
            "title": "Alerte sauvegarde nocturne",
            "platform": "whatsapp",
            "target": "+22790000000",
            "message": "La sauvegarde automatique du système a réussi.",
            "send_time": "23:30",
            "frequency": "daily",
            "ai_enhanced": True,
        }
        resp = self.client.post("/api/scheduler/tasks", json=payload)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        task = data.get("task")
        self.assertIsNotNone(task)
        task_id = task["id"]
        self.assertEqual(task["title"], "Alerte sauvegarde nocturne")
        self.assertEqual(task["status"], "active")

        # 2. Toggle status
        resp = self.client.post(f"/api/scheduler/tasks/{task_id}/toggle")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "paused")

        resp = self.client.post(f"/api/scheduler/tasks/{task_id}/toggle")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "active")

        # 3. Delete
        resp = self.client.delete(f"/api/scheduler/tasks/{task_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("deleted"))

        # Verify deletion
        self.assertIsNone(scheduler_store.get_task(task_id))

    def test_create_task_validation(self):
        # Missing title or message
        resp = self.client.post("/api/scheduler/tasks", json={"title": ""})
        self.assertEqual(resp.status_code, 400)

