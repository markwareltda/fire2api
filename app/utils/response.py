from typing import Any

from fastapi.responses import JSONResponse


def success_response(
    data: Any = None, message: str = "Success", meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Cria uma resposta de sucesso padronizada"""
    return {
        "success": True,
        "message": message,
        "data": data if data is not None else [],
        "meta": meta or {},
    }


def error_response(
    message: str = "Error",
    errors: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cria uma resposta de erro padronizada"""
    return {
        "success": False,
        "message": message,
        "data": [],
        "errors": errors or [{"message": message}],
        "meta": meta or {},
    }


def error_json_response(
    status_code: int,
    message: str = "Error",
    errors: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_response(message=message, errors=errors, meta=meta),
    )
