"""Initial Fire2API 0.0.1 metastore schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE queries (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            route_path VARCHAR(500) NOT NULL,
            method VARCHAR(10) NOT NULL DEFAULT 'GET',
            query_sql TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_queries_path_method UNIQUE(route_path, method)
        )
    """)
    op.execute("""
        CREATE TABLE parameters (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            query_id INTEGER NOT NULL,
            name VARCHAR(128) COLLATE NOCASE NOT NULL,
            param_type VARCHAR(20) NOT NULL DEFAULT 'string',
            source VARCHAR(10) NOT NULL DEFAULT 'query',
            position INTEGER NOT NULL DEFAULT 0,
            default_value TEXT,
            required INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            validation_rule TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(query_id) REFERENCES queries(id) ON DELETE CASCADE,
            CONSTRAINT uq_parameters_query_name UNIQUE(query_id, name)
        )
    """)
    op.execute("""
        CREATE TABLE access_keys (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL DEFAULT '',
            key_hash VARCHAR(64) NOT NULL,
            key_prefix VARCHAR(16) NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            usage_count INTEGER NOT NULL DEFAULT 0,
            last_used_at VARCHAR(40),
            last_used_path VARCHAR(500),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_access_keys_key_hash UNIQUE(key_hash)
        )
    """)
    op.execute("""
        CREATE TABLE execution_history (
            execution_id VARCHAR(32) PRIMARY KEY NOT NULL,
            owner_hash VARCHAR(80) NOT NULL,
            route_type VARCHAR(20) NOT NULL DEFAULT 'api',
            route_ref VARCHAR(500) NOT NULL,
            http_method VARCHAR(10) NOT NULL DEFAULT 'GET',
            statement_type VARCHAR(30) NOT NULL DEFAULT 'select',
            status VARCHAR(30) NOT NULL,
            affected_rows INTEGER,
            started_at VARCHAR(40),
            finished_at VARCHAR(40),
            error_message VARCHAR(300),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute(
        "CREATE INDEX idx_execution_owner_created "
        "ON execution_history(owner_hash, created_at)"
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "access_key_id",
            sa.Integer(),
            sa.ForeignKey("access_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("route_path", sa.String(500), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_hash", sa.String(64)),
        sa.Column("execution_id", sa.String(32)),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("expires_at", sa.String(40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()
        ),
        sa.UniqueConstraint(
            "access_key_id", "method", "route_path", "key_hash", name="uq_idempotency_scope"
        ),
    )
    op.execute("CREATE INDEX idx_idempotency_expires ON idempotency_records(expires_at)")
    op.execute("""
        CREATE TABLE admin_audit (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            action VARCHAR(100) NOT NULL,
            resource_type VARCHAR(50) NOT NULL,
            resource_id VARCHAR(100),
            request_id VARCHAR(40),
            remote_addr_hash VARCHAR(80),
            outcome VARCHAR(20) NOT NULL DEFAULT 'success',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX idx_admin_audit_created ON admin_audit(created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE admin_audit")
    op.execute("DROP TABLE idempotency_records")
    op.execute("DROP TABLE execution_history")
    op.execute("DROP TABLE access_keys")
    op.execute("DROP TABLE parameters")
    op.execute("DROP TABLE queries")
