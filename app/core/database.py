from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import get_settings


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


@lru_cache
def get_metastore_engine() -> Engine:
    settings = get_settings()
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        _sqlite_url(settings.sqlite_path),
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
        cursor.close()

    return engine


def get_session() -> Session:
    factory = sessionmaker(bind=get_metastore_engine(), expire_on_commit=False)
    return factory()


@contextmanager
def get_db_connection() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        settings.sqlite_path,
        timeout=settings.sqlite_busy_timeout_ms / 1000,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@lru_cache
def get_firebird_engine() -> Engine:
    settings = get_settings()
    if not settings.firebird_configured:
        raise RuntimeError("Conexao Firebird nao configurada")
    url = URL.create(
        drivername="firebird+firebird",
        username=settings.firebird_user,
        password=settings.firebird_password,
        host=settings.firebird_host,
        port=settings.firebird_port,
        database=settings.firebird_db_path,
        query={"charset": settings.firebird_charset},
    )
    engine = create_engine(
        url,
        pool_size=settings.firebird_pool_size,
        max_overflow=settings.firebird_max_overflow,
        pool_pre_ping=True,
        echo=False,
    )

    def safe_terminate(dbapi_connection) -> None:
        terminate = getattr(dbapi_connection, "terminate", None)
        if callable(terminate):
            terminate()
        else:
            dbapi_connection.close()

    engine.dialect.do_terminate = safe_terminate  # type: ignore[method-assign]
    return engine


def dispose_engines() -> None:
    if get_metastore_engine.cache_info().currsize:
        get_metastore_engine().dispose()
    if get_firebird_engine.cache_info().currsize:
        get_firebird_engine().dispose()
