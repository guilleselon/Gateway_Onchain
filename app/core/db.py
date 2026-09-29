"""
Database connection and session management.

Exposes:
- Base: declarative base for all models.
- engine: SQLAlchemy engine with WAL + connection pool.
- SessionLocal: session factory.
- get_session(): context manager to open/close sessions.
- init_db(): create all tables.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

IS_SQLITE = DATABASE_URL.startswith("sqlite")

# --- Engine ---
# For SQLite: allow cross-thread access (FastHTML/worker use threads),
# wait up to 30s if the DB is locked, and use a pool for concurrency.
if IS_SQLITE:
    connect_args = {
        "check_same_thread": False,
        "timeout": 30,
    }
    engine = create_engine(
        DATABASE_URL,
        connect_args=connect_args,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
    )

    # Enable WAL mode and other optimizations on every new connection.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
else:
    # PostgreSQL or other backends
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
    )


# --- Sessions ---
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


# --- Declarative base ---
class Base(DeclarativeBase):
    pass


# --- Session context manager ---
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


# --- Table creation ---
def init_db() -> None:
    """
    Create all tables. Imports models inside to avoid a circular import
    (models imports Base from this module).
    """
    from app.core import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

