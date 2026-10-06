"""
Payment business logic.

Responsibilities:
- create_payment(): validate input, compute proxy, store the payment.
- State transitions: user_claimed, detected, deploying, confirmed, failed.
- Queries: by id, by external_ref, lists for the worker.
- Maintenance: expire overdue payments.

Merchant data (wallet, webhook, secret, name) is stored on each Payment,
not in a separate table. The same API key can be used for multiple
wallets and URLs.
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


# --- Constants ---
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
    """
    Random uint256, limited to 63 bits so it fits in SQLite
    (which uses signed 64-bit INTEGER). 2^63 possible values is
    more than enough to avoid collisions.
    """
    return secrets.randbits(63)


def _generate_salt() -> str:
    """Random bytes32 in hex, with 0x prefix."""
    return "0x" + os.urandom(32).hex()


def _calculate_amount_token(amount_usd_cents: int, usd_rate: Decimal,
                            decimals: int) -> int:
    """
    Convert a USD amount to the token amount.

    Formula: amount_token = amount_usd / usd_rate * 10^decimals
    """
    if usd_rate <= 0:
        raise PaymentError(f"invalid rate: {usd_rate}")
    amount_usd = Decimal(amount_usd_cents) / Decimal(100)
    amount_token_dec = amount_usd / usd_rate
    return int(amount_token_dec * Decimal(10 ** decimals))


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def create_payment(
    session: Session,
    external_ref: str,
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

    Idempotent: if a payment with the same (api_key_id, external_ref)
    already exists, it is returned as-is instead of creating a duplicate.
    This protects against client retries caused by network timeouts.

    Steps:
      1. Idempotency check.
      2. Validate token and chain.
      3. Generate unique order_id and salt.
      4. Compute the proxy address pointing to wallet_address.
      5. Compute amount_token_suggested with the latest rate.
      6. Store the Payment with status=pending and expires_at.

    Args:
        session: SQLAlchemy session.
        external_ref: merchant-side identifier for this payment.
        amount_usd_cents: expected amount in USD cents.
        token_id: chosen token.
        wallet_address: destination address (the proxy's processor).
        webhook_url: URL to notify once the payment is confirmed.
        webhook_secret: secret used to sign the webhook (HMAC).
        api_key_id: id of the ApiKey used, for auditing (optional).
        merchant_name: human-readable merchant name, for the UI (optional).

    Returns:
        The newly created (or existing) Payment.

    Raises:
        PaymentError if anything is wrong.
    """
    # --- Idempotency: return existing payment if one exists ---
    if api_key_id is not None and external_ref:
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

    # --- Basic validation ---
    if amount_usd_cents <= 0:
        raise PaymentError("amount_usd_cents must be greater than 0")
    if not external_ref:
        raise PaymentError("external_ref is empty")
    if not wallet_address:
        raise PaymentError("wallet_address is empty")
    if not webhook_url:
        raise PaymentError("webhook_url is empty")
    if not webhook_secret:
        raise PaymentError("webhook_secret is empty")

    # --- Validate token and chain ---
    token = session.get(Token, token_id)
    if not token or not token.active:
        raise PaymentError(f"token {token_id} does not exist or is inactive")

    chain = session.get(Chain, token.chain_id)
    if not chain or not chain.active:
        raise PaymentError(f"chain {token.chain_id} does not exist or is inactive")

    # --- Latest rate (required to suggest the amount) ---
    rate = rates.get_latest_rate(session, token.id)
    if not rate:
        raise PaymentError(
            f"no rate stored for {token.symbol}. "
            f"Run rates.update_all_rates() first."
        )

    # --- Generate unique contract identifiers ---
    order_id = _generate_order_id()
    salt = _generate_salt()

    # --- Compute the proxy address ---
    w3 = get_web3(chain.rpc_url)
    factory = get_factory_contract(w3, chain.factory_address)
    try:
        proxy_address = blockchain.calculate_proxy(
            w3, factory, order_id, wallet_address, salt,
        )
    except Exception as e:
        raise PaymentError(f"could not compute proxy: {e}") from e

    # --- Suggested amount in token (stored as string to avoid overflow) ---
    amount_token_suggested = _calculate_amount_token(
        amount_usd_cents, rate.usd_rate, token.decimals,
    )

    # --- Persist ---
    now = _utcnow()
    payment = Payment(
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
        f"Payment created id={payment.id} external_ref={external_ref} "
        f"merchant={merchant_name or '(unnamed)'} token={token.symbol} "
        f"usd_cents={amount_usd_cents} proxy={proxy_address}"
    )
    return payment


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def mark_user_claimed(session: Session, payment: Payment) -> None:
    """The user pressed 'I paid'. Does not change the flow, only priority."""
    if payment.status not in (PaymentStatus.PENDING, PaymentStatus.USER_CLAIMED):
        return
    payment.status = PaymentStatus.USER_CLAIMED
    payment.user_claimed_at = _utcnow()


def mark_detected(session: Session, payment: Payment) -> None:
    """
    Funds are present in the proxy. Fix the rate, compute the USD amount,
    and update the status to detected (or late_detected if expired).
    """
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

    # Stored as string to avoid SQLite overflow on cheap tokens
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
    """The gateway has sent the proxy deployment transaction."""
    payment.status = PaymentStatus.DEPLOYING
    payment.deploy_tx_hash = deploy_tx_hash


def mark_confirmed(session: Session, payment: Payment,
                   tx_hash: str, block_number: int) -> None:
    """The proxy was deployed and the Paid event was emitted."""
    payment.status = PaymentStatus.CONFIRMED
    payment.tx_hash = tx_hash
    payment.block_number = block_number
    payment.confirmed_at = _utcnow()


def mark_failed(session: Session, payment: Payment, reason: str) -> None:
    """Irrecoverable failure."""
    payment.status = PaymentStatus.FAILED
    log.error(f"payment {payment.id} failed: {reason}")


def expire_overdue_payments(session: Session) -> int:
    """
    Mark as 'expired' those pending/user_claimed payments whose expires_at
    has already passed. The worker keeps watching them for 24h more in
    case the funds arrive late.
    """
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
    """
    Count how many payments for this API key were confirmed today (UTC).
    Used for the daily deploy quota.
    """
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
# Queries (used by the API and the worker)
# ---------------------------------------------------------------------------

def get_payment(session: Session, payment_id: int) -> Payment | None:
    return session.get(Payment, payment_id)


def get_payment_by_external_ref(session: Session, external_ref: str,
                                api_key_id: int | None = None) -> Payment | None:
    """
    Find a payment by external_ref.

    If api_key_id is provided, filter by that key too, so a merchant
    cannot query another merchant's payments.
    """
    q = session.query(Payment).filter_by(external_ref=external_ref)
    if api_key_id is not None:
        q = q.filter_by(api_key_id=api_key_id)
    return q.first()


def get_payments_to_poll(session: Session, chain_id: int) -> list[Payment]:
    """
    Payments the worker should check with get_balance:
    - pending, user_claimed: still within TTL.
    - expired, late_detected: past TTL but within 24h.
    """
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
    """Payments detected but not yet deployed."""
    return (
        session.query(Payment)
        .filter(Payment.status == PaymentStatus.DETECTED)
        .all()
    )


def get_payments_to_webhook(session: Session) -> list[Payment]:
    """Confirmed payments whose webhook has not been sent yet."""
    return (
        session.query(Payment)
        .filter(Payment.status == PaymentStatus.CONFIRMED)
        .filter(Payment.webhook_sent_at.is_(None))
        .all()
    )
