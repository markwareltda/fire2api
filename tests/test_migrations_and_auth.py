from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect

from app.core.access_key_service import AccessKeyService
from app.core.migrations import current_revision, head_revision, upgrade_metastore
from app.core.settings import INVALID_ADMIN_API_KEY, Settings


def test_empty_database_migrates_and_second_upgrade_is_idempotent():
    upgrade_metastore()
    assert current_revision() == head_revision() == "0001_initial"
    upgrade_metastore()
    assert current_revision() == "0001_initial"


def test_pre_release_legacy_database_is_rejected_without_mutation(monkeypatch, tmp_path):
    from app.core import migrations

    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE queries (id INTEGER PRIMARY KEY, query_sql TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO queries (id, query_sql) VALUES (1, 'select 1 from rdb$database')"
        )

    monkeypatch.setattr(migrations, "get_metastore_engine", lambda: engine)
    with pytest.raises(RuntimeError, match="remova o banco SQLite antigo"):
        migrations.upgrade_metastore()

    with engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == {"queries"}
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM queries").scalar_one() == 1
    engine.dispose()


def test_access_keys_are_hashed_and_manual_key_requires_32_chars():
    upgrade_metastore()
    created = AccessKeyService.create_key(description="test")
    assert created["plain_key"].startswith("f2a_")
    assert "key_hash" not in created
    assert AccessKeyService.validate_token(created["plain_key"])["id"] == created["id"]

    try:
        AccessKeyService.create_key(description="weak", plain_key="short")
    except ValueError as exc:
        assert "32" in str(exc)
    else:
        raise AssertionError("weak key accepted")

    assert AccessKeyService.delete_key(created["id"])
    assert AccessKeyService.validate_token(created["plain_key"]) is None


def test_insecure_example_admin_key_and_invalid_timeout_ceiling_are_rejected():
    with pytest.raises(ValidationError, match="exemplo"):
        Settings(admin_api_key=INVALID_ADMIN_API_KEY)
    with pytest.raises(ValidationError, match="QUERY_TIMEOUT_SECONDS"):
        Settings(
            admin_api_key="valid-admin-key-with-at-least-32-characters",
            query_timeout_seconds=181,
            query_timeout_max_seconds=180,
        )
