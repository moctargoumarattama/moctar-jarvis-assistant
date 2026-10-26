"""Moteur de Proactivité Bienveillante & Compagnon Quotidien pour JARVIS / M.O.C.T.A.R.

Permet à M.O.C.T.A.R d'agir avec l'attention d'un véritable partenaire complice :
1. Détection des alertes d'infrastructure VPS / Sentinel :
   "Moctar, j'ai vu une alerte sur le VPS [nom], tu veux que je regarde ?"
2. Bienveillance sur les sessions de travail prolongées (prévention de la fatigue) :
   "Moctar, ça fait un bon moment que tu es sur ce code, un petit verre d'eau ou une pause ?"
3. Régulation stricte anti-spam :
   Cooldown de 45 minutes minimum, mémoire des alertes déjà notifiées, mode Ne Pas Déranger.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

LOGGER = logging.getLogger("moctar.proactive_companion")

_DEFAULT_COOLDOWN_SECONDS = 2700.0  # 45 minutes
_DEFAULT_FATIGUE_HOURS = 2.5        # Alerte après 2h30 de travail continu


class ProactiveCompanion:
    """Gestionnaire d'intelligence proactive et de bienveillance quotidienne."""

    _instance: Optional[ProactiveCompanion] = None

    def __init__(self, cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS) -> None:
        self.cooldown_seconds = float(os.getenv("MOCTAR_PROACTIVE_COOLDOWN_SECONDS", str(cooldown_seconds)))
        self.fatigue_threshold_seconds = float(os.getenv("MOCTAR_FATIGUE_HOURS", str(_DEFAULT_FATIGUE_HOURS))) * 3600.0
        
        self._last_trigger_time: float = 0.0
        self._work_session_start: float = time.monotonic()
        self._last_activity_time: float = time.monotonic()
        self._seen_alert_hashes: Set[str] = set()
        self._dnd_until: float = 0.0  # Do Not Disturb timestamp
        self._pending_suggestion: Optional[Dict[str, Any]] = None

    @classmethod
    def get_instance(cls) -> ProactiveCompanion:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def record_activity(self) -> None:
        """Enregistre une activité utilisateur pour mesurer la durée de session continue."""
        now = time.monotonic()
        # Si inactif depuis plus de 45 minutes, nouvelle session
        if (now - self._last_activity_time) > 2700.0:
            self._work_session_start = now
        self._last_activity_time = now

    def set_do_not_disturb(self, duration_minutes: int = 120) -> None:
        """Active le mode 'Ne pas déranger' pendant la durée spécifiée."""
        self._dnd_until = time.monotonic() + (duration_minutes * 60.0)
        LOGGER.info("Mode Ne Pas Déranger activé jusqu'à dans %d minutes.", duration_minutes)

    def is_in_cooldown(self) -> bool:
        """Vérifie si le compagnon est en période de repos / cooldown."""
        now = time.monotonic()
        if now < self._dnd_until:
            return True
        return (now - self._last_trigger_time) < self.cooldown_seconds

    def check_infrastructure_alert(self, runtime: Any) -> Optional[Dict[str, Any]]:
        """Détecte si un serveur VPS ou service critique nécessite l'attention de Moctar."""
        if not runtime:
            return None

        # 1. Vérification des VPS (support format dictionnaire ou liste)
        vps_data_source = getattr(runtime, "vps", None)
        vps_items = []
        if isinstance(vps_data_source, dict):
            if "servers" in vps_data_source and isinstance(vps_data_source["servers"], list):
                vps_items = vps_data_source["servers"]
            else:
                for k, v in vps_data_source.items():
                    if isinstance(v, dict):
                        item = dict(v)
                        if "name" not in item and "hostname" not in item:
                            item["name"] = k
                        vps_items.append(item)
        elif isinstance(vps_data_source, list):
            vps_items = vps_data_source

        for vps in vps_items:
            if not isinstance(vps, dict):
                continue
            vps_name = vps.get("name") or vps.get("hostname") or vps.get("host") or vps.get("ip") or "serveur"
            status = str(vps.get("status") or "").lower()
            severity = str(vps.get("severity") or "").lower()
            cpu_load = vps.get("cpu_percent") if vps.get("cpu_percent") is not None else vps.get("cpu")
            ram_load = vps.get("ram_percent") if vps.get("ram_percent") is not None else vps.get("ram")
            alerts = vps.get("alerts") or []
            incidents = vps.get("incidents") or []

            is_alert = (
                status in ("error", "critical", "down", "unreachable", "alert")
                or severity in ("critical", "high", "alert")
                or (isinstance(cpu_load, (int, float)) and cpu_load >= 95.0)
                or (isinstance(ram_load, (int, float)) and ram_load >= 95.0)
                or bool(alerts)
                or bool(incidents)
            )
            if is_alert:
                alert_hash = f"vps_{vps_name}_{status}_{severity}"
                if alert_hash not in self._seen_alert_hashes:
                    self._seen_alert_hashes.add(alert_hash)
                    return {
                        "category": "infra_alert",
                        "message": f"Moctar, j'ai vu une alerte sur le VPS {vps_name}, tu veux que je regarde ?",
                        "target": vps_name,
                        "priority": "high",
                    }

        # 2. Vérification des anomalies Sentinel / Cyberdéfense
        cyber = getattr(runtime, "cyber", None)
        if cyber and hasattr(cyber, "snapshot"):
            try:
                snap = cyber.snapshot()
                recent_bans = snap.get("active_bans_count", 0)
                if recent_bans >= 5 and "mass_ban_alert" not in self._seen_alert_hashes:
                    self._seen_alert_hashes.add("mass_ban_alert")
                    return {
                        "category": "cyber_alert",
                        "message": "Moctar, la sentinelle a bloqué plusieurs attaques suspectes récentes sur le réseau, tout est sous cloche.",
                        "target": "sentinel",
                        "priority": "normal",
                    }
            except Exception:
                pass

        return None

    def register_initiative(
        self,
        message: str,
        category: str = "custom",
        priority: str = "normal",
        target: Optional[str] = None,
    ) -> None:
        """Enregistre une initiative proactive personnalisée pour la boucle vocale."""
        if not message or not message.strip():
            return
        self._pending_suggestion = {
            "category": category,
            "message": message.strip(),
            "target": target,
            "priority": priority,
        }
        LOGGER.info("⚡ [PROACTIVE INITIATIVE REGISTERED]: %s", message.strip())

    def check_system_resources(self) -> Optional[Dict[str, Any]]:
        """Détecte une saturation critique de la RAM sur le Dell Precision M4800."""
        try:
            import psutil
            ram = psutil.virtual_memory().percent
            if ram >= 90.0:
                alert_hash = f"ram_high_{int(ram // 5) * 5}"
                if alert_hash not in self._seen_alert_hashes:
                    self._seen_alert_hashes.add(alert_hash)
                    return {
                        "category": "system_resource_alert",
                        "message": f"Moctar, la mémoire vive est à {int(ram)}%. Tu veux que je libère les caches et optimise le système ?",
                        "target": "ram",
                        "priority": "normal",
                    }
        except Exception:
            pass
        return None

    def check_circadian_wellness(self) -> Optional[Dict[str, Any]]:
        """Conseils de bienveillance nocturne et de gestion de l'énergie circadienne."""
        now_dt = datetime.now()
        hour = now_dt.hour
        now = time.monotonic()
        # Nuit tardive : entre 23h et 5h du matin
        if hour >= 23 or hour < 5:
            # Ne pas harceler : au moins 30 minutes de session active
            session_duration = now - self._work_session_start
            if session_duration >= 1800.0:
                alert_key = f"circadian_late_night_{now_dt.strftime('%Y%m%d')}"
                if alert_key not in self._seen_alert_hashes:
                    self._seen_alert_hashes.add(alert_key)
                    return {
                        "category": "circadian_wellness",
                        "message": "Moctar, il se fait tard dans la nuit. Si tu fatigues un peu, pense à préserver ton sommeil mon ami. Tu veux qu'on sauvegarde tout et qu'on reprenne demain ?",
                        "priority": "low",
                    }
        return None

    def check_vision_fatigue_alert(self) -> Optional[Dict[str, Any]]:
        """Vérifie si le capteur NeuroVision détecte des signes physiologiques de fatigue ou d'assoupissement."""
        try:
            from vision_engine import VisionEngine
            v = VisionEngine.get_instance()
            if not v.is_running():
                return None

            metrics = v.get_metrics()
            score = metrics.get("fatigue_score", 0)
            yawns = metrics.get("yawn_count", 0)
            level = metrics.get("fatigue_level", "Normal")

            if score >= 70 or yawns >= 4 or "Sévère" in level:
                return {
                    "category": "neurovision_fatigue",
                    "message": "Moctar, tes clignements sont lourds et je perçois une fatigue soutenue. Fais une vraie pause ou prends un café, je veille sur le poste.",
                    "priority": "normal",
                }
            elif score >= 50 or yawns >= 2:
                return {
                    "category": "neurovision_fatigue",
                    "message": "Moctar, je remarque quelques bâillements et une baisse de tonus. Un petit verre d'eau ou un étirement rapide te ferait du bien mon ami.",
                    "priority": "low",
                }
        except Exception:
            pass
        return None

    def check_fatigue_alert(self) -> Optional[Dict[str, Any]]:
        """Vérifie si Moctar est sur une session de code continue prolongée (basée sur la vision ou le temps)."""
        # Priorité aux métriques biométriques réelles si le capteur est actif
        vision_alert = self.check_vision_fatigue_alert()
        if vision_alert:
            return vision_alert

        now = time.monotonic()
        session_duration = now - self._work_session_start
        if session_duration >= self.fatigue_threshold_seconds:
            # Réinitialise la session pour éviter la répétition immédiate
            self._work_session_start = now
            return {
                "category": "wellbeing_break",
                "message": "Moctar, ça fait un bon moment que tu es sur ce module, tout roule ? Un petit verre d'eau ou une pause ?",
                "priority": "low",
            }
        return None

    def evaluate_proactive_event(self, runtime: Any = None) -> Optional[Dict[str, Any]]:
        """Évalue l'ensemble des déclencheurs et retourne une suggestion proactive si légitime."""
        # 0. Vérification prioritaire du mode silence / DND et cooldown avant toute consommation
        if self.is_in_cooldown() or (time.monotonic() < self._dnd_until):
            return None

        # Priorité 0 : Initiative explicite enregistrée (ex: tâche d'arrière-plan terminée)
        if self._pending_suggestion:
            event = self.pop_pending_suggestion()
            self._last_trigger_time = time.monotonic()
            LOGGER.info("⚡ [PROACTIVE EVENT POPPED]: %s", event.get("message"))
            return event

        # Priorité 1 : Alerte infrastructure / VPS
        infra_event = self.check_infrastructure_alert(runtime)
        if infra_event:
            self._last_trigger_time = time.monotonic()
            LOGGER.info("⚡ [PROACTIVE EVENT]: %s", infra_event["message"])
            return infra_event

        # Priorité 2 : Alerte ressources locales Dell M4800
        res_event = self.check_system_resources()
        if res_event:
            self._last_trigger_time = time.monotonic()
            LOGGER.info("⚡ [PROACTIVE EVENT]: %s", res_event["message"])
            return res_event

        # Priorité 3 : Bienveillance circadienne nocturne
        circadian_event = self.check_circadian_wellness()
        if circadian_event:
            self._last_trigger_time = time.monotonic()
            LOGGER.info("⚡ [PROACTIVE EVENT]: %s", circadian_event["message"])
            return circadian_event

        # Priorité 4 : Bienveillance session de travail prolongée
        fatigue_event = self.check_fatigue_alert()
        if fatigue_event:
            self._last_trigger_time = time.monotonic()
            LOGGER.info("⚡ [PROACTIVE EVENT]: %s", fatigue_event["message"])
            return fatigue_event

        return None

    def pop_pending_suggestion(self) -> Optional[Dict[str, Any]]:
        """Récupère et consomme la suggestion proactive en attente."""
        sugg = self._pending_suggestion
        self._pending_suggestion = None
        return sugg

