from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine, text

from app.core.dynamic_loader import DynamicRouteLoader
from app.core.query_service import QueryService
from app.schemas.query import QueryCreateSchema


@pytest.mark.parametrize(
    ("method", "sql", "statement_type"),
    [
        ("GET", "select 'Mixed Case' as value from rdb$database", "select"),
        ("GET", "with x as (select 1 n from rdb$database) select n from x", "select"),
        ("POST", "insert into customer(name) values (:name) returning id", "insert"),
        ("POST", "execute procedure create_customer(:name)", "procedure"),
        ("PUT", "update customer set name=:name where id=:id", "update"),
        ("PATCH", "update or insert into customer(id) values(:id) matching(id)", "upsert"),
        ("DELETE", "delete from customer where id=:id", "delete"),
    ],
)
def test_sql_policy_accepts_matching_statements(method, sql, statement_type):
    result = QueryService.validate_query(sql, method)
    assert result == {"valid": True, "error": None, "statement_type": statement_type}


@pytest.mark.parametrize(
    ("method", "sql"),
    [
        ("GET", "delete from customer"),
        ("POST", "update customer set name='x'"),
        ("DELETE", "drop table customer"),
        ("POST", "execute block as begin end"),
        ("GET", "select 1 from rdb$database; delete from customer"),
        ("PUT", "set transaction read write"),
    ],
)
def test_sql_policy_rejects_dangerous_or_mismatched_statements(method, sql):
    assert QueryService.validate_query(sql, method)["valid"] is False


def test_sql_is_not_mutated_when_registered():
    from app.core.migrations import upgrade_metastore

    upgrade_metastore()
    sql = "select 'Customer McCloud' as DisplayName from rdb$database"
    query_id = QueryService.create_query(
        {"route_path": "/case-test", "method": "GET", "query_sql": sql}
    )
    assert QueryService.get_query_by_id(query_id)["query_sql"] == sql


def test_parameter_sources_conversion_and_unknown_fields():
    definitions = [
        {"name": "id", "source": "path", "param_type": "integer", "required": True},
        {
            "name": "active",
            "source": "query",
            "param_type": "boolean",
            "required": False,
            "default_value": "true",
        },
        {"name": "name", "source": "body", "param_type": "string", "required": True},
    ]
    params, options = QueryService.bind_parameters(
        definitions,
        {"path": {"iD": "42"}, "query": {"limit": "25"}, "body": {"NaMe": "Ada"}},
    )
    assert params == {"ID": 42, "ACTIVE": True, "NAME": "Ada"}
    assert options["limit"] == "25"
    with pytest.raises(ValueError, match="Campos desconhecidos"):
        QueryService.bind_parameters(
            definitions,
            {"path": {"id": "42"}, "query": {}, "body": {"name": "Ada", "role": "admin"}},
        )


def test_parameter_detection_ignores_literals_comments_reserved_and_duplicates():
    detected = QueryService.detect_parameters(
        "/customer/{customer_id}/{customer_id}",
        """
        select :customer_id, :status, ':ignored', :status
        from customer
        where note = 'value:also_ignored'
        -- :commented
        /* :block_comment */
        rows :LIMIT
        """,
        "GET",
    )
    assert detected == [
        {
            "name": "CUSTOMER_ID",
            "source": "path",
            "param_type": "string",
            "required": True,
        },
        {
            "name": "STATUS",
            "source": "query",
            "param_type": "string",
            "required": True,
        },
    ]


def test_parameter_detection_uses_body_for_writes():
    assert QueryService.detect_parameters(
        "/customer/{id}", "update customer set name=:name where id=:id", "PATCH"
    ) == [
        {"name": "ID", "source": "path", "param_type": "string", "required": True},
        {"name": "NAME", "source": "body", "param_type": "string", "required": True},
    ]


def test_parameter_draft_merge_preserves_configuration_and_suppression():
    existing = [
        {
            "id": 7,
            "name": "id",
            "source": "query",
            "param_type": "integer",
            "required": False,
        },
        {
            "id": 8,
            "name": "legacy",
            "source": "query",
            "param_type": "boolean",
            "required": False,
        },
    ]
    detected = QueryService.detect_parameters(
        "/customer/{id}", "select :id, :name from rdb$database", "GET"
    )
    merged, suppressed = QueryService.merge_parameter_drafts(
        existing, detected, {"name", "removed"}
    )
    by_name = {item["name"]: item for item in merged}
    assert set(by_name) == {"ID", "LEGACY"}
    assert by_name["ID"]["id"] == 7
    assert by_name["ID"]["param_type"] == "integer"
    assert by_name["ID"]["source"] == "path"
    assert by_name["ID"]["required"] is True
    assert by_name["LEGACY"]["detected"] is False
    assert suppressed == {"NAME"}

    restored, suppressed = QueryService.merge_parameter_drafts(merged, detected, set())
    assert [item["name"] for item in restored] == ["ID", "LEGACY", "NAME"]
    assert suppressed == set()


