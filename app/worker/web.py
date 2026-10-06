"""
Worker exposed as a Web Service.

Render's free tier does not include background workers, so this entry point
wraps the four worker threads inside a minimal HTTP server. The server
only responds to /health and / to satisfy Render's health checks; the real
work happens in the background threads.

Usage:
    uvicorn app.worker.web:app --host 0.0.0.0 --port $PORT

Or as a Render start command:
    uvicorn app.worker.web:app --host 0.0.0.0 --port $PORT

WARNING: On Render's free tier, Web Services sleep after 15 minutes of
inactivity. To keep the worker alive, ping /health every 5 minutes from
an external service (cron-job.org, UptimeRobot, etc.).
"""

import atexit
import threading
import time
from datetime import datetime, timezone

from fasthtml.common import JSONResponse, fast_app

from app.config import setup_logging
from app.core.db import engine
from app.worker import deploy, polling
from app.worker import rates as worker_rates
from app.worker import webhooks as worker_webhooks

# Ensure the DB connection pool is closed cleanly on shutdown.
atexit.register(engine.dispose)

log = setup_logging("gateway_worker_web")

INTERVAL_POLLING = 60
INTERVAL_DEPLOY = 15
INTERVAL_WEBHOOKS = 30
INTERVAL_RATES = 300

# Shared state so /health can report the status of each thread.
_thread_state: dict[str, dict] = {
    "polling": {"running": False, "last_run": None, "last_count": 0},
    "deploy": {"running": False, "last_run": None, "last_count": 0},
    "webhooks": {"running": False, "last_run": None, "last_count": 0},
    "rates": {"running": False, "last_run": None, "last_count": 0},
}
_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

def _loop(name: str, fn, interval: int, stop_event: threading.Event) -> None:
    log.info(f"[{name}] started (every {interval}s)")
    with _state_lock:
        _thread_state[name]["running"] = True
    while not stop_event.is_set():
        try:
            n = fn()
            with _state_lock:
                _thread_state[name]["last_run"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                _thread_state[name]["last_count"] = n or 0
            if n:
                log.info(f"[{name}] processed: {n}")
        except Exception as e:
            log.exception(f"[{name}] cycle error: {e}")
        stop_event.wait(interval)
    with _state_lock:
        _thread_state[name]["running"] = False
    log.info(f"[{name}] stopped")


def _start_worker() -> threading.Event:
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
    return stop_event


# ---------------------------------------------------------------------------
# HTTP server (just for Render's health checks)
# ---------------------------------------------------------------------------

app, rt = fast_app(pico=False)


@rt("/health", methods=["GET"])
def health():
    """
    Health check. Reports whether the worker threads are running.
    Render pings this to decide if the service is alive.
    """
    with _state_lock:
        snapshot = {k: dict(v) for k, v in _thread_state.items()}

    all_running = all(s["running"] for s in snapshot.values())

    # Also check the DB is reachable
    db_ok = False
    try:
        from sqlalchemy import text
        from app.core.db import engine as _engine
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        log.error(f"Health check: DB unreachable: {e}")

    payload = {
        "status": "ok" if (all_running and db_ok) else "degraded",
        "db": db_ok,
        "worker_threads": snapshot,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    status_code = 200 if (all_running and db_ok) else 503
    return JSONResponse(payload, status_code=status_code)


@rt("/", methods=["GET"])
def root():
    return JSONResponse({
        "service": "gateway-worker",
        "status": "running",
        "ts": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

_start_worker()
log.info("Worker threads started; HTTP wrapper is ready.")
