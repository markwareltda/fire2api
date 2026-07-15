from __future__ import annotations

import hashlib
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from ..core.access_key_service import AccessKeyService
from ..core.audit import record_admin_audit
from ..core.dynamic_loader import dynamic_loader
from ..core.execution_service import execution_service
from ..core.query_service import QueryService
from ..schemas.access_key import AccessKeyCreateSchema, AccessKeyUpdateSchema
from ..schemas.query import (
    ParameterCreateSchema,
    ParameterReorderSchema,
    ParameterUpdateSchema,
    QueryCreateSchema,
    QueryTestSchema,
    QueryValidateSchema,
)
from ..utils.response import error_json_response, success_response

logger = logging.getLogger(__name__)
router = APIRouter()


def _audit(
    request: Request, action: str, resource_type: str, resource_id=None, outcome="success"
) -> None:
    remote = request.client.host if request.client else "unknown"
    remote_hash = "sha256:" + hashlib.sha256(remote.encode()).hexdigest()
    record_admin_audit(
        action,
        resource_type,
        resource_id,
        request_id=getattr(request.state, "request_id", None),
        remote_addr_hash=remote_hash,
        outcome=outcome,
    )


def _reload(request: Request) -> int:
    return dynamic_loader.reload_app(request.app)


@router.post("/routes/refresh")
def refresh_routes(request: Request):
    count = _reload(request)
    _audit(request, "routes.refresh", "route")
    return success_response({"routes_reloaded": count}, "Rotas recarregadas com sucesso")


@router.post("/query/validate")
def validate_query(payload: QueryValidateSchema):
    validation = QueryService.validate_query(payload.query_sql, payload.method)
    if not validation["valid"]:
        return error_json_response(422, "Query invalida", [{"detail": validation["error"]}])
    return success_response(validation, "Query valida")


@router.get("/query")
def list_queries():
    rows = QueryService.get_all_queries()
    return success_response(rows, "Queries obtidas com sucesso", {"count": len(rows)})


@router.post("/query")
def create_query(payload: QueryCreateSchema, request: Request):
    try:
        query_id = QueryService.create_query(payload.model_dump())
        _reload(request)
        _audit(request, "query.create", "query", query_id)
        return success_response(
            QueryService.get_query_by_id(query_id),
            "Query criada com sucesso",
            {"query_id": query_id},
        )
    except ValueError as exc:
        return error_json_response(422, "Query invalida", [{"detail": str(exc)}])
    except sqlite3.IntegrityError:
        return error_json_response(409, "Ja existe uma rota com este caminho e metodo")


@router.get("/query/{query_id}")
def get_query(query_id: int):
    row = QueryService.get_query_by_id(query_id)
    if row is None:
        raise HTTPException(404, "Query nao encontrada")
    return success_response(row, "Query obtida com sucesso", {"query_id": query_id})


@router.put("/query/{query_id}")
def update_query(query_id: int, payload: QueryCreateSchema, request: Request):
    if QueryService.get_query_by_id(query_id) is None:
        raise HTTPException(404, "Query nao encontrada")
    try:
        QueryService.update_query(query_id, payload.model_dump())
        _reload(request)
        _audit(request, "query.update", "query", query_id)
        return success_response(
            QueryService.get_query_by_id(query_id),
            "Query atualizada com sucesso",
            {"query_id": query_id},
        )
    except ValueError as exc:
        return error_json_response(422, "Query invalida", [{"detail": str(exc)}])
    except sqlite3.IntegrityError:
        return error_json_response(409, "Ja existe uma rota com este caminho e metodo")


@router.delete("/query/{query_id}")
def delete_query(query_id: int, request: Request):
    if not QueryService.delete_query(query_id):
        raise HTTPException(404, "Query nao encontrada")
    _reload(request)
    _audit(request, "query.delete", "query", query_id)
    return success_response({"id": query_id}, "Query removida fisicamente")


@router.post("/query/{query_id}/test")
async def test_query(query_id: int, payload: QueryTestSchema, request: Request):
    query = QueryService.get_query_by_id(query_id)
    if query is None:
        raise HTTPException(404, "Query nao encontrada")
    try:
        params, options = QueryService.bind_parameters(
            query["parameters"],
            {"path": payload.path, "query": payload.query, "body": payload.body},
        )
        if query["method"] == "GET":
            QueryService.apply_query_options(
                query["query_sql"],
                params,
                options,
                execution_service.settings.query_max_rows_hard,
            )
        execution_id, result = await execution_service.execute(
            owner_hash=request.state.owner_hash,
            route_type="admin_test",
            route_ref=query["route_path"],
            http_method=query["method"],
            query_sql=query["query_sql"],
            params=params,
            options=options,
            request=request,
            rollback=True,
        )
        _audit(request, "query.test", "query", query_id)
        return success_response(
            {"rows": result.rows, "affected_rows": result.affected_rows, "rolled_back": True},
            "Teste executado com rollback",
            {"execution_id": execution_id},
        )
    except ValueError as exc:
        return error_json_response(422, "Parametros invalidos", [{"detail": str(exc)}])
    except Exception as exc:
        logger.error(
            "Teste de query falhou query_id=%s error_type=%s",
            query_id,
            type(exc).__name__,
        )
        return error_json_response(500, "Falha ao testar query")


