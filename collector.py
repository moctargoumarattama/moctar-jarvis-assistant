"""Une seule collecte partagée, sondes parallèles et historique borné."""
from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import shutil
import socket
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, TypedDict

import httpx
import psutil

from config import SERVICES, Service, Settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DiskMetrics(TypedDict):
    path: str
    used: int | None
    total: int | None
    percent: float | None
    available: bool
    mounted: bool
    error: str | None


class Snapshot(TypedDict):
    timestamp: str
    hostname: str
    platform: str
    uptime_seconds: float
    global_status: str
    hardware: dict[str, Any]
    services: list[dict[str, Any]]
    infrastructure: dict[str, Any]
    vps: list[dict[str, Any]]
    events: list[dict[str, Any]]
    actions: dict[str, bool]
    collector: dict[str, Any]


async def run_command(arguments: list[str], timeout: float) -> tuple[int, str, str]:
    """Sans shell; tuer et récolter le sous-processus sur timeout/annulation."""
    process = await asyncio.create_subprocess_exec(
        *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout)
        return process.returncode or 0, output.decode(errors="replace").strip(), error.decode(errors="replace").strip()
    except BaseException:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        with contextlib.suppress(Exception):
            await process.communicate()
        raise


class Collector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.hostname = socket.gethostname()
        self.systemctl = shutil.which("systemctl") if platform.system() == "Linux" else None
        self.client: httpx.AsyncClient | None = None
        self.events: deque[dict[str, Any]] = deque(maxlen=settings.event_limit)
        self._event_id = 0
        self._states: dict[str, Any] = {}
        self._hardware_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="noc-hardware")

    async def start(self) -> None:
        self.client = httpx.AsyncClient(
            # Toutes les sondes utilisent HTTP sur 127.0.0.1 : aucun magasin TLS nécessaire.
            timeout=self.settings.http_timeout, trust_env=False, follow_redirects=False, verify=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
        )
        # Les appels suivants auront un delta CPU réel; aucun sleep bloquant.
        await asyncio.get_running_loop().run_in_executor(self._hardware_executor, self._prime_cpu)
        self.add_event("info", f"Collecteur démarré · sondes toutes les {self.settings.interval:g} secondes")
        if not self.systemctl:
            self.add_event("warning", "systemd indisponible sur cet hôte · états des services inconnus")

    @staticmethod
    def _prime_cpu() -> None:
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None
        self._hardware_executor.shutdown(wait=False, cancel_futures=True)

    def add_event(self, level: str, message: str) -> None:
        self._event_id += 1
        self.events.append({"id": self._event_id, "timestamp": utc_now(), "level": level, "message": message[:500]})

    def transition(self, key: str, state: Any, message: str, healthy: bool) -> None:
        previous = self._states.get(key)
        if key not in self._states or previous != state:
            self._states[key] = state
            self.add_event("info" if healthy else "error", message)

    async def systemd_status(self, name: str) -> tuple[str, str | None]:
        if not self.systemctl:
            return "unknown", "systemd indisponible"
        try:
            code, output, error = await run_command([self.systemctl, "is-active", name + ".service"], self.settings.systemctl_timeout)
            state = output if output in {"active", "inactive", "failed", "activating", "deactivating", "reloading", "maintenance"} else "unknown"
            return state, None if state == "active" else (error[:200] or f"systemd : {state} (code {code})")
        except asyncio.TimeoutError:
            return "unknown", "Sonde systemd expirée"
        except (OSError, RuntimeError) as exc:
            return "unknown", f"systemd indisponible ({type(exc).__name__})"

    async def http_probe(self, url: str, headers: dict[str, str] | None = None, read_json: bool = False) -> dict[str, Any]:
        try:
            async with asyncio.timeout(self.settings.http_timeout):
                return await self._http_probe(url, headers, read_json)
        except asyncio.TimeoutError:
            return {"ok": False, "status": None, "latency_ms": None, "data": None, "error": "Sonde HTTP expirée"}

    async def _http_probe(self, url: str, headers: dict[str, str] | None = None, read_json: bool = False) -> dict[str, Any]:
        started = time.perf_counter()
        if not self.client:
            return {"ok": False, "status": None, "latency_ms": None, "data": None, "error": "Client HTTP indisponible"}
        try:
            # streaming : une page applicative volumineuse n'est jamais chargée en RAM.
            async with self.client.stream("GET", url, headers=headers) as response:
                data = None
                if read_json and response.is_success:
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(chunks) + len(chunk) > 16384:
                            raise ValueError("Réponse JSON trop volumineuse")
                        chunks.extend(chunk)
                    import json
                    data = json.loads(chunks)
                    if not isinstance(data, dict):
                        raise ValueError("Réponse JSON non objet")
                # Les redirections normales d'une application constituent une réponse.
                ok = 200 <= response.status_code < 400
                return {"ok": ok, "status": response.status_code, "latency_ms": round((time.perf_counter() - started) * 1000, 2), "data": data, "error": None if ok else f"HTTP {response.status_code}"}
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            return {"ok": False, "status": None, "latency_ms": None, "data": None, "error": f"Sonde HTTP : {type(exc).__name__}"}

    async def service_probe(self, service: Service) -> dict[str, Any]:
        systemd, http = await asyncio.gather(
            self.systemd_status(service.name),
            self.http_probe(f"http://127.0.0.1:{service.port}/"),
        )
        state, systemd_error = systemd
        return {
            "name": service.name, "label": service.label, "port": service.port,
            "systemd": state, "healthy": state == "active" and http["ok"],
            "http_status": http["status"], "latency_ms": http["latency_ms"],
            "checked_at": utc_now(), "error": http["error"] or systemd_error,
        }

    async def cloudflare_probe(self) -> dict[str, Any]:
        state, error = await self.systemd_status("cloudflared")
        return {"name": "cloudflared", "systemd": state, "healthy": state == "active", "error": error}

    async def whatsapp_probe(self) -> dict[str, Any]:
        systemd, http = await asyncio.gather(
            self.systemd_status("klasora-whatsapp"),
            self.http_probe("http://127.0.0.1:3001/session/1/status", {"x-gateway-secret": self.settings.gateway_secret}, read_json=True),
        )
        state, error = systemd
        data = http["data"] or {}
        connected = data.get("connected") is True
        phone = data.get("phone")
        return {
            "name": "klasora-whatsapp", "systemd": state,
            "healthy": state == "active" and http["ok"] and connected,
            "connected": connected, "status": "CONNECTE" if connected else ("DECONNECTE" if http["data"] is not None else "INCONNU"),
            "phone": str(phone)[:64] if isinstance(phone, (str, int)) and not isinstance(phone, bool) else None,
            "latency_ms": http["latency_ms"], "error": http["error"] or error,
        }

    @staticmethod
    def disk_metrics(path: str, require_mount: bool = False) -> DiskMetrics:
        mounted = os.path.ismount(path)
        if mounted and require_mount:
            try:
                # Un bind systemd peut être un mountpoint tout en restant sur /.
                mounted = os.stat(path).st_dev != os.stat(os.path.abspath(os.sep)).st_dev
            except OSError:
                mounted = False
        if require_mount and not mounted:
            return {"path": path, "used": None, "total": None, "percent": None, "available": False, "mounted": False, "error": "Sauvegarde non montée"}
        try:
            usage = psutil.disk_usage(path)
            return {"path": path, "used": usage.used, "total": usage.total, "percent": usage.percent, "available": True, "mounted": mounted, "error": None}
        except (OSError, ValueError) as exc:
            return {"path": path, "used": None, "total": None, "percent": None, "available": False, "mounted": mounted, "error": f"Disque indisponible ({type(exc).__name__})"}

    def hardware_metrics(self) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        temperature = None
        try:
            sensors = psutil.sensors_temperatures()
            cpu_readings = [reading.current for name, readings in sensors.items() if any(part in name.lower() for part in ("coretemp", "k10temp", "cpu", "zenpower")) for reading in readings if reading.current is not None and -20 <= reading.current <= 150]
            if cpu_readings:
                temperature = round(max(cpu_readings), 1)
        except (AttributeError, OSError, RuntimeError):
            pass
        return {
            "cpu": {"percent": psutil.cpu_percent(interval=None), "cores": psutil.cpu_percent(interval=None, percpu=True)},
            "memory": {"used": memory.total - memory.available, "total": memory.total, "percent": memory.percent},
            "swap": {"used": swap.used, "total": swap.total, "percent": swap.percent},
            "temperature_c": temperature,
            "disks": {"root": self.disk_metrics(os.path.abspath(os.sep)), "backup": self.disk_metrics(self.settings.backup_path, require_mount=True)},
            "process_rss_bytes": psutil.Process().memory_info().rss,
        }

    async def collect(self) -> Snapshot:
        results = await asyncio.gather(
            asyncio.get_running_loop().run_in_executor(self._hardware_executor, self.hardware_metrics),
            *(self.service_probe(service) for service in SERVICES),
            self.cloudflare_probe(), self.whatsapp_probe(), return_exceptions=True,
        )
        hardware = results[0]
        hardware_error = isinstance(hardware, BaseException)
        if hardware_error:
            hardware = {"cpu": {"percent": None, "cores": []}, "memory": {"used": None, "total": None, "percent": None}, "swap": {"used": None, "total": None, "percent": None}, "temperature_c": None, "disks": {"root": self.disk_metrics(os.path.abspath(os.sep)), "backup": self.disk_metrics(self.settings.backup_path, True)}, "process_rss_bytes": None}
        services = []
        for service, result in zip(SERVICES, results[1:5]):
            if isinstance(result, BaseException):
                result = {"name": service.name, "label": service.label, "port": service.port, "systemd": "unknown", "healthy": False, "http_status": None, "latency_ms": None, "checked_at": utc_now(), "error": "Erreur de collecte"}
            services.append(result)
            self.transition(service.name, (result["healthy"], result["systemd"], result["http_status"]), f"{service.label} · {'opérationnel' if result['healthy'] else 'indisponible'} · systemd {result['systemd']}", result["healthy"])
        cloudflare = results[5]
        whatsapp = results[6]
        if isinstance(cloudflare, BaseException):
            cloudflare = {"name": "cloudflared", "systemd": "unknown", "healthy": False, "error": "Erreur de collecte"}
        if isinstance(whatsapp, BaseException):
            whatsapp = {"name": "klasora-whatsapp", "systemd": "unknown", "healthy": False, "connected": False, "status": "INCONNU", "phone": None, "latency_ms": None, "error": "Erreur de collecte"}
        self.transition("cloudflared", (cloudflare["healthy"], cloudflare["systemd"]), f"Cloudflare Tunnel · {'actif' if cloudflare['healthy'] else 'indisponible'}", cloudflare["healthy"])
        self.transition("whatsapp", (whatsapp["healthy"], whatsapp["status"]), f"Passerelle WhatsApp · {whatsapp['status']}", whatsapp["healthy"])
        alerts = {
            "cpu": hardware["cpu"]["percent"] is not None and hardware["cpu"]["percent"] >= 95,
            "ram": hardware["memory"]["percent"] is not None and hardware["memory"]["percent"] >= 90,
            "temperature": hardware["temperature_c"] is not None and hardware["temperature_c"] >= 85,
            "disk": hardware["disks"]["root"]["percent"] is not None and hardware["disks"]["root"]["percent"] >= 90,
        }
        for name, alert in alerts.items():
            if alert or self._states.get("hardware:" + name) is True:
                self.transition("hardware:" + name, alert, f"{name.upper()} · {'seuil critique atteint' if alert else 'retour à la normale'}", not alert)
        root_available = hardware["disks"]["root"]["available"]
        self.transition("root-disk", root_available, "Disque racine · " + ("accessible" if root_available else "mesure indisponible"), root_available)
        self.transition("hardware-collector", not hardware_error, "Métriques matérielles · " + ("disponibles" if not hardware_error else "collecte indisponible"), not hardware_error)
        healthy = not hardware_error and root_available and all(service["healthy"] for service in services) and cloudflare["healthy"] and whatsapp["healthy"] and not any(alerts.values())
        try:
            uptime = max(0, time.time() - psutil.boot_time())
        except (OSError, RuntimeError):
            uptime = 0
        return {
            "timestamp": utc_now(), "hostname": self.hostname, "platform": platform.system(), "uptime_seconds": uptime,
            "global_status": "operational" if healthy else "incident", "hardware": hardware, "services": services,
            "infrastructure": {"cloudflared": cloudflare, "whatsapp": whatsapp}, "vps": [], "events": list(self.events),
            "actions": {"restart_enabled": bool(self.settings.enable_restart and self.systemctl and shutil.which("sudo"))},
            "collector": {"last_success": utc_now() if not hardware_error else None, "error": "Métriques matérielles indisponibles" if hardware_error else None},
        }
