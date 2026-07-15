from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.routing import Mount

from ..utils.response import error_response, success_response
from .execution_service import (
    ExecutionCanceledError,
    ExecutionCapacityError,
    ExecutionTimeoutError,
    execution_service,
)
from .idempotency import IdempotencyConflict, IdempotencyService
from .query_service import QueryService

logger = logging.getLogger(__name__)


class DynamicRouteLoader:
    def __init__(self) -> None:
        self.router = APIRouter()
        self.loaded_routes: set[tuple[str, str]] = set()
        self._lock = threading.RLock()

    def load_routes(self) -> int:
        router = APIRouter()
        loaded: set[tuple[str, str]] = set()
        for query in QueryService.get_all_queries(active_only=True):
            try:
                self._validate_configuration(query)
                self._add_route(router, query)
                loaded.add((query["route_path"], query["method"]))
            except Exception as exc:
                logger.error(
                    "Rota dinamica ignorada id=%s error_type=%s",
                    query.get("id"),
                    type(exc).__name__,
                )
        with self._lock:
            self.router = router
            self.loaded_routes = loaded
        return len(loaded)

    @staticmethod
    def _validate_configuration(query: dict[str, Any]) -> None:
        QueryService.validate_parameter_configuration(
            query["route_path"],
            query["query_sql"],
            query["method"],
            query["parameters"],
        )

    def _add_route(self, router: APIRouter, query: dict[str, Any]) -> None:
        method = query["method"]
        definitions = query["parameters"]

        async def endpoint(request: Request):
            body: dict[str, Any] = {}
            if method != "GET":
                raw = await request.body()
                if raw:
                    try:
                        parsed = await request.json()
                    except Exception:
                        return JSONResponse(
                            error_response("Body deve ser JSON valido"), status_code=400
                        )
                    if not isinstance(parsed, dict):
                        return JSONResponse(
                            error_response("Body deve ser um objeto JSON plano"), status_code=422
                        )
                    if any(isinstance(value, (dict, list)) for value in parsed.values()):
                        return JSONResponse(
                            error_response("Body deve conter somente valores JSON escalares"),
                            status_code=422,
                        )
                    body = parsed
            sources = {
                "path": dict(request.path_params),
                "query": dict(request.query_params),
                "body": body,
            }
            try:
                params, options = QueryService.bind_parameters(definitions, sources)
                if method == "GET":
                    QueryService.apply_query_options(
                        query["query_sql"],
                        params,
                        options,
                        execution_service.settings.query_max_rows_hard,
                    )
            except ValueError as exc:
                return JSONResponse(error_response(str(exc)), status_code=422)

            idempotency = None
            idempotency_key = request.headers.get("Idempotency-Key")
            if method != "GET" and idempotency_key:
                try:
                    idempotency = IdempotencyService.begin(
                        access_key_id=request.state.access_key_id,
                        method=method,
                        route_path=query["route_path"],
                        idempotency_key=idempotency_key,
                        request_hash=IdempotencyService.request_hash(
                            {"params": params, "options": options}
                        ),
                    )
                except (IdempotencyConflict, ValueError) as exc:
                    return JSONResponse(error_response(str(exc)), status_code=409)
            execution_succeeded = False
            try:
                execution_id, result = await execution_service.execute(
                    owner_hash=request.state.owner_hash,
                    route_type="api",
                    route_ref=query["route_path"],
                    http_method=method,
                    query_sql=query["query_sql"],
                    params=params,
                    options=options,
                    request=request,
                )
                execution_succeeded = True
                data: Any = (
                    result.rows
                    if method == "GET"
                    else {
                        "rows": result.rows,
                        "affected_rows": result.affected_rows,
                    }
                )
                payload = success_response(
                    data=data,
                    message="Consulta executada com sucesso",
                    meta={
                        "execution_id": execution_id,
                        **({"count": len(result.rows)} if method == "GET" else {}),
                    },
                )
                payload = jsonable_encoder(payload)
                if idempotency:
                    IdempotencyService.complete(idempotency.record_id, payload, execution_id)
                return JSONResponse(payload, status_code=200)
            except ExecutionCapacityError as exc:
                response = JSONResponse(
                    error_response(
                        "Capacidade de execucao esgotada", meta={"execution_id": exc.execution_id}
                    ),
                    status_code=429,
                )
            except ExecutionTimeoutError as exc:
                response = JSONResponse(
                    error_response(
                        "Tempo limite de execucao excedido", meta={"execution_id": exc.execution_id}
                    ),
                    status_code=504,
                )
            except ExecutionCanceledError as exc:
                response = JSONResponse(
                    error_response("Execucao cancelada", meta={"execution_id": exc.execution_id}),
                    status_code=409,
                )
            except Exception as exc:
                logger.error(
                    "Falha na rota dinamica query_id=%s error_type=%s",
                    query["id"],
                    type(exc).__name__,
                )
                response = JSONResponse(
                    error_response("Erro ao executar consulta"), status_code=500
                )
            if idempotency and not execution_succeeded:
                IdempotencyService.abandon(idempotency.record_id)
            return response

        openapi_extra = self._openapi(definitions, method)
        router.add_api_route(
            query["route_path"],
            endpoint,
            methods=[method],
            name=f"dynamic_{query['id']}_{method.lower()}",
            operation_id=f"fire2api_{query['id']}_{method.lower()}",
            summary=query.get("description") or f"{method} {query['route_path']}",
            tags=[
                tag.strip() for tag in (query.get("tags") or "Fire2API").split(",") if tag.strip()
            ],
            openapi_extra=openapi_extra,
        )

    @staticmethod
    def _openapi(definitions: list[dict[str, Any]], method: str) -> dict[str, Any]:
        type_map = {
            "string": {"type": "string"},
            "integer": {"type": "integer"},
            "float": {"type": "number"},
            "boolean": {"type": "boolean"},
            "date": {"type": "string", "format": "date"},
            "datetime": {"type": "string", "format": "date-time"},
        }
        parameters = []
        body_properties = {}
        body_required = []
        for item in definitions:
            field_schema = type_map[item["param_type"]]
            if item["source"] == "body":
                body_properties[item["name"]] = field_schema
                if item["required"]:
                    body_required.append(item["name"])
            else:
                parameters.append(
                    {
                        "name": item["name"],
                        "in": item["source"],
                        "required": True if item["source"] == "path" else bool(item["required"]),
                        "description": item.get("description") or "",
                        "schema": field_schema,
                    }
                )
        if method == "GET":
            parameters.extend(
                [
                    {
                        "name": "LIMIT",
                        "in": "query",
                        "required": False,
                        "description": (
                            "Máximo de linhas retornadas, limitado por "
                            "QUERY_MAX_ROWS_HARD."
                        ),
                        "schema": {"type": "integer", "minimum": 1},
                    },
                    {
                        "name": "OFFSET",
                        "in": "query",
                        "required": False,
                        "description": (
                            "Quantidade de linhas ignoradas antes do primeiro resultado."
                        ),
                        "schema": {"type": "integer", "minimum": 0},
                    },
                    {
                        "name": "ORDER_BY",
                        "in": "query",
                        "required": False,
                        "description": "Ordenação segura, por exemplo: nome ASC, id DESC.",
                        "schema": {"type": "string"},
                    },
                ]
            )
        extra: dict[str, Any] = {
            "parameters": parameters,
            "security": [{"BearerAuth": []}],
        }
        if body_properties:
            body_schema: dict[str, Any] = {
                "type": "object",
                "properties": body_properties,
                "additionalProperties": False,
            }
            if body_required:
                body_schema["required"] = body_required
            extra["requestBody"] = {
                "required": bool(body_required),
                "content": {"application/json": {"schema": body_schema}},
            }
        return extra

    def apply_to_app(self, app: FastAPI, api_prefix: str = "/api", **_ignored) -> int:
        with self._lock:
            kept = []
            for route in app.router.routes:
                original_router = getattr(route, "original_router", None)
                if getattr(original_router, "_fire2api_dynamic", False):
                    continue
                if isinstance(route, APIRoute) and str(route.name).startswith("dynamic_"):
                    continue
                kept.append(route)
            app.router.routes = kept
            self.router._fire2api_dynamic = True  # type: ignore[attr-defined]
            previous_count = len(app.router.routes)
            app.include_router(self.router, prefix=api_prefix)
            added = app.router.routes[previous_count:]
            base = app.router.routes[:previous_count]
            insertion = next(
                (
                    index
                    for index, route in enumerate(base)
                    if isinstance(route, Mount) and route.path in ("", "/")
                ),
                len(base),
            )
            app.router.routes = base[:insertion] + added + base[insertion:]
            app.openapi_schema = None
            return len(self.loaded_routes)

    def reload_app(self, app: FastAPI) -> int:
        self.load_routes()
        return self.apply_to_app(app)


dynamic_loader = DynamicRouteLoader()
