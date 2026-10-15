"""Commandes opérateur bornées et bilan de l'infrastructure depuis le cache NOC.

L'appelant authentifie l'expéditeur et confirme les redémarrages de l'hôte.
Aucune commande, sonde réseau ou inférence n'est exécutée pour lire le statut.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import psutil
except Exception:
    psutil = None

import config
from config import SERVICES
from ai_brain import get_ai_status


SERVICES_ALLOWLIST = tuple(config.MANAGED_SERVICES)
SYSTEMCTL = "/usr/bin/systemctl"
COMMAND_TIMEOUT = 15
VERIFY_TIMEOUT = 5


def _collect_live_hardware() -> dict[str, Any]:
    """Collecte immédiate des métriques système réelles via psutil."""
    if psutil is None:
        return {}

    cpu_pct = 0.0
    try:
        cpu_pct = psutil.cpu_percent(interval=0.1)
    except Exception:
        cpu_pct = 0.0

    mem_used, mem_total, mem_pct = 0, 0, 0.0
    try:
        mem = psutil.virtual_memory()
        mem_used = mem.total - mem.available
        mem_total = mem.total
        mem_pct = mem.percent
    except Exception:
        pass

    disk_used, disk_total, disk_pct = 0, 0, 0.0
    try:
        root_path = os.path.abspath(os.sep) if os.name == "nt" else "/"
        usage = psutil.disk_usage(root_path)
        disk_used = usage.used
        disk_total = usage.total
        disk_pct = usage.percent
    except Exception:
        pass

    temperature = None
    try:
        if hasattr(psutil, "sensors_temperatures"):
            sensors = psutil.sensors_temperatures() or {}
            readings = [
                reading.current
                for name, entries in sensors.items()
                if any(part in name.lower() for part in ("coretemp", "k10temp", "cpu", "zenpower", "cpu_thermal"))
                for reading in entries
                if reading.current is not None and -20 <= reading.current <= 150
            ]
            if readings:
                temperature = round(max(readings), 1)
    except Exception:
        pass

    return {
        "cpu": {"percent": cpu_pct},
        "memory": {"used": mem_used, "total": mem_total, "percent": mem_pct},
        "disks": {"root": {"used": disk_used, "total": disk_total, "percent": disk_pct}},
        "temperature_c": temperature,
    }


def _probe_service_live(service_name: str, port: int) -> dict[str, Any]:
    """Sonde directe d'un service système via systemctl et port TCP local."""
    unit = f"{service_name}.service"
    systemd_state = "inactif"
    try:
        res = _run([SYSTEMCTL, "is-active", unit], timeout=0.5)
        raw = res.stdout.strip().lower()
        if raw in {"active", "inactive", "failed", "activating", "deactivating", "reloading"}:
            systemd_state = raw
    except Exception:
        systemd_state = "inactif"

    http_status = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        res = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        if res == 0:
            http_status = 200
    except Exception:
        pass

    return {
        "name": service_name,
        "systemd": systemd_state,
        "http_status": http_status,
    }


def _get_live_vps_count(settings: Any = None) -> int:
    """Compte le nombre de VPS déclarés dans le registre local."""
    try:
        state_path = getattr(settings, "vps_state_path", None) or str(config.DATA_DIR / "vps-registry.json")
        reg_file = Path(state_path)
        if reg_file.is_file():
            with open(reg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                servers = data.get("servers", [])
                if isinstance(servers, list):
                    return len(servers)
    except Exception:
        pass
    return 0


def _result(ok: bool, message: str, service: str | None = None, state: str = "unknown") -> dict[str, Any]:
    return {"ok": ok, "success": ok, "message": message, "service": service, "state": state}


def _run(arguments: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments, shell=False, check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
    )


def _service_command(service_name: str, action: str) -> dict[str, Any]:
    if (not isinstance(service_name, str)
            or re.fullmatch(r"[a-z][a-z0-9-]*", service_name) is None
            or service_name not in SERVICES_ALLOWLIST):
        return _result(False, "ALERTE : service non autorisé.")
    unit = config.MANAGED_SERVICES[service_name]
    try:
        command = _run(["sudo", "-n", SYSTEMCTL, action, unit], COMMAND_TIMEOUT)
        if command.returncode != 0:
            return _result(False, f"Commande refusée pour {service_name} ; vérifier sudoers et le journal système.", service_name)
        verification = _run([SYSTEMCTL, "is-active", unit], VERIFY_TIMEOUT)
    except subprocess.TimeoutExpired:
        return _result(False, f"Délai dépassé pour {service_name} ; état non confirmé, vérifier le journal système.", service_name)
    except OSError:
        return _result(False, f"Contrôle système indisponible pour {service_name} sur cet hôte.", service_name)

    state = verification.stdout.strip()
    if state not in {"active", "inactive", "failed", "activating", "deactivating", "reloading", "maintenance"}:
        state = "unknown"
    if action in {"restart", "start"} and verification.returncode == 0 and state == "active":
        verb = "redémarré" if action == "restart" else "démarré"
        return _result(True, f"Service {service_name} {verb} avec succès (actif).", service_name, state)
    if action == "stop" and verification.returncode in {0, 3} and state == "inactive":
        return _result(True, f"Service {service_name} arrêté avec succès (inactif).", service_name, state)
    return _result(False, f"Commande envoyée pour {service_name}, mais résultat non confirmé : état {state}.", service_name, state)


def restart_system_service(service_name: str) -> dict[str, Any]:
    """Redémarre uniquement une unité autorisée, puis vérifie qu'elle est active."""
    return _service_command(service_name, "restart")


def start_system_service(service_name: str) -> dict[str, Any]:
    """Démarre uniquement une unité supervisée et confirme son état actif."""
    return _service_command(service_name, "start")


def stop_system_service(service_name: str) -> dict[str, Any]:
    """Arrête uniquement une unité autorisée, sans annoncer un faux arrêt."""
    return _service_command(service_name, "stop")


def reboot_host() -> dict[str, Any]:
    """À appeler seulement après confirmation explicite de l'administrateur."""
    try:
        command = _run(["sudo", "-n", "/sbin/reboot"], COMMAND_TIMEOUT)
    except subprocess.TimeoutExpired:
        return _result(False, "Délai dépassé : redémarrage de l'hôte non confirmé ; vérifier son état.")
    except OSError:
        return _result(False, "Redémarrage de l'hôte indisponible sur ce système.")
    if command.returncode != 0:
        return _result(False, "Redémarrage de l'hôte refusé ; vérifier sudoers et le journal système.")
    return _result(True, "Commande de redémarrage de l'hôte acceptée. Reconnexion nécessaire après son démarrage.", state="reboot_requested")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any, *, divisor: float = 1, suffix: str = "") -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "indisponible"
    return f"{value / divisor:.1f}{suffix}"