def test_atomic_query_configuration_create_update_and_delete_parameters():
    query_id = QueryService.save_query_configuration(
        None,
        {
            "route_path": "/atomic/{id}",
            "method": "GET",
            "query_sql": "select :id, :filter from rdb$database",
        },
        [
            {
                "name": "id",
                "source": "path",
                "param_type": "integer",
                "required": True,
            },
            {
                "name": "filter",
                "source": "query",
                "param_type": "string",
                "required": False,
                "default_value": "all",
            },
        ],
    )
    created = QueryService.get_query_by_id(query_id)
    assert created is not None
    assert [item["name"] for item in created["parameters"]] == ["ID", "FILTER"]
    original_id = created["parameters"][0]["id"]

    QueryService.save_query_configuration(
        query_id,
        {
            "route_path": "/atomic/{id}",
            "method": "GET",
            "query_sql": "select :id from rdb$database",
            "description": "updated",
        },
        [
            {
                **created["parameters"][0],
                "name": "id",
                "source": "path",
                "param_type": "integer",
                "required": True,
            }
        ],
    )
    updated = QueryService.get_query_by_id(query_id)
    assert updated is not None
    assert updated["description"] == "updated"
    assert len(updated["parameters"]) == 1
    assert updated["parameters"][0]["id"] == original_id


def test_atomic_configuration_validation_rolls_back_and_checks_defaults():
    before = len(QueryService.get_all_queries())
    with pytest.raises(ValueError, match="sem configuracao"):
        QueryService.save_query_configuration(
            None,
            {
                "route_path": "/missing/{id}",
                "method": "GET",
                "query_sql": "select :id from rdb$database",
            },
            [],
        )
    assert len(QueryService.get_all_queries()) == before

    with pytest.raises(ValueError, match="Valor padrao invalido"):
        QueryService.validate_parameter_configuration(
            "/invalid-default",
            "select :page from rdb$database",
            "GET",
            [
                {
                    "name": "page",
                    "source": "query",
                    "param_type": "integer",
                    "required": False,
                    "default_value": "not-a-number",
                }
            ],
        )
    with pytest.raises(ValueError, match="regex"):
        QueryService.validate_parameter_configuration(
            "/invalid-regex-default",
            "select :code from rdb$database",
            "GET",
            [
                {
                    "name": "code",
                    "source": "query",
                    "param_type": "string",
                    "required": False,
                    "default_value": "abc",
                    "validation_rule": r"\d+",
                }
            ],
        )


def test_atomic_configuration_rolls_back_when_sqlite_operation_fails():
    first = QueryService.save_query_configuration(
        None,
        {
            "route_path": "/atomic-conflict-a",
            "method": "GET",
            "query_sql": "select 1 from rdb$database",
        },
        [],
    )
    second = QueryService.save_query_configuration(
        None,
        {
            "route_path": "/atomic-conflict-b",
            "method": "GET",
            "query_sql": "select 2 from rdb$database",
        },
        [],
    )
    with pytest.raises(sqlite3.IntegrityError):
        QueryService.save_query_configuration(
            second,
            {
                "route_path": "/atomic-conflict-a",
                "method": "GET",
                "query_sql": "select 3 from rdb$database",
                "description": "must roll back",
            },
            [],
        )
    assert QueryService.get_query_by_id(first)["query_sql"] == "select 1 from rdb$database"
    unchanged = QueryService.get_query_by_id(second)
    assert unchanged["route_path"] == "/atomic-conflict-b"
    assert unchanged["query_sql"] == "select 2 from rdb$database"


