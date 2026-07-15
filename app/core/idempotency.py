from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .database import get_db_connection
from .settings import get_settings


class IdempotencyConflict(Exception):
    pass


@dataclass(slots=True)
class IdempotencyDecision:
    record_id: int


class IdempotencyService:
    @staticmethod
    def request_hash(payload: Any) -> str:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def _key_hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @classmethod
    def begin(
        cls,
        *,
        access_key_id: int,
        method: str,
        route_path: str,
        idempotency_key: str,
        request_hash: str,
    ) -> IdempotencyDecision:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("Idempotency-Key deve conter entre 1 e 200 caracteres")
        if any(char in idempotency_key for char in "\r\n\t"):
            raise ValueError("Idempotency-Key invalida")
        now = datetime.now(UTC)
        expires = now + timedelta(hours=get_settings().idempotency_ttl_hours)
        key_hash = cls._key_hash(idempotency_key)
        with get_db_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM idempotency_records WHERE expires_at <= ?", (now.isoformat(),)
            )
            row = conn.execute(
                """
                SELECT * FROM idempotency_records
                WHERE access_key_id = ? AND method = ? AND route_path = ? AND key_hash = ?
            """,
                (access_key_id, method, route_path, key_hash),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO idempotency_records
                        (access_key_id, method, route_path, key_hash, request_hash, status, expires_at)
                    VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                    (
                        access_key_id,
                        method,
                        route_path,
                        key_hash,
                        request_hash,
                        expires.isoformat(),
                    ),
                )
                conn.commit()
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite nao retornou o ID idempotente")
                return IdempotencyDecision(record_id=int(cursor.lastrowid))
            data = dict(row)
            conn.commit()
        if data["request_hash"] != request_hash:
            raise IdempotencyConflict("Idempotency-Key reutilizada com payload diferente")
        if data["status"] == "completed":
            raise IdempotencyConflict("Requisicao com esta Idempotency-Key ja foi processada")
        raise IdempotencyConflict(
            "Requisicao com esta Idempotency-Key ainda esta em execucao "
            "ou possui resultado indisponivel"
        )

    @classmethod
    def complete(cls, record_id: int, payload: dict[str, Any], execution_id: str) -> None:
        response_hash = cls.request_hash(payload)
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE idempotency_records SET status = 'completed',
                    response_hash = ?, execution_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'running'
            """,
                (response_hash, execution_id, record_id),
            )
            conn.commit()
            if cursor.rowcount != 1:
                raise RuntimeError("Registro idempotente nao estava em estado running")

    @staticmethod
    def abandon(record_id: int) -> None:
        with get_db_connection() as conn:
            conn.execute(
                "DELETE FROM idempotency_records WHERE id = ? AND status = 'running'", (record_id,)
            )
            conn.commit()
