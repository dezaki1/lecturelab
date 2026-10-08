"""SQLite engine (WAL), sessions and programmatic Alembic upgrades."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_engine: Engine | None = None


def load_extensions(dbapi_conn) -> None:  # type: ignore[no-untyped-def]
    """sqlite-vec provides the vec0 virtual table used for vector search (M6)."""
    import sqlite_vec

    dbapi_conn.enable_load_extension(True)
    try:
        sqlite_vec.load(dbapi_conn)
    finally:
        dbapi_conn.enable_load_extension(False)


def install_extension_loader(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _load(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        load_extensions(dbapi_conn)


def make_engine(db_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    install_extension_loader(engine)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    return engine


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def run_migrations(engine: Engine) -> None:
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def init_db(db_path: Path) -> Engine:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(db_path)
    run_migrations(_engine)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database not initialised; call init_db() first")
    return _engine


def session_scope() -> Session:
    """A new session for use outside request handlers (threads, jobs)."""
    return Session(get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