def test_loader_rejects_missing_bind_definition_and_path_case_is_preserved():
    route = QueryCreateSchema(
        route_path="/Case-Sensitive/{CustomerID}",
        method="GET",
        query_sql="select :CustomerID from rdb$database",
    )
    assert route.route_path == "/case-sensitive/{CustomerID}"
    with pytest.raises(ValueError, match="CUSTOMERID"):
        DynamicRouteLoader._validate_configuration(
            {
                **route.model_dump(),
                "parameters": [],
            }
        )
    query_id = QueryService.create_query(route.model_dump())
    QueryService.add_parameter(
        {
            "query_id": query_id,
            "name": "CustomerID",
            "source": "path",
            "required": True,
        }
    )
    saved = QueryService.get_query_by_id(query_id)
    assert saved["route_path"] == "/case-sensitive/{CUSTOMERID}"
    DynamicRouteLoader._validate_configuration(saved)

    with pytest.raises(ValueError, match="duplicado"):
        QueryCreateSchema(
            route_path="/duplicate/{id}/{ID}",
            method="GET",
            query_sql="select :id from rdb$database",
        )


def test_parameter_sync_is_additive_and_preserves_manual_configuration():
    query_id = QueryService.create_query(
        {
            "route_path": "/customer/{id}",
            "method": "PATCH",
            "query_sql": "update customer set name=:name where id=:id",
        }
    )
    stale_id = QueryService.add_parameter(
        {
            "query_id": query_id,
            "name": "legacy",
            "source": "query",
            "param_type": "integer",
            "required": False,
        }
    )
    id_parameter = QueryService.add_parameter(
        {
            "query_id": query_id,
            "name": "id",
            "source": "body",
            "param_type": "integer",
            "required": False,
        }
    )

    result = QueryService.sync_detected_parameters(query_id)
    by_name = {item["name"]: item for item in result["parameters"]}
    assert result["created"] == ["NAME"]
    assert result["updated"] == ["ID"]
    assert result["stale_names"] == ["LEGACY"]
    assert by_name["ID"]["id"] == id_parameter
    assert by_name["ID"]["source"] == "path"
    assert by_name["ID"]["required"] is True
    assert by_name["ID"]["param_type"] == "integer"
    assert by_name["LEGACY"]["id"] == stale_id
    assert by_name["NAME"]["source"] == "body"

    repeated = QueryService.sync_detected_parameters(query_id)
    assert repeated["created"] == []
    assert repeated["updated"] == []


def test_order_by_and_pagination_are_strict():
    sql, params = QueryService.apply_query_options(
        "select id from customer",
        {},
        {"order_by": "name DESC, id ASC", "limit": "10", "offset": "5"},
        100,
    )
    assert "ORDER BY name DESC, id ASC" in sql
    assert "ROWS :__f2a_row_start TO :__f2a_row_end" in sql
    assert params["__f2a_row_start"] == 6
    assert params["__f2a_row_end"] == 15

    nested_first_sql = """
        /* FIRST in this comment is not pagination */
        SELECT item_id
        FROM (
            SELECT (SELECT FIRST 1 code FROM codes ORDER BY id DESC) AS item_id
            FROM items
        ) result
    """
    paged_sql, paged_params = QueryService.apply_query_options(
        nested_first_sql,
        {},
        {"order_by": "item_id ASC", "limit": "50000", "offset": "20000"},
        20000,
    )
    assert "ORDER BY item_id ASC" in paged_sql
    assert "ROWS :__f2a_row_start TO :__f2a_row_end" in paged_sql
    assert paged_params["__f2a_row_start"] == 20001
    assert paged_params["__f2a_row_end"] == 40000

    native_sql = "SELECT FIRST 10 id FROM customer"
    unchanged_sql, unchanged_params = QueryService.apply_query_options(
        native_sql, {}, {"limit": "5", "offset": "2"}, 100
    )
    assert unchanged_sql == native_sql
    assert unchanged_params == {}

    with pytest.raises(ValueError, match="ORDER_BY"):
        QueryService.apply_query_options(
            "select id from customer", {}, {"order_by": "id; drop table customer"}, 100
        )


