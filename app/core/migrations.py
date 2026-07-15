from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from .database import get_metastore_engine

INITIAL_REVISION = "0001_initial"


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return cfg


def head_revision() -> str:
    revision = ScriptDirectory.from_config(_config()).get_current_head()
    if revision is None:
        raise RuntimeError("Alembic nao possui uma revision head")
    return revision


def upgrade_metastore() -> None:
    cfg = _config()
    connection = get_metastore_engine().connect()
    cfg.attributes["connection"] = connection
    try:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names()) - {"sqlite_sequence"}
        if tables and "alembic_version" not in tables:
            raise RuntimeError(
                "Metastore incompativel com Fire2API 0.0.1; "
                "remova o banco SQLite antigo e inicie uma instancia limpa"
            )
        command.upgrade(cfg, "head")
        connection.commit()
    finally:
        connection.close()


def current_revision() -> str | None:
    with get_metastore_engine().connect() as connection:
        tables = inspect(connection).get_table_names()
        if "alembic_version" not in tables:
            return None
        return connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one_or_none()
