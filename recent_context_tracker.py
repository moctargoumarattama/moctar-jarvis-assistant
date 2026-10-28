# -*- coding: utf-8 -*-
"""Traqueur de Contexte Court-Terme & Résolution d'Anaphores (Anaphora Resolution).

Permet à JARVIS de comprendre naturellement les pronoms et références implicites
('relance-le', 'ferme-le', 'son statut', 'coupe-la') en maintenant une mémoire vivante
des dernières entités manipulées (services système, fenêtres, applications, playlists).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

LOGGER = logging.getLogger("jarvis.context_tracker")

# Durée de validité du contexte court-terme (en secondes, défaut 5 minutes)
CONTEXT_VALIDITY_SECONDS = 300.0


class RecentContextTracker:
    """Registre filaire des dernières entités système ou multimédia manipulées."""

    _instance: Optional[RecentContextTracker] = None

    def __init__(self) -> None:
        self._last_service: Optional[Dict[str, Any]] = None
        self._last_app: Optional[Dict[str, Any]] = None
        self._last_media: Optional[Dict[str, Any]] = None
        self._last_topic: Optional[Dict[str, Any]] = None
        self._last_entity_type: Optional[str] = None
        self._last_timestamp: float = 0.0

    @classmethod
    def get_instance(cls) -> RecentContextTracker:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def record_service(self, service_name: str, display_name: Optional[str] = None) -> None:
        """Enregistre le dernier service systemd manipulé ou interrogé."""
        clean_name = str(service_name).strip()
        if not clean_name.endswith(".service"):
            unit_name = clean_name + ".service"
        else:
            unit_name = clean_name
        now = time.time()
        self._last_service = {
            "name": unit_name,
            "short_name": clean_name.replace(".service", ""),
            "display_name": display_name or clean_name,
            "timestamp": now,
        }
        self._last_entity_type = "service"
        self._last_timestamp = now
        LOGGER.debug("⚡ [CONTEXT RECORD SERVICE]: %s", unit_name)

    def record_app(self, app_name: str, window_title: Optional[str] = None, pid: Optional[int] = None) -> None:
        """Enregistre la dernière application active ou fenêtre manipulée."""
        now = time.time()
        self._last_app = {
            "name": str(app_name).strip(),
            "window_title": window_title or app_name,
            "pid": pid,
            "timestamp": now,
        }
        self._last_entity_type = "app"
        self._last_timestamp = now
        LOGGER.debug("⚡ [CONTEXT RECORD APP]: %s", app_name)

    def record_media(self, action: str, title: Optional[str] = None) -> None:
        """Enregistre la dernière action de lecture média ou playlist."""
        now = time.time()
        self._last_media = {
            "action": action,
            "title": title or "flux audio",
            "timestamp": now,
        }
        self._last_entity_type = "media"
        self._last_timestamp = now
        LOGGER.debug("⚡ [CONTEXT RECORD MEDIA]: %s", action)

    def get_last_entity(self) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Retourne le type et les données de la dernière entité valide."""
        now = time.time()
        if (now - self._last_timestamp) > CONTEXT_VALIDITY_SECONDS:
            return None, None
        if self._last_entity_type == "service":
            return "service", self._last_service
        if self._last_entity_type == "app":
            return "app", self._last_app
        if self._last_entity_type == "media":
            return "media", self._last_media
        return None, None

    def resolve_anaphoric_intent(self, text: str) -> Optional[Dict[str, Any]]:
        """Détecte si la phrase contient une anaphore ('relance-le', 'ferme-la')
        et la résout vers l'entité contextuelle appropriée.
        """
        if not text:
            return None
        clean = text.strip().lower()
        now = time.time()

        if (now - self._last_timestamp) > CONTEXT_VALIDITY_SECONDS:
            return None

        # 1. Intention de Relance / Redémarrage ('relance-le', 'redémarre-le', 'recommence', 'relance ça')
        is_restart = bool(re.search(
            r"\b(?:relance|red[eé]marre|red[eé]marrer|relancer)\s+(?:[cç]a|le|la|les|ce\s+truc)\b",
            clean,
        )) or clean in ("relance-le", "relance le", "redémarre-le", "redemarre-le", "relance ca", "relance ça", "recommence")

        if is_restart:
            if self._last_entity_type == "service" and self._last_service:
                return {
                    "action": "restart_service",
                    "target": self._last_service["name"],
                    "short_name": self._last_service["short_name"],
                    "display_name": self._last_service["display_name"],
                    "category": "services",
                }
            if self._last_entity_type == "media":
                return {
                    "action": "resume_media",
                    "category": "media",
                }
            if self._last_entity_type == "app" and self._last_app:
                return {
                    "action": "restart_app",
                    "target": self._last_app["name"],
                    "category": "apps",
                }

        # 2. Intention d'Arrêt / Fermeture ('ferme-le', 'coupe-le', 'stoppe-le', 'arrête-le')
        is_stop = bool(re.search(
            r"\b(?:ferme|coupe|stoppe|arr[eê]te)\s+(?:[cç]a|le|la)\b",
            clean,
        )) or clean in ("ferme-le", "ferme le", "coupe-le", "coupe le", "arrête-le", "arrete le", "stoppe-le")

        if is_stop:
            if self._last_entity_type == "media":
                return {"action": "pause_media", "category": "media"}
            if self._last_entity_type == "app" and self._last_app:
                return {"action": "close_active_window", "target": self._last_app["name"], "category": "system_tools"}
            if self._last_entity_type == "service" and self._last_service:
                return {
                    "action": "stop_service",
                    "target": self._last_service["name"],
                    "short_name": self._last_service["short_name"],
                    "category": "services",
                }

        # 3. Intention de Statut ('son statut', 'quel est son état', 'comment il va')
        is_status = bool(re.search(
            r"\b(?:son\s+statut|son\s+[eé]tat|quel\s+est\s+son\s+[eé]tat|c['’]est\s+quoi\s+son\s+statut)\b",
            clean,
        ))
        if is_status and self._last_service:
            return {
                "action": "status_service",
                "target": self._last_service["name"],
                "short_name": self._last_service["short_name"],
                "display_name": self._last_service.get("display_name", self._last_service["short_name"]),
                "category": "services",
            }

        return None


def get_context_tracker() -> RecentContextTracker:
    return RecentContextTracker.get_instance()
