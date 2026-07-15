from __future__ import annotations

from .database import get_db_connection


def record_admin_audit(
    action: str,
    resource_type: str,
    resource_id: object | None = None,
    *,
    request_id: str | None = None,
    remote_addr_hash: str | None = None,
    outcome: str = "success",
) -> None:
    """Record metadata only; never accept SQL, parameters, keys, bodies or responses."""
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO admin_audit
                (action, resource_type, resource_id, request_id, remote_addr_hash, outcome)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                action[:100],
                resource_type[:50],
                str(resource_id)[:100] if resource_id is not None else None,
                request_id[:40] if request_id else None,
                remote_addr_hash[:80] if remote_addr_hash else None,
                outcome[:20],
            ),
        )
        conn.commit()
