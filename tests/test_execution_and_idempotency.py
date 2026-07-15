from __future__ import annotations

import asyncio
import logging

import pytest

from app.core.access_key_service import AccessKeyService
from app.core.database import get_db_connection
from app.core.execution_service import (
    ExecutionCanceledError,
    ExecutionCapacityError,
    ExecutionService,
    ExecutionTimeoutError,
)
from app.core.idempotency import IdempotencyConflict, IdempotencyService
from app.core.migrations import upgrade_metastore
from app.core.query_service import QueryExecutionResult, QueryService
from app.core.settings import get_settings


def test_idempotency_lifecycle_conflicts_and_abandon():
    upgrade_metastore()
    access_key = AccessKeyService.create_key(description="idempotency direct")
    common = {
        "access_key_id": access_key["id"],
        "method": "POST",
        "route_path": "/direct",
        "idempotency_key": "same-key",
        "request_hash": IdempotencyService.request_hash({"a": 1}),
    }
    decision = IdempotencyService.begin(**common)
    with pytest.raises(IdempotencyConflict, match="execucao"):
        IdempotencyService.begin(**common)
    IdempotencyService.complete(
        decision.record_id,
        {"success": True, "data": {"sensitive": "must-not-be-stored"}},
        "execution-1",
    )
    with get_db_connection() as connection:
        stored = dict(
            connection.execute(
                "SELECT * FROM idempotency_records WHERE id = ?", (decision.record_id,)
            ).fetchone()
        )
    assert stored["status"] == "completed"
    assert stored["execution_id"] == "execution-1"
    assert "must-not-be-stored" not in repr(stored)
    original_admin_key = get_settings().admin_api_key
    get_settings().admin_api_key = "rotated-admin-key-with-at-least-32-characters"
    with pytest.raises(IdempotencyConflict, match="ja foi processada"):
        IdempotencyService.begin(**common)
    get_settings().admin_api_key = original_admin_key
    with pytest.raises(IdempotencyConflict, match="payload diferente"):
        IdempotencyService.begin(**{**common, "request_hash": "different"})

    abandoned = IdempotencyService.begin(**{**common, "idempotency_key": "abandon"})
    IdempotencyService.abandon(abandoned.record_id)
    retried = IdempotencyService.begin(**{**common, "idempotency_key": "abandon"})
    assert retried.record_id > 0
    IdempotencyService.abandon(retried.record_id)


@pytest.mark.asyncio
async def test_execution_service_success_capacity_and_cancel(monkeypatch):
    upgrade_metastore()

    def fake_execute(*args, **kwargs):
        return QueryExecutionResult([{"VALUE": 1}], 0, "select")

    monkeypatch.setattr(QueryService, "execute_query", fake_execute)
    service = ExecutionService()
    service.startup()
    execution_id, result = await service.execute(
        owner_hash="owner",
        route_type="api",
        route_ref="/test",
        http_method="GET",
        query_sql="select 1 from rdb$database",
        params={},
    )
    assert result.rows == [{"VALUE": 1}]
    records = service.list_executions("owner")
    assert records[0]["execution_id"] == execution_id
    assert records[0]["status"] == "completed"
    assert service.request_cancel(execution_id, owner_hash="owner")["reason"] == "already_finished"

    async def no_capacity(_context):
        return False

    monkeypatch.setattr(service, "_acquire", no_capacity)
    with pytest.raises(ExecutionCapacityError):
        await service.execute(
            owner_hash="owner",
            route_type="api",
            route_ref="/busy",
            http_method="GET",
            query_sql="select 1 from rdb$database",
            params={},
        )
    await service.shutdown()


@pytest.mark.asyncio
async def test_execution_service_canceled_failed_and_timeout(monkeypatch, caplog):
    upgrade_metastore()
    caplog.set_level(logging.ERROR)
    service = ExecutionService()
    service.startup()

    def canceled(*args, **kwargs):
        raise QueryService.QueryExecutionCanceledError("cancel")

    monkeypatch.setattr(QueryService, "execute_query", canceled)
    with pytest.raises(ExecutionCanceledError):
        await service.execute(
            owner_hash="owner-errors",
            route_type="api",
            route_ref="/cancel",
            http_method="GET",
            query_sql="select 1 from rdb$database",
            params={},
        )

    def failed(*args, **kwargs):
        raise RuntimeError("database detail must not leak")

    monkeypatch.setattr(QueryService, "execute_query", failed)
    with pytest.raises(RuntimeError):
        await service.execute(
            owner_hash="owner-errors",
            route_type="api",
            route_ref="/failed",
            http_method="GET",
            query_sql="select 1 from rdb$database",
            params={},
        )
    assert "database detail must not leak" not in caplog.text

    def slow(*args, **kwargs):
        import time

        time.sleep(0.1)
        return QueryExecutionResult([], 0, "select")

    monkeypatch.setattr(QueryService, "execute_query", slow)
    service.settings.query_timeout_seconds = 0.01
    with pytest.raises(ExecutionTimeoutError) as timeout:
        await service.execute(
            owner_hash="owner-errors",
            route_type="api",
            route_ref="/timeout",
            http_method="GET",
            query_sql="select 1 from rdb$database",
            params={},
        )
    await asyncio.sleep(0.12)
    timeout_record = next(
        item
        for item in service.list_executions("owner-errors")
        if item["execution_id"] == timeout.value.execution_id
    )
    assert timeout_record["status"] == "timeout"
    await service.shutdown()
    await service.shutdown()  # idempotent
