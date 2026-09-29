"""
Exchange rate update task for the gateway worker.

Every cycle, it queries CoinGecko for all active tokens and stores
the rates in the database.
"""

from app.config import setup_logging
from app.core import rates

log = setup_logging("gateway_worker")


def run_once() -> int:
    """
    Run one rate update cycle.
    Returns how many rates were refreshed.
    """
    n = rates.update_all_rates()
    if n:
        log.info(f"rates updated: {n}")
    return n

