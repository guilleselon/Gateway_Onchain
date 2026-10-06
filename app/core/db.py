"""
Database connection and session management.

Supports SQLite (local development) and PostgreSQL (production) transparently.

The right driver and configuration are chosen based on the DATABASE_URL:

    sqlite:///./gateway.db              → SQLite with WAL mode
    postgresql://user:pass@host/db      → PostgreSQL via psycopg2
    postgres://user:pass@host/db        → PostgreSQL (legacy alias)

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
    """
    Normalize the database URL for SQLAlchemy 2.x.

    Handles:
        postgres://...           -> postgresql+psycopg2://...
        postgresql://...         -> postgresql+psycopg2://...
        postgresql+psycopg2://   -> unchanged
        postgresql+psycopg://    -> unchanged
        sqlite:///...            -> unchanged

    The +psycopg2 suffix is required because SQLAlchemy 2.x defaults to
    psycopg (v3) for plain postgresql:// URLs. We install psycopg2-binary,
    so we force the psycopg2 dialect explicitly.
    """
    # Legacy alias that SQLAlchemy 2.x no longer accepts
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    # Force psycopg2 driver if no driver was specified
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return url


DATABASE_URL_NORMALIZED = _normalize_db_url(DATABASE_URL)
IS_SQLITE = DATABASE_URL_NORMALIZED.startswith("sqlite")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

if IS_SQLITE:
    # SQLite configuration for local development and single-node deployments.
    #
    # check_same_thread=False: FastHTML runs on ASGI and the worker uses
    # threads, so the same connection may be touched from different threads.
    #
    # timeout=30: if the database is locked by another writer, wait up to
    # 30 seconds before raising an error. Combined with WAL mode, this
    # eliminates almost all "database is locked" errors.
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

    # Enable WAL and related pragmas on every new SQLite connection.
    # WAL allows one writer and multiple readers concurrently, which is
    # what we need for the worker + app running at the same time.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

else:
    # PostgreSQL configuration for production.
    #
    # pool_pre_ping=True: tests connections before reusing them. Render
    # and similar platforms sometimes close idle connections on their
    # side; without this, the next query would fail with a stale socket.
    #
    # pool_size / max_overflow: 20 persistent + 40 temporary connections.
    # Fine for a few hundred requests per minute. Bump if you scale.
    #
    # pool_recycle=1800: recreate connections after 30 minutes to avoid
    # stale sockets on platforms with aggressive idle timeouts.
    engine = create_engine(
        DATABASE_URL_NORMALIZED,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
        pool_recycle=1800,
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Session context manager
# ---------------------------------------------------------------------------

@contextmanager
def get_session():
    """
    Usage:
        with get_session() as s:
            s.add(obj)
    Commits on exit, rolls back on exception, always closes the session.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """
    Create all tables. Imports models inside to avoid a circular import
    (models imports Base from this module).
    """
    from app.core import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# Auto-initialization
# ---------------------------------------------------------------------------

def ensure_db_initialized() -> None:
    """
    Auto-initialize the database on startup.

    Idempotent: safe to run every time the app boots. It:
      1. Creates all tables if they do not exist.
      2. Seeds the chain from .env if there is none.
      3. Seeds MON and USDC tokens if there are none.
      4. Generates a demo API key if there are none, and prints it to the log.

    Intended for platforms without shell access (e.g. Render free tier).
    """
    log = logging.getLogger("gateway.db")

    # 1. Create tables (idempotent: does nothing if they exist)
    init_db()
    log.info("Database tables ensured.")

    # Deferred imports to avoid circular imports at module load time
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
        # 2. Seed the chain if the table is empty
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

        # 3. Seed tokens if the table is empty
        if s.query(Token).count() == 0:
            tokens_seed = [
                {
                    "symbol": "MON",
                    "address": None,
                    "decimals": 18,
                    "coingecko_id": "monad",
                },
                {
                    "symbol": "USDC",
                    "address": None,
                    "decimals": 6,
                    "coingecko_id": "usd-coin",
                },
            ]
            for t in tokens_seed:
                s.add(Token(chain_id=chain.id, active=True, **t))
            log.info("Seeded MON and USDC tokens.")
        else:
            # Make sure MON exists even if there are other tokens
            if not s.query(Token).filter_by(symbol="MON").first():
                s.add(Token(
                    chain_id=chain.id,
                    symbol="MON",
                    address=None,
                    decimals=18,
                    coingecko_id="monad",
                    active=True,
                ))
                log.info("Seeded MON token.")

        # 4. Generate a demo API key if there are none
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
