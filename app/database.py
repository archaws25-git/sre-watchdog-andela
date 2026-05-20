"""Database module for the SRE Watchdog application.

Provides the SQLAlchemy engine, declarative base, session factory, and a
FastAPI-compatible dependency-injection generator for database sessions.

SQLite is opened in WAL (Write-Ahead Logging) mode so that concurrent
BackgroundTask writes do not block reads from the API layer.

Typical usage::

    from app.database import Base, engine, get_db

    # In a FastAPI route:
    def my_route(db: Session = Depends(get_db)):
        ...
"""

from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import get_settings

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

settings = get_settings()

engine: Engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _enable_wal_mode(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
    """Enable WAL journal mode on every new SQLite connection.

    WAL mode allows concurrent readers and a single writer without blocking,
    which is important for the BackgroundTask Gate 2 writes running alongside
    API read requests.

    Args:
        dbapi_connection: The raw DBAPI connection provided by SQLAlchemy.
        _connection_record: The connection pool record (unused).
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


# ---------------------------------------------------------------------------
# ORM base and session factory
# ---------------------------------------------------------------------------

Base = declarative_base()
"""Shared declarative base for all SQLAlchemy ORM models.

All model classes in ``app/models/db_models.py`` must inherit from this base
so that ``Base.metadata.create_all(engine)`` creates every table in one call.
"""

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
"""Session factory bound to the application engine.

Use ``get_db()`` for FastAPI dependency injection rather than instantiating
``SessionLocal`` directly in route handlers.
"""


# ---------------------------------------------------------------------------
# Dependency-injection helper
# ---------------------------------------------------------------------------

def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy database session for FastAPI dependency injection.

    Opens a new ``SessionLocal`` session, yields it to the caller, and
    guarantees the session is closed in a ``finally`` block regardless of
    whether the request succeeds or raises an exception.

    Yields:
        Session: An active SQLAlchemy ORM session.

    Example::

        from fastapi import Depends
        from sqlalchemy.orm import Session
        from app.database import get_db

        @router.get("/example")
        def example_route(db: Session = Depends(get_db)):
            return db.execute(text("SELECT 1")).scalar()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
