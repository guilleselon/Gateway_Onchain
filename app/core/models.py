"""
Gateway database models.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaymentStatus:
    PENDING = "pending"
    USER_CLAIMED = "user_claimed"
    DETECTED = "detected"
    DEPLOYING = "deploying"
    CONFIRMED = "confirmed"
    LATE_DETECTED = "late_detected"
    FAILED = "failed"
    EXPIRED = "expired"

    ALL = (PENDING, USER_CLAIMED, DETECTED, DEPLOYING, CONFIRMED,
           LATE_DETECTED, FAILED, EXPIRED)
    TERMINAL_OK = (CONFIRMED, LATE_DETECTED)
    TERMINAL_ERR = (FAILED, EXPIRED)
    TERMINAL = TERMINAL_OK + TERMINAL_ERR


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    key_preview: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fee_bps_default: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Chain(Base):
    __tablename__ = "chains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    chain_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    rpc_url: Mapped[str] = mapped_column(String(500), nullable=False)
    factory_address: Mapped[str] = mapped_column(String(42), nullable=False)
    master_address: Mapped[str] = mapped_column(String(42), nullable=False)
    explorer_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confirmations_required: Mapped[int] = mapped_column(
        Integer, default=5, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Token(Base):
    __tablename__ = "tokens"
    __table_args__ = (
        UniqueConstraint("chain_id", "address", name="uq_token_chain_address"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    coingecko_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), nullable=False)
    usd_rate: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_payments_status", "status"),
        Index("ix_payments_api_key_id", "api_key_id"),
        Index("ix_payments_created_at", "created_at"),
        Index("ix_payments_deploy_tx", "deploy_tx_hash"),
        Index("ix_payments_public_token", "public_token", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    public_token: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )

    api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_keys.id"), nullable=True
    )
    external_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    merchant_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False)
    webhook_url: Mapped[str] = mapped_column(String(500), nullable=False)
    webhook_secret: Mapped[str] = mapped_column(String(128), nullable=False)

    amount_usd_cents_expected: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    amount_token_suggested: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )

    token_id: Mapped[int] = mapped_column(ForeignKey("tokens.id"), nullable=False)

    amount_token_received: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    amount_usd_cents_received: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    amount_usd_micros_received: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    rate_used: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10), nullable=True
    )
    rate_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rate_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    proxy_address: Mapped[str] = mapped_column(
        String(42), unique=True, nullable=False
    )
    salt: Mapped[str] = mapped_column(String(66), nullable=False)
    order_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)

    payer_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=PaymentStatus.PENDING, nullable=False
    )
    tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confirmations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    deploy_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    deploy_nonce: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    fee_bps_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fee_amount_token: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )

    user_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    webhook_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint(
            "chain_id", "tx_hash", "log_index", name="uq_event_chain_tx_log"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int] = mapped_column(ForeignKey("chains.id"), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id"), nullable=False
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class WebhookAttempt(Base):
    __tablename__ = "webhook_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
