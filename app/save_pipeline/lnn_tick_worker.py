from __future__ import annotations

import logging
import threading
from typing import Optional

from app.storage.memories import get_memory_repository

LOGGER = logging.getLogger(__name__)


def _lnn_tick_loop(stop_event: threading.Event, interval_seconds: float, tau_disuse_decay: float = 0.01,
                   weight_decay: float = 0.001) -> None:
    repo = get_memory_repository()
    while not stop_event.is_set():
        try:
            dt_minutes = interval_seconds / 60.0
            repo.decay_all_activations(tau_disuse_decay, dt_minutes)
            repo.decay_all_tau(tau_disuse_decay, dt_minutes)
            if weight_decay > 0:
                repo.decay_all_weights(weight_decay)
        except Exception:
            LOGGER.exception("lnn_tick: decay pass failed")
        stop_event.wait(interval_seconds)


def start_lnn_tick_worker(stop_event: threading.Event, interval_seconds: float = 60.0,
                          tau_disuse_decay: float = 0.01, weight_decay: float = 0.001) -> threading.Thread:
    worker = threading.Thread(
        target=_lnn_tick_loop,
        args=(stop_event, interval_seconds, tau_disuse_decay, weight_decay),
        daemon=True,
        name="titan-lnn-tick",
    )
    worker.start()
    LOGGER.info("lnn_tick worker started (interval=%.1fs, tau_disuse_decay=%.4f, weight_decay=%.4f)",
                interval_seconds, tau_disuse_decay, weight_decay)
    return worker
