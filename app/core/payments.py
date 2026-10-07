"""
Payment business logic.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import get_factory_contract, get_web3, setup_logging
from app.core import blockchain, rates
from app.core.models import Chain, Payment, PaymentStatus, Token

log = setup_logging("gateway_payments")


PAYMENT_TTL_MINUTES = 30
PAYMENT_MONITOR_HOURS = 24


class PaymentError(Exception):
    """Business error while handling payments."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _generate_order_id() -> int:
    return secrets.randbits(63)


def _generate_salt() -> str:
    return "0x" + os.urandom(32).hex()


def _generate_public_token() -> str:
    """32+ char URL-safe token for the customer-facing payment URL."""
    return secrets.token_urlsafe(24)


def _generate_external_ref() -> str:
    """Random external_ref for merchants that don't send one."""
    return "auto-" + secrets.token_hex(12)


def _calculate_amount_token(amount_usd_cents: int, usd_rate: Decimal,
                            decimals: int) -> int:
    if usd_rate <= 0:
        raise PaymentError(f"invalid rate: {usd_rate}")
    amount_usd = Decimal(amount_usd_cents) / Decimal(100)
    amount_token_dec = amount_usd / usd_rate
    return int(amount_token_dec * Decimal(10 ** decimals))


def _ensure_rate(session: Session, token: Token):
    """Return the latest rate for a token, fetching on demand if needed."""
    rate = rates.get_latest_rate(session, token.id)
    if rate is not None:
        return rate

    log.info(f"No rate stored for {token.symbol}; fetching on demand...")
    try:
        usd_rate, source = rates.fetch_rate(token)
    except Exception as e:
        raise PaymentError(
            f"no rate stored for {token.symbol} and could not fetch one "
            f"from any provider: {e}"
        ) from e

    rate = rates.save_rate(session, token.id, usd_rate, source)
    log.info(f"Rate fetched on demand: {token.symbol} = {usd_rate} USD ({source})")
    return rate


# ---------------------------------------------------------------------------
# Public token helpers
# ---------------------------------------------------------------------------

def ensure_public_token(session: Session, payment: Payment) -> str:
    """
    Make sure the payment has a public_token. If it doesn't (older rows),
    generate one and save it.
    """
    if payment.public_token:
        return payment.public_token
    payment.public_token = _generate_public_token()
    session.flush()
    return payment.public_token


