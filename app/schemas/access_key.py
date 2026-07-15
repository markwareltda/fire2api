from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AccessKeyCreateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(default="", max_length=200)
    is_active: bool = True
    plain_key: str | None = Field(default=None, max_length=512)

    @field_validator("plain_key")
    @classmethod
    def validate_plain_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) < 32:
            raise ValueError("plain_key manual deve ter ao menos 32 caracteres")
        return value


class AccessKeyUpdateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(default="", max_length=200)
    is_active: bool = True
