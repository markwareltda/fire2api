from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core.access_key_service import AccessKeyService
from app.core.query_service import QueryExecutionResult
from app.main import app
from tests.conftest import TEST_ADMIN_KEY


def test_public_system_admin_auth_and_no_report_routes():
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/admin").status_code == 200
        assert client.get("/api/base/admin/query").status_code == 401
        response = client.get(
            "/api/base/admin/query", headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"}
        )
        assert response.status_code == 200
        assert client.get("/api/base/report/queries").status_code == 404
        schema = client.get("/openapi.json").json()
        assert all("/api/base/admin" not in path for path in schema["paths"])
        assert all("/api/base/report" not in path for path in schema["paths"])


def test_admin_crud_supports_same_path_with_different_methods():
    headers = {"Authorization": f"Bearer {TEST_ADMIN_KEY}"}
    with TestClient(app) as client:
        get_payload = {
            "route_path": "/customers",
            "method": "GET",
            "query_sql": "select id from customer",
            "description": "list",
            "tags": "customer",
            "is_active": True,
        }
        post_payload = {
            **get_payload,
            "method": "POST",
            "query_sql": "insert into customer(name) values (:name)",
        }
        first = client.post("/api/base/admin/query", headers=headers, json=get_payload)
        second = client.post("/api/base/admin/query", headers=headers, json=post_payload)
        assert first.status_code == second.status_code == 200
        post_id = second.json()["data"]["id"]
        parameter = client.post(
            f"/api/base/admin/query/{post_id}/parameter",
            headers=headers,
            json={"name": "name", "source": "body", "required": True},
        )
        assert parameter.status_code == 200
        duplicate = client.post("/api/base/admin/query", headers=headers, json=get_payload)
        assert duplicate.status_code == 409
        schema = client.get("/openapi.json").json()
        assert {"get", "post"}.issubset(schema["paths"]["/api/customers"])
        operations = schema["paths"]["/api/customers"]
        serialized = json.dumps(operations)
        assert "_query" not in serialized
        assert "_definitions" not in serialized
        assert "query_sql" not in serialized
        assert "select id from customer" not in serialized
        assert operations["get"]["operationId"].startswith("fire2api_")
        assert {item["name"] for item in operations["get"]["parameters"]} == {
            "LIMIT",
            "OFFSET",
            "ORDER_BY",
        }
        assert not operations["post"].get("parameters")


def test_dynamic_read_write_body_validation_and_idempotency(monkeypatch):
    admin_headers = {"Authorization": f"Bearer {TEST_ADMIN_KEY}"}
    consumer = AccessKeyService.create_key(description="http integration")
    consumer_headers = {"Authorization": f"Bearer {consumer['plain_key']}"}
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs)
        method = kwargs["http_method"]
        result = QueryExecutionResult(
            rows=[{"ID": kwargs["params"].get("ID", 7)}],
            affected_rows=1,
            statement_type="select" if method == "GET" else "insert",
        )
        return f"execution-{len(calls)}", result

    monkeypatch.setattr("app.core.dynamic_loader.execution_service.execute", fake_execute)
    with TestClient(app) as client:
        path_query = client.post(
            "/api/base/admin/query",
            headers=admin_headers,
            json={
                "route_path": "/customer/{id}",
                "method": "GET",
                "query_sql": "select :id as id from rdb$database",
                "is_active": True,
            },
        ).json()["data"]
        parameter = client.post(
            f"/api/base/admin/query/{path_query['id']}/parameter",
            headers=admin_headers,
            json={"name": "id", "param_type": "integer", "source": "path", "required": True},
        )
        assert parameter.status_code == 200

        created = client.post(
            "/api/base/admin/query",
            headers=admin_headers,
            json={
                "route_path": "/customer",
                "method": "POST",
                "query_sql": "insert into customer(name) values (:name) returning id",
                "is_active": True,
            },
        ).json()["data"]
        body_parameter = client.post(
            f"/api/base/admin/query/{created['id']}/parameter",
            headers=admin_headers,
            json={"name": "name", "param_type": "string", "source": "body", "required": True},
        )
        assert body_parameter.status_code == 200

        read = client.get("/api/customer/42", headers=consumer_headers)
        assert read.status_code == 200
        assert read.json()["data"] == [{"ID": 42}]
        assert read.json()["meta"]["count"] == 1
        invalid_order = client.get(
            "/api/customer/42?ORDER_BY=ID%3BDELETE", headers=consumer_headers
        )
        invalid_limit = client.get("/api/customer/42?LIMIT=abc", headers=consumer_headers)
        assert invalid_order.status_code == invalid_limit.status_code == 422
        assert len(calls) == 1

        write_headers = {**consumer_headers, "Idempotency-Key": "create-ada"}
        first = client.post("/api/customer", headers=write_headers, json={"NaMe": "Ada"})
        replay = client.post("/api/customer", headers=write_headers, json={"name": "Ada"})
        assert first.status_code == 200
        assert replay.status_code == 409
        assert first.json()["data"]["affected_rows"] == 1
        assert "ja foi processada" in replay.json()["message"]
        assert len(calls) == 2  # one read and one write; replay did not execute

        conflict = client.post("/api/customer", headers=write_headers, json={"name": "Grace"})
        assert conflict.status_code == 409
        unknown = client.post(
            "/api/customer", headers=consumer_headers, json={"name": "Ada", "role": "admin"}
        )
        assert unknown.status_code == 422

        def fail_after_commit(*args, **kwargs):
            raise RuntimeError("metastore unavailable after Firebird commit")

        monkeypatch.setattr(
            "app.core.dynamic_loader.IdempotencyService.complete", fail_after_commit
        )
        uncertain_headers = {**consumer_headers, "Idempotency-Key": "uncertain-result"}
        uncertain = client.post(
            "/api/customer", headers=uncertain_headers, json={"name": "Linus"}
        )
        blocked_retry = client.post(
            "/api/customer", headers=uncertain_headers, json={"name": "Linus"}
        )
        assert uncertain.status_code == 500
        assert blocked_retry.status_code == 409
        assert "resultado indisponivel" in blocked_retry.json()["message"]
        assert len(calls) == 3  # the uncertain committed write is never executed twice


