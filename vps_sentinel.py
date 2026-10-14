#!/usr/bin/env python3
"""Piège TELNET borné ; seuls les IP publics hors administration sont bloqués."""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import ipaddress
import json
import logging
import os
import re
import shutil
import signal
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sentinel_crypto import encrypt

LOGGER = logging.getLogger("noc-sentinel")
DEFAULT_PROTECTED = ("127.0.0.0/8", "::1/128", "100.64.0.0/10", "192.168.11.0/24", "187.77.171.225/32", "100.68.163.14/32", "fc00::/7", "fe80::/10")
DEFAULT_URL = "http://100.68.163.14:5050/api/sentinel/incident"
MAX_LINE_BYTES = 256
MAX_STATE_BYTES = 32768


def local_day() -> str:
    return datetime.now(ZoneInfo("Africa/Casablanca")).strftime("%Y-%m-%d")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _integer_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise ValueError(f"Configuration invalide : {name}") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"Configuration invalide : {name}")
    return value


@dataclass(frozen=True)
class Settings:
    token: str
    url: str = DEFAULT_URL
    port: int = 22222
    firewall: str = "ufw"
    max_clients: int = 16
    state_file: Path = Path("/var/lib/noc-sentinel/counters.json")
    protected: tuple = tuple(ipaddress.ip_network(value) for value in DEFAULT_PROTECTED)

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("NOC_VPS_TOKEN", "").strip()
        if not token or len(token) > 4096 or any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise ValueError("NOC_VPS_TOKEN est obligatoire, ASCII et sans espace.")
        url = os.environ.get("SENTINEL_REPORT_URL", DEFAULT_URL).strip()
        try:
            parsed = urllib.parse.urlsplit(url)
            host, port = parsed.hostname, parsed.port
        except ValueError:
            raise ValueError("SENTINEL_REPORT_URL invalide") from None
        if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.path != "/api/sentinel/incident" or (port is not None and not 1 <= port <= 65535):
            raise ValueError("SENTINEL_REPORT_URL doit désigner /api/sentinel/incident sans identifiants ni paramètres.")
        if parsed.scheme == "http":
            try:
                address = ipaddress.ip_address(host)
                private_transport = address.is_loopback or (address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10"))
            except ValueError:
                private_transport = host.lower() == "localhost"
            if not private_transport:
                raise ValueError("HTTP est réservé au transport Tailscale ou loopback ; utilisez HTTPS ailleurs.")
        firewall = os.environ.get("SENTINEL_FIREWALL", "ufw").lower()
        if firewall not in {"ufw", "iptables"}:
            raise ValueError("SENTINEL_FIREWALL doit être ufw ou iptables.")
        additional = [value.strip() for value in os.environ.get("SENTINEL_PROTECTED_CIDRS", "").split(",") if value.strip()]
        if len(additional) > 64:
            raise ValueError("Au plus 64 réseaux supplémentaires sont acceptés.")
        try:
            protected = tuple(ipaddress.ip_network(value, strict=False) for value in (*DEFAULT_PROTECTED, *additional))
        except ValueError:
            raise ValueError("SENTINEL_PROTECTED_CIDRS contient un réseau invalide.") from None
        state_file = Path(os.environ.get("SENTINEL_STATE_FILE", "/var/lib/noc-sentinel/counters.json"))
        if not state_file.is_absolute():
            raise ValueError("SENTINEL_STATE_FILE doit être un chemin absolu.")
        return cls(token, url, _integer_env("SENTINEL_PORT", 22222, 1, 65535), firewall, _integer_env("SENTINEL_MAX_CLIENTS", 16, 1, 16), state_file, protected)


def eligible_ip(source: str, protected: tuple) -> tuple[str | None, str | None]:
    try:
        address = ipaddress.ip_address(source)
        if getattr(address, "ipv4_mapped", None) is not None:
            address = address.ipv4_mapped
    except ValueError:
        return None, "invalid_source"
    if any(address.version == network.version and address in network for network in protected):
        return str(address), "protected"
    if not address.is_global or address.is_multicast or address.is_unspecified:
        return str(address), "non_global"
    return str(address), None


class Counters:
    """Petit compteur atomique persistant ; le changement de jour remet le total à zéro."""

    def __init__(self, path: Path, day_provider=local_day):
        self.path, self.day_provider = path, day_provider
        self.day, self.blocked_today = day_provider(), 0
        self.cache: OrderedDict[str, tuple[float, bool]] = OrderedDict()
        if path.is_symlink():
            raise ValueError("Le fichier de compteur ne doit pas être un lien symbolique.")
        try:
            if path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("day") == self.day and type(value.get("blocked_today")) is int and 0 <= value["blocked_today"] <= 2**31 - 1:
                self.blocked_today = value["blocked_today"]
            if isinstance(value, dict) and isinstance(value.get("cache"), list):
                for item in value["cache"][-256:]:
                    if isinstance(item, list) and len(item) == 3 and isinstance(item[0], str) and type(item[1]) in {int, float} and type(item[2]) is bool:
                        address = ipaddress.ip_address(item[0])
                        if address.is_global and not address.is_multicast and time.time() - 60 <= item[1] <= time.time():
                            self.cache[str(address)] = (float(item[1]), item[2])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, OverflowError):
            LOGGER.warning("Compteur précédent indisponible ; un nouveau compteur borné sera enregistré.")

    def refresh_day(self) -> None:
        today = self.day_provider()
        if today != self.day:
            self.day, self.blocked_today = today, 0
            self.save()

    def increment(self) -> None:
        self.refresh_day()
        self.blocked_today = min(2**31 - 1, self.blocked_today + 1)
        self.save()

    def remember(self, source: str, blocked: bool) -> None:
        self.cache[source] = (time.time(), blocked)
        self.cache.move_to_end(source)
        while len(self.cache) > 256:
            self.cache.popitem(last=False)

    def recent(self, source: str) -> bool | None:
        item = self.cache.get(source)
        if item is None or time.time() - item[0] > 60:
            return None
        self.cache.move_to_end(source)
        return item[1]

    def save(self) -> None:
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.is_symlink():
                raise OSError
            raw = json.dumps({"day": self.day, "blocked_today": self.blocked_today, "cache": [[ip, stamp, blocked] for ip, (stamp, blocked) in self.cache.items()]}, separators=(",", ":")).encode()
            if len(raw) > MAX_STATE_BYTES:
                raise OSError
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError:
            LOGGER.error("Compteur non persisté ; vérifier les droits du répertoire d'état.")
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink()


