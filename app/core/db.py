"""
Database connection and session management.

Supports SQLite (local development) and PostgreSQL (production) transparently.

The right driver and configuration are chosen based on the DATABASE_URL:

    sqlite:///./gateway.db              -> SQLite with WAL mode
    postgresql://user:pass@host/db      -> PostgreSQL via psycopg2
    postgres://user:pass@host/db        -> PostgreSQL (legacy alias)

Note: Render and some other PaaS providers still emit `postgres://` URLs.
SQLAlchemy 2.x requires `postgresql://`, and by default picks the psycopg
(v3) driver for that prefix. Since we install `psycopg2-binary`, we force
the psycopg2 dialect with the explicit `+psycopg2` suffix.
"""

import logging
import secrets
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL


# ---------------------------------------------------------------------------
# Normalize the URL
# ---------------------------------------------------------------------------

def _normalize_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


DATABASE_URL_NORMALIZED = _normalize_db_url(DATABASE_URL)
IS_SQLITE = DATABASE_URL_NORMALIZED.startswith("sqlite")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

if IS_SQLITE:
    connect_args = {
        "check_same_thread": False,
        "timeout": 30,
    }
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
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
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


# ---------------------------------------------------------------------------
# Lightweight schema migrations
# ---------------------------------------------------------------------------

def apply_schema_migrations() -> None:
    """
    Apply small schema changes that `create_all` cannot handle
    (e.g. adding a column to an existing table).

    Idempotent: checks the current schema before doing anything.
    Runs on every startup but only acts if changes are needed.
    """
    log = logging.getLogger("gateway.db")
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "payments" not in inspector.get_table_names():
        return  # table doesn't exist yet; create_all will create it

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


# ---------------------------------------------------------------------------
# Auto-initialization
# ---------------------------------------------------------------------------

def ensure_db_initialized() -> None:
    """
    Auto-initialize the database on startup.

    Idempotent: safe to run every time the app boots. It:
      0. Applies lightweight schema migrations (adds missing columns).
      1. Creates all tables if they do not exist.
      2. Seeds the chain from .env if there is none.
      3. Seeds MON and USDC tokens if there are none.
      4. Generates a demo API key if there are none, and prints it to the log.
    """
    log = logging.getLogger("gateway.db")

    # 0. Schema migrations first (adds columns to existing tables)
    try:
        apply_schema_migrations()
    except Exception as e:
        log.error(f"Schema migration failed: {e}")

    # 1. Create tables
    init_db()
    log.info("Database tables ensured.")

    from app.config import (
        CHAIN_CONFIRMATIONS,
        CHAIN_EXPLORER,
        CHAIN_ID,
        CHAIN_NAME,
        FACTORY_ADDRESS,
        MASTER_ADDRESS,
        RPC_URL,
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
