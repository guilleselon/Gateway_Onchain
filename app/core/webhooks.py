"""
Webhook signing and delivery to the merchant.

Responsibilities:
- Sign payloads with HMAC-SHA256.
- POST to the payment's webhook_url.
- Record each attempt in webhook_attempts.
- Compute the next retry with exponential backoff.

Signature format:
    signature = HMAC-SHA256(webhook_secret, timestamp + "." + raw_body)
    Sent in headers:
        X-Webhook-Signature: <hex>
        X-Webhook-Timestamp: <unix_ts>
        X-Webhook-Payment-Id: <id>

The URL and the secret are read from the Payment itself, not from a
merchant table. Note: signature verification does NOT live here. The
gateway only signs; the receiver (the merchant) verifies.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.config import setup_logging
from app.core.models import Chain, Payment, Token, WebhookAttempt

log = setup_logging("gateway_webhooks")

# Backoff in seconds. Index 0 = first attempt (immediate).
BACKOFF_SECONDS = [0, 30, 60, 300, 900, 3600, 21600]
MAX_ATTEMPTS = len(BACKOFF_SECONDS)

# Webhook rate limiting per destination URL
WEBHOOK_MAX_FAILURES = 10
WEBHOOK_WINDOW_SECONDS = 300


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    """
    Compute HMAC-SHA256 over 'timestamp.body'.

    Same convention used by Stripe, GitHub and Shopify. The receiver
    recomputes the signature and compares in constant time.
    """
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def build_payload(payment: Payment, token: Token,
                  chain: Chain | None = None) -> dict:
    """
    JSON payload sent to the merchant.

    `amount_token_received` is stored as a string in the DB, but the
    payload exposes it as an int for JSON compatibility. If the value
    exceeds 2^63, most JSON parsers handle it as a bignum, so there is
    no loss of precision in the wire format.
    """
    explorer_url = None
    if chain and chain.explorer_url and payment.tx_hash:
        explorer_url = f"{chain.explorer_url.rstrip('/')}/tx/{payment.tx_hash}"

    return {
        "payment_id": payment.id,
        "external_ref": payment.external_ref,
        "merchant_name": payment.merchant_name,
        "status": payment.status,
        "token_symbol": token.symbol,
        "amount_token_received": (
            int(payment.amount_token_received)
            if payment.amount_token_received else None
        ),
        "amount_usd_cents_received": payment.amount_usd_cents_received,
        "rate_used": str(payment.rate_used) if payment.rate_used else None,
        "rate_source": payment.rate_source,
        "wallet_address": payment.wallet_address,
        "proxy_address": payment.proxy_address,
        "tx_hash": payment.tx_hash,
        "block_number": payment.block_number,
        "explorer_url": explorer_url,
        "confirmed_at": (
            payment.confirmed_at.isoformat() if payment.confirmed_at else None
        ),
        "fee_bps_used": payment.fee_bps_used,
        "fee_amount_token": (
            int(payment.fee_amount_token)
            if payment.fee_amount_token else None
        ),
    }


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def send_webhook(session: Session, payment: Payment, token: Token) -> bool:
    """
    POST to the payment's webhook_url. Records the attempt.

    Returns:
        True on 2xx response, False otherwise.
    """
    chain = session.get(Chain, token.chain_id) if token else None

    body_dict = build_payload(payment, token, chain)
    body = json.dumps(body_dict, separators=(",", ":")).encode("utf-8")
    timestamp = int(time.time())
    signature = sign_payload(payment.webhook_secret, timestamp, body)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Timestamp": str(timestamp),
        "X-Webhook-Payment-Id": str(payment.id),
    }

    previous_attempts = (
        session.query(WebhookAttempt)
        .filter_by(payment_id=payment.id)
        .count()
    )
    attempt_number = previous_attempts + 1

    response_code = None
    response_body = None
    success = False
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(payment.webhook_url, content=body, headers=headers)
        response_code = r.status_code
        response_body = r.text[:1000]
        success = 200 <= r.status_code < 300
    except Exception as e:
        response_body = f"exception: {e}"

    attempt = WebhookAttempt(
        payment_id=payment.id,
        attempt_number=attempt_number,
        response_code=response_code,
        response_body=response_body,
        success=success,
    )
    session.add(attempt)
    session.flush()

    if success:
        payment.webhook_sent_at = datetime.now(timezone.utc)
        log.info(f"payment {payment.id}: webhook OK (attempt {attempt_number})")
    else:
        log.warning(
            f"payment {payment.id}: webhook failed (attempt {attempt_number}, "
            f"code={response_code}, "
            f"body={response_body[:100] if response_body else ''})"
        )

    return success


def next_attempt_seconds(payment_id: int, session: Session) -> int | None:
    """
    Seconds until the next retry, according to the backoff list.

    Returns:
        Seconds, or None if retries are exhausted.
    """
    n = (
        session.query(WebhookAttempt)
        .filter_by(payment_id=payment_id)
        .filter(WebhookAttempt.success.is_(False))
        .count()
    )
    if n >= MAX_ATTEMPTS:
        return None
    return BACKOFF_SECONDS[n]


def last_attempt(session: Session, payment_id: int) -> WebhookAttempt | None:
    return (
        session.query(WebhookAttempt)
        .filter_by(payment_id=payment_id)
        .order_by(WebhookAttempt.attempted_at.desc())
        .first()
    )


def is_url_blocked(session: Session, webhook_url: str) -> bool:
    """
    Return True if the URL has received too many recent failed attempts.
    Used to temporarily block abusive destination URLs.
    """
    from datetime import timedelta
    limit = datetime.now(timezone.utc) - timedelta(
        seconds=WEBHOOK_WINDOW_SECONDS
    )

    from app.core.models import Payment as _P
    failed = (
        session.query(WebhookAttempt)
        .join(_P, WebhookAttempt.payment_id == _P.id)
        .filter(_P.webhook_url == webhook_url)
        .filter(WebhookAttempt.success.is_(False))
        .filter(WebhookAttempt.attempted_at >= limit)
        .count()
    )
    return failed >= WEBHOOK_MAX_FAILURES