@router.get("/query/{query_id}/parameter")
def list_parameters(query_id: int):
    if QueryService.get_query_by_id(query_id) is None:
        raise HTTPException(404, "Query nao encontrada")
    rows = QueryService.get_query_parameters(query_id)
    return success_response(
        rows, "Parametros obtidos com sucesso", {"count": len(rows), "query_id": query_id}
    )


@router.post("/query/{query_id}/parameter")
def add_parameter(query_id: int, payload: ParameterCreateSchema, request: Request):
    if QueryService.get_query_by_id(query_id) is None:
        raise HTTPException(404, "Query nao encontrada")
    try:
        data = payload.model_dump()
        data["query_id"] = query_id
        parameter_id = QueryService.add_parameter(data)
        _reload(request)
        _audit(request, "parameter.create", "parameter", parameter_id)
        return success_response(
            QueryService.get_parameter_by_id(parameter_id),
            "Parametro criado com sucesso",
            {"parameter_id": parameter_id},
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        return error_json_response(422, "Parametro invalido", [{"detail": str(exc)}])


@router.put("/query/{query_id}/parameter/reorder")
def reorder_parameters(query_id: int, payload: ParameterReorderSchema, request: Request):
    if not QueryService.reorder_parameters(query_id, payload.parameter_ids):
        return error_json_response(422, "Lista de parametros invalida")
    _reload(request)
    _audit(request, "parameter.reorder", "query", query_id)
    rows = QueryService.get_query_parameters(query_id)
    return success_response(rows, "Parametros reordenados", {"count": len(rows)})


@router.put("/query/{query_id}/parameter/{parameter_id}")
def update_parameter(
    query_id: int,
    parameter_id: int,
    payload: ParameterUpdateSchema,
    request: Request,
):
    current = QueryService.get_parameter_by_id(parameter_id)
    if current is None or int(current["query_id"]) != query_id:
        raise HTTPException(404, "Parametro nao encontrado")
    try:
        QueryService.update_parameter(parameter_id, payload.model_dump())
        _reload(request)
        _audit(request, "parameter.update", "parameter", parameter_id)
        return success_response(
            QueryService.get_parameter_by_id(parameter_id), "Parametro atualizado"
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        return error_json_response(422, "Parametro invalido", [{"detail": str(exc)}])


@router.delete("/query/{query_id}/parameter/{parameter_id}")
def delete_parameter(query_id: int, parameter_id: int, request: Request):
    current = QueryService.get_parameter_by_id(parameter_id)
    if current is None or int(current["query_id"]) != query_id:
        raise HTTPException(404, "Parametro nao encontrado")
    QueryService.delete_parameter(parameter_id)
    _reload(request)
    _audit(request, "parameter.delete", "parameter", parameter_id)
    return success_response({"id": parameter_id}, "Parametro removido fisicamente")


@router.get("/access-key")
def list_access_keys():
    rows = AccessKeyService.list_keys()
    return success_response(rows, "Access Keys obtidas com sucesso", {"count": len(rows)})


@router.post("/access-key")
def create_access_key(payload: AccessKeyCreateSchema, request: Request):
    try:
        created = AccessKeyService.create_key(**payload.model_dump())
        _audit(request, "access_key.create", "access_key", created["id"])
        return success_response(
            created, "Access Key criada; copie a chave agora", {"access_key_id": created["id"]}
        )
    except ValueError as exc:
        return error_json_response(422, "Access Key invalida", [{"detail": str(exc)}])


@router.put("/access-key/{key_id}")
def update_access_key(key_id: int, payload: AccessKeyUpdateSchema, request: Request):
    if not AccessKeyService.update_key(key_id, **payload.model_dump()):
        raise HTTPException(404, "Access Key nao encontrada")
    _audit(request, "access_key.update", "access_key", key_id)
    return success_response(AccessKeyService.get_key_by_id(key_id), "Access Key atualizada")


@router.delete("/access-key/{key_id}")
def delete_access_key(key_id: int, request: Request):
    if not AccessKeyService.delete_key(key_id):
        raise HTTPException(404, "Access Key nao encontrada")
    _audit(request, "access_key.delete", "access_key", key_id)
    return success_response({"id": key_id}, "Access Key removida fisicamente")


@router.get("/executions")
def list_executions(limit: int = 200):
    rows = execution_service.list_executions("admin", is_admin=True, limit=limit)
    return success_response(rows, "Execucoes obtidas com sucesso", {"count": len(rows)})


@router.post("/executions/{execution_id}/cancel")
def cancel_execution(execution_id: str, request: Request):
    result = execution_service.request_cancel(execution_id, owner_hash="admin", is_admin=True)
    if not result["ok"]:
        status = 404 if result["reason"] == "not_found" else 409
        return error_json_response(status, "Nao foi possivel cancelar a execucao")
    _audit(request, "execution.cancel", "execution", execution_id)
    return success_response(result, "Cancelamento solicitado")
