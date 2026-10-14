"""Commandes opérateur bornées et bilan de l'infrastructure depuis le cache NOC.

L'appelant authentifie l'expéditeur et confirme les redémarrages de l'hôte.
Aucune commande, sonde réseau ou inférence n'est exécutée pour lire le statut.
"""
from __future__ import annotations

import math
import re
import subprocess
from datetime import datetime, timezone
from typing import Any

from config import SERVICES


SERVICES_ALLOWLIST = ("merca-market", "klasora", "babamarket", "moc-mal", "jarvis")
SYSTEMCTL = "/usr/bin/systemctl"
COMMAND_TIMEOUT = 15
VERIFY_TIMEOUT = 5


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
    unit = service_name + ".service"
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
    if action == "restart" and verification.returncode == 0 and state == "active":
        return _result(True, f"Service {service_name} redémarré avec succès (actif).", service_name, state)
    if action == "stop" and verification.returncode in {0, 3} and state == "inactive":
        return _result(True, f"Service {service_name} arrêté avec succès (inactif).", service_name, state)
    return _result(False, f"Commande envoyée pour {service_name}, mais résultat non confirmé : état {state}.", service_name, state)


def restart_system_service(service_name: str) -> dict[str, Any]:
    """Redémarre uniquement une unité autorisée, puis vérifie qu'elle est active."""
    return _service_command(service_name, "restart")


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
        """Formate les dernières sondes partagées ; ne déclenche aucune collecte."""
        snapshot = _mapping(getattr(self.runtime, "latest", None))
        hardware = _mapping(snapshot.get("hardware"))
        now = datetime.now(timezone.utc)
        collector = _mapping(snapshot.get("collector"))
        timestamp = collector.get("last_success") or snapshot.get("timestamp")
        age = _timestamp_age(timestamp, now)
        lines = ["M.O.C.T.A.R — État de l'infrastructure", f"Dell · {_text(snapshot.get('hostname'))}"]
        if timestamp:
            freshness = f"âge {age:.0f} s" if age is not None else "âge inconnu"
            lines.append(f"Dernière collecte : {_text(timestamp)} ({freshness}).")
        else:
            lines.append("Dernière collecte : indisponible ; première collecte en attente.")
        interval = getattr(self.settings, "interval", 3)
        stale_after = max(10, 3 * interval) if isinstance(interval, (float, int)) else 10
        if collector.get("error") or (age is not None and age > stale_after):
            lines.append("ALERTE : mesures anciennes ou collecte indisponible ; état actuel non confirmé.")
        lines.extend([
            f"CPU : {_number(_mapping(hardware.get('cpu')).get('percent'), suffix=' %')}",
            f"RAM : {_capacity(hardware.get('memory'))}",
            f"Disque système : {_capacity(_mapping(hardware.get('disks')).get('root'))}",
            f"Température : {_number(hardware.get('temperature_c'), suffix=' °C')}",
            "", "Services web (dernières sondes locales) :",
        ])
        probes = snapshot.get("services", [])
        by_name = {item.get("name"): item for item in probes if isinstance(item, dict)} if isinstance(probes, list) else {}
        for service in SERVICES:
            probe = _mapping(by_name.get(service.name))
            lines.append(f"• {service.label} : systemd {_text(probe.get('systemd'), 'inconnu')} ; HTTP {_text(probe.get('http_status'))} (127.0.0.1:{service.port}).")

        lines.extend(["", "VPS (rapports reçus par le registre) :"])
        servers = snapshot.get("vps", [])
        vps_snapshot = getattr(self.runtime, "vps_snapshot", None)
        if callable(vps_snapshot):
            try:
                servers = vps_snapshot()
            except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
                lines.append("ALERTE : registre indisponible ; derniers rapports affichés.")
        servers = servers if isinstance(servers, list) else []
        if not servers:
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
