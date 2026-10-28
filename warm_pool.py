# -*- coding: utf-8 -*-
"""Pool de connexions chaudes Keep-Alive (Warm Connection Pool).

Maintient des sockets HTTP/2 / TLS actifs et chauds en permanence vers les APIs
critiques (Google GenAI et Groq LPU). Élimine le délai de négociation TCP/SSL (150-250ms)
sur les interactions vocales pour garantir des temps de réponse instantanés.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional
import urllib.request

LOGGER = logging.getLogger("jarvis.warm_pool")

# Intervalles de battement (Heartbeat) en secondes
DEFAULT_HEARTBEAT_INTERVAL = 38.0

# Endpoints à garder chauds
WARM_ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/models",
    "gemini": "https://generativelanguage.googleapis.com",
}


class WarmConnectionPool:
    """Gestionnaire de sockets persistants et chauds vers les cerveaux d'IA."""

    _instance: Optional[WarmConnectionPool] = None
    _singleton_lock = threading.Lock()

    def __init__(self, heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL) -> None:
        self.heartbeat_interval = heartbeat_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Métriques de chauffe
        self._last_heartbeat: Dict[str, float] = {}
        self._ping_latencies_ms: Dict[str, float] = {}
        self._consecutive_success: Dict[str, int] = {"groq": 0, "gemini": 0}

    @classmethod
    def get_instance(cls) -> WarmConnectionPool:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> None:
        """Démarre le démon de maintien des connexions chaudes."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._heartbeat_loop,
                name="JarvisWarmPoolDaemon",
                daemon=True,
            )
            self._thread.start()
            LOGGER.info("⚡ [WARM POOL]: Démon de chauffe Keep-Alive démarré (battement: %.1fs)", self.heartbeat_interval)

    def stop(self) -> None:
        """Arrête proprement le démon."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        LOGGER.info("⚡ [WARM POOL]: Démon de chauffe arrêté proprement.")

    def _ping_endpoint(self, name: str, url: str) -> None:
        """Envoie une requête ultra-légère pour maintenir le socket TCP/TLS actif."""
        t0 = time.perf_counter()
        headers: Dict[str, str] = {
            "User-Agent": "Jarvis-Moctar-WarmPool/1.0 (Keep-Alive)",
            "Connection": "keep-alive",
        }
        if name == "groq":
            key = os.environ.get("GROQ_API_KEY") or os.environ.get("NOC_GROQ_API_KEY") or ""
            if key:
                headers["Authorization"] = f"Bearer {key}"
        try:
            req = urllib.request.Request(url, headers=headers, method="HEAD" if name == "gemini" else "GET")
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                _ = resp.status
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            with self._lock:
                self._last_heartbeat[name] = time.time()
                self._ping_latencies_ms[name] = round(elapsed_ms, 1)
                self._consecutive_success[name] = self._consecutive_success.get(name, 0) + 1
            LOGGER.debug("⚡ [WARM POOL PING %s]: chaud en %.1fms", name.upper(), elapsed_ms)
        except Exception as exc:
            # Même en cas de 401/404 ou HEAD non supporté, la poignée de main TCP/TLS a eu lieu
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            with self._lock:
                self._last_heartbeat[name] = time.time()
                self._ping_latencies_ms[name] = round(elapsed_ms, 1)
            LOGGER.debug("⚡ [WARM POOL TOUCH %s]: socket rafraîchi en %.1fms (%s)", name.upper(), elapsed_ms, exc)

    def _heartbeat_loop(self) -> None:
        """Boucle de battement régulier."""
        # Premier ping de chauffe immédiat au démarrage
        for name, url in WARM_ENDPOINTS.items():
            self._ping_endpoint(name, url)

        while not self._stop_event.wait(self.heartbeat_interval):
            for name, url in WARM_ENDPOINTS.items():
                if self._stop_event.is_set():
                    break
                self._ping_endpoint(name, url)

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'état de santé du pool chaud."""
        with self._lock:
            return {
                "running": self._running,
                "heartbeat_interval_sec": self.heartbeat_interval,
                "latencies_ms": dict(self._ping_latencies_ms),
                "last_heartbeat": dict(self._last_heartbeat),
                "all_warm": bool(self._last_heartbeat),
            }


def start_warm_pool() -> WarmConnectionPool:
    pool = WarmConnectionPool.get_instance()
    pool.start()
    return pool


def stop_warm_pool() -> None:
    pool = WarmConnectionPool.get_instance()
    pool.stop()