def test_admin_authentication_is_temporarily_throttled():
    headers = {"Authorization": "Bearer invalid-admin-token-with-at-least-32-characters"}
    with TestClient(app) as client:
        responses = [client.get("/api/base/admin/query", headers=headers) for _ in range(10)]
        assert all(response.status_code == 401 for response in responses[:9])
        assert responses[-1].status_code == 429
        assert responses[-1].headers["Retry-After"]
        assert client.get("/api/base/admin/query", headers=headers).status_code == 429


def test_admin_parameter_key_and_execution_endpoints(monkeypatch):
    headers = {"Authorization": f"Bearer {TEST_ADMIN_KEY}"}

    async def fake_test(**kwargs):
        return "test-execution", QueryExecutionResult([{"OK": 1}], 1, "update")

    monkeypatch.setattr("app.admin.router.execution_service.execute", fake_test)
    with TestClient(app) as client:
        validation = client.post(
            "/api/base/admin/query/validate",
            headers=headers,
            json={"method": "PATCH", "query_sql": "update item set name=:name where id=:id"},
        )
        assert validation.status_code == 200
        invalid = client.post(
            "/api/base/admin/query/validate",
            headers=headers,
            json={"method": "GET", "query_sql": "delete from item"},
        )
        assert invalid.status_code == 422

        query = client.post(
            "/api/base/admin/query",
            headers=headers,
            json={
                "route_path": "/admin-flow",
                "method": "PATCH",
                "query_sql": "update item set name=:name where id=:id",
                "is_active": False,
            },
        ).json()["data"]
        first = client.post(
            f"/api/base/admin/query/{query['id']}/parameter",
            headers=headers,
            json={"name": "id", "param_type": "integer", "source": "body", "required": True},
        ).json()["data"]
        second = client.post(
            f"/api/base/admin/query/{query['id']}/parameter",
            headers=headers,
            json={"name": "name", "param_type": "string", "source": "body", "required": True},
        ).json()["data"]
        updated = client.put(
            f"/api/base/admin/query/{query['id']}/parameter/{first['id']}",
            headers=headers,
            json={
                "name": "id",
                "param_type": "integer",
                "source": "body",
                "required": True,
                "position": 2,
            },
        )
        assert updated.status_code == 200
        reordered = client.put(
            f"/api/base/admin/query/{query['id']}/parameter/reorder",
            headers=headers,
            json={"parameter_ids": [second["id"], first["id"]]},
        )
        assert reordered.status_code == 200
        tested = client.post(
            f"/api/base/admin/query/{query['id']}/test",
            headers=headers,
            json={"body": {"id": 1, "name": "new"}},
        )
        assert tested.status_code == 200
        assert tested.json()["data"]["rolled_back"] is True
        assert (
            client.delete(
                f"/api/base/admin/query/{query['id']}/parameter/{first['id']}", headers=headers
            ).status_code
            == 200
        )

        key_response = client.post(
            "/api/base/admin/access-key",
            headers=headers,
            json={"description": "admin flow", "is_active": True},
        )
        key = key_response.json()["data"]
        assert key["plain_key"]
        assert (
            client.put(
                f"/api/base/admin/access-key/{key['id']}",
                headers=headers,
                json={"description": "disabled", "is_active": False},
            ).status_code
            == 200
        )
        assert (
            client.delete(f"/api/base/admin/access-key/{key['id']}", headers=headers).status_code
            == 200
        )
        assert client.get("/api/base/admin/executions", headers=headers).status_code == 200
        assert client.post("/api/base/admin/routes/refresh", headers=headers).status_code == 200
        assert (
            client.delete(f"/api/base/admin/query/{query['id']}", headers=headers).status_code
            == 200
        )