async def run_firewall(command: list[str]) -> tuple[int, bytes]:
    """Commande fixe, cinq secondes, quatre Kio maximum, enfant nettoyé à l'annulation."""
    process = None
    try:
        async with asyncio.timeout(5):
            process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}, limit=4096)
            output = bytearray()
            while True:
                chunk = await process.stdout.read(512)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 4096:
                    raise RuntimeError("Sortie de commande trop grande")
            return await process.wait(), bytes(output)
    finally:
        if process is not None and process.returncode is None:
            with contextlib.suppress(OSError, ProcessLookupError):
                process.kill()
                await process.wait()


class Firewall:
    def __init__(self, settings: Settings, counters: Counters):
        self.settings, self.counters = settings, counters
        self.lock = asyncio.Lock()

    def command(self, source: str) -> list[str] | None:
        if self.settings.firewall == "ufw":
            binary = shutil.which("ufw")
            try:
                enabled = any(re.fullmatch(r"\s*ENABLED\s*=\s*yes\s*", line) for line in Path("/etc/ufw/ufw.conf").read_text(encoding="ascii").splitlines())
            except (OSError, UnicodeError):
                enabled = False
            return [binary, "insert", "1", "deny", "from", source, "to", "any", "comment", "noc-sentinel"] if binary and enabled else None
        name = "ip6tables" if ipaddress.ip_address(source).version == 6 else "iptables"
        binary = shutil.which(name)
        return [binary, "-w", "2", "-I", "INPUT", "1", "-s", source, "-j", "DROP"] if binary else None

    async def block(self, source: str) -> tuple[bool, bool, str | None]:
        source, reason = eligible_ip(source, self.settings.protected)
        if reason:
            return False, False, reason
        try:
            async with asyncio.timeout(6):
                async with self.lock:
                    return await self._block_locked(source)
        except TimeoutError:
            return False, False, "firewall_busy"

    async def _block_locked(self, source: str) -> tuple[bool, bool, str | None]:
        recent = self.counters.recent(source)
        if recent is not None:
            return recent, False, None if recent else "cooldown"
        command = self.command(source)
        if command is None:
            return False, False, "firewall_unavailable"
        try:
            code, output = await run_firewall(command)
        except (OSError, TimeoutError, RuntimeError):
            self.counters.remember(source, False)
            return False, False, "firewall_error"
        blocked = code == 0
        new_block = blocked and not (self.settings.firewall == "ufw" and (b"Skipping" in output or b"already exists" in output))
        self.counters.remember(source, blocked)
        if new_block:
            self.counters.increment()
        return blocked, new_block, None if blocked else "firewall_refused"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def post_report(settings: Settings, payload: dict) -> bool:
    body = json.dumps(encrypt(payload, settings.token), separators=(",", ":")).encode("ascii")
    request = urllib.request.Request(settings.url, data=body, method="POST", headers={"Content-Type": "application/json", "X-VPS-Token": settings.token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for attempt in range(2):
        try:
            with opener.open(request, timeout=3) as response:
                return 200 <= response.status < 300
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code < 500 and code != 429:
                return False
        except (OSError, ValueError, http.client.HTTPException):
            pass
        if attempt == 0:
            time.sleep(0.5)
    return False


async def _line(reader: asyncio.StreamReader) -> str:
    raw = await reader.readuntil(b"\n")
    if len(raw) > MAX_LINE_BYTES:
        raise ValueError("Ligne trop longue")
    # Les négociations TELNET binaires et les contrôles ne sont jamais conservés.
    cleaned = bytearray()
    index = 0
    while index < len(raw):
        if raw[index] != 255:
            cleaned.append(raw[index])
            index += 1
        elif index + 1 >= len(raw):
            break
        elif raw[index + 1] == 250:  # IAC SB ... IAC SE
            end = raw.find(b"\xff\xf0", index + 2)
            index = len(raw) if end < 0 else end + 2
        elif raw[index + 1] in {251, 252, 253, 254}:
            index += 3
        else:
            index += 2
    return "".join(char for char in cleaned.decode("utf-8", errors="ignore").strip() if char.isprintable())[:64]


async def credentials(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> tuple[str, str, str]:
    """TELNET uniquement ; un banner SSH n'est pas une négociation d'authentification."""
    username = ""
    try:
        async with asyncio.timeout(5):
            writer.write(b"login: ")
            await writer.drain()
            username = await _line(reader)
            if username.startswith("SSH-"):
                return "", "", "ssh_negotiation"
            writer.write(b"Password: ")
            await writer.drain()
            return username, await _line(reader), "credentials"
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, ConnectionError, OSError):
        return username, "", "negotiation_incomplete"


class Sentinel:
    def __init__(self, settings: Settings, counters: Counters):
        self.settings, self.counters = settings, counters
        self.firewall = Firewall(settings, counters)
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=64)
        self.clients: set[asyncio.Task] = set()
        self.reporter_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentinel-report")
        self.agent = socket.gethostname()[:128]

    def base_payload(self, kind: str) -> dict:
        self.counters.refresh_day()
        return {"kind": kind, "event_id": uuid.uuid4().hex, "sent_at": time.time(), "agent": self.agent, "day": self.counters.day, "blocked_today": self.counters.blocked_today}

    def enqueue(self, payload: dict) -> None:
        if self.queue.full():
            self.queue.get_nowait()
            self.queue.task_done()
        self.queue.put_nowait(payload)

    def connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        source = str(peer[0]) if isinstance(peer, tuple) and peer else "unknown"
        if len(self.clients) >= self.settings.max_clients:
            writer.close()
            self.enqueue({**self.base_payload("incident"), "source_ip": source, "username": "", "password": "", "blocked": False, "new_block": False, "ban_error": "capacity", "timestamp": utc_timestamp()})
            return
        task = asyncio.create_task(self.handle(reader, writer, source))
        self.clients.add(task)
        task.add_done_callback(self.clients.discard)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, source: str) -> None:
        try:
            username, password, capture = await credentials(reader, writer)
            writer.close()
            blocked, new_block, reason = await self.firewall.block(source)
            payload = {**self.base_payload("incident"), "source_ip": source, "username": username, "password": password, "blocked": blocked, "new_block": new_block, "capture": capture, "timestamp": utc_timestamp()}
            if reason:
                payload["ban_error"] = reason
            self.enqueue(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Le contenu de l'exception peut inclure les identifiants fournis : ne pas le journaliser.
            LOGGER.warning("Tentative interrompue ; aucune donnée sensible dans le journal.")
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 1)

    async def heartbeat(self) -> None:
        while True:
            self.enqueue(self.base_payload("heartbeat"))
            await asyncio.sleep(10)

    async def report_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            payload = await self.queue.get()
            try:
                if not await loop.run_in_executor(self.reporter_pool, post_report, self.settings, payload):
                    LOGGER.warning("Rapport non transmis ; le prochain heartbeat réconciliera le compteur.")
            except Exception:
                LOGGER.warning("Rapport non transmis ; vérifier réseau, dépendance cryptographique et configuration.")
            finally:
                self.queue.task_done()

    async def run(self) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop.set)
        reporter, heartbeat = asyncio.create_task(self.report_loop()), asyncio.create_task(self.heartbeat())
        try:
            server = await asyncio.start_server(self.connected, host="0.0.0.0", port=self.settings.port, limit=MAX_LINE_BYTES, backlog=32)
            LOGGER.info("Piège TELNET actif sur 0.0.0.0:%s ; aucune négociation SSH.", self.settings.port)
            async with server:
                await stop.wait()
        finally:
            for task in (*self.clients, reporter, heartbeat):
                task.cancel()
            await asyncio.gather(*self.clients, reporter, heartbeat, return_exceptions=True)
            self.reporter_pool.shutdown(wait=False, cancel_futures=True)
            self.counters.save()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = Settings.from_env()
        if sys.platform != "linux":
            raise ValueError("La sentinelle nécessite Linux.")
        local_day()
        encrypt({"kind": "heartbeat"}, settings.token)
        counters = Counters(settings.state_file)
        asyncio.run(Sentinel(settings, counters).run())
    except (ValueError, OSError, ImportError, ZoneInfoNotFoundError):
        LOGGER.error("Sentinelle non démarrée : vérifier configuration, cryptography, fuseau horaire et port disponible.")
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
