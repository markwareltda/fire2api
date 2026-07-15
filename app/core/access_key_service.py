from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import Any

from .database import get_db_connection


class AccessKeyService:
    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_token() -> str:
        return f"f2a_{secrets.token_urlsafe(36)}"

    @staticmethod
    def _row(row) -> dict[str, Any]:
        data = dict(row)
        data["is_active"] = bool(data["is_active"])
        data.pop("key_hash", None)
        return data

    @classmethod
    def list_keys(cls) -> list[dict[str, Any]]:
        with get_db_connection() as conn:
            rows = conn.execute("""
                SELECT id, description, key_prefix, is_active, usage_count,
                       last_used_at, last_used_path, created_at, updated_at
                FROM access_keys ORDER BY created_at DESC, id DESC
            """).fetchall()
        return [cls._row(row) for row in rows]

    @classmethod
    def get_key_by_id(cls, key_id: int) -> dict[str, Any] | None:
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT id, description, key_prefix, is_active, usage_count,
                       last_used_at, last_used_path, created_at, updated_at
                FROM access_keys WHERE id = ?
            """,
                (key_id,),
            ).fetchone()
        return cls._row(row) if row else None

    @classmethod
    def create_key(
        cls,
        *,
        description: str = "",
        is_active: bool = True,
        plain_key: str | None = None,
    ) -> dict[str, Any]:
        token = (plain_key or "").strip() or cls.generate_token()
        if len(token) < 32:
            raise ValueError("Access Key deve ter ao menos 32 caracteres")
        try:
            with get_db_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO access_keys
                        (description, key_hash, key_prefix, is_active)
                    VALUES (?, ?, ?, ?)
                """,
                    (description.strip(), cls.token_hash(token), token[:12], int(is_active)),
                )
                conn.commit()
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite nao retornou o ID da Access Key")
                key_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("Access Key ja cadastrada") from exc
        result = cls.get_key_by_id(key_id)
        if result is None:
            raise RuntimeError("Falha ao obter Access Key criada")
        result["plain_key"] = token
        return result

    @classmethod
    def update_key(cls, key_id: int, *, description: str, is_active: bool) -> bool:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE access_keys SET description = ?, is_active = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
                (description.strip(), int(is_active), key_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete_key(key_id: int) -> bool:
        with get_db_connection() as conn:
            cursor = conn.execute("DELETE FROM access_keys WHERE id = ?", (key_id,))
            conn.commit()
            return cursor.rowcount > 0

    @classmethod
    def has_active_keys(cls) -> bool:
        with get_db_connection() as conn:
            return (
                conn.execute("SELECT 1 FROM access_keys WHERE is_active = 1 LIMIT 1").fetchone()
                is not None
            )

    @classmethod
    def validate_token(cls, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with get_db_connection() as conn:
            row = conn.execute(
                """
                SELECT id, description, key_prefix, is_active, usage_count,
                       last_used_at, last_used_path, created_at, updated_at
                FROM access_keys WHERE key_hash = ? AND is_active = 1 LIMIT 1
            """,
                (cls.token_hash(token),),
            ).fetchone()
        return cls._row(row) if row else None

    @staticmethod
    def register_usage(key_id: int, path: str) -> None:
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE access_keys SET usage_count = usage_count + 1,
                    last_used_at = CURRENT_TIMESTAMP, last_used_path = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
                (path[:500], key_id),
            )
            conn.commit()
