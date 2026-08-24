import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scheduler import scheduler_store


class SchedulerStoreTests(unittest.TestCase):
    def test_create_task_persists_media_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "scheduler.db"
            with patch.object(scheduler_store, "DB_PATH", db_path):
                task = scheduler_store.create_task(
                    title="Post video",
                    platform="facebook",
                    target="mon.groupe",
                    message="Nouvelle video",
                    media_paths=["C:/demo/video.mp4", "C:/demo/photo.jpg"],
                )

        self.assertEqual("C:/demo/video.mp4", task["media_path"])
        self.assertEqual("video", task["media_type"])
        self.assertEqual(["C:/demo/video.mp4", "C:/demo/photo.jpg"], task["media_paths"])
        self.assertEqual(["video", "image"], task["media_types"])

    def test_update_task_recomputes_media_type_from_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "scheduler.db"
            with patch.object(scheduler_store, "DB_PATH", db_path):
                task = scheduler_store.create_task(
                    title="Photo",
                    platform="whatsapp",
                    target="+212700000000",
                    message="Bonjour",
                )
                updated = scheduler_store.update_task(
                    task["id"],
                    media_paths=["C:/demo/photo.jpg", "C:/demo/movie.mp4"],
                )

        self.assertEqual("C:/demo/photo.jpg", updated["media_path"])
        self.assertEqual("image", updated["media_type"])
        self.assertEqual(["C:/demo/photo.jpg", "C:/demo/movie.mp4"], updated["media_paths"])
        self.assertEqual(["image", "video"], updated["media_types"])


if __name__ == "__main__":
    unittest.main()
