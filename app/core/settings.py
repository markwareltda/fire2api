from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ID = "fire2api"
API_TITLE = "Fire2API by Markware"
API_VERSION = "0.0.1"
INVALID_ADMIN_API_KEY = "replace-with-at-least-32-random-characters"


class Settings(BaseSettings):
    """Runtime configuration. `.env.example` is the canonical inventory."""

    app_id: str = APP_ID
    api_title: str = API_TITLE
    api_version: str = API_VERSION
    api_debug: bool = False
    app_env: str = "production"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    admin_api_key: str = Field(default="", min_length=32, validate_default=True)

    firebird_user: str | None = None
    firebird_password: str | None = None
    firebird_host: str = "localhost"
    firebird_port: int = 3050
    firebird_db_path: str | None = None
    firebird_charset: str = "UTF8"

    sqlite_db_path: str = "data/metastore.db"
    sqlite_busy_timeout_ms: int = 5000

    query_max_concurrency_global: int = 8
    query_max_concurrency_per_token: int = 2
    query_acquire_timeout_ms: int = 300
    query_timeout_seconds: int = 60
    query_timeout_max_seconds: int = 180
    query_cancel_on_client_disconnect: bool = True
    query_max_rows_hard: int = 1000
    query_fetch_chunk_size: int = 200
    query_history_size: int = 500
    firebird_pool_size: int = 8
    firebird_max_overflow: int = 16

    cors_allowed_origins: str = ""
    max_request_body_bytes: int = 1_048_576
    idempotency_ttl_hours: int = 24
    admin_session_minutes: int = 30
    auth_rate_limit_attempts: int = 10
    auth_rate_limit_window_seconds: int = 60
    auth_rate_limit_lockout_seconds: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator(
        "port",
        "firebird_port",
        "sqlite_busy_timeout_ms",
        "query_max_concurrency_global",
        "query_max_concurrency_per_token",
        "query_acquire_timeout_ms",
        "query_timeout_seconds",
        "query_timeout_max_seconds",
        "query_max_rows_hard",
        "query_fetch_chunk_size",
        "query_history_size",
        "firebird_pool_size",
        "firebird_max_overflow",
        "max_request_body_bytes",
        "idempotency_ttl_hours",
        "admin_session_minutes",
        "auth_rate_limit_attempts",
        "auth_rate_limit_window_seconds",
        "auth_rate_limit_lockout_seconds",
    )
    @classmethod
    def positive_integer(cls, value: int, info):
        if int(value) <= 0:
            raise ValueError(f"{info.field_name} deve ser maior que zero")
        return int(value)

    @field_validator("admin_api_key")
    @classmethod
    def reject_example_admin_key(cls, value: str) -> str:
        if value == INVALID_ADMIN_API_KEY:
            raise ValueError("ADMIN_API_KEY de exemplo nao pode ser usada")
        return value

    @model_validator(mode="after")
    def validate_timeout_ceiling(self) -> Settings:
        if self.query_timeout_seconds > self.query_timeout_max_seconds:
            raise ValueError(
                "QUERY_TIMEOUT_SECONDS nao pode exceder QUERY_TIMEOUT_MAX_SECONDS"
            )
        return self

    @property
    def sqlite_path(self) -> Path:
        return Path(self.sqlite_db_path).expanduser().resolve()

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_allowed_origins.split(",") if item.strip()]

    @property
    def firebird_configured(self) -> bool:
        return bool(self.firebird_user and self.firebird_password and self.firebird_db_path)


@lru_cache
def get_settings() -> Settings:
    return Settings()
