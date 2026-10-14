"""Réception Sentinel : incidents bornés, enrichissement séparé des sondes."""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import re
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from config import Settings
from sentinel_crypto import decrypt

LOGGER = logging.getLogger("noc-dashboard.sentinel")
try:
    DAY_ZONE = ZoneInfo("Africa/Casablanca")
except ZoneInfoNotFoundError:
    # Windows peut ne pas disposer de la base IANA ; Ubuntu la fournit.
    DAY_ZONE = timezone.utc


def local_day(stamp: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if stamp is None else stamp, DAY_ZONE).date().isoformat()


class SentinelPacket(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Annotated[str, Field(pattern=r"^(incident|heartbeat)$")]
    event_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")]
    sent_at: Annotated[float, Field(ge=0, strict=True)]
    agent: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
    day: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    blocked_today: Annotated[int, Field(ge=0, le=100000000, strict=True)]
    source_ip: str | None = None
    username: Annotated[str, Field(max_length=64)] = ""
    password: Annotated[str, Field(max_length=64)] = ""
    blocked: bool = False
    new_block: bool = False
    protected: bool = False
    capacity: bool | None = None
    capture: Annotated[str, Field(pattern=r"^(credentials|ssh_negotiation|negotiation_incomplete|protected|capacity)$")] | None = None
    ban_error: Annotated[str, Field(max_length=160)] | None = None
    timestamp: Annotated[str, Field(max_length=40)] | None = None

    @model_validator(mode="after")
    def valid_incident(self):
        if self.kind == "incident":
            if not self.source_ip:
                raise ValueError("IP absente")
            address = ipaddress.ip_address(self.source_ip)
            self.source_ip = str(address)
            if self.blocked and not address.is_global:
                raise ValueError("Une adresse privée ne peut pas être signalée bloquée")
            for text in (self.username, self.password):
                if any(ord(char) < 32 or ord(char) == 127 for char in text):
                    raise ValueError("Identifiants contenant des contrôles")
            if self.protected and self.blocked:
                raise ValueError("Adresse protégée signalée bloquée")
            if self.timestamp:
                timestamp = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
                if timestamp.tzinfo is None or abs(timestamp.timestamp() - self.sent_at) > 300:
                    raise ValueError("Horodatage incohérent")
        if self.day != local_day(self.sent_at):
            raise ValueError("Jour du compteur incohérent")
        return self


class SentinelManager:
    def __init__(self, settings: Settings, runtime: Any):
        self.settings = settings
        self.runtime = runtime
        self.incidents: deque[dict[str, Any]] = deque(maxlen=50)
        self.replays: OrderedDict[str, float] = OrderedDict()
        self.agents: dict[str, dict[str, Any]] = {}
        self.counters: dict[str, int] = {}
        self.counter_day = local_day()
        self.geo_cache: OrderedDict[str, tuple[float, str, str]] = OrderedDict()
        self.geo_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self.queue_dropped = 0
        self.task: asyncio.Task | None = None
        self.client: httpx.AsyncClient | None = None
        self._persist_lock = asyncio.Lock()
        self._geo_next_request = 0.0

    async def start(self) -> None:
        if not self.settings.vps_token:
            return
        await asyncio.to_thread(self._load_counters)
        if self.settings.sentinel_geoip_enabled:
            self.task = asyncio.create_task(self._geo_worker(), name="noc-sentinel-geoip")

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        if self.client:
            await self.client.aclose()

    def _load_counters(self) -> None:
        path = Path(self.settings.sentinel_state_path)
        try:
            if not path.is_file() or path.stat().st_size > 8192:
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("day") != local_day() or not isinstance(data.get("agents"), dict):
                return
            self.counters = {name: count for name, count in list(data["agents"].items())[:8] if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) and type(count) is int and 0 <= count <= 100000000}
        except (OSError, ValueError, AttributeError):
            LOGGER.warning("Compteurs Sentinel non disponibles ; reprise sur le prochain heartbeat")

    def _save_counters(self, data: dict[str, Any]) -> None:
        path = Path(self.settings.sentinel_state_path)
        temporary = path.with_suffix(".tmp")
        try:
            if not path.parent.is_dir() or path.is_symlink() or temporary.is_symlink():
                return
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, separators=(",", ":"))
            os.replace(temporary, path)
        except OSError:
            LOGGER.warning("Persistance du compteur Sentinel indisponible")

    async def _persist(self) -> None:
        async with self._persist_lock:
            await asyncio.to_thread(self._save_counters, {"day": self.counter_day, "agents": dict(self.counters)})

    def _roll_day(self) -> None:
        day = local_day()
        if day != self.counter_day:
            self.counters.clear()
            self.counter_day = day

    async def accept(self, envelope: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.vps_token:
            raise HTTPException(503, "Sentinel désactivé : jeton VPS non configuré")
        try:
            packet = SentinelPacket.model_validate(await asyncio.to_thread(decrypt, envelope, self.settings.vps_token))
        except (ValueError, ValidationError, TypeError):
            raise HTTPException(422, "Incident Sentinel chiffré invalide") from None
        now = time.time()
        if abs(now - packet.sent_at) > 300:
            raise HTTPException(422, "Horodatage Sentinel expiré")
        for key, stamp in list(self.replays.items()):
            if now - stamp > 600:
                self.replays.pop(key, None)
        replay_key = packet.agent + ":" + packet.event_id
        if replay_key in self.replays:
            return {"ok": True, "duplicate": True}
        self._roll_day()
        if packet.agent not in self.agents:
            for agent, entry in list(self.agents.items()):
                if time.monotonic() - entry["seen"] > self.settings.sentinel_stale_seconds:
                    self.agents.pop(agent, None)
        if packet.agent not in self.agents and len(self.agents) >= 8:
            raise HTTPException(429, "Limite d'agents Sentinel atteinte")
        if packet.day == self.counter_day and packet.agent not in self.counters and len(self.counters) >= 8:
            raise HTTPException(429, "Limite de compteurs d'agents pour la journée atteinte")
        self.replays[replay_key] = now
        while len(self.replays) > 1024:
            self.replays.popitem(last=False)
        seen = datetime.fromtimestamp(packet.sent_at, timezone.utc).isoformat(timespec="seconds")
        self.agents[packet.agent] = {"seen": time.monotonic(), "last_seen": seen}
        count_changed = False
        if packet.day == self.counter_day and packet.blocked_today > self.counters.get(packet.agent, 0):
            self.counters[packet.agent] = packet.blocked_today
            count_changed = True
        if packet.kind == "incident":
            timestamp = packet.timestamp or seen
            incident = {"id": replay_key, "timestamp": timestamp, "source_ip": packet.source_ip, "country_code": "", "country_name": "Indéterminé", "username": packet.username, "password": packet.password, "blocked": packet.blocked, "agent": packet.agent, "ban_error": packet.ban_error, "protected": packet.protected}
            self.incidents.append(incident)
            state = "bloquée" if packet.blocked else "non bloquée"
            self.runtime.collector.add_event("warning" if packet.blocked else "error", f"Sentinel · {packet.source_ip} · tentative {state}")
            if self.settings.sentinel_geoip_enabled:
                try:
                    self.geo_queue.put_nowait(incident)
                except asyncio.QueueFull:
                    self.queue_dropped += 1
        # Publications sans nouvelle sonde : pas de latence supplémentaire du collecteur.
        await self.runtime.publish_module_update()
        if count_changed:
            await self._persist()
        return {"ok": True, "duplicate": False}

    def snapshot(self) -> dict[str, Any]:
        self._roll_day()
        current = time.monotonic()
        last_seen = max((entry["last_seen"] for entry in self.agents.values()), default=None)
        active = any(current - entry["seen"] <= self.settings.sentinel_stale_seconds for entry in self.agents.values())
        return {"configured": bool(self.settings.vps_token), "agent_status": "active" if active else "disconnected" if self.agents else "waiting", "last_seen": last_seen, "blocked_today": sum(self.counters.values()), "incidents": list(self.incidents), "queue_dropped": self.queue_dropped}

    async def _geo_worker(self) -> None:
        while True:
            incident = await self.geo_queue.get()
            try:
                country_code, country_name = await self.lookup_country(incident["source_ip"])
                incident["country_code"] = country_code
                incident["country_name"] = country_name
                if incident in self.incidents:
                    await self.runtime.publish_module_update()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.debug("GeoIP indisponible : %s", type(exc).__name__)
            finally:
                self.geo_queue.task_done()

    async def lookup_country(self, source_ip: str) -> tuple[str, str]:
        address = ipaddress.ip_address(source_ip)
        if not address.is_global:
            return "", "Réseau privé"
        cached = self.geo_cache.get(source_ip)
        if cached and time.monotonic() < cached[0]:
            self.geo_cache.move_to_end(source_ip)
            return cached[1], cached[2]
        result = ("", "Indéterminé")
        try:
            # Limite à 40 requêtes/minute, indépendante des sondes et des SSE.
            await asyncio.sleep(max(0, self._geo_next_request - time.monotonic()))
            self._geo_next_request = time.monotonic() + 1.5
            if self.client is None:
                self.client = await asyncio.to_thread(lambda: httpx.AsyncClient(timeout=2, trust_env=False, follow_redirects=False, limits=httpx.Limits(max_connections=1, max_keepalive_connections=1)))
            async with asyncio.timeout(2):
                async with self.client.stream("GET", f"https://ipwho.is/{address}", params={"fields": "success,country,country_code"}) as response:
                    if response.status_code == 200:
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(content) + len(chunk) > 16384:
                                raise ValueError("GeoIP trop volumineux")
                            content.extend(chunk)
                        data = json.loads(content)
                        code, name = data.get("country_code"), data.get("country")
                        if data.get("success") is True and isinstance(code, str) and re.fullmatch(r"[A-Z]{2}", code) and isinstance(name, str):
                            result = code, name[:80]
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError, AttributeError):
            pass
        self.geo_cache[source_ip] = (time.monotonic() + (86400 if result[0] else 300), *result)
        while len(self.geo_cache) > 256:
            self.geo_cache.popitem(last=False)
        return result
