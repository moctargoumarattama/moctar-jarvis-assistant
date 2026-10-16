"""Derniers rapports VPS persistants, sans confondre historique et présence réseau."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("noc-dashboard")


class VPSRegistry:
    def __init__(self, settings: Any, report_model: Any):
        self.settings = settings
        self.report_model = report_model
        self.path = Path(settings.vps_state_path) if settings.vps_state_path else None
        self.max_bytes = settings.max_vps * 8192 + 1024
        self._persist_lock = asyncio.Lock()

    async def load(self) -> dict[str, dict[str, Any]]:
        if not self.settings.vps_token or self.path is None:
            return {}
        return await asyncio.to_thread(self._load)

    def _load(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        try:
            if self.path.is_symlink():
                raise ValueError("Registre VPS non régulier")
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError("Registre VPS non régulier")
                raw = handle.read(self.max_bytes + 1)
            if len(raw) > self.max_bytes:
                raise ValueError("Registre VPS trop volumineux")
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("servers"), list):
                raise ValueError("Format du registre VPS invalide")
            now = datetime.now(timezone.utc)
            monotonic = time.monotonic()
            for entry in data["servers"][:self.settings.max_vps]:
                try:
                    report = self.report_model.model_validate(entry["report"]).model_dump()
                    last_seen = datetime.fromisoformat(entry["last_seen"].replace("Z", "+00:00"))
                    if last_seen.tzinfo is None:
                        continue
                    age = (now - last_seen).total_seconds()
                    if not 0 <= age <= self.settings.vps_retention_seconds:
                        continue
                    result[report["hostname"]] = {
                        "report": report, "last_seen": last_seen.isoformat(timespec="milliseconds"),
                        "seen": monotonic - age, "stale": True, "restored": True,
                    }
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                    continue
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, RecursionError):
            LOGGER.warning("Registre VPS indisponible ; reprise au prochain rapport authentifié")
        return result

    async def persist(self, servers: dict[str, dict[str, Any]]) -> None:
        async with self._persist_lock:
            _, cancelled = await self._persist_locked(servers)
            if cancelled:
                raise asyncio.CancelledError

    async def upsert(self, servers: dict[str, dict[str, Any]], hostname: str, entry: dict[str, Any]) -> bool:
        """Reports and removals share a lock, including their in-memory mutation."""
        async with self._persist_lock:
            if hostname not in servers and len(servers) >= self.settings.max_vps:
                return False
            servers[hostname] = entry
            _, cancelled = await self._persist_locked(servers)
            if cancelled:
                raise asyncio.CancelledError
            return True

    async def remove(self, servers: dict[str, dict[str, Any]], hostname: str) -> bool:
        async with self._persist_lock:
            if hostname not in servers:
                return False
            remaining = {name: entry for name, entry in servers.items() if name != hostname}
            saved, cancelled = await self._persist_locked(remaining)
            if saved:
                servers.pop(hostname, None)
            if cancelled:
                raise asyncio.CancelledError
            if not saved:
                raise OSError("Suppression VPS non enregistrée")
            return True

    async def _persist_locked(self, servers: dict[str, dict[str, Any]]) -> tuple[bool, bool]:
        if self.path is None:
            return True, False
        # Snapshot and mutation stay under the same lock until the writer ends.
        payload = {"version": 1, "servers": [
            {"report": item["report"], "last_seen": item["last_seen"]}
            for item in list(servers.values())[:self.settings.max_vps]
        ]}
        writer = asyncio.create_task(asyncio.to_thread(self._write, payload))
        cancelled = False
        while True:
            try:
                return await asyncio.shield(writer), cancelled
            except asyncio.CancelledError:
                if writer.cancelled():
                    raise
                # Cancellation cannot release the lock while a write is running.
                cancelled = True

    def _write(self, payload: dict[str, Any]) -> bool:
        temporary: str | None = None
        try:
            content = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(content) > self.max_bytes or self.path.is_symlink() or self.path.parent.is_symlink():
                raise ValueError("Registre VPS non enregistrable")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".vps-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            return True
        except (OSError, ValueError, TypeError):
            LOGGER.warning("Persistance VPS indisponible ; les rapports restent visibles en mémoire")
            return False
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