def get_payment_by_public_token(session: Session,
                                public_token: str) -> Payment | None:
    """Find a payment by its public token (used in the customer URL)."""
    return (
        session.query(Payment)
        .filter_by(public_token=public_token)
        .first()
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def create_payment(
    session: Session,
    external_ref: str | None,
    amount_usd_cents: int,
    token_id: int,
    wallet_address: str,
    webhook_url: str,
    webhook_secret: str,
    api_key_id: int | None = None,
    merchant_name: str | None = None,
) -> Payment:
    """
    Create a new payment.

    If `external_ref` is empty/None, a random one is generated
    ('auto-<24 hex>'). If the merchant provides one, it is kept and
    idempotency per (api_key_id, external_ref) applies.
    """
    # Autogenerate external_ref if not provided
    if not external_ref:
        external_ref = _generate_external_ref()

    # Idempotency: only meaningful when the merchant sends its own ref
    if api_key_id is not None:
        existing = (
            session.query(Payment)
            .filter_by(api_key_id=api_key_id, external_ref=external_ref)
            .first()
        )
        if existing is not None:
            log.info(
                f"Payment already exists for external_ref={external_ref} "
                f"(id={existing.id}); returning it."
            )
            return existing

    # Validation
    if amount_usd_cents <= 0:
        raise PaymentError("amount_usd_cents must be greater than 0")
    if not wallet_address:
        raise PaymentError("wallet_address is empty")
    if not webhook_url:
        raise PaymentError("webhook_url is empty")
    if not webhook_secret:
        raise PaymentError("webhook_secret is empty")

    token = session.get(Token, token_id)
    if not token or not token.active:
        raise PaymentError(f"token {token_id} does not exist or is inactive")

    chain = session.get(Chain, token.chain_id)
    if not chain or not chain.active:
        raise PaymentError(f"chain {token.chain_id} does not exist or is inactive")

    rate = _ensure_rate(session, token)

    order_id = _generate_order_id()
    salt = _generate_salt()
    public_token = _generate_public_token()

    w3 = get_web3(chain.rpc_url)
    factory = get_factory_contract(w3, chain.factory_address)
    try:
        proxy_address = blockchain.calculate_proxy(
            w3, factory, order_id, wallet_address, salt,
        )
    except Exception as e:
        raise PaymentError(f"could not compute proxy: {e}") from e

    amount_token_suggested = _calculate_amount_token(
        amount_usd_cents, rate.usd_rate, token.decimals,
    )

    now = _utcnow()
    payment = Payment(
        public_token=public_token,
        api_key_id=api_key_id,
        external_ref=external_ref,
        merchant_name=merchant_name,
        wallet_address=wallet_address,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        amount_usd_cents_expected=amount_usd_cents,
        amount_token_suggested=str(amount_token_suggested),
        token_id=token.id,
        proxy_address=proxy_address,
        salt=salt,
        order_id=order_id,
        status=PaymentStatus.PENDING,
        created_at=now,
        expires_at=now + timedelta(minutes=PAYMENT_TTL_MINUTES),
        fee_bps_used=None,
        fee_amount_token=None,
    )
    session.add(payment)
    session.flush()

    log.info(
        f"Payment created id={payment.id} public_token={public_token[:8]}... "
        f"external_ref={external_ref} "
        f"merchant={merchant_name or '(unnamed)'} token={token.symbol} "
        f"usd_cents={amount_usd_cents} proxy={proxy_address}"
    )
    return payment


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def mark_user_claimed(session: Session, payment: Payment) -> None:
    if payment.status not in (PaymentStatus.PENDING, PaymentStatus.USER_CLAIMED):
        return
    payment.status = PaymentStatus.USER_CLAIMED
    payment.user_claimed_at = _utcnow()


def mark_detected(session: Session, payment: Payment) -> None:
    if payment.status not in (
        PaymentStatus.PENDING,
        PaymentStatus.USER_CLAIMED,
        PaymentStatus.EXPIRED,
        PaymentStatus.LATE_DETECTED,
    ):
        return

    token = session.get(Token, payment.token_id)
    if not token:
        log.error(f"payment {payment.id}: token {payment.token_id} does not exist")
        return
    chain = session.get(Chain, token.chain_id)
    if not chain:
        log.error(f"payment {payment.id}: chain {token.chain_id} does not exist")
        return

    w3 = get_web3(chain.rpc_url)
    balance = blockchain.get_balance(w3, payment.proxy_address)
    if balance <= 0:
        return

    rate = rates.get_latest_rate(session, token.id)
    if not rate:
        log.error(f"payment {payment.id}: no rate for token {token.symbol}")
        return

    amount_token_dec = Decimal(balance) / Decimal(10 ** token.decimals)
    amount_usd = amount_token_dec * rate.usd_rate
    amount_usd_cents = int(amount_usd * 100)

    payment.amount_token_received = str(balance)
    payment.amount_usd_cents_received = amount_usd_cents
    payment.rate_used = rate.usd_rate
    payment.rate_source = rate.source
    payment.rate_fetched_at = rate.fetched_at
    payment.detected_at = _utcnow()

    if payment.status == PaymentStatus.EXPIRED:
        payment.status = PaymentStatus.LATE_DETECTED
    else:
        payment.status = PaymentStatus.DETECTED

    log.info(
        f"payment {payment.id} detected: {balance} (base units), "
        f"${amount_usd_cents / 100:.2f} at rate {rate.usd_rate}"
    )


def mark_deploying(session: Session, payment: Payment,
                   deploy_tx_hash: str) -> None:
    payment.status = PaymentStatus.DEPLOYING
    payment.deploy_tx_hash = deploy_tx_hash


def mark_confirmed(session: Session, payment: Payment,
                   tx_hash: str, block_number: int) -> None:
    payment.status = PaymentStatus.CONFIRMED
    payment.tx_hash = tx_hash
    payment.block_number = block_number
    payment.confirmed_at = _utcnow()


def mark_failed(session: Session, payment: Payment, reason: str) -> None:
    payment.status = PaymentStatus.FAILED
    log.error(f"payment {payment.id} failed: {reason}")


def expire_overdue_payments(session: Session) -> int:
    now = _utcnow()
    payments = (
        session.query(Payment)
        .filter(Payment.status.in_([
            PaymentStatus.PENDING,
            PaymentStatus.USER_CLAIMED,
        ]))
        .filter(Payment.expires_at < now)
        .all()
    )
    for p in payments:
        p.status = PaymentStatus.EXPIRED
    if payments:
        log.info(f"{len(payments)} payments expired")
    return len(payments)


def count_confirmed_today(session: Session, api_key_id: int) -> int:
    from sqlalchemy import func
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (
        session.query(func.count(Payment.id))
        .filter(Payment.api_key_id == api_key_id)
        .filter(Payment.status.in_([
            PaymentStatus.CONFIRMED,
            PaymentStatus.LATE_DETECTED,
        ]))
        .filter(Payment.confirmed_at >= today_start)
        .scalar()
    ) or 0


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_payment(session: Session, payment_id: int) -> Payment | None:
    return session.get(Payment, payment_id)


def get_payment_by_external_ref(session: Session, external_ref: str,
                                api_key_id: int | None = None) -> Payment | None:
    q = session.query(Payment).filter_by(external_ref=external_ref)
    if api_key_id is not None:
        q = q.filter_by(api_key_id=api_key_id)
    return q.first()


def get_payments_to_poll(session: Session, chain_id: int) -> list[Payment]:
    limit = _utcnow() - timedelta(hours=PAYMENT_MONITOR_HOURS)
    return (
        session.query(Payment)
        .join(Token, Payment.token_id == Token.id)
        .filter(Token.chain_id == chain_id)
        .filter(Payment.status.in_([
            PaymentStatus.PENDING,
            PaymentStatus.USER_CLAIMED,
            PaymentStatus.EXPIRED,
            PaymentStatus.LATE_DETECTED,
        ]))
        .filter(Payment.created_at > limit)
        .all()
    )


def get_payments_to_deploy(session: Session) -> list[Payment]:
    return (
        session.query(Payment)
        .filter(Payment.status == PaymentStatus.DETECTED)
        .all()
    )


def get_payments_to_webhook(session: Session) -> list[Payment]:
    from sqlalchemy import func
    from app.core.models import WebhookAttempt
    from app.core.webhooks import MAX_ATTEMPTS

    failed = (
        session.query(
            WebhookAttempt.payment_id.label("payment_id"),
            func.count(WebhookAttempt.id).label("n"),
        )
        .filter(WebhookAttempt.success.is_(False))
        .group_by(WebhookAttempt.payment_id)
        .subquery()
    )

    return (
        session.query(Payment)
        .outerjoin(failed, Payment.id == failed.c.payment_id)
        .filter(Payment.status == PaymentStatus.CONFIRMED)
        .filter(Payment.webhook_sent_at.is_(None))
        .filter(func.coalesce(failed.c.n, 0) < MAX_ATTEMPTS)
        .all()
    )
