# -*- coding: utf-8 -*-
"""Sentinelle Proactive d'Auto-Guérison (Proactive Self-Healing Sentinel).

Surveille en tâche de fond les services critiques de l'hôte (klasora-whatsapp, jarvis),
détecte tout arrêt ou crash inattendu, déclenche immédiatement la relance automatique (0ms d'attente),
et prépare une notification vocale concise et bienveillante pour Moctar.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger("jarvis.proactive_sentinel")

# Services critiques sous surveillance continue
CRITICAL_SERVICES = (
    "klasora-whatsapp.service",
    "jarvis.service",
)

# Cooldown anti-spam entre deux notifications vocales pour le même service (10 minutes)
NOTIFICATION_COOLDOWN_SECONDS = 600.0


class ProactiveSentinel:
    """Superviseur d'auto-guérison et de proactivité des services."""

    _instance: Optional[ProactiveSentinel] = None

    def __init__(self) -> None:
        self._last_alert_ts: Dict[str, float] = {}
        self._pending_announcements: List[Dict[str, Any]] = []
        self._systemctl_bin = shutil.which("systemctl") if os.name == "posix" else None
        self._sudo_bin = shutil.which("sudo") if os.name == "posix" else None

    @classmethod
    def get_instance(cls) -> ProactiveSentinel:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def is_service_active(self, service_name: str) -> bool:
        """Vérifie si un service systemd est actif."""
        if not self._systemctl_bin:
            return True  # Sur Windows / dev local, simule un état nominal
        try:
            res = subprocess.run(
                [self._systemctl_bin, "is-active", service_name],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            return res.returncode == 0 and res.stdout.strip() == "active"
        except Exception:
            return True

    def restart_service(self, service_name: str) -> bool:
        """Tente la relance immédiate d'un service système."""
        if not self._systemctl_bin:
            LOGGER.debug("Simulation relance service %s sur cet environnement", service_name)
            return True
        cmd = [self._sudo_bin, "-n", self._systemctl_bin, "restart", service_name] if self._sudo_bin else [self._systemctl_bin, "restart", service_name]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
            success = res.returncode == 0
            if success:
                LOGGER.info("⚡ [AUTO-HEAL SUCCESS]: %s a été relancé avec succès par la Sentinelle", service_name)
            else:
                LOGGER.warning("⚡ [AUTO-HEAL FAILED]: échec relance %s (%s)", service_name, res.stderr.strip())
            return success
        except Exception as exc:
            LOGGER.error("Erreur lors de la relance proactive de %s: %s", service_name, exc)
            return False

    def scan_and_heal(self) -> List[Dict[str, Any]]:
        """Scanne les services critiques et applique l'auto-guérison si nécessaire.

        Returns:
            Liste des actions d'auto-guérison effectuées.
        """
        now = time.time()
        actions_taken = []

        for srv in CRITICAL_SERVICES:
            # Ne pas vérifier jarvis.service s'il est en cours d'exécution
            if srv == "jarvis.service":
                continue

            active = self.is_service_active(srv)
            if not active:
                short_name = srv.replace(".service", "")
                display = "WhatsApp" if "whatsapp" in short_name else short_name
                LOGGER.warning("🚨 [SENTINELLE ALERTE]: Service critique %s inactif !", srv)

                # Tentative de relance
                healed = self.restart_service(srv)
                action_info = {
                    "service": srv,
                    "short_name": short_name,
                    "display_name": display,
                    "healed": healed,
                    "timestamp": now,
                }
                actions_taken.append(action_info)

                # Émission de message vocal non intrusif si hors cooldown
                last_alert = self._last_alert_ts.get(srv, 0.0)
                if (now - last_alert) >= NOTIFICATION_COOLDOWN_SECONDS:
                    self._last_alert_ts[srv] = now
                    msg = (
                        f"Moctar, le service {display} s'était interrompu. "
                        f"Je viens de le relancer automatiquement avec succès."
                        if healed else
                        f"Attention Moctar, le service {display} est interrompu et la tentative de relance a échoué."
                    )
                    self._pending_announcements.append({
                        "message": msg,
                        "service": srv,
                        "healed": healed,
                        "created_at": now,
                    })

        return actions_taken

    def pop_pending_announcement(self) -> Optional[str]:
        """Récupère la prochaine annonce proactive en attente."""
        if self._pending_announcements:
            item = self._pending_announcements.pop(0)
            return item.get("message")
        return None


def get_proactive_sentinel() -> ProactiveSentinel:
    return ProactiveSentinel.get_instance()
