from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from .database import get_db_connection, get_firebird_engine

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QueryExecutionResult:
    rows: list[dict[str, Any]]
    affected_rows: int
    statement_type: str


class QueryService:
    class QueryExecutionCanceledError(Exception):
        pass

    METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    TYPES = {"string", "integer", "float", "boolean", "date", "datetime"}
    SOURCES = {"path", "query", "body"}
    RESERVED_PARAMETER_NAMES = {"LIMIT", "OFFSET", "ORDER_BY"}
    FORBIDDEN = {
        "CREATE",
        "ALTER",
        "DROP",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT",
        "CONNECT",
        "DISCONNECT",
    }
    _ORDER_ITEM = re.compile(
        r"^[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)?(?:\s+(?:ASC|DESC))?$",
        re.IGNORECASE,
    )

    @staticmethod
    def _bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on", "sim"}:
                return True
            if normalized in {"false", "0", "no", "off", "nao", "não"}:
                return False
        raise ValueError("booleano invalido")

    @staticmethod
    def _normalize_query(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["is_active"] = bool(result["is_active"])
        return result

    @staticmethod
    def _normalize_route_path(route_path: Any) -> str:
        return "".join(
            part.upper() if part.startswith("{") else part.lower()
            for part in re.split(r"(\{[A-Za-z_][A-Za-z0-9_]*\})", str(route_path).strip())
        )

    @staticmethod
    def _canonical_parameter_name(name: Any) -> str:
        return str(name).upper()

    @classmethod
    def get_all_queries(cls, *, active_only: bool = False) -> list[dict[str, Any]]:
        with get_db_connection() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM queries WHERE is_active = 1 ORDER BY route_path, method"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM queries ORDER BY route_path, method").fetchall()
            result = []
            for row in rows:
                query = cls._normalize_query(row)
                query["parameters"] = [
                    cls._normalize_parameter(item)
                    for item in conn.execute(
                        "SELECT * FROM parameters WHERE query_id = ? ORDER BY position, name",
                        (query["id"],),
                    ).fetchall()
                ]
                result.append(query)
        return result

    @classmethod
    def get_query_by_id(cls, query_id: int) -> dict[str, Any] | None:
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM queries WHERE id = ?", (query_id,)).fetchone()
            if row is None:
                return None
            result = cls._normalize_query(row)
            result["parameters"] = [
                cls._normalize_parameter(item)
                for item in conn.execute(
                    "SELECT * FROM parameters WHERE query_id = ? ORDER BY position, name",
                    (query_id,),
                ).fetchall()
            ]
        return result

    @classmethod
    def get_query_by_path(cls, route_path: str, method: str = "GET") -> dict[str, Any] | None:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT id FROM queries WHERE route_path = ? AND method = ? AND is_active = 1",
                (cls._normalize_route_path(route_path), method.upper()),
            ).fetchone()
        return cls.get_query_by_id(int(row["id"])) if row else None

    @classmethod
    def create_query(cls, data: dict[str, Any]) -> int:
        validation = cls.validate_query(data["query_sql"], data.get("method", "GET"))
        if not validation["valid"]:
            raise ValueError(validation["error"])
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO queries
                    (route_path, method, query_sql, description, tags, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    cls._normalize_route_path(data["route_path"]),
                    data.get("method", "GET").upper(),
                    data["query_sql"],
                    data.get("description", ""),
                    data.get("tags", ""),
                    int(data.get("is_active", True)),
                ),
            )
            conn.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite nao retornou o ID da query")
            return int(cursor.lastrowid)

    @classmethod
    def update_query(cls, query_id: int, data: dict[str, Any]) -> bool:
        validation = cls.validate_query(data["query_sql"], data.get("method", "GET"))
        if not validation["valid"]:
            raise ValueError(validation["error"])
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE queries SET route_path = ?, method = ?, query_sql = ?,
                    description = ?, tags = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (
                    cls._normalize_route_path(data["route_path"]),
                    data.get("method", "GET").upper(),
                    data["query_sql"],
                    data.get("description", ""),
                    data.get("tags", ""),
                    int(data.get("is_active", True)),
                    query_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete_query(query_id: int) -> bool:
        with get_db_connection() as conn:
            cursor = conn.execute("DELETE FROM queries WHERE id = ?", (query_id,))
            conn.commit()
            return cursor.rowcount > 0

    @classmethod
    def _normalize_parameter(cls, row: Any) -> dict[str, Any]:
        result = dict(row)
        result["name"] = cls._canonical_parameter_name(result["name"])
        result["required"] = bool(result["required"])
        return result

    @classmethod
    def get_query_parameters(cls, query_id: int) -> list[dict[str, Any]]:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM parameters WHERE query_id = ? ORDER BY position, name",
                (query_id,),
            ).fetchall()
        return [cls._normalize_parameter(row) for row in rows]

    @classmethod
    def get_parameter_by_id(cls, parameter_id: int) -> dict[str, Any] | None:
        with get_db_connection() as conn:
            row = conn.execute("SELECT * FROM parameters WHERE id = ?", (parameter_id,)).fetchone()
        return cls._normalize_parameter(row) if row else None

    @classmethod
    def detect_parameters(
        cls, route_path: str, query_sql: str, method: str = "GET"
    ) -> list[dict[str, Any]]:
        """Detect route placeholders and SQLAlchemy-style named binds without mutating state."""
        method = str(method).upper()
        default_source = "query" if method == "GET" else "body"
        detected: list[dict[str, Any]] = []
        seen: set[str] = set()

        for name in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", str(route_path)):
            name = cls._canonical_parameter_name(name)
            if name in seen:
                continue
            seen.add(name)
            detected.append(
                {
                    "name": name,
                    "source": "path",
                    "param_type": "string",
                    "required": True,
                }
            )

        scrubbed = cls._strip_literals(cls._strip_comments(str(query_sql)))
        for match in re.finditer(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", scrubbed):
            name = cls._canonical_parameter_name(match.group(1))
            if (
                name in seen
                or name in cls.RESERVED_PARAMETER_NAMES
                or name.startswith("__F2A_")
            ):
                continue
            seen.add(name)
            detected.append(
                {
                    "name": name,
                    "source": default_source,
                    "param_type": "string",
                    "required": True,
                }
            )
        return detected

    @classmethod
    def sync_detected_parameters(cls, query_id: int) -> dict[str, Any]:
        """Add missing detected parameters and enforce path semantics without deleting user data."""
        query = cls.get_query_by_id(query_id)
        if query is None:
            raise ValueError("Query nao encontrada")
        candidates = cls.detect_parameters(query["route_path"], query["query_sql"], query["method"])
        detected_names = [item["name"] for item in candidates]
        existing = {
            cls._canonical_parameter_name(item["name"]): item for item in query["parameters"]
        }
        created: list[str] = []
        updated: list[str] = []

        with get_db_connection() as conn:
            position = int(
                conn.execute(
                    "SELECT COALESCE(MAX(position), 0) FROM parameters WHERE query_id = ?",
                    (query_id,),
                ).fetchone()[0]
            )
            for candidate in candidates:
                current = existing.get(cls._canonical_parameter_name(candidate["name"]))
                if current is None:
                    position += 1
                    conn.execute(
                        """
                        INSERT INTO parameters
                            (query_id, name, param_type, source, position, required, description)
                        VALUES (?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            query_id,
                            candidate["name"],
                            candidate["param_type"],
                            candidate["source"],
                            position,
                            "Detectado automaticamente pelo editor",
                        ),
                    )
                    created.append(candidate["name"])
                elif candidate["source"] == "path" and (
                    current["source"] != "path" or not current["required"]
                ):
                    conn.execute(
                        """
                        UPDATE parameters
                        SET source = 'path', required = 1, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND query_id = ?
                        """,
                        (current["id"], query_id),
                    )
                    updated.append(candidate["name"])
            conn.commit()

        parameters = cls.get_query_parameters(query_id)
        return {
            "created": created,
            "updated": updated,
            "detected_names": detected_names,
            "stale_names": [
                item["name"]
                for item in parameters
                if cls._canonical_parameter_name(item["name"]) not in detected_names
            ],
            "parameters": parameters,
        }

    @classmethod
    def merge_parameter_drafts(
        cls,
        parameters: list[dict[str, Any]],
        detected: list[dict[str, Any]],
        suppressed: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Merge detector results into editable drafts without deleting user state."""
        active_names = {cls._canonical_parameter_name(item["name"]) for item in detected}
        suppressed = {
            cls._canonical_parameter_name(name) for name in (suppressed or ())
        } & active_names
        drafts = [dict(item) for item in parameters]
        by_name = {
            cls._canonical_parameter_name(item.get("name")): item
            for item in drafts
            if item.get("name")
        }
        for candidate in detected:
            name = cls._canonical_parameter_name(candidate["name"])
            current = by_name.get(name)
            if current is None:
                if name in suppressed:
                    continue
                current = {
                    "id": None,
                    "name": name,
                    "param_type": candidate["param_type"],
                    "source": candidate["source"],
                    "default_value": None,
                    "required": candidate["required"],
                    "description": None,
                    "validation_rule": None,
                }
                drafts.append(current)
                by_name[name] = current
            if candidate["source"] == "path":
                current["source"] = "path"
                current["required"] = True
        for position, item in enumerate(drafts, 1):
            if item.get("name"):
                item["name"] = cls._canonical_parameter_name(item["name"])
            item["position"] = position
            item["detected"] = cls._canonical_parameter_name(item.get("name")) in active_names
        return drafts, suppressed

    @classmethod
    def validate_parameter_configuration(
        cls,
        route_path: str,
        query_sql: str,
        method: str,
        parameters: list[dict[str, Any]],
    ) -> None:
        method = str(method).upper()
        validation = cls.validate_query(query_sql, method)
        if not validation["valid"]:
            raise ValueError(validation["error"])

        names: list[str] = []
        for parameter in parameters:
            cls._validate_parameter_data(parameter)
            name = cls._canonical_parameter_name(parameter["name"])
            if name in cls.RESERVED_PARAMETER_NAMES or name.startswith("__F2A_"):
                raise ValueError(f"Nome de parametro reservado: {name}")
            names.append(name)
            default = parameter.get("default_value")
            if default not in (None, ""):
                try:
                    converted = cls.convert_value(
                        default, str(parameter.get("param_type", "string"))
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Valor padrao invalido para {name}") from exc
                rule = parameter.get("validation_rule")
                if rule and not re.fullmatch(str(rule), str(converted)):
                    raise ValueError(f"Valor padrao de {name} nao atende a regex")

        if len(names) != len(set(names)):
            raise ValueError("Nomes de parametros devem ser unicos")

        detected = cls.detect_parameters(route_path, query_sql, method)
        missing = [item["name"] for item in detected if item["name"] not in names]
        if missing:
            raise ValueError("Parametros detectados sem configuracao: " + ", ".join(missing))

        path_names = {item["name"] for item in cls.detect_parameters(route_path, "", method)}
        defined_path = {
            cls._canonical_parameter_name(item["name"])
            for item in parameters
            if item.get("source", "query") == "path"
        }
        if path_names != defined_path:
            raise ValueError("Parametros path devem corresponder exatamente ao caminho")
        if any(
            item.get("source") == "path" and not bool(item.get("required")) for item in parameters
        ):
            raise ValueError("Parametros path devem ser obrigatorios")
        if method == "GET" and any(item.get("source") == "body" for item in parameters):
            raise ValueError("GET nao aceita parametros body")

    @classmethod
    def save_query_configuration(
        cls,
        query_id: int | None,
        data: dict[str, Any],
        parameters: list[dict[str, Any]],
    ) -> int:
        """Persist a query and the complete parameter snapshot in one transaction."""
        route_path = cls._normalize_route_path(data["route_path"])
        method = str(data.get("method", "GET")).upper()
        normalized = [dict(item) for item in parameters]
        for position, item in enumerate(normalized, 1):
            item["name"] = cls._canonical_parameter_name(item["name"])
            item["position"] = position
        cls.validate_parameter_configuration(route_path, str(data["query_sql"]), method, normalized)

        with get_db_connection() as conn:
            if query_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO queries
                        (route_path, method, query_sql, description, tags, is_active)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        route_path,
                        method,
                        data["query_sql"],
                        data.get("description", ""),
                        data.get("tags", ""),
                        int(data.get("is_active", True)),
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite nao retornou o ID da query")
                saved_query_id = int(cursor.lastrowid)
            else:
                saved_query_id = int(query_id)
                cursor = conn.execute(
                    """
                    UPDATE queries SET route_path = ?, method = ?, query_sql = ?,
                        description = ?, tags = ?, is_active = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        route_path,
                        method,
                        data["query_sql"],
                        data.get("description", ""),
                        data.get("tags", ""),
                        int(data.get("is_active", True)),
                        saved_query_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise ValueError("Query nao encontrada")

            existing_ids = {
                int(row[0])
                for row in conn.execute(
                    "SELECT id FROM parameters WHERE query_id = ?", (saved_query_id,)
                ).fetchall()
            }
            retained_ids = [int(item["id"]) for item in normalized if item.get("id") is not None]
            if len(retained_ids) != len(set(retained_ids)) or not set(retained_ids).issubset(
                existing_ids
            ):
                raise ValueError("Parametro existente nao pertence a rota")

            removed_ids = existing_ids - set(retained_ids)
            for parameter_id in removed_ids:
                conn.execute(
                    "DELETE FROM parameters WHERE query_id = ? AND id = ?",
                    (saved_query_id, parameter_id),
                )

            # Temporary unique names make swaps and renames safe in the snapshot.
            for parameter_id in retained_ids:
                conn.execute(
                    "UPDATE parameters SET name = ? WHERE id = ? AND query_id = ?",
                    (f"__f2a_tmp_{parameter_id}", parameter_id, saved_query_id),
                )

            for item in normalized:
                values = (
                    item["name"],
                    item.get("param_type", "string"),
                    item.get("source", "query"),
                    int(item["position"]),
                    item.get("default_value") or None,
                    int(bool(item.get("required", False))),
                    item.get("description") or None,
                    item.get("validation_rule") or None,
                )
                if item.get("id") is None:
                    conn.execute(
                        """
                        INSERT INTO parameters
                            (query_id, name, param_type, source, position,
                             default_value, required, description, validation_rule)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (saved_query_id, *values),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE parameters SET name = ?, param_type = ?, source = ?,
                            position = ?, default_value = ?, required = ?, description = ?,
                            validation_rule = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND query_id = ?
                        """,
                        (*values, int(item["id"]), saved_query_id),
                    )
            conn.commit()
        return saved_query_id

    @classmethod
    def add_parameter(cls, data: dict[str, Any]) -> int:
        cls._validate_parameter_data(data)
        with get_db_connection() as conn:
            position = data.get("position")
            if not position:
                position = conn.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 FROM parameters WHERE query_id = ?",
                    (data["query_id"],),
                ).fetchone()[0]
            cursor = conn.execute(
                """
                INSERT INTO parameters
                    (query_id, name, param_type, source, position, default_value,
                     required, description, validation_rule)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    data["query_id"],
                    cls._canonical_parameter_name(data["name"]),
                    data.get("param_type", "string"),
                    data.get("source", "query"),
                    int(position),
                    data.get("default_value"),
                    int(data.get("required", False)),
                    data.get("description"),
                    data.get("validation_rule"),
                ),
            )
            conn.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite nao retornou o ID do parametro")
            return int(cursor.lastrowid)

    @classmethod
    def update_parameter(cls, parameter_id: int, data: dict[str, Any]) -> bool:
        cls._validate_parameter_data(data)
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE parameters SET name = ?, param_type = ?, source = ?,
                    position = ?, default_value = ?, required = ?, description = ?,
                    validation_rule = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
                (
                    cls._canonical_parameter_name(data["name"]),
                    data.get("param_type", "string"),
                    data.get("source", "query"),
                    int(data.get("position") or 1),
                    data.get("default_value"),
                    int(data.get("required", False)),
                    data.get("description"),
                    data.get("validation_rule"),
                    parameter_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    @classmethod
    def _validate_parameter_data(cls, data: dict[str, Any]) -> None:
        if data.get("param_type", "string") not in cls.TYPES:
            raise ValueError("Tipo de parametro invalido")
        if data.get("source", "query") not in cls.SOURCES:
            raise ValueError("Origem de parametro invalida")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(data["name"])):
            raise ValueError("Nome de parametro invalido")
        rule = data.get("validation_rule")
        if rule:
            try:
                re.compile(rule)
            except re.error as exc:
                raise ValueError("validation_rule nao e uma expressao regular valida") from exc

    @staticmethod
    def delete_parameter(parameter_id: int) -> bool:
        with get_db_connection() as conn:
            cursor = conn.execute("DELETE FROM parameters WHERE id = ?", (parameter_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def reorder_parameters(query_id: int, parameter_ids: list[int]) -> bool:
        if not parameter_ids or len(parameter_ids) != len(set(parameter_ids)):
            return False
        with get_db_connection() as conn:
            existing = {
                int(row[0])
                for row in conn.execute(
                    "SELECT id FROM parameters WHERE query_id = ?", (query_id,)
                ).fetchall()
            }
            if existing != set(parameter_ids):
                return False
            for position, parameter_id in enumerate(parameter_ids, 1):
                conn.execute(
                    "UPDATE parameters SET position = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (position, parameter_id),
                )
            conn.commit()
        return True

    @classmethod
    def validate_query(cls, query_sql: str, method: str = "GET") -> dict[str, Any]:
        try:
            method = method.upper()
            if method not in cls.METHODS:
                return {"valid": False, "error": "Metodo HTTP nao suportado"}
            clean = cls._strip_comments(str(query_sql)).strip()
            statements = cls._split_statements(clean)
            if len(statements) != 1 or not statements[0].strip():
                return {"valid": False, "error": "Apenas uma instrucao SQL e permitida"}
            statement = statements[0].strip()
            if re.match(r"^SET\s+(TRANSACTION|AUTODDL|TERM)\b", statement, re.IGNORECASE):
                return {"valid": False, "error": "Controle transacional nao e permitido"}
            if not cls._balanced(statement):
                return {"valid": False, "error": "Parenteses nao balanceados"}
            scrubbed = cls._strip_literals(statement)
            tokens = re.findall(r"\b[A-Z][A-Z_]*\b", scrubbed.upper())
            if "EXECUTE" in tokens and "BLOCK" in tokens:
                return {"valid": False, "error": "EXECUTE BLOCK nao e permitido"}
            forbidden = cls.FORBIDDEN.intersection(tokens)
            if forbidden:
                return {"valid": False, "error": f"Comando nao permitido: {sorted(forbidden)[0]}"}
            statement_type = cls.statement_type(statement)
            allowed = {
                "GET": {"select"},
                "POST": {"insert", "procedure"},
                "PUT": {"update", "merge", "upsert", "procedure"},
                "PATCH": {"update", "merge", "upsert", "procedure"},
                "DELETE": {"delete", "procedure"},
            }[method]
            if statement_type not in allowed:
                return {
                    "valid": False,
                    "error": f"Instrucao {statement_type or 'desconhecida'} incompatível com {method}",
                }
            return {"valid": True, "error": None, "statement_type": statement_type}
        except Exception:
            return {"valid": False, "error": "Erro ao validar SQL"}

    @classmethod
    def statement_type(cls, statement: str) -> str:
        scrubbed = cls._strip_literals(cls._strip_comments(statement))
        upper = scrubbed.upper().lstrip()
        if re.match(r"EXECUTE\s+PROCEDURE\b", upper):
            return "procedure"
        if re.match(r"UPDATE\s+OR\s+INSERT\b", upper):
            return "upsert"
        top = cls._top_level_words(scrubbed)
        operations = [
            word for word in top if word in {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"}
        ]
        if upper.startswith("WITH"):
            return operations[-1].lower() if operations else ""
        first = re.match(r"([A-Z]+)", upper)
        return first.group(1).lower() if first else ""

    @staticmethod
    def _top_level_words(sql: str) -> list[str]:
        words: list[str] = []
        depth = 0
        current: list[str] = []
        for char in sql:
            if char == "(":
                if depth == 0 and current:
                    words.append("".join(current).upper())
                    current = []
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif depth == 0 and (char.isalnum() or char == "_"):
                current.append(char)
            elif depth == 0 and current:
                words.append("".join(current).upper())
                current = []
        if current:
            words.append("".join(current).upper())
        return words

    @classmethod
    def bind_parameters(
        cls, definitions: list[dict[str, Any]], sources: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        normalized_sources: dict[str, dict[str, Any]] = {}
        for source, values in sources.items():
            normalized: dict[str, Any] = {}
            for raw_name, value in values.items():
                name = cls._canonical_parameter_name(raw_name)
                if name in normalized:
                    raise ValueError(
                        f"Parametro duplicado ignorando maiusculas/minusculas em {source}: {name}"
                    )
                normalized[name] = value
            normalized_sources[source] = normalized

        allowed_by_source = {
            source: {
                cls._canonical_parameter_name(item["name"])
                for item in definitions
                if item["source"] == source
            }
            for source in cls.SOURCES
        }
        extras = {"LIMIT", "OFFSET", "ORDER_BY"}
        for source, values in normalized_sources.items():
            unknown = set(values) - allowed_by_source[source]
            if source == "query":
                unknown -= extras
            if unknown:
                raise ValueError(f"Campos desconhecidos em {source}: {', '.join(sorted(unknown))}")

        bound: dict[str, Any] = {}
        for definition in definitions:
            name = cls._canonical_parameter_name(definition["name"])
            values = normalized_sources[definition["source"]]
            if name in values:
                raw = values[name]
            elif definition.get("default_value") is not None:
                raw = definition["default_value"]
            elif definition.get("required"):
                raise ValueError(f"Parametro obrigatorio ausente: {name}")
            else:
                raw = None
            if raw is not None:
                raw = cls.convert_value(raw, definition["param_type"])
                rule = definition.get("validation_rule")
                if rule and re.fullmatch(rule, str(raw)) is None:
                    raise ValueError(f"Parametro invalido: {name}")
            bound[name] = raw
        query_values = normalized_sources["query"]
        options = {
            "limit": query_values.get("LIMIT"),
            "offset": query_values.get("OFFSET"),
            "order_by": query_values.get("ORDER_BY"),
        }
        return bound, options

    @classmethod
    def _execution_bindings(cls, sql: str, params: dict[str, Any]) -> dict[str, Any]:
        scrubbed = cls._strip_literals(cls._strip_comments(sql))
        execution_params: dict[str, Any] = {}
        for match in re.finditer(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", scrubbed):
            actual_name = match.group(1)
            if actual_name in params:
                execution_params[actual_name] = params[actual_name]
                continue
            canonical = cls._canonical_parameter_name(actual_name)
            if canonical in params:
                execution_params[actual_name] = params[canonical]
        return execution_params

    @classmethod
    def convert_value(cls, value: Any, param_type: str) -> Any:
        try:
            if param_type == "string":
                return str(value)
            if param_type == "integer":
                if isinstance(value, bool):
                    raise ValueError
                return int(value)
            if param_type == "float":
                if isinstance(value, bool):
                    raise ValueError
                return float(value)
            if param_type == "boolean":
                return cls._bool(value)
            if param_type == "date":
                return (
                    value
                    if isinstance(value, date) and not isinstance(value, datetime)
                    else date.fromisoformat(str(value))
                )
            if param_type == "datetime":
                return (
                    value
                    if isinstance(value, datetime)
                    else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Valor invalido para tipo {param_type}") from exc
        raise ValueError("Tipo de parametro invalido")

    @classmethod
    def apply_query_options(
        cls, sql: str, params: dict[str, Any], options: dict[str, Any], max_rows: int
    ) -> tuple[str, dict[str, Any]]:
        order_by = options.get("order_by")
        if order_by:
            items = [item.strip() for item in str(order_by).split(",")]
            if not items or any(not cls._ORDER_ITEM.fullmatch(item) for item in items):
                raise ValueError("ORDER_BY invalido")
            top_level_words = cls._top_level_words(
                cls._strip_literals(cls._strip_comments(sql))
            )
            has_top_level_order = any(
                first == "ORDER" and second == "BY"
                for first, second in zip(top_level_words, top_level_words[1:], strict=False)
            )
            if has_top_level_order:
                raise ValueError("ORDER_BY extra nao permitido quando o SQL ja possui ordenacao")
            sql = f"{sql.rstrip()} ORDER BY {', '.join(items)}"

        raw_limit = options.get("limit")
        try:
            limit = min(int(raw_limit), max_rows) if raw_limit not in (None, "") else max_rows
            offset = int(options.get("offset") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("LIMIT/OFFSET invalidos") from exc
        if limit <= 0 or offset < 0:
            raise ValueError("LIMIT/OFFSET invalidos")
        scrubbed = cls._strip_literals(cls._strip_comments(sql))
        pagination_words = set(cls._top_level_words(scrubbed))
        if pagination_words.intersection({"ROWS", "FIRST", "SKIP", "OFFSET", "FETCH"}):
            return sql, params
        params = dict(params)
        if offset:
            params["__f2a_row_start"] = offset + 1
            params["__f2a_row_end"] = offset + limit
            sql = f"{sql.rstrip()} ROWS :__f2a_row_start TO :__f2a_row_end"
        else:
            params["__f2a_row_limit"] = limit
            sql = f"{sql.rstrip()} ROWS :__f2a_row_limit"
        return sql, params

    @classmethod
    def execute_query(
        cls,
        query_sql: str,
        params: dict[str, Any] | None = None,
        *,
        method: str = "GET",
        options: dict[str, Any] | None = None,
        max_rows: int = 1000,
        fetch_chunk_size: int = 200,
        timeout_seconds: int | None = None,
        is_canceled: Callable[[], bool] | None = None,
        register_dbapi_connection: Callable[[Any], None] | None = None,
        rollback: bool = False,
    ) -> QueryExecutionResult:
        validation = cls.validate_query(query_sql, method)
        if not validation["valid"]:
            raise ValueError(validation["error"])
        statement_type = validation["statement_type"]
        params = dict(params or {})
        sql = query_sql
        if method == "GET":
            sql, params = cls.apply_query_options(sql, params, options or {}, max_rows)
        params = cls._execution_bindings(sql, params)
        connection = get_firebird_engine().connect()
        transaction = connection.begin()
        try:
            if timeout_seconds:
                cls._try_set_statement_timeout(connection, timeout_seconds)
            if register_dbapi_connection:
                register_dbapi_connection(cls._extract_dbapi_connection(connection))
            if is_canceled and is_canceled():
                raise cls.QueryExecutionCanceledError("Execucao cancelada")
            result = connection.execute(text(sql), params)
            rows: list[dict[str, Any]] = []
            if result.returns_rows:
                while len(rows) < max_rows:
                    if is_canceled and is_canceled():
                        raise cls.QueryExecutionCanceledError("Execucao cancelada")
                    chunk = result.mappings().fetchmany(min(fetch_chunk_size, max_rows - len(rows)))
                    if not chunk:
                        break
                    rows.extend(dict(row) for row in chunk)
            affected = max(0, int(result.rowcount or 0))
            if method != "GET" and statement_type != "procedure" and result.returns_rows:
                affected = max(affected, len(rows))
            if method == "GET" or rollback:
                transaction.rollback()
            else:
                if is_canceled and is_canceled():
                    raise cls.QueryExecutionCanceledError("Execucao cancelada")
                transaction.commit()
            return QueryExecutionResult(rows, affected, statement_type)
        except Exception:
            if transaction.is_active:
                transaction.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def request_cancel(dbapi_connection: Any) -> None:
        if dbapi_connection is None:
            return
        for name in ("cancel_operation", "cancel"):
            method = getattr(dbapi_connection, name, None)
            if callable(method):
                try:
                    method()
                except Exception as exc:
                    logger.debug(
                        "Driver nao aceitou cancelamento error_type=%s",
                        type(exc).__name__,
                    )
                return

    @staticmethod
    def _extract_dbapi_connection(connection: Any) -> Any:
        try:
            proxy = connection.connection
            return getattr(proxy, "dbapi_connection", None) or getattr(proxy, "connection", None)
        except Exception:
            return None

    @staticmethod
    def _try_set_statement_timeout(connection: Any, seconds: int) -> None:
        try:
            raw = QueryService._extract_dbapi_connection(connection)
            if raw is not None and hasattr(raw, "statement_timeout"):
                raw.statement_timeout = seconds
        except Exception as exc:
            logger.debug(
                "Driver nao aceitou statement timeout error_type=%s",
                type(exc).__name__,
            )

    @staticmethod
    def _strip_comments(sql: str) -> str:
        output: list[str] = []
        quoted = False
        index = 0
        while index < len(sql):
            char = sql[index]
            if char == "'":
                output.append(char)
                if quoted and index + 1 < len(sql) and sql[index + 1] == "'":
                    output.append("'")
                    index += 2
                    continue
                quoted = not quoted
                index += 1
                continue
            if not quoted and sql.startswith("--", index):
                newline = sql.find("\n", index + 2)
                index = len(sql) if newline < 0 else newline
                output.append(" ")
                continue
            if not quoted and sql.startswith("/*", index):
                closing = sql.find("*/", index + 2)
                index = len(sql) if closing < 0 else closing + 2
                output.append(" ")
                continue
            output.append(char)
            index += 1
        return "".join(output)

    @staticmethod
    def _strip_literals(sql: str) -> str:
        output: list[str] = []
        i = 0
        while i < len(sql):
            if sql[i] == "'":
                output.append("''")
                i += 1
                while i < len(sql):
                    if sql[i] == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                        i += 2
                    elif sql[i] == "'":
                        i += 1
                        break
                    else:
                        i += 1
            else:
                output.append(sql[i])
                i += 1
        return "".join(output)

    @staticmethod
    def _split_statements(sql: str) -> list[str]:
        statements: list[str] = []
        current: list[str] = []
        quoted = False
        i = 0
        while i < len(sql):
            char = sql[i]
            if char == "'":
                current.append(char)
                if quoted and i + 1 < len(sql) and sql[i + 1] == "'":
                    current.append("'")
                    i += 2
                    continue
                quoted = not quoted
            elif char == ";" and not quoted:
                if "".join(current).strip():
                    statements.append("".join(current))
                current = []
            else:
                current.append(char)
            i += 1
        if "".join(current).strip():
            statements.append("".join(current))
        return statements

    @staticmethod
    def _balanced(sql: str) -> bool:
        depth = 0
        for char in QueryService._strip_literals(sql):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    return False
        return depth == 0
