from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .database import get_db_connection
from .query_service import QueryExecutionResult, QueryService
from .settings import get_settings

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "canceled", "timeout"}


class ExecutionCapacityError(Exception):
    def __init__(self, execution_id: str):
        self.execution_id = execution_id


class ExecutionTimeoutError(Exception):
    def __init__(self, execution_id: str):
        self.execution_id = execution_id


class ExecutionCanceledError(Exception):
    def __init__(self, execution_id: str):
        self.execution_id = execution_id


@dataclass
class ExecutionContext:
    execution_id: str
    owner_hash: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    dbapi_connection: Any = None
    slots: bool = False

    def canceled(self) -> bool:
        return self.cancel_event.is_set()

    def register(self, connection: Any) -> None:
        self.dbapi_connection = connection

    def cancel(self) -> None:
        self.cancel_event.set()
        QueryService.request_cancel(self.dbapi_connection)


class ExecutionService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.executor: ThreadPoolExecutor | None = None
        self.global_sem: asyncio.Semaphore | None = None
        self.owner_sems: dict[str, asyncio.Semaphore] = {}
        self.owner_lock: asyncio.Lock | None = None
        self.contexts: dict[str, ExecutionContext] = {}
        self.context_lock = threading.Lock()

    def startup(self) -> None:
        if self.executor:
            return
        workers = min(
            self.settings.query_max_concurrency_global,
            self.settings.firebird_pool_size + self.settings.firebird_max_overflow,
        )
        self.executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="f2a")
        self.global_sem = asyncio.Semaphore(self.settings.query_max_concurrency_global)
        self.owner_lock = asyncio.Lock()

    async def shutdown(self) -> None:
        with self.context_lock:
            for context in self.contexts.values():
                context.cancel()
            self.contexts.clear()
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
        self.executor = None

    async def execute(
        self,
        *,
        owner_hash: str,
        route_type: str,
        route_ref: str,
        http_method: str,
        query_sql: str,
        params: dict[str, Any],
        options: dict[str, Any] | None = None,
        request: Any = None,
        rollback: bool = False,
    ) -> tuple[str, QueryExecutionResult]:
        if not self.executor or not self.global_sem:
            raise RuntimeError("ExecutionService nao inicializado")
        execution_id = uuid.uuid4().hex
        context = ExecutionContext(execution_id, owner_hash)
        validation = QueryService.validate_query(query_sql, http_method)
        statement_type = validation.get("statement_type", "unknown")
        self._insert_record(context, route_type, route_ref, http_method, statement_type)
        with self.context_lock:
            self.contexts[execution_id] = context
        if not await self._acquire(context):
            self._status(execution_id, "failed", "Capacidade esgotada")
            with self.context_lock:
                self.contexts.pop(execution_id, None)
            raise ExecutionCapacityError(execution_id)
        self._status(execution_id, "running", started=True)
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self.executor, self._worker, context, http_method, query_sql, params, options, rollback
        )
        future.add_done_callback(lambda _: self._release(context))
        disconnect = None
        if request is not None and self.settings.query_cancel_on_client_disconnect:
            disconnect = asyncio.create_task(self._watch_disconnect(request, context))
        try:
            return execution_id, await asyncio.wait_for(
                asyncio.shield(future), timeout=max(0.001, self.settings.query_timeout_seconds)
            )
        except QueryService.QueryExecutionCanceledError as exc:
            raise ExecutionCanceledError(execution_id) from exc
        except TimeoutError as exc:
            context.cancel()
            self._status(execution_id, "timeout", "Tempo limite excedido", finished=True)
            raise ExecutionTimeoutError(execution_id) from exc
        finally:
            if disconnect:
                disconnect.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await disconnect

    def _worker(self, context, method, sql, params, options, rollback) -> QueryExecutionResult:
        try:
            result = QueryService.execute_query(
                sql,
                params,
                method=method,
                options=options,
                max_rows=self.settings.query_max_rows_hard,
                fetch_chunk_size=self.settings.query_fetch_chunk_size,
                timeout_seconds=self.settings.query_timeout_seconds,
                is_canceled=context.canceled,
                register_dbapi_connection=context.register,
                rollback=rollback,
            )
            self._status(
                context.execution_id,
                "completed",
                finished=True,
                affected_rows=result.affected_rows,
            )
            return result
        except QueryService.QueryExecutionCanceledError:
            self._status(context.execution_id, "canceled", "Cancelado", finished=True)
            raise
        except Exception as exc:
            logger.error(
                "Execucao Firebird falhou execution_id=%s error_type=%s",
                context.execution_id,
                type(exc).__name__,
            )
            self._status(
                context.execution_id, "failed", "Erro ao executar no Firebird", finished=True
            )
            raise
        finally:
            with self.context_lock:
                self.contexts.pop(context.execution_id, None)

    async def _watch_disconnect(self, request: Any, context: ExecutionContext) -> None:
        while True:
            await asyncio.sleep(0.2)
            if await request.is_disconnected():
                context.cancel()
                return
            with self.context_lock:
                if context.execution_id not in self.contexts:
                    return

    async def _acquire(self, context: ExecutionContext) -> bool:
        assert self.global_sem is not None and self.owner_lock is not None
        timeout = self.settings.query_acquire_timeout_ms / 1000
        async with self.owner_lock:
            owner_sem = self.owner_sems.setdefault(
                context.owner_hash, asyncio.Semaphore(self.settings.query_max_concurrency_per_token)
            )
        try:
            await asyncio.wait_for(self.global_sem.acquire(), timeout)
            try:
                await asyncio.wait_for(owner_sem.acquire(), timeout)
            except TimeoutError:
                self.global_sem.release()
                return False
        except TimeoutError:
            return False
        context.slots = True
        return True

    def _release(self, context: ExecutionContext) -> None:
        if not context.slots:
            return
        if self.global_sem:
            self.global_sem.release()
        owner = self.owner_sems.get(context.owner_hash)
        if owner:
            owner.release()
        context.slots = False

    def _insert_record(self, context, route_type, route_ref, method, statement_type) -> None:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO execution_history
                    (execution_id, owner_hash, route_type, route_ref, http_method,
                     statement_type, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)
            """,
                (
                    context.execution_id,
                    context.owner_hash,
                    route_type,
                    route_ref,
                    method,
                    statement_type,
                    self._now(),
                ),
            )
            conn.commit()

    def _status(
        self,
        execution_id: str,
        status: str,
        error: str | None = None,
        *,
        started: bool = False,
        finished: bool = False,
        affected_rows: int | None = None,
    ) -> bool:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE execution_history SET status = ?, error_message = ?,
                    started_at = CASE WHEN ? THEN COALESCE(started_at, ?) ELSE started_at END,
                    finished_at = CASE WHEN ? THEN ? ELSE finished_at END,
                    affected_rows = COALESCE(?, affected_rows)
                WHERE execution_id = ?
                  AND status NOT IN ('completed','failed','canceled','timeout')
            """,
                (
                    status,
                    error,
                    int(started),
                    self._now(),
                    int(finished),
                    self._now(),
                    affected_rows,
                    execution_id,
                ),
            )
            if status in TERMINAL:
                conn.execute(
                    """
                    DELETE FROM execution_history WHERE execution_id IN (
                        SELECT execution_id FROM execution_history
                        WHERE status IN ('completed','failed','canceled','timeout')
                        ORDER BY created_at DESC LIMIT -1 OFFSET ?
                    )
                """,
                    (self.settings.query_history_size,),
                )
            conn.commit()
            return cursor.rowcount == 1

    def list_executions(self, owner_hash: str, *, is_admin: bool = False, limit: int = 100):
        safe_limit = max(1, min(int(limit), 1000))
        with get_db_connection() as conn:
            if is_admin:
                rows = conn.execute(
                    "SELECT * FROM execution_history ORDER BY created_at DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM execution_history WHERE owner_hash = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (owner_hash, safe_limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def request_cancel(self, execution_id: str, *, owner_hash: str, is_admin: bool = False):
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT owner_hash, status FROM execution_history WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        if not row or (not is_admin and row["owner_hash"] != owner_hash):
            return {"ok": False, "reason": "not_found"}
        if row["status"] in TERMINAL:
            return {"ok": False, "reason": "already_finished", "status": row["status"]}
        with self.context_lock:
            context = self.contexts.get(execution_id)
        if context:
            context.cancel()
        self._status(execution_id, "cancel_requested", "Cancelamento solicitado")
        return {"ok": True, "status": "cancel_requested"}

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()


execution_service = ExecutionService()
