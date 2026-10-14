"""Honeypot HTTP local : blocage applicatif, audit heuristique et état borné.

Ce module ne modifie aucun pare-feu. Les simulations utilisent exclusivement
des adresses réservées à la documentation et restent séparées des attaques.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import stat
import tempfile
import time
from collections import Counter, OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOGGER = logging.getLogger("noc-dashboard.cyberdefense")
TRAP_PATHS = frozenset({"/wp-login.php", "/wp-admin", "/wp-admin.php", "/.env", "/phpmyadmin", "/actuator/env", "/api/config"})
MAX_BANS = 512
MAX_ATTEMPTS = 256
MAX_STATE_BYTES = 1024 * 1024
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
SANDBOX = ipaddress.ip_network("198.51.100.0/24")
try:
    DAY_ZONE = ZoneInfo("Africa/Casablanca")
except ZoneInfoNotFoundError:
    DAY_ZONE = timezone.utc


def _timestamp(stamp: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if stamp is None else stamp, timezone.utc).isoformat(timespec="seconds")


def _day(stamp: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if stamp is None else stamp, DAY_ZONE).date().isoformat()


def _address(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    # Une adresse avec zone IPv6, port, préfixe CIDR ou espaces n'est pas un IP client.
    if not isinstance(raw, str) or not raw or raw.strip() != raw or "%" in raw or "/" in raw:
        raise ValueError("Adresse IP invalide")
    value = ipaddress.ip_address(raw)
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped:
        value = value.ipv4_mapped
    return value


def _valid_timestamp(raw: Any) -> bool:
    if not isinstance(raw, str) or len(raw) > 40:
        return False
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return value.tzinfo is not None and 0 <= value.timestamp() <= time.time() + 300
    except (ValueError, OverflowError, OSError):
        return False


class CyberDefenseManager:
    """État indépendant des sondes système, sérialisé par un verrou asynchrone."""

    def __init__(self, settings: Any, runtime: Any):
        self.settings = settings
        self.runtime = runtime
        self.state_path = Path(getattr(settings, "cyber_state_path", "/var/lib/noc-dashboard/blocked_ips.json"))
        self.bans: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.attempts: deque[dict[str, Any]] = deque(maxlen=MAX_ATTEMPTS)
        self._traffic: dict[int, dict[str, int]] = {}
        self._counter_day = _day()
        self._blocked_today = 0
        self._lock = asyncio.Lock()
        self._persistence = {"status": "starting", "error": None}
        self._protected = {_address("187.77.171.225")}
        configured = getattr(settings, "cyber_protected_ips", ())
        if isinstance(configured, str):
            configured = configured.split(",")
        for item in configured:
            try:
                self._protected.add(_address(str(item).strip()))
            except ValueError:
                LOGGER.warning("Adresse de protection Cyberdéfense invalide ignorée")

    def _is_protected(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return (address in self._protected or not address.is_global or address.is_multicast
                or address.is_reserved or address.is_unspecified
                or isinstance(address, ipaddress.IPv4Address) and address in TAILSCALE)

    def is_banned(self, raw_ip: str) -> bool:
        """Vérifie l'IP de la connexion ; les simulations ne filtrent personne."""
        try:
            address = _address(raw_ip)
        except ValueError:
            return False
        entry = self.bans.get(str(address))
        return bool(entry and not entry["simulated"] and not self._is_protected(address))

    async def start(self) -> None:
        async with self._lock:
            try:
                stored = await asyncio.to_thread(self._read_state)
                if stored is None:
                    # Le premier incident crée l'état : aucune écriture ni
                    # création de répertoire au démarrage des sondes/API.
                    self._persistence = {"status": "missing", "error": None}
                    return
                entries, attempts, counters = stored
                self.bans.update(entries)
                self.attempts.extend(attempts)
                if counters.get("day") == _day():
                    self._blocked_today = counters["blocked_today"]
                self._persistence = {"status": "ready", "error": None}
                self._rebuild_traffic()
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                self._persistence_error(exc)

    async def close(self) -> None:
        # Toutes les écritures sont attendues avant libération du verrou.
        async with self._lock:
            pass

    def _read_state(self) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]], dict[str, Any]] | None:
        path = self.state_path
        self._check_parent()
        if path.is_symlink():
            raise ValueError("Le fichier d'état ne doit pas être un lien symbolique")
        try:
            flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as handle:
                metadata = os.fstat(handle.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("Le fichier d'état doit être un fichier ordinaire")
                if metadata.st_size > MAX_STATE_BYTES:
                    raise ValueError("Fichier d'état trop volumineux")
                payload = handle.read(MAX_STATE_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(payload) > MAX_STATE_BYTES:
            raise ValueError("Fichier d'état trop volumineux")
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("banned_ips"), list):
            raise ValueError("Format du fichier d'état invalide")
        if len(data["banned_ips"]) > MAX_BANS:
            raise ValueError("Le fichier dépasse la capacité de bannissement")
        entries: list[tuple[str, dict[str, Any]]] = []
        known: set[str] = set()
        for row in data["banned_ips"]:
            entry = self._validate_ban(row)
            if entry and entry["ip"] not in known:
                entries.append((entry["ip"], entry))
                known.add(entry["ip"])
        raw_attempts = data.get("recent_attempts", [])
        if not isinstance(raw_attempts, list) or len(raw_attempts) > MAX_ATTEMPTS:
            raise ValueError("Journal du fichier d'état invalide")
        attempts = [valid for row in raw_attempts if (valid := self._validate_attempt(row)) is not None]
        counters = data.get("counters", {})
        if not isinstance(counters, dict) or type(counters.get("blocked_today", 0)) is not int or not 0 <= counters.get("blocked_today", 0) <= 100000000:
            raise ValueError("Compteur du fichier d'état invalide")
        counters = {"day": counters.get("day"), "blocked_today": counters.get("blocked_today", 0)}
        return entries, attempts, counters

    def _validate_ban(self, row: Any) -> dict[str, Any] | None:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or row["path"] not in TRAP_PATHS or type(row.get("simulated")) is not bool or not _valid_timestamp(row.get("timestamp")):
            return None
        try:
            address = _address(row.get("ip"))
        except ValueError:
            return None
        simulated = row["simulated"]
        if simulated:
            if not isinstance(address, ipaddress.IPv4Address) or address not in SANDBOX or address in {SANDBOX.network_address, SANDBOX.broadcast_address}:
                return None
        elif self._is_protected(address):
            return None
        hits = row.get("hits", 1)
        if type(hits) is not int or not 1 <= hits <= 100000000:
            return None
        # Le motif provient du chemin autorisé, pas de texte arbitraire stocké.
        return {"ip": str(address), "timestamp": row["timestamp"], "reason": f"Tentative {row['path']}", "path": row["path"], "simulated": simulated, "hits": hits}

    def _validate_attempt(self, row: Any) -> dict[str, Any] | None:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or row["path"] not in TRAP_PATHS or not _valid_timestamp(row.get("timestamp")) or type(row.get("simulated")) is not bool or type(row.get("blocked")) is not bool:
            return None
        try:
            address = _address(row.get("ip"))
        except ValueError:
            return None
        simulated = row["simulated"]
        if simulated and (not isinstance(address, ipaddress.IPv4Address) or address not in SANDBOX):
            return None
        protected = not simulated and self._is_protected(address)
        return {"timestamp": row["timestamp"], "ip": str(address), "path": row["path"], "simulated": simulated, "blocked": row["blocked"] and not protected, "protected": protected}

    def _check_parent(self) -> None:
        try:
            if self.state_path.parent.resolve() != self.state_path.parent.absolute():
                raise ValueError("Le dossier d'état ne doit pas traverser un lien symbolique")
        except RuntimeError:
            raise ValueError("Le dossier d'état contient une boucle de liens") from None

    def _write_state(self, payload: dict[str, Any]) -> None:
        """Remplacement atomique après fsync ; le fichier précédent reste intact."""
        path = self.state_path
        self._check_parent()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._check_parent()
        if path.is_symlink():
            raise ValueError("Le fichier d'état ne doit pas être un lien symbolique")
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("Le fichier d'état doit être un fichier ordinaire")
        except FileNotFoundError:
            pass
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            # La synchronisation du dossier protège le renommage après coupure.
            if os.name == "posix":
                directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _persistence_error(self, exc: BaseException) -> None:
        LOGGER.warning("Persistance Cyberdéfense indisponible : %s", type(exc).__name__)
        self._persistence = {"status": "error", "error": "État non sauvegardé : vérifier le JSON, le chemin et les permissions ; les changements en mémoire peuvent être perdus au redémarrage."}

    async def _persist_locked(self) -> bool:
        payload = {"version": 1, "banned_ips": [dict(row) for row in self.bans.values()], "recent_attempts": [dict(row) for row in self.attempts], "counters": {"day": self._counter_day, "blocked_today": self._blocked_today}}
        self._persistence = {"status": "saving", "error": None}
        writer = asyncio.create_task(asyncio.to_thread(self._write_state, payload))
        cancelled = False
        # Même plusieurs annulations HTTP attendent l'écriture en cours avant
        # libération du verrou : aucun remplacement tardif d'un état plus récent.
        while True:
            try:
                await asyncio.shield(writer)
                break
            except asyncio.CancelledError:
                if writer.cancelled():
                    raise
                cancelled = True
            except (OSError, ValueError, TypeError) as exc:
                self._persistence_error(exc)
                if cancelled:
                    raise asyncio.CancelledError from None
                return False
        self._persistence = {"status": "ready", "error": None}
        if cancelled:
            raise asyncio.CancelledError
        return True

    def _roll_day(self) -> None:
        current = _day()
        if current != self._counter_day:
            self._counter_day = current
            self._blocked_today = 0

    def _prune_traffic(self) -> None:
        cutoff = int(time.time() // 60) - 59
        self._traffic = {minute: counts for minute, counts in self._traffic.items() if minute >= cutoff}

    def _record(self, address: str, path: str, simulated: bool, blocked: bool, protected: bool) -> None:
        now = time.time()
        self.attempts.append({"timestamp": _timestamp(now), "ip": address, "path": path, "simulated": simulated, "blocked": blocked, "protected": protected})
        minute = int(now // 60)
        counts = self._traffic.setdefault(minute, {"count": 0, "simulated_count": 0})
        counts["count"] += 1
        counts["simulated_count"] += int(simulated)
        self._prune_traffic()

    def _rebuild_traffic(self) -> None:
        for attempt in self.attempts:
            minute = int(datetime.fromisoformat(attempt["timestamp"].replace("Z", "+00:00")).timestamp() // 60)
            counts = self._traffic.setdefault(minute, {"count": 0, "simulated_count": 0})
            counts["count"] += 1
            counts["simulated_count"] += int(attempt["simulated"])
        self._prune_traffic()

    async def _notify(self, level: str, message: str) -> None:
        try:
            self.runtime.collector.add_event(level, message)
            await self.runtime.publish_module_update()
        except Exception:
            # Un client SSE déconnecté ne doit pas annuler un ban déjà enregistré.
            LOGGER.exception("Publication Cyberdéfense indisponible")

    async def trap(self, raw_ip: str, path: str) -> dict[str, Any]:
        if path not in TRAP_PATHS:
            raise ValueError("Route piège non autorisée")
        try:
            address = _address(raw_ip)
        except ValueError:
            # Certains transports de test ou Unix n'ont pas d'IP distante. Ne
            # jamais les transformer en bannissement ni provoquer une erreur500.
            return {"blocked": False, "protected": True, "ignored": True, "capacity": False, "persisted": False, "cyber": self.snapshot()}
        ip = str(address)
        protected = self._is_protected(address)
        async with self._lock:
            self._roll_day()
            capacity = not protected and ip not in self.bans and len(self.bans) >= MAX_BANS
            blocked = not protected and not capacity
            if blocked:
                if ip in self.bans:
                    self.bans[ip]["hits"] = min(100000000, self.bans[ip]["hits"] + 1)
                else:
                    self.bans[ip] = {"ip": ip, "timestamp": _timestamp(), "reason": f"Tentative {path}", "path": path, "simulated": False, "hits": 1}
                    self._blocked_today += 1
            self._record(ip, path, False, blocked, protected)
            persisted = await self._persist_locked()
        state = "IP protégée, aucun bannissement" if protected else "capacité atteinte, aucun bannissement" if capacity else "bannissement applicatif"
        await self._notify("warning" if blocked else "info", f"Cyberdéfense · {ip} · {path} · {state}")
        return {"blocked": blocked, "protected": protected, "capacity": capacity, "persisted": persisted, "cyber": self.snapshot()}

    async def simulate(self, path: str) -> dict[str, Any]:
        if path not in TRAP_PATHS:
            raise ValueError("Route piège non autorisée")
        async with self._lock:
            if len(self.bans) >= MAX_BANS:
                return {"ok": False, "error": "Capacité atteinte ; débannir une IP avant de simuler.", "cyber": self.snapshot()}
            ip = next((f"198.51.100.{index}" for index in range(1, 255) if f"198.51.100.{index}" not in self.bans), None)
            if ip is None:
                return {"ok": False, "error": "Toutes les adresses de simulation sont utilisées ; débannir un test.", "cyber": self.snapshot()}
            self.bans[ip] = {"ip": ip, "timestamp": _timestamp(), "reason": f"Tentative {path}", "path": path, "simulated": True, "hits": 1}
            self._record(ip, path, True, True, False)
            persisted = await self._persist_locked()
        await self._notify("info", f"Cyberdéfense · simulation {ip} · {path} · aucune IP cliente bloquée")
        return {"ok": True, "ip": ip, "simulated": True, "persisted": persisted, "cyber": self.snapshot()}

    async def unban(self, raw_ip: str) -> dict[str, Any]:
        ip = str(_address(raw_ip))
        async with self._lock:
            removed = self.bans.pop(ip, None)
            persisted = await self._persist_locked() if removed else self._persistence["status"] == "ready"
        if removed:
            await self._notify("info", f"Cyberdéfense · {ip} · bannissement applicatif supprimé")
        return {"ok": True, "removed": removed is not None, "persisted": persisted, "cyber": self.snapshot()}

    def _analyze(self, attempts: list[dict[str, Any]], exercise: bool = False) -> dict[str, Any]:
        paths = {entry["path"] for entry in attempts}
        prefix = "Exercice — " if exercise else ""
        recommendations = (["Scénarios de test uniquement : aucune IP cliente et aucun pare-feu ne sont modifiés."] if exercise else ["Le blocage actuel couvre le dashboard HTTP. Il ne remplace pas les règles du pare-feu ni celles de Sentinel sur le VPS."])
        rules: list[dict[str, str]] = []
        score = 0
        if paths & {"/.env", "/api/config", "/actuator/env"}:
            score += 45
            recommendations.append(prefix + "Recherche de secrets : garder les fichiers .env hors du répertoire public et tester un honeytoken factice, sans exposer de secret réel.")
            rules.append({"id": "secret-scan", "description": "Corréler les accès aux chemins de configuration sensibles.", "pattern": r"^/(?:\.env|api/config|actuator/env)$"})
        if paths & {"/wp-login.php", "/wp-admin", "/wp-admin.php"}:
            score += 30
            recommendations.append(prefix + "Scan WordPress : vérifier les autres hôtes HTTP et appliquer une limitation de débit ciblée sur les chemins de connexion.")
            rules.append({"id": "wordpress-scan", "description": "Surveiller les sondes WordPress répétées.", "pattern": r"^/(?:wp-login\.php|wp-admin(?:\.php)?)$"})
        if "/phpmyadmin" in paths:
            score += 30
            recommendations.append(prefix + "Scan phpMyAdmin : restreindre les consoles d'administration au VPN et vérifier les versions réellement installées.")
            rules.append({"id": "database-admin-scan", "description": "Surveiller les tentatives sur la console d'administration SQL.", "pattern": r"^/phpmyadmin$"})
        repeated = max(Counter(entry["ip"] for entry in attempts).values(), default=0)
        score = min(100, score + min(20, max(0, repeated - 1) * 5) + (10 if len(paths) >= 3 else 0))
        if attempts:
            recommendations.append(prefix + "Corréler les IP et horodatages des scans réels avec les incidents SSH de Sentinel ; une corrélation doit être vérifiée avant d'élargir un blocage.")
        else:
            recommendations.append("Aucun scénario de test récent." if exercise else "Aucun scan public récent observé. Les simulations et les adresses protégées sont exclues du score ; utiliser le simulateur pour vérifier le parcours.")
        summary = (f"Exercice : indice de suspicion {score}/100 pour {len(attempts)} tentative(s) simulée(s) ; aucune attaque réelle comptabilisée." if exercise else f"Indice heuristique de suspicion : {score}/100, calculé sur {len(attempts)} tentative(s) publique(s) dans la dernière heure. Ce score n'est pas un taux de détection ni une analyse par un modèle IA.")
        return {"score": score, "recommendations": recommendations, "rules": rules, "summary": summary}

    def _audit(self) -> dict[str, Any]:
        cutoff = time.time() - 3600
        recent = [entry for entry in self.attempts if datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp() >= cutoff]
        result = self._analyze([entry for entry in recent if not entry["simulated"] and not entry["protected"]])
        result["simulation"] = self._analyze([entry for entry in recent if entry["simulated"]], exercise=True)
        return result

    async def audit_refresh(self) -> dict[str, Any]:
        await self._notify("info", "Cyberdéfense · recommandations heuristiques recalculées · aucune règle pare-feu installée")
        return {"ok": True, "cyber": self.snapshot()}

    def snapshot(self) -> dict[str, Any]:
        self._roll_day()
        self._prune_traffic()
        current_minute = int(time.time() // 60)
        traffic = [{"timestamp": _timestamp(minute * 60), "count": self._traffic.get(minute, {}).get("count", 0), "simulated_count": self._traffic.get(minute, {}).get("simulated_count", 0)} for minute in range(current_minute - 59, current_minute + 1)]
        real_bans = sum(not entry["simulated"] for entry in self.bans.values())
        return {"banned_ips": [dict(entry) for entry in reversed(self.bans.values())], "recent_attempts": [dict(entry) for entry in reversed(self.attempts)], "active_bans": real_bans, "simulated_bans": len(self.bans) - real_bans, "blocked_today": self._blocked_today, "traffic": traffic, "audit": self._audit(), "persistence": dict(self._persistence), "mode": "application"}