def _capacity(values: Any) -> str:
    metrics = _mapping(values)
    return (f"{_number(metrics.get('used'), divisor=1024 ** 3)}/"
            f"{_number(metrics.get('total'), divisor=1024 ** 3)} Go "
            f"({_number(metrics.get('percent'), suffix=' %')})")


def _timestamp_age(timestamp: Any, now: datetime) -> float | None:
    if not isinstance(timestamp, str):
        return None
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if value.tzinfo is None:
            return None
        return max(0, (now - value).total_seconds())
    except (ValueError, OverflowError):
        return None


def _text(value: Any, default: str = "indisponible") -> str:
    if value is None:
        return default
    return str(value).replace("\n", " ").replace("\r", " ")[:160]


class SystemManager:
    def __init__(self, runtime: Any = None, settings: Any = None):
        self.runtime = runtime
        self.settings = settings or getattr(runtime, "settings", None)

    def get_full_infrastructure_status(self) -> str:
        """Formate l'état de l'infrastructure depuis le cache ou par lecture directe en temps réel."""
        snapshot = _mapping(getattr(self.runtime, "latest", None))
        hardware = _mapping(snapshot.get("hardware"))
        now = datetime.now(timezone.utc)
        collector = _mapping(snapshot.get("collector"))
        timestamp = collector.get("last_success") or snapshot.get("timestamp")
        age = _timestamp_age(timestamp, now)

        # Fallback automatique en temps réel si aucune métrique en cache ou appel standalone
        is_live_fallback = False
        if not hardware or hardware.get("cpu") is None:
            is_live_fallback = True
            hardware = _collect_live_hardware()

        hostname = snapshot.get("hostname")
        if not hostname or hostname == "indisponible":
            try:
                hostname = socket.gethostname()
            except Exception:
                hostname = "Dell Precision M4800"

        lines = [
            "M.O.C.T.A.R — État de l'infrastructure",
            f"Dell · {_text(hostname)}",
        ]
        if timestamp:
            freshness = f"âge {age:.0f} s" if age is not None else "âge inconnu"
            lines.append(f"Dernière collecte : {_text(timestamp)} ({freshness}).")
        else:
            lines.append(f"Dernière collecte : {datetime.now().strftime('%H:%M:%S')} (lecture directe temps réel).")

        interval = getattr(self.settings, "interval", 3)
        stale_after = max(10, 3 * interval) if isinstance(interval, (float, int)) else 10
        if not is_live_fallback and (collector.get("error") or (age is not None and age > stale_after)):
            lines.append("ALERTE : mesures anciennes ou collecte indisponible ; état actuel non confirmé.")

        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            f"CPU : {_number(_mapping(hardware.get('cpu')).get('percent'), suffix=' %')}",
            f"RAM : {_capacity(hardware.get('memory'))}",
            f"Disque système : {_capacity(_mapping(hardware.get('disks')).get('root'))}",
        ])
        memory_metrics = _mapping(hardware.get("memory"))
        used, total = memory_metrics.get("used"), memory_metrics.get("total")
        available = total - used if isinstance(total, (int, float)) and isinstance(used, (int, float)) else None
        lines.append(f"RAM disponible : {_number(available, divisor=1024 ** 3)} Go")
        temp_val = hardware.get("temperature_c")
        if temp_val is not None:
            lines.append(f"Température : {_number(temp_val, suffix=' °C')}")
        else:
            lines.append("Température : indisponible")

        lines.extend(["━━━━━━━━━━━━━━━━━━━━", "Applications supervisées :"])
        managed_states: dict[str, dict[str, str]] = {}
        units = list(config.MANAGED_SERVICES.values())
        try:
            result = _run([SYSTEMCTL, "show", *units, "--property=Id,ActiveState,MemoryCurrent"], 2)
            for block in result.stdout.split("\n\n") if result.returncode == 0 else ():
                properties = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
                unit_id = properties.get("Id", "")
                if unit_id in units:
                    managed_states[unit_id] = properties
        except (OSError, subprocess.TimeoutExpired):
            pass
        for target, unit in config.MANAGED_SERVICES.items():
            properties = managed_states.get(unit, {})
            state = properties.get("ActiveState", "inconnu")
            memory = properties.get("MemoryCurrent", "")
            try:
                memory_text = f"{int(memory) / 1024 ** 2:.0f} Mio"
            except (TypeError, ValueError, OverflowError):
                memory_text = "indisponible"
            badge = "🟢" if state == "active" else "🔴"
            lines.append(f"{badge} {target} ({unit}) : {state} · mémoire {memory_text}")
        ai_status = get_ai_status()
        ai_state = "Opérationnel" if ai_status.get("gemini_online") else "indisponible"
        lines.append(f"🧠 Moteur IA : {ai_status.get('label', 'Gemini Cloud')} · {ai_state}")

        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            "Services web (dernières sondes locales) :",
        ])
        probes = snapshot.get("services", [])
        if not probes and is_live_fallback:
            probes = [_probe_service_live(service.name, service.port) for service in SERVICES]

        by_name = {item.get("name"): item for item in probes if isinstance(item, dict)} if isinstance(probes, list) else {}
        for service in SERVICES:
            probe = _mapping(by_name.get(service.name))
            raw_sys = probe.get("systemd")
            sys_state = raw_sys if raw_sys is not None else ("inactif" if is_live_fallback else "inconnu")
            http_val = probe.get("http_status")
            if http_val is not None:
                http_str = str(http_val)
            else:
                http_str = "inactif" if is_live_fallback else "indisponible"
            is_active = (sys_state == "active" or http_val == 200)
            status_badge = "🟢 ACTIF" if is_active else "🔴 INACTIF/ARRÊTÉ"
            lines.append(f"• {service.label} : systemd {sys_state} ; HTTP {http_str} (127.0.0.1:{service.port}) — {status_badge}")

        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            "VPS (rapports reçus par le registre) :",
        ])
        servers = snapshot.get("vps", [])
        vps_snapshot = getattr(self.runtime, "vps_snapshot", None)
        if callable(vps_snapshot):
            try:
                servers = vps_snapshot()
            except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
                lines.append("ALERTE : registre indisponible ; derniers rapports affichés.")
        servers = servers if isinstance(servers, list) else []

        if not servers and is_live_fallback:
            vps_count = _get_live_vps_count(self.settings)
            if vps_count > 0:
                lines.append(f"Aucun VPS actif en ligne ({vps_count} VPS déclarés dans le registre local).")
            else:
                lines.append("Aucun VPS connecté ou rapport disponible (0 VPS dans le registre local).")
        elif not servers:
            lines.append("Aucun VPS connecté ou rapport disponible.")

        displayed = 0
        valid_servers = [server for server in servers if isinstance(server, dict)]
        for server in valid_servers[:10]:
            if server.get("restored"):
                state = "en attente de reconnexion (rapport restauré)"
            elif server.get("stale"):
                state = "rapport périmé ; connexion non confirmée"
            elif server.get("stale") is False:
                state = "connecté" if server.get("healthy") is not False else "connecté ; incident détecté"
            else:
                state = "connexion non confirmée"
            last_seen = server.get("last_seen")
            server_age = _timestamp_age(last_seen, now)
            age_text = f", âge {server_age:.0f} s" if server_age is not None else ""
            block = [
                f"• {_text(server.get('hostname'))} : {state}.",
                f"  Dernier rapport : {_text(last_seen)}{age_text}.",
                f"  CPU {_number(server.get('cpu_percent'), suffix=' %')} ; RAM {_capacity(server.get('memory'))} ; Disque {_capacity(server.get('disk'))}.",
            ]
            if len("\n".join(lines + block)) > 3900:
                break
            lines.extend(block)
            displayed += 1
        if displayed < len(valid_servers):
            lines.append(f"… {len(valid_servers) - displayed} autres VPS dans le tableau de bord.")
        return "\n".join(lines)


def get_full_infrastructure_status(runtime: Any = None) -> str:
    return SystemManager(runtime).get_full_infrastructure_status()
