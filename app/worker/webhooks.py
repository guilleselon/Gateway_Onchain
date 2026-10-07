"""
Webhook delivery task for the gateway worker.

Every cycle, it looks at confirmed payments with no webhook sent yet
and retries according to the backoff defined in core/webhooks.py.

The URL and secret are read from the Payment itself. There is no
merchant table.

Before sending, it checks that the destination URL is not blocked
due to too many recent failed attempts.

Payments whose retries are already exhausted are filtered out at the
query level (see payments.get_payments_to_webhook), so they never
reach this loop and never spam the log.
"""

from datetime import datetime, timezone

from app.config import setup_logging
from app.core import payments, webhooks
from app.core.db import get_session
from app.core.models import Token

log = setup_logging("gateway_worker")


def run_once() -> int:
    """
    Run one webhook cycle.
    Returns how many webhooks were sent successfully.
    """
    sent = 0
    now = datetime.now(timezone.utc)

    with get_session() as s:
        pending = payments.get_payments_to_webhook(s)
        blocked_urls_cache: dict[str, bool] = {}

        for p in pending:
            token = s.get(Token, p.token_id)
            if not token:
                log.warning(f"payment {p.id}: token {p.token_id} does not exist")
                continue

            # Is the destination URL blocked due to recent abuse?
            if p.webhook_url not in blocked_urls_cache:
                blocked_urls_cache[p.webhook_url] = (
                    webhooks.is_url_blocked(s, p.webhook_url)
                )
            if blocked_urls_cache[p.webhook_url]:
                log.warning(
                    f"payment {p.id}: URL {p.webhook_url} is blocked due to "
                    f"too many failures. Skipping."
                )
                continue

            # Should we retry now?
            next_wait = webhooks.next_attempt_seconds(p.id, s)
            if next_wait is None:
                # Retries exhausted. Silent: the query already filters
                # these out, so we only reach here if something changed
                # mid-cycle.
                continue

            last = webhooks.last_attempt(s, p.id)
            if last is not None:
                ts = last.attempted_at
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                elapsed = (now - ts).total_seconds()
                if elapsed < next_wait:
                    continue

            # Send
            if webhooks.send_webhook(s, p, token):
                sent += 1

    return sent
