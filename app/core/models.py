from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Query(Base):
    __tablename__ = "queries"
    __table_args__ = (UniqueConstraint("route_path", "method", name="uq_queries_path_method"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    route_path: Mapped[str] = mapped_column(String(500))
    method: Mapped[str] = mapped_column(String(10), default="GET")
    query_sql: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())

    parameters: Mapped[list[Parameter]] = relationship(
        back_populates="query", cascade="all, delete-orphan", order_by="Parameter.position"
    )


class Parameter(Base):
    __tablename__ = "parameters"
    __table_args__ = (UniqueConstraint("query_id", "name", name="uq_parameters_query_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("queries.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128, collation="NOCASE"))
    param_type: Mapped[str] = mapped_column(String(20), default="string")
    source: Mapped[str] = mapped_column(String(10), default="query")
    position: Mapped[int] = mapped_column(Integer, default=0)
    default_value: Mapped[str | None] = mapped_column(Text)
    required: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(Text)
    validation_rule: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())

    query: Mapped[Query] = relationship(back_populates="parameters")


class AccessKey(Base):
    __tablename__ = "access_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16), default="")
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[str | None] = mapped_column(String(40))
    last_used_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class ExecutionHistory(Base):
    __tablename__ = "execution_history"
    __table_args__ = (Index("idx_execution_owner_created", "owner_hash", "created_at"),)

    execution_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_hash: Mapped[str] = mapped_column(String(80))
    route_type: Mapped[str] = mapped_column(String(20), default="api")
    route_ref: Mapped[str] = mapped_column(String(500))
    http_method: Mapped[str] = mapped_column(String(10), default="GET")
    statement_type: Mapped[str] = mapped_column(String(30), default="select")
    status: Mapped[str] = mapped_column(String(30))
    affected_rows: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[str | None] = mapped_column(String(40))
    finished_at: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "access_key_id",
            "method",
            "route_path",
            "key_hash",
            name="uq_idempotency_scope",
        ),
        Index("idx_idempotency_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    access_key_id: Mapped[int] = mapped_column(ForeignKey("access_keys.id", ondelete="CASCADE"))
    method: Mapped[str] = mapped_column(String(10))
    route_path: Mapped[str] = mapped_column(String(500))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="running")
    response_hash: Mapped[str | None] = mapped_column(String(64))
    execution_id: Mapped[str | None] = mapped_column(String(32))
    expires_at: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class AdminAudit(Base):
    __tablename__ = "admin_audit"
    __table_args__ = (Index("idx_admin_audit_created", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    request_id: Mapped[str | None] = mapped_column(String(40))
    remote_addr_hash: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(20), default="success")
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
