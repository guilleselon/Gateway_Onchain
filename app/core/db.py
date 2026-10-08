"""
Database connection and session management.
"""

import logging
import secrets
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


DATABASE_URL_NORMALIZED = _normalize_db_url(DATABASE_URL)
IS_SQLITE = DATABASE_URL_NORMALIZED.startswith("sqlite")


if IS_SQLITE:
    connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(
        DATABASE_URL_NORMALIZED,
        connect_args=connect_args,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
else:
    engine = create_engine(
        DATABASE_URL_NORMALIZED,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
        pool_recycle=1800,
    )


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from app.core import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def apply_schema_migrations() -> None:
    """Idempotent schema migrations (adds missing columns)."""
    log = logging.getLogger("gateway.db")
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "payments" not in inspector.get_table_names():
        return

    columns = [c["name"] for c in inspector.get_columns("payments")]

    if "public_token" not in columns:
        log.info("Migration: adding 'public_token' column to payments...")
        with engine.begin() as conn:
            if IS_SQLITE:
                conn.execute(text(
                    "ALTER TABLE payments ADD COLUMN public_token VARCHAR(40)"
                ))
            else:
                conn.execute(text(
                    "ALTER TABLE payments "
                    "ADD COLUMN IF NOT EXISTS public_token VARCHAR(40)"
                ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ix_payments_public_token ON payments (public_token)"
            ))
        log.info("Migration complete: public_token added.")

    if "amount_usd_micros_received" not in columns:
        log.info(
            "Migration: adding 'amount_usd_micros_received' column to payments..."
        )
        with engine.begin() as conn:
            if IS_SQLITE:
                conn.execute(text(
                    "ALTER TABLE payments "
                    "ADD COLUMN amount_usd_micros_received BIGINT"
                ))
            else:
                conn.execute(text(
                    "ALTER TABLE payments "
                    "ADD COLUMN IF NOT EXISTS amount_usd_micros_received BIGINT"
                ))
        log.info("Migration complete: amount_usd_micros_received added.")


def backfill_micros() -> None:
    """
    One-time backfill of amount_usd_micros_received for payments detected
    before the micros column existed.

    Recomputes from amount_token_received + rate_used.
    Only touches rows where micros IS NULL.
    """
    log = logging.getLogger("gateway.db")
    from decimal import Decimal
    from app.core.models import Payment, Token

    try:
        with get_session() as s:
            rows = (
                s.query(Payment)
                .filter(Payment.amount_usd_micros_received.is_(None))
                .filter(Payment.amount_token_received.isnot(None))
                .filter(Payment.rate_used.isnot(None))
                .all()
            )

            if not rows:
                log.info("Backfill: no rows need micros.")
                return

            updated = 0
            skipped = 0
            for p in rows:
                token = s.get(Token, p.token_id)
                if not token:
                    skipped += 1
                    continue
                try:
                    raw = Decimal(p.amount_token_received)
                    divisor = Decimal(10 ** token.decimals)
                    amount_token_dec = raw / divisor
                    amount_usd = amount_token_dec * p.rate_used
                    micros = int(round(amount_usd * 1_000_000))
                    p.amount_usd_micros_received = micros
                    p.amount_usd_cents_received = micros // 10_000
                    updated += 1
                except Exception as e:
                    log.warning(f"Backfill: payment {p.id} failed: {e}")
                    skipped += 1

            log.info(
                f"Backfill: updated {updated} payment(s), skipped {skipped}."
            )
    except Exception as e:
        log.error(f"Backfill failed: {e}")


def ensure_db_initialized() -> None:
    """Idempotent auto-initialization on startup."""
    log = logging.getLogger("gateway.db")

    try:
        apply_schema_migrations()
    except Exception as e:
        log.error(f"Schema migration failed: {e}")

    try:
        backfill_micros()
    except Exception as e:
        log.error(f"Backfill failed: {e}")

    init_db()
    log.info("Database tables ensured.")

    from app.config import (
        CHAIN_CONFIRMATIONS, CHAIN_EXPLORER, CHAIN_ID, CHAIN_NAME,
        FACTORY_ADDRESS, MASTER_ADDRESS, RPC_URL,
    )
    from app.core.auth import hash_api_key
    from app.core.models import ApiKey, Chain, Token

    with get_session() as s:
        if s.query(Chain).count() == 0:
            chain = Chain(
                name=CHAIN_NAME,
                chain_id=CHAIN_ID,
                rpc_url=RPC_URL,
                factory_address=FACTORY_ADDRESS,
                master_address=MASTER_ADDRESS,
                explorer_url=CHAIN_EXPLORER or None,
                confirmations_required=CHAIN_CONFIRMATIONS,
                active=True,
            )
            s.add(chain)
            s.flush()
            log.info(f"Seeded chain '{CHAIN_NAME}' (id={chain.id}).")
        else:
            chain = s.query(Chain).first()

        if s.query(Token).count() == 0:
            for t in [
                {"symbol": "MON", "address": None, "decimals": 18,
                 "coingecko_id": "monad"},
                {"symbol": "USDC", "address": None, "decimals": 6,
                 "coingecko_id": "usd-coin"},
            ]:
                s.add(Token(chain_id=chain.id, active=True, **t))
            log.info("Seeded MON and USDC tokens.")

        if s.query(ApiKey).count() == 0:
            plain_key = "sk_live_" + secrets.token_hex(32)
            key_hash = hash_api_key(plain_key)
            key_preview = plain_key[:16] + "..."
            s.add(ApiKey(
                name="Demo Merchant",
                key_hash=key_hash,
                key_preview=key_preview,
                active=True,
            ))
            log.warning("=" * 60)
            log.warning("  NO API KEY FOUND. A DEMO KEY WAS CREATED:")
            log.warning(f"  {plain_key}")
            log.warning("  Copy it now. It will not be shown again.")
            log.warning("=" * 60)
