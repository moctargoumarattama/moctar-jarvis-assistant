"""Contrôle du noyau Python du script, sans sudo, rsync réel ni volume de production."""

import ast
import contextlib
import io
import json
import sqlite3
import stat
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "backup-babamarket.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")
CORE = TEXT.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
NAMESPACE = {"__name__": "backup_test"}
exec(compile(CORE, str(SCRIPT), "exec"), NAMESPACE)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "babamarket"
        self.destination = self.root / "backups"
        self.source.mkdir()
        self.destination.mkdir()

    def test_script_has_fixed_privileged_timeout_and_valid_core(self):
        self.assertIn("exec /usr/bin/timeout --signal=TERM --kill-after=5 280 /usr/bin/python3 -", TEXT)
        self.assertNotIn("$NOC_BACKUP_TIMEOUT", TEXT)
        ast.parse(CORE)
        self.assertNotIn("os.environ", CORE)

    def test_configuration_is_parsed_without_executing_shell_and_requires_safe_owner(self):
        path = self.root / "config.env"
        path.write_text('NOC_BACKUP_SOURCE="/srv/baba market"\nNOC_BACKUP_PATH=/mnt/backups\nNOC_ADMIN_TOKEN=$(touch /tmp/unsafe)\n', encoding="utf-8")
        metadata = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0, st_size=path.stat().st_size)
        with patch.object(Path, "lstat", return_value=metadata):
            values = NAMESPACE["read_configuration"](path)
        self.assertEqual(values, {"NOC_BACKUP_SOURCE": "/srv/baba market", "NOC_BACKUP_PATH": "/mnt/backups"})
        metadata.st_mode = stat.S_IFREG | 0o660
        with patch.object(Path, "lstat", return_value=metadata), self.assertRaises(NAMESPACE["BackupError"]):
            NAMESPACE["read_configuration"](path)

    def test_missing_source_never_guesses_a_system_directory(self):
        with patch.object(NAMESPACE["subprocess"], "run", return_value=types.SimpleNamespace(returncode=0, stdout="")):
            with self.assertRaisesRegex(NAMESPACE["BackupError"], "NOC_BACKUP_SOURCE"):
                NAMESPACE["source_path"]({})
        with self.assertRaises(NAMESPACE["BackupError"]):
            NAMESPACE["source_path"]({"NOC_BACKUP_SOURCE": str(Path(self.root.anchor))})

    def test_destination_must_be_mounted_and_outside_source(self):
        config = {"NOC_BACKUP_PATH": str(self.destination)}
        with patch.object(NAMESPACE["os"].path, "ismount", return_value=False), self.assertRaises(NAMESPACE["BackupError"]):
            NAMESPACE["destination_path"](config, self.source)
        with patch.object(NAMESPACE["os"].path, "ismount", return_value=True), patch.dict(NAMESPACE, {"different_root_device": lambda _path: True}):
            self.assertEqual(NAMESPACE["destination_path"](config, self.source), self.destination)
            with self.assertRaises(NAMESPACE["BackupError"]):
                NAMESPACE["destination_path"]({"NOC_BACKUP_PATH": str(self.source)}, self.source)

    def test_artificial_systemd_mount_on_root_filesystem_is_rejected(self):
        with patch.object(Path, "stat", side_effect=[types.SimpleNamespace(st_dev=7), types.SimpleNamespace(st_dev=7)]):
            self.assertFalse(NAMESPACE["different_root_device"](self.destination))
        with patch.object(Path, "stat", side_effect=[types.SimpleNamespace(st_dev=8), types.SimpleNamespace(st_dev=7)]):
            self.assertTrue(NAMESPACE["different_root_device"](self.destination))
        with patch.object(NAMESPACE["os"].path, "ismount", return_value=True), patch.dict(NAMESPACE, {"different_root_device": lambda _path: False}):
            with self.assertRaisesRegex(NAMESPACE["BackupError"], "filesystem racine"):
                NAMESPACE["destination_path"]({"NOC_BACKUP_PATH": str(self.destination)}, self.source)

    def test_sqlite_online_copy_contains_committed_wal_data(self):
        database = self.source / "database.DB"
        with contextlib.closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE records (value TEXT)")
            connection.execute("INSERT INTO records VALUES ('committed-wal')")
            connection.commit()
            self.assertIn(database, NAMESPACE["sqlite_databases"](self.source))
            target = self.destination / "database.DB"
            NAMESPACE["copy_sqlite"](database, target, time.monotonic() + 5)
            with contextlib.closing(sqlite3.connect(target)) as restored:
                self.assertEqual(restored.execute("SELECT value FROM records").fetchone()[0], "committed-wal")
                self.assertEqual(restored.execute("PRAGMA quick_check").fetchone(), ("ok",))
        unrelated = self.source / "other.db"
        unrelated.write_bytes(b"binary-format-not-sqlite")
        self.assertNotIn(unrelated, NAMESPACE["sqlite_databases"](self.source))

    def test_rsync_command_preserves_private_staging_and_excludes_sidecars(self):
        with patch.object(NAMESPACE["subprocess"], "run", return_value=types.SimpleNamespace(returncode=0)) as execute:
            NAMESPACE["copy_files"](self.source, self.destination, 10)
        command = execute.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/rsync")
        self.assertIn("--no-owner", command)
        self.assertIn("--chmod=Du=rwx,Dgo=,Fu=rwX,Fgo=", command)
        self.assertIn("--exclude=*-wal", command)
        self.assertIn("--exclude=*-shm", command)
        self.assertIn("--exclude=.venv/", command)
        self.assertEqual(command[-2:], [str(self.source) + "/", str(self.destination) + "/"])


if __name__ == "__main__":
    unittest.main()
