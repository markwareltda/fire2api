from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest_plugins = ["nicegui.testing.user_plugin"]

TEST_ADMIN_KEY = "test-admin-key-with-more-than-32-characters"
TEST_DB = Path(os.environ.get("SQLITE_DB_PATH", Path(__file__).parent / ".test-metastore.db"))
os.environ["ADMIN_API_KEY"] = TEST_ADMIN_KEY
os.environ["SQLITE_DB_PATH"] = str(TEST_DB)
os.environ["APP_ENV"] = "testing"
os.environ["API_DEBUG"] = "false"


@pytest.fixture(autouse=True)
def reset_auth_rate_limiter():
    from app.core.rate_limit import auth_rate_limiter

    auth_rate_limiter.clear()
    yield
    auth_rate_limiter.clear()


def pytest_sessionstart(session):
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(TEST_DB) + suffix)
        if path.exists():
            path.unlink()


def pytest_sessionfinish(session, exitstatus):
    from app.core.database import dispose_engines

    dispose_engines()