def test_query_and_parameter_crud_paths():
    query_id = QueryService.create_query(
        {
            "route_path": "/crud",
            "method": "GET",
            "query_sql": "select :value from rdb$database",
            "is_active": True,
        }
    )
    assert QueryService.get_query_by_path("/crud")["id"] == query_id
    assert QueryService.update_query(
        query_id,
        {
            "route_path": "/crud",
            "method": "GET",
            "query_sql": "select :value from rdb$database",
            "description": "updated",
            "is_active": False,
        },
    )
    assert QueryService.get_query_by_path("/crud") is None
    assert QueryService.get_query_by_id(query_id)["description"] == "updated"

    first = QueryService.add_parameter(
        {"query_id": query_id, "name": "value", "param_type": "integer", "source": "query"}
    )
    second = QueryService.add_parameter(
        {"query_id": query_id, "name": "flag", "param_type": "boolean", "source": "query"}
    )
    assert QueryService.update_parameter(
        first,
        {"name": "value", "param_type": "float", "source": "query", "position": 2},
    )
    assert QueryService.reorder_parameters(query_id, [second, first])
    assert not QueryService.reorder_parameters(query_id, [first])
    assert not QueryService.reorder_parameters(query_id, [first, first])
    assert [item["id"] for item in QueryService.get_query_parameters(query_id)] == [second, first]
    assert QueryService.delete_parameter(first)
    assert QueryService.get_parameter_by_id(first) is None
    assert QueryService.delete_query(query_id)
    assert QueryService.get_query_by_id(query_id) is None


@pytest.mark.parametrize(
    ("value", "kind", "expected"),
    [
        ("2", "integer", 2),
        ("2.5", "float", 2.5),
        ("false", "boolean", False),
        ("2026-07-13", "date", "2026-07-13"),
        ("2026-07-13T10:30:00Z", "datetime", "2026-07-13T10:30:00+00:00"),
        (7, "string", "7"),
    ],
)
def test_all_parameter_conversions(value, kind, expected):
    converted = QueryService.convert_value(value, kind)
    assert (
        converted.isoformat() == expected
        if hasattr(converted, "isoformat")
        else converted == expected
    )


@pytest.mark.parametrize(
    ("value", "kind"),
    [(True, "integer"), (False, "float"), ("maybe", "boolean"), ("x", "date"), (1, "unknown")],
)
def test_invalid_parameter_conversions(value, kind):
    with pytest.raises(ValueError):
        QueryService.convert_value(value, kind)


def test_validation_edge_cases_and_parameter_rules():
    assert not QueryService.validate_query("", "GET")["valid"]
    assert not QueryService.validate_query("select (1 from rdb$database", "GET")["valid"]
    assert QueryService.validate_query("select 'drop table x' from rdb$database", "GET")["valid"]
    assert QueryService.validate_query("select '-- not a comment' from rdb$database", "GET")[
        "valid"
    ]
    assert QueryService.validate_query("select '/* not a comment */' from rdb$database", "GET")[
        "valid"
    ]
    assert QueryService.validate_query("/* safe */ select 1 from rdb$database -- end", "GET")[
        "valid"
    ]
    assert not QueryService.validate_query("select 1 from rdb$database", "TRACE")["valid"]
    with pytest.raises(ValueError, match="Tipo"):
        QueryService.add_parameter({"query_id": 1, "name": "x", "param_type": "uuid"})
    with pytest.raises(ValueError, match="Origem"):
        QueryService.add_parameter({"query_id": 1, "name": "x", "source": "cookie"})
    with pytest.raises(ValueError, match="Nome"):
        QueryService.add_parameter({"query_id": 1, "name": "bad-name"})
    with pytest.raises(ValueError, match="expressao regular"):
        QueryService.add_parameter({"query_id": 1, "name": "x", "validation_rule": "["})


def test_execute_query_commit_rollback_read_and_cancel(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'execution.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT)"))
    monkeypatch.setattr("app.core.query_service.get_firebird_engine", lambda: engine)
    original_options = QueryService.apply_query_options
    monkeypatch.setattr(
        QueryService,
        "apply_query_options",
        classmethod(lambda cls, sql, params, options, max_rows: (sql, params)),
    )

    inserted = QueryService.execute_query(
        "insert into item(name) values (:name) returning id, name",
        {"name": "Ada"},
        method="POST",
    )
    assert inserted.rows[0]["name"] == "Ada"
    assert inserted.affected_rows == 1
    rolled_back = QueryService.execute_query(
        "insert into item(name) values (:name) returning id",
        {"name": "Grace"},
        method="POST",
        rollback=True,
    )
    assert rolled_back.rows
    assert rolled_back.affected_rows == 1
    selected = QueryService.execute_query(
        "select id, name from item limit 10",
        method="GET",
    )
    assert [row["name"] for row in selected.rows] == ["Ada"]
    with pytest.raises(QueryService.QueryExecutionCanceledError):
        QueryService.execute_query(
            "select id from item limit 10",
            method="GET",
            is_canceled=lambda: True,
        )
    monkeypatch.setattr(QueryService, "apply_query_options", original_options)
    engine.dispose()
