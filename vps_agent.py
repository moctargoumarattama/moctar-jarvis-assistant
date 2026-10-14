#!/usr/bin/env python3
"""Agent Linux autonome : collecte /proc et publie un rapport sans dépendance."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


SERVICE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}\Z")
STOP = threading.Event()


class ConfigurationError(ValueError):
    """Configuration invalide, affichable sans exposer de secret."""


@dataclass(frozen=True)
class Settings:
    report_url: str
    token: str
    services: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> "Settings":
        url = os.environ.get("NOC_REPORT_URL", "").strip()
        token = os.environ.get("NOC_VPS_TOKEN", "").strip()
        if not url or not token:
            raise ConfigurationError("NOC_REPORT_URL et NOC_VPS_TOKEN sont obligatoires.")
        if len(token) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise ConfigurationError("NOC_VPS_TOKEN doit être un jeton ASCII sans espace.")
        try:
            parsed = urllib.parse.urlsplit(url)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise ConfigurationError("NOC_REPORT_URL est invalide.") from exc
        if (
            parsed.scheme not in {"https", "http"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/api/vps/report"
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ConfigurationError("NOC_REPORT_URL doit désigner /api/vps/report, sans identifiants ni paramètres.")
        try:
            address = ipaddress.ip_address(hostname)
            private_transport = address.is_loopback or (
                address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10")
            )
        except ValueError:
            private_transport = hostname.lower() == "localhost"
        allow_http = os.environ.get("NOC_ALLOW_INSECURE_HTTP", "false").lower() in {"1", "true", "yes"}
        if parsed.scheme == "http" and not private_transport and not allow_http:
            raise ConfigurationError("HTTP est réservé au loopback ou à Tailscale ; utilisez HTTPS ailleurs ou NOC_ALLOW_INSECURE_HTTP=true.")
        names = tuple(name.strip() for name in os.environ.get("NOC_VPS_SERVICES", "").split(",") if name.strip())
        if len(names) > 16 or any(len(name) > 128 or not SERVICE_PATTERN.fullmatch(name) for name in names):
            raise ConfigurationError("NOC_VPS_SERVICES accepte au plus 16 noms systemd valides de 128 caractères.")
        return cls(url.rstrip("/"), token, tuple(dict.fromkeys(names)))


class CpuSampler:
    def __init__(self) -> None:
        self.previous: tuple[int, int] | None = None

    def sample(self) -> float:
        fields = Path("/proc/stat").read_text(encoding="ascii").partition("\n")[0].split()
        if not fields or fields[0] != "cpu" or len(fields) < 5:
            raise OSError("Statistiques CPU indisponibles.")
        values = [int(value) for value in fields[1:9]]
        total = sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        previous = self.previous
        self.previous = (total, idle)
        if previous is None or total <= previous[0]:
            return 0.0
        delta_total, delta_idle = total - previous[0], idle - previous[1]
        return round(max(0.0, min(100.0, 100.0 * (delta_total - delta_idle) / delta_total)), 1)


def memory_metrics() -> dict[str, int | float]:
    values = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        key, _, raw = line.partition(":")
        fields = raw.split()
        if fields:
            values[key] = int(fields[0]) * 1024
    total = values["MemTotal"]
    available = values.get("MemAvailable")
    if available is None:
        available = sum(values.get(key, 0) for key in ("MemFree", "Buffers", "Cached", "SReclaimable")) - values.get("Shmem", 0)
    used = max(0, min(total, total - available))
    return {"used": used, "total": total, "percent": round(100.0 * used / total, 1) if total else 0.0}


def service_status(name: str) -> dict[str, str]:
    unit = name if name.endswith(".service") else name + ".service"
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            check=False,
            text=True,
        )
        value = result.stdout.strip()
        status = value if value in {"active", "inactive", "failed", "activating", "deactivating", "reloading"} else "unknown"
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        status = "unknown"
    return {"name": name, "status": status}


def collect_report(settings: Settings, cpu: CpuSampler) -> dict:
    disk = shutil.disk_usage("/")
    services = []
    if settings.services:
        with ThreadPoolExecutor(max_workers=min(4, len(settings.services))) as executor:
            services = list(executor.map(service_status, settings.services))
    cpu_percent = cpu.sample()
    if not math.isfinite(cpu_percent):
        cpu_percent = 0.0
    return {
        "hostname": socket.gethostname()[:128] or "unknown",
        "cpu_percent": cpu_percent,
        "memory": memory_metrics(),
        "disk": {"used": disk.used, "total": disk.total, "percent": round(100.0 * disk.used / disk.total, 1) if disk.total else 0.0},
        "services": services,
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Ne jamais transmettre le jeton à une cible de redirection."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def send_report(settings: Settings, report: dict) -> bool:
    body = json.dumps(report, allow_nan=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        settings.report_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "x-vps-token": settings.token},
    )
    # Pas de proxy hérité : un proxy non choisi ne doit pas recevoir le jeton VPS.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for attempt in range(2):
        if STOP.is_set():
            return False
        try:
            with opener.open(request, timeout=3.0) as response:
                if 200 <= response.status < 300:
                    return True
        except urllib.error.HTTPError as exc:
            print(f"Rapport refusé : HTTP {exc.code}.", file=sys.stderr, flush=True)
            exc.close()
            # Une erreur d'authentification ou de validation ne bénéficie pas d'un nouvel essai.
            if exc.code < 500 and exc.code != 429:
                return False
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            print("Rapport non transmis : réseau indisponible ou délai dépassé.", file=sys.stderr, flush=True)
        if attempt == 0 and STOP.wait(1.0):
            return False
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="envoyer un rapport puis quitter (cron)")
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_environment()
        if sys.platform != "linux":
            raise ConfigurationError("L'agent VPS nécessite Linux et /proc.")
    except ConfigurationError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 2
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signal, _frame: STOP.set())
    cpu = CpuSampler()
    try:
        cpu.sample()
    except (OSError, ValueError):
        print("Collecte CPU indisponible : vérifiez l'accès à /proc.", file=sys.stderr)
        return 1
    if STOP.wait(0.25):
        return 0
    next_tick = time.monotonic()
    while not STOP.is_set():
        try:
            accepted = send_report(settings, collect_report(settings, cpu))
        except (OSError, ValueError, KeyError, UnicodeError):
            print("Collecte des métriques indisponible ; nouvel essai au prochain cycle.", file=sys.stderr, flush=True)
            accepted = False
        if args.once:
            return 0 if accepted else 1
        next_tick += 5.0
        now = time.monotonic()
        while next_tick <= now:
            next_tick += 5.0
        STOP.wait(max(0.0, next_tick - now))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
