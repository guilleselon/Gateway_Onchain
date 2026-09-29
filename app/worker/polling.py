"""
Balance polling task for the gateway worker.

Every cycle, it looks at pending/user_claimed/expired/late_detected
payments and checks whether the proxy already has balance. If it does,
the payment is marked as detected (or late_detected).
"""

from app.config import setup_logging
from app.core import payments
from app.core.db import get_session
from app.core.models import Chain

log = setup_logging("gateway_worker")


def run_once() -> int:
    """
    Run one polling cycle.
    Returns how many payments changed state.
    """
    processed = 0
    with get_session() as s:
        chains = s.query(Chain).filter(Chain.active.is_(True)).all()
        for chain in chains:
            pending = payments.get_payments_to_poll(s, chain.id)
            for p in pending:
                previous = p.status
                payments.mark_detected(s, p)
                if p.status != previous:
                    processed += 1
                    log.info(f"payment {p.id}: {previous} -> {p.status}")
    return processed

