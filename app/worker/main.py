"""
Gateway worker entry point.

Starts four threads:
- polling:   checks proxy balances every 60s.
- deploy:    deploys detected proxies every 15s.
- webhooks:  retries pending webhooks every 30s.
- rates:     refreshes USD rates every 300s.

Usage:
    python -m app.worker.main

Stops all threads on Ctrl+C (SIGINT) or SIGTERM.
"""

import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import setup_logging
from app.worker import deploy, polling
from app.worker import rates as worker_rates
from app.worker import webhooks as worker_webhooks

log = setup_logging("gateway_worker")

INTERVAL_POLLING = 60
INTERVAL_DEPLOY = 15
INTERVAL_WEBHOOKS = 30
INTERVAL_RATES = 300


def _loop(name: str, fn, interval: int, stop_event: threading.Event) -> None:
    log.info(f"[{name}] started (every {interval}s)")
    while not stop_event.is_set():
        try:
            n = fn()
            if n:
                log.info(f"[{name}] processed: {n}")
        except Exception as e:
            log.exception(f"[{name}] cycle error: {e}")
        stop_event.wait(interval)
    log.info(f"[{name}] stopped")


def start_worker() -> tuple[threading.Event, list[threading.Thread]]:
    """Start the four threads. Returns (stop_event, threads)."""
    stop_event = threading.Event()
    threads = [
        threading.Thread(
            target=_loop,
            args=("polling", polling.run_once, INTERVAL_POLLING, stop_event),
            daemon=True,
        ),
        threading.Thread(
            target=_loop,
            args=("deploy", deploy.run_once, INTERVAL_DEPLOY, stop_event),
            daemon=True,
        ),
        threading.Thread(
            target=_loop,
            args=("webhooks", worker_webhooks.run_once,
                  INTERVAL_WEBHOOKS, stop_event),
            daemon=True,
        ),
        threading.Thread(
            target=_loop,
            args=("rates", worker_rates.run_once, INTERVAL_RATES, stop_event),
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()
    return stop_event, threads


def main() -> int:
    log.info("Starting gateway worker...")
    stop_event, threads = start_worker()

    def _handler(signum, frame):
        log.info("Signal received, stopping...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    try:
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()

    for t in threads:
        t.join(timeout=5)

    log.info("Worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

