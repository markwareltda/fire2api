from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HTTPMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
ParameterSource = Literal["path", "query", "body"]
ParameterType = Literal["string", "integer", "float", "boolean", "date", "datetime"]


class QueryCreateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_path: str = Field(min_length=2, max_length=500)
    method: HTTPMethod = "GET"
    query_sql: str = Field(min_length=1)
    description: str = Field(default="", max_length=2000)
    tags: str = Field(default="", max_length=500)
    is_active: bool = True

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        return str(value).upper()

    @field_validator("route_path")
    @classmethod
    def validate_route_path(cls, value: str) -> str:
        value = value.strip()
        value = "".join(
            part if part.startswith("{") else part.lower()
            for part in re.split(r"(\{[a-zA-Z_][a-zA-Z0-9_]*\})", value)
        )
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("route_path deve iniciar com / e nao terminar com /")
        if value == "/base" or value.startswith("/base/"):
            raise ValueError("/api/base e reservado pelo sistema")
        if not re.fullmatch(r"/[a-zA-Z0-9_/{}/-]+", value):
            raise ValueError("route_path contem caracteres invalidos")
        names = re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", value)
        if len(names) != value.count("{") or len(names) != value.count("}"):
            raise ValueError("parametro de path invalido")
        if len(names) != len({name.upper() for name in names}):
            raise ValueError("parametro de path duplicado")
        return value


class QueryValidateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: HTTPMethod
    query_sql: str = Field(min_length=1)

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        return str(value).upper()


class QueryTestSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: dict[str, object] = Field(default_factory=dict)
    query: dict[str, object] = Field(default_factory=dict)
    body: dict[str, object] = Field(default_factory=dict)


class ParameterCreateSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    param_type: ParameterType = "string"
    source: ParameterSource = "query"
    default_value: str | None = None
    required: bool = False
    description: str | None = Field(default=None, max_length=1000)
    validation_rule: str | None = Field(default=None, max_length=500)
    position: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", value):
            raise ValueError("nome de parametro invalido")
        return value


class ParameterUpdateSchema(ParameterCreateSchema):
    pass


class ParameterReorderSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameter_ids: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        if len(self.parameter_ids) != len(set(self.parameter_ids)):
            raise ValueError("parameter_ids nao pode conter duplicatas")
        if any(item <= 0 for item in self.parameter_ids):
            raise ValueError("parameter_id invalido")
        return self
