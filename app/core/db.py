"""
Database connection and session management.

Supports SQLite (local development) and PostgreSQL (production) transparently.

The right driver and configuration are chosen based on the DATABASE_URL:

    sqlite:///./gateway.db              → SQLite with WAL mode
    postgresql://user:pass@host/db      → PostgreSQL
    postgres://user:pass@host/db        → PostgreSQL (aliased)

Note: Render and some other PaaS providers still emit `postgres://` URLs.
SQLAlchemy 2.x requires `postgresql://`, so we normalize it here.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL


# ---------------------------------------------------------------------------
# Normalize the URL
# ---------------------------------------------------------------------------

def _normalize_db_url(url: str) -> str:
    """
    SQLAlchemy 2.x dropped support for the legacy `postgres://` prefix.
    Normalize it to `postgresql://` so connections work out of the box
    on platforms that still emit the old format (Render, Heroku).
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
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
    engine = create_engine(
        DATABASE_URL_NORMALIZED,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
        # Recycle connections after 30 minutes to avoid stale sockets
        # on platforms with aggressive idle timeouts.
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
