# -*- coding: utf-8 -*-
"""Moteur de Hedging Spéculatif Dual-Engine (Dual-Engine Speculative Racing).

Élimine définitivement les attentes de 7 à 15 secondes dues aux pics de charge cloud :
- Lance le moteur primaire (ex: Gemini).
- Si le primaire ne répond pas sous le délai de hedging (défaut: 550ms), lance
  simultanément le moteur ultra-rapide Groq LPU en parallèle.
- Le premier moteur à délivrer une réponse valide (< 800ms) gagne la parole.
- Si l'un des deux échoue (503 ou 429), l'autre prend le relais de façon totalement invisible.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

LOGGER = logging.getLogger("jarvis.hedging")

DEFAULT_HEDGE_DELAY_SEC = 0.55  # 550 ms


_HEDGE_EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None
_HEDGE_LOCK = threading.Lock()


def get_hedge_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Retourne l'executor persistant pour éviter l'overhead de création de threads."""
    global _HEDGE_EXECUTOR
    if _HEDGE_EXECUTOR is None:
        with _HEDGE_LOCK:
            if _HEDGE_EXECUTOR is None:
                _HEDGE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=4, thread_name_prefix="JarvisHedge"
                )
    return _HEDGE_EXECUTOR


def execute_hedged_call(
    primary_fn: Callable[[], Any],
    secondary_fn: Callable[[], Any],
    primary_name: str = "Gemini",
    secondary_name: str = "Groq",
    hedge_delay: float = DEFAULT_HEDGE_DELAY_SEC,
    validator: Optional[Callable[[Any], bool]] = None,
) -> Tuple[Any, str, float]:
    """Exécute un appel spéculatif concurrent avec hedging temporel.

    Returns:
        (result, winner_name, duration_ms)
    """
    t0 = time.perf_counter()
    executor = get_hedge_executor()

    def _is_valid(res: Any) -> bool:
        if res is None:
            return False
        if validator:
            return validator(res)
        if isinstance(res, str):
            clean = res.strip()
            return bool(clean and not clean.startswith("Mode dégradé") and not clean.startswith("[DEGRADE]"))
        if isinstance(res, dict):
            content = res.get("content") or res.get("reply") or ""
            return bool(content and not str(content).startswith("Mode dégradé"))
        return True

    primary_future = executor.submit(primary_fn)

    # Attente bornée du premier moteur
    try:
        res1 = primary_future.result(timeout=hedge_delay)
        if _is_valid(res1):
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            LOGGER.info("⚡ [HEDGING WINNER: %s] (%.0fms): réponse immédiate", primary_name, elapsed_ms)
            return res1, primary_name, elapsed_ms
    except concurrent.futures.TimeoutError:
        # Le primaire tarde : déclenchement immédiat du secours en parallèle
        LOGGER.info(
            "⚡ [HEDGING TRIGGERED]: %s n'a pas répondu sous %.0fms. Lancement immédiat de %s en parallèle...",
            primary_name,
            hedge_delay * 1000.0,
            secondary_name,
        )
    except Exception as exc1:
        LOGGER.warning("⚡ [HEDGING %s FAILED]: %s. Bascule directe sur %s...", primary_name, exc1, secondary_name)

    # Second moteur lancé (soit suite à timeout, soit suite à échec précoce)
    secondary_future = executor.submit(secondary_fn)

    # Course entre les deux (ou attente du secondaire)
    done, not_done = concurrent.futures.wait(
        [primary_future, secondary_future],
        return_when=concurrent.futures.FIRST_COMPLETED,
    )

    for fut in done:
        if fut.exception() is None:
            val = fut.result()
            if _is_valid(val):
                winner = primary_name if fut is primary_future else secondary_name
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                LOGGER.info("⚡ [HEDGING WINNER: %s] (%.0fms)", winner, elapsed_ms)
                return val, winner, elapsed_ms

    # Si le premier fini n'était pas valide ou a échoué, on attend le second
    for fut in not_done:
        try:
            val = fut.result(timeout=4.0)
            if _is_valid(val):
                winner = primary_name if fut is primary_future else secondary_name
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                LOGGER.info("⚡ [HEDGING LATE WINNER: %s] (%.0fms)", winner, elapsed_ms)
                return val, winner, elapsed_ms
        except Exception:
            pass

    # Si aucun n'a réussi
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    LOGGER.warning("⚡ [HEDGING EXHAUSTED]: Les deux moteurs ont échoué après %.0fms", elapsed_ms)
    return None, "none", elapsed_ms
