from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from nicegui import app as nicegui_app
from nicegui import ui

from .core.access_key_service import AccessKeyService
from .core.audit import record_admin_audit
from .core.auth_service import AuthService
from .core.dynamic_loader import dynamic_loader
from .core.execution_service import execution_service
from .core.migrations import current_revision
from .core.query_service import QueryService
from .core.rate_limit import auth_rate_limiter
from .core.settings import get_settings
from .schemas.query import QueryCreateSchema
from .ui_components import (
    alert,
    brand,
    confirm_dialog,
    empty_state,
    format_timestamp,
    install_theme,
    method_badge,
    page_header,
    section_card,
    status_badge,
)

NAV_ITEMS = [
    ("dashboard", "Dashboard", "space_dashboard"),
    ("routes", "Rotas", "route"),
    ("keys", "Access Keys", "key"),
    ("executions", "Execuções", "monitor_heart"),
    ("system", "Sistema", "dns"),
]

logger = logging.getLogger(__name__)


def _display_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return json.loads(json.dumps(rows, default=str))


def install_ui(fastapi_app: FastAPI) -> None:
    settings = get_settings()

    def signature(expiry: int) -> str:
        digest = hmac.new(
            settings.admin_api_key.encode(),
            f"fire2api-admin:{expiry}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{expiry}.{digest}"

    def authenticated() -> bool:
        marker = str(nicegui_app.storage.user.get("admin_marker", ""))
        try:
            expiry_text, _digest = marker.split(".", 1)
            expiry = int(expiry_text)
        except (ValueError, TypeError):
            return False
        return expiry >= int(time.time()) and hmac.compare_digest(signature(expiry), marker)

    def remote_address() -> str:
        try:
            request = ui.context.client.request
            return request.client.host if request.client else "unknown"
        except (AttributeError, RuntimeError):
            return "unknown"

    def audit_login_failure(remote: str) -> None:
        try:
            record_admin_audit(
                "auth.failure",
                "admin_ui",
                outcome="failure",
                remote_addr_hash="sha256:" + hashlib.sha256(remote.encode()).hexdigest(),
            )
        except Exception as exc:
            logger.warning(
                "Falha ao registrar auditoria de autenticacao da UI error_type=%s",
                type(exc).__name__,
            )

    def login_page() -> None:
        with ui.column().classes("w-full min-h-screen items-center justify-center p-4 sm:p-6"):
            with ui.card().classes("f2-card w-full max-w-sm p-7 sm:p-8 gap-6"):
                brand()
                with ui.column().classes("gap-1"):
                    ui.label("Acesse o painel").classes("text-2xl font-semibold tracking-tight")
                    ui.label(
                        "Use a chave administrativa configurada nesta instância."
                    ).classes("text-sm text-slate-400")
                key = (
                    ui.input(
                        "Chave administrativa",
                        placeholder="Cole sua ADMIN_API_KEY",
                        password=True,
                        password_toggle_button=True,
                    )
                    .props(
                        'outlined stack-label autofocus autocomplete=current-password '
                        'aria-label="Chave administrativa"'
                    )
                    .classes("w-full")
                )
                key.mark("login-key")
                error = ui.label().props('role="alert" aria-live="polite"').classes(
                    "text-sm text-red-300 min-h-5"
                )

                def submit() -> None:
                    error.set_text("")
                    remote = remote_address()
                    retry_after = auth_rate_limiter.retry_after("admin_ui", remote)
                    if retry_after:
                        error.set_text(
                            f"Muitas tentativas. Tente novamente em {retry_after} segundos."
                        )
                        return
                    login_button.disable()
                    login_button.props("loading")
                    if not AuthService.validate_admin_token(key.value):
                        retry_after = auth_rate_limiter.register_failure("admin_ui", remote)
                        audit_login_failure(remote)
                        error.set_text("A chave administrativa é inválida.")
                        login_button.enable()
                        if retry_after:
                            error.set_text(
                                "Muitas tentativas invalidas. "
                                f"Tente novamente em {retry_after} segundos."
                            )
                        login_button.props(remove="loading")
                        key.run_method("focus")
                        return
                    auth_rate_limiter.register_success("admin_ui", remote)
                    expiry = int(time.time()) + settings.admin_session_minutes * 60
                    nicegui_app.storage.user["admin_marker"] = signature(expiry)
                    ui.navigate.to("/")

                key.on("keydown.enter", submit)
                login_button = (
                    ui.button("Entrar", on_click=submit, icon="login")
                    .props("unelevated no-caps size=md")
                    .classes("w-full")
                )
                login_button.mark("login-submit")
                ui.label(
                    "A chave não é armazenada na sessão do navegador."
                ).classes("text-xs text-slate-500 text-center")

    @ui.page("/")
    def admin_page() -> None:
        install_theme()
        if not authenticated():
            login_page()
            return

        view: dict[str, Any] = {"section": "dashboard", "editor": None}

        def reset_page_scroll() -> None:
            # Quasar restores the body position asynchronously after closing an overlay.
            # Wait for the refreshed section and any drawer/dialog transition to settle.
            ui.run_javascript(
                "setTimeout(() => window.scrollTo({top: 0, behavior: 'auto'}), 120)"
            )

        def perform_logout() -> None:
            nicegui_app.storage.user.clear()
            ui.navigate.to("/")

        def require_authenticated() -> bool:
            if authenticated():
                return True
            nicegui_app.storage.user.clear()
            ui.notify("Sua sessao expirou. Entre novamente.", type="warning")
            ui.navigate.to("/")
            return False

        def apply_navigation(section: str) -> None:
            if not require_authenticated():
                return
            view["editor"] = None
            view["section"] = section
            navigation.refresh()
            main_content.refresh()
            ui.run_javascript(
                f"if (window.innerWidth < 1024) getElement({drawer.id}).hide()"
            )
            reset_page_scroll()

        def request_navigation(section: str) -> None:
            editor = view.get("editor")
            if editor and editor.get("dirty"):
                confirm_dialog(
                    "Descartar alterações?",
                    "As alterações feitas nesta rota ainda não foram salvas.",
                    confirm_label="Descartar",
                    on_confirm=lambda: apply_navigation(section),
                )
                return
            apply_navigation(section)

        def request_logout() -> None:
            editor = view.get("editor")
            if editor and editor.get("dirty"):
                confirm_dialog(
                    "Sair sem salvar?",
                    "As alterações feitas nesta rota serão descartadas ao encerrar a sessão.",
                    confirm_label="Sair",
                    on_confirm=perform_logout,
                )
                return
            perform_logout()

        with ui.left_drawer(fixed=True, bordered=True) as drawer:
            drawer.props("width=248 breakpoint=1024 show-if-above").classes("f2-sidebar")
            with ui.column().classes("w-full h-full p-0 gap-5"):
                with ui.element("div").classes("px-2 py-2"):
                    brand()
                ui.separator().classes("bg-slate-800")

                @ui.refreshable
                def navigation() -> None:
                    with ui.column().classes("w-full gap-1"):
                        for key, label, icon in NAV_ITEMS:
                            classes = "f2-nav-button"
                            if view["section"] == key:
                                classes += " f2-nav-active"
                            nav_button = ui.button(
                                label,
                                icon=icon,
                                on_click=lambda _, target=key: request_navigation(target),
                            ).props(
                                f'flat no-caps aria-label="Abrir {label}"'
                            ).classes(classes)
                            nav_button.mark(f"nav-{key}")

                navigation()
                ui.space()
                ui.separator().classes("bg-slate-800")
                ui.button("Sair", icon="logout", on_click=request_logout).props(
                    "flat no-caps"
                ).classes("f2-nav-button")

        with ui.header().classes(
            "f2-mobile-header lg:hidden h-16 px-4 items-center gap-3"
        ):
            ui.button(icon="menu", on_click=drawer.toggle).props(
                'flat round aria-label="Abrir navegação"'
            )
            brand(compact=True)
            ui.space()
            ui.button(icon="logout", on_click=request_logout).props(
                'flat round aria-label="Sair"'
            )

        def test_dialog(query: dict[str, Any]) -> None:
            if not require_authenticated():
                return
            current = QueryService.get_query_by_id(query["id"])
            if current is None:
                ui.notify("Rota não encontrada", type="negative")
                return
            result_state: dict[str, Any] = {
                "error": None,
                "rows": [],
                "execution_id": None,
                "affected_rows": 0,
                "ran": False,
                "page_limit": None,
                "page_offset": 0,
            }
            controls: dict[str, Any] = {}
            with ui.dialog() as dialog, ui.card().classes(
                "f2-dialog w-[calc(100vw-2rem)] max-w-[1200px] "
                "max-h-[calc(100dvh-2rem)] overflow-y-auto p-0 gap-0"
            ):
                with ui.row().classes(
                    "w-full items-start gap-4 px-5 sm:px-6 py-5 border-b border-slate-700"
                ):
                    with ui.column().classes("gap-1 min-w-0 grow"):
                        ui.label("Testar rota").classes("text-xl sm:text-2xl font-semibold")
                        with ui.row().classes("items-center gap-2 min-w-0 flex-nowrap"):
                            method_badge(current["method"])
                            ui.label(f"/api{current['route_path']}").classes(
                                "font-mono text-sm text-slate-400 truncate"
                            )
                    ui.button(icon="close", on_click=dialog.close).props(
                        'flat round aria-label="Fechar teste"'
                    )
                with ui.column().classes("w-full gap-5 p-5 sm:p-6"):
                    alert(
                        "Teste seguro: a execução administrativa sempre faz rollback.",
                        kind="success",
                        icon="verified_user",
                    )
                    with ui.element("div").classes(
                        "grid grid-cols-1 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] "
                        "w-full gap-6 items-start"
                    ):
                        with ui.column().classes("w-full gap-5"):
                            for source, label in [
                                ("path", "Caminho"),
                                ("query", "Query string"),
                                ("body", "Body"),
                            ]:
                                definitions = [
                                    item
                                    for item in current["parameters"]
                                    if item["source"] == source
                                ]
                                if not definitions:
                                    continue
                                with ui.column().classes("w-full gap-3"):
                                    ui.label(label).classes(
                                        "text-xs uppercase tracking-wider text-slate-400 font-semibold"
                                    )
                                    with ui.element("div").classes(
                                        "grid grid-cols-1 sm:grid-cols-2 gap-3 w-full"
                                    ):
                                        for item in definitions:
                                            field_label = item["name"] + (
                                                " *" if item["required"] else ""
                                            )
                                            default = item.get("default_value")
                                            control: Any
                                            if item["param_type"] == "boolean":
                                                control = ui.switch(
                                                    field_label,
                                                    value=(
                                                        QueryService._bool(default)
                                                        if default not in (None, "")
                                                        else False
                                                    ),
                                                )
                                            elif item["param_type"] in {"integer", "float"}:
                                                control = ui.number(
                                                    field_label,
                                                    value=(
                                                        float(default)
                                                        if default not in (None, "")
                                                        else None
                                                    ),
                                                    format=(
                                                        "%.0f"
                                                        if item["param_type"] == "integer"
                                                        else "%.6f"
                                                    ),
                                                ).props("outlined")
                                            else:
                                                control = ui.input(
                                                    field_label, value=default
                                                ).props("outlined")
                                                if item["param_type"] == "date":
                                                    control.props("type=date")
                                                elif item["param_type"] == "datetime":
                                                    control.props("type=datetime-local")
                                            control.classes("w-full")
                                            controls[item["name"]] = control

                            limit = offset = order_by = None
                            if current["method"] == "GET":
                                with ui.column().classes("w-full gap-3"):
                                    ui.label("Opções GET").classes(
                                        "text-xs uppercase tracking-wider text-slate-400 font-semibold"
                                    )
                                    with ui.element("div").classes(
                                        "grid grid-cols-2 gap-3 w-full"
                                    ):
                                        limit = ui.number(
                                            "LIMIT",
                                            value=settings.query_max_rows_hard,
                                            format="%.0f",
                                        ).props(
                                            f"outlined min=1 max={settings.query_max_rows_hard} step=1"
                                        )
                                        offset = ui.number(
                                            "OFFSET", value=0, format="%.0f"
                                        ).props("outlined min=0 step=1")
                                    order_by = (
                                        ui.input(
                                            "ORDER_BY", placeholder="NOME ASC, ID DESC"
                                        )
                                        .props("outlined stack-label")
                                        .classes("w-full")
                                    )

                            async def run() -> None:
                                if not require_authenticated():
                                    return
                                run_button.disable()
                                run_button.props("loading")
                                try:
                                    sources: dict[str, dict[str, Any]] = {
                                        "path": {},
                                        "query": {},
                                        "body": {},
                                    }
                                    page_limit: int | None = None
                                    page_offset = 0
                                    for definition in current["parameters"]:
                                        value = controls[definition["name"]].value
                                        if definition["required"] and value in (None, ""):
                                            raise ValueError(
                                                f"Parâmetro obrigatório: {definition['name']}"
                                            )
                                        if value not in (None, ""):
                                            sources[definition["source"]][
                                                definition["name"]
                                            ] = value
                                    if current["method"] == "GET":
                                        if limit and limit.value not in (None, ""):
                                            page_limit = int(limit.value)
                                            if page_limit > settings.query_max_rows_hard:
                                                raise ValueError(
                                                    "LIMIT máximo permitido nesta instância: "
                                                    f"{settings.query_max_rows_hard}. Para "
                                                    "aumentar, ajuste QUERY_MAX_ROWS_HARD e "
                                                    "reinicie a aplicação."
                                                )
                                            if page_limit <= 0:
                                                raise ValueError("LIMIT deve ser maior que zero")
                                            sources["query"]["LIMIT"] = page_limit
                                        if offset and offset.value not in (None, ""):
                                            page_offset = int(offset.value)
                                            if page_offset < 0:
                                                raise ValueError(
                                                    "OFFSET deve ser maior ou igual a zero"
                                                )
                                            sources["query"]["OFFSET"] = page_offset
                                        if order_by and order_by.value:
                                            sources["query"]["ORDER_BY"] = order_by.value
                                    params, options = QueryService.bind_parameters(
                                        current["parameters"], sources
                                    )
                                    execution_id, result = await execution_service.execute(
                                        owner_hash="admin-ui",
                                        route_type="admin_test",
                                        route_ref=current["route_path"],
                                        http_method=current["method"],
                                        query_sql=current["query_sql"],
                                        params=params,
                                        options=options,
                                        rollback=True,
                                    )
                                    record_admin_audit("query.test", "query", current["id"])
                                    result_state.update(
                                        {
                                            "error": None,
                                            "rows": result.rows,
                                            "execution_id": execution_id,
                                            "affected_rows": result.affected_rows,
                                            "ran": True,
                                            "page_limit": page_limit,
                                            "page_offset": page_offset,
                                        }
                                    )
                                except ValueError as exc:
                                    result_state.update(
                                        {
                                            "error": str(exc),
                                            "execution_id": None,
                                            "rows": [],
                                            "affected_rows": 0,
                                            "ran": True,
                                        }
                                    )
                                except Exception as exc:
                                    logger.warning(
                                        "Falha no teste administrativo error_type=%s",
                                        type(exc).__name__,
                                    )
                                    result_state.update(
                                        {
                                            "error": "Nao foi possivel executar o teste.",
                                            "execution_id": None,
                                            "rows": [],
                                            "affected_rows": 0,
                                            "ran": True,
                                        }
                                    )
                                finally:
                                    run_button.enable()
                                    run_button.props(remove="loading")
                                    result_panel.refresh()

                            run_button = (
                                ui.button("Executar teste", on_click=run, icon="play_arrow")
                                .props("unelevated no-caps")
                                .classes("w-full sm:w-auto")
                            )

                        with ui.column().classes(
                            "w-full min-h-[280px] gap-4 lg:border-l lg:border-slate-700 lg:pl-6"
                        ):

                            @ui.refreshable
                            def result_panel() -> None:
                                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                                    ui.label("Resultado").classes("text-lg font-semibold")
                                    ui.space()
                                    if result_state["execution_id"]:
                                        ui.badge(
                                            f"ID {result_state['execution_id']}", color="grey"
                                        ).props("outline")
                                if result_state["error"]:
                                    alert(result_state["error"], kind="error")
                                elif not result_state["ran"]:
                                    empty_state(
                                        "Aguardando execução",
                                        "Preencha os parâmetros e execute o teste para visualizar o resultado.",
                                        icon="table_view",
                                    )
                                elif result_state["rows"]:
                                    rows = _display_rows(result_state["rows"])
                                    columns = [
                                        {
                                            "name": key,
                                            "label": key,
                                            "field": key,
                                            "align": "left",
                                        }
                                        for key in rows[0]
                                    ]
                                    ui.label(f"{len(rows)} linha(s) retornada(s)").classes(
                                        "text-sm text-slate-400"
                                    )
                                    if result_state["page_limit"] is not None:
                                        with ui.row().classes("gap-2 flex-wrap"):
                                            ui.badge(
                                                f"LIMIT {result_state['page_limit']}",
                                                color="grey",
                                            ).props("outline")
                                            ui.badge(
                                                f"OFFSET {result_state['page_offset']}",
                                                color="grey",
                                            ).props("outline")
                                    ui.table(
                                        columns=columns,
                                        rows=rows,
                                        row_key=columns[0]["name"],
                                        pagination=10,
                                    ).props("flat dense").classes("f2-table w-full")
                                else:
                                    alert(
                                        f"Execução concluída sem linhas. Linhas afetadas: "
                                        f"{result_state['affected_rows']}",
                                        kind="success",
                                    )

                            result_panel()
            dialog.open()

        def dashboard_page() -> None:
            queries = QueryService.get_all_queries()
            keys = AccessKeyService.list_keys()
            executions = execution_service.list_executions("admin", is_admin=True, limit=20)
            page_header(
                "Dashboard",
                "Visão rápida da configuração e da atividade desta instância.",
            )
            metrics = [
                (
                    "Rotas cadastradas",
                    len(queries),
                    "route",
                    f"{sum(item['is_active'] for item in queries)} ativa(s)",
                ),
                (
                    "Rotas ativas",
                    sum(item["is_active"] for item in queries),
                    "check_circle",
                    "Disponíveis na API",
                ),
                (
                    "Access Keys ativas",
                    sum(item["is_active"] for item in keys),
                    "key",
                    f"{len(keys)} cadastrada(s)",
                ),
                (
                    "Execuções recentes",
                    len(executions),
                    "query_stats",
                    "Últimos 20 registros",
                ),
            ]
            with ui.element("div").classes(
                "grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 w-full"
            ):
                for title, value, icon, detail in metrics:
                    with ui.card().classes("f2-card f2-card-flat f2-metric p-5 gap-3"):
                        with ui.row().classes("w-full items-start justify-between"):
                            with ui.column().classes("gap-1"):
                                ui.label(title).classes("text-sm text-slate-400")
                                ui.label(str(value)).classes(
                                    "text-3xl font-semibold tracking-tight"
                                )
                            with ui.element("div").classes(
                                "w-9 h-9 rounded-lg bg-blue-500/10 text-blue-400 "
                                "flex items-center justify-center"
                            ):
                                ui.icon(icon).classes("text-xl")
                        ui.label(detail).classes("text-xs text-slate-500")

        def start_editor(existing: dict[str, Any] | None = None) -> None:
            if not require_authenticated():
                return
            initial = dict(existing or {})
            view["section"] = "routes"
            view["editor"] = {
                "query_id": initial.get("id"),
                "route_path": initial.get("route_path", "/"),
                "method": initial.get("method", "GET"),
                "query_sql": initial.get("query_sql", "SELECT "),
                "description": initial.get("description", ""),
                "tags": initial.get("tags", ""),
                "is_active": bool(initial.get("is_active", True)),
                "parameters": [dict(item) for item in initial.get("parameters", [])],
                "suppressed": set(),
                "detected_names": set(),
                "generation": 0,
                "dirty": False,
            }
            navigation.refresh()
            main_content.refresh()
            reset_page_scroll()

        def close_editor() -> None:
            if not require_authenticated():
                return
            editor = view.get("editor")
            if editor and editor.get("dirty"):
                confirm_dialog(
                    "Descartar alterações?",
                    "As alterações feitas nesta rota ainda não foram salvas.",
                    confirm_label="Descartar",
                    on_confirm=lambda: apply_navigation("routes"),
                )
                return
            apply_navigation("routes")

        def route_editor_page() -> None:
            state = view["editor"]

            def set_value(key: str, value: Any) -> None:
                state[key] = value
                state["dirty"] = True

            def set_detection_value(key: str, value: Any) -> None:
                set_value(key, value)
                schedule_detection()

            with ui.element("div").classes(
                "f2-sticky-bar w-full flex items-center gap-3 flex-wrap"
            ):
                ui.button(icon="arrow_back", on_click=close_editor).props(
                    'flat round aria-label="Voltar para rotas"'
                )
                with ui.column().classes("gap-0 min-w-0 grow"):
                    ui.label(
                        "Editar rota" if state["query_id"] else "Nova rota"
                    ).classes("text-xl sm:text-2xl font-semibold truncate")
                    ui.label("Configuração e parâmetros").classes("text-xs text-slate-400")
                ui.button("Cancelar", on_click=close_editor).props(
                    "flat no-caps"
                ).classes("max-sm:hidden")

                def save() -> None:
                    if not require_authenticated():
                        return
                    validation_label.set_text("")
                    save_button.disable()
                    save_button.props("loading")
                    try:
                        payload = QueryCreateSchema(
                            route_path=state["route_path"],
                            method=state["method"],
                            query_sql=state["query_sql"],
                            description=state["description"],
                            tags=state["tags"],
                            is_active=state["is_active"],
                        ).model_dump()
                        action = "query.update" if state["query_id"] else "query.create"
                        state["query_id"] = QueryService.save_query_configuration(
                            state["query_id"], payload, state["parameters"]
                        )
                        saved = QueryService.get_query_by_id(state["query_id"])
                        if saved:
                            state["parameters"] = [
                                dict(item) for item in saved["parameters"]
                            ]
                        state["dirty"] = False
                        record_admin_audit(action, "query", state["query_id"])
                        dynamic_loader.reload_app(fastapi_app)
                        ui.notify("Rota salva com sucesso", type="positive")
                        main_content.refresh()
                    except ValueError as exc:
                        validation_label.set_text(str(exc))
                        validation_label.classes(
                            replace="text-sm text-red-300 min-h-5"
                        )
                    except Exception as exc:
                        logger.warning(
                            "Falha ao salvar rota na UI error_type=%s",
                            type(exc).__name__,
                        )
                        validation_label.set_text("Nao foi possivel salvar a rota.")
                        validation_label.classes(
                            replace="text-sm text-red-300 min-h-5"
                        )
                    finally:
                        save_button.enable()
                        save_button.props(remove="loading")

                save_button = ui.button("Salvar rota", icon="save", on_click=save).props(
                    "unelevated no-caps"
                )
                save_button.mark("route-save")

            validation_label = ui.label().props('role="alert" aria-live="polite"').classes(
                "text-sm text-red-300 min-h-5 mb-2"
            )
            with ui.column().classes("w-full gap-5"):
                with section_card():
                    with ui.column().classes("gap-1"):
                        ui.label("Configuração geral").classes("text-lg font-semibold")
                        ui.label(
                            "Defina o endereço, o método e como a rota aparece no painel."
                        ).classes("text-sm text-slate-400")
                    with ui.element("div").classes(
                        "grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_180px] gap-4 w-full"
                    ):
                        path = (
                            ui.input(
                                "Caminho",
                                value=state["route_path"],
                                placeholder="/clientes/{ID}",
                                on_change=lambda e: set_detection_value(
                                    "route_path", e.value
                                ),
                            )
                            .props("outlined stack-label")
                            .classes("w-full")
                        )
                        path.mark("route-path")
                        ui.select(
                            ["GET", "POST", "PUT", "PATCH", "DELETE"],
                            value=state["method"],
                            label="Método",
                            on_change=lambda e: set_detection_value(
                                "method", e.value
                            ),
                        ).props("outlined").classes("w-full")
                    with ui.element("div").classes(
                        "grid grid-cols-1 md:grid-cols-2 gap-4 w-full"
                    ):
                        ui.input(
                            "Descrição",
                            value=state["description"],
                            on_change=lambda e: set_value("description", e.value or ""),
                        ).props("outlined stack-label").classes("w-full")
                        ui.input(
                            "Tags",
                            value=state["tags"],
                            placeholder="clientes, financeiro",
                            on_change=lambda e: set_value("tags", e.value or ""),
                        ).props("outlined stack-label").classes("w-full")
                    ui.switch(
                        "Rota ativa",
                        value=state["is_active"],
                        on_change=lambda e: set_value("is_active", e.value),
                    )

                with section_card():
                    with ui.column().classes("gap-1"):
                        ui.label("SQL Firebird").classes("text-lg font-semibold")
                        ui.label(
                            "O SQL é preservado exatamente como informado e usa binds nomeados."
                        ).classes("text-sm text-slate-400")
                    sql = ui.codemirror(
                        value=state["query_sql"],
                        language="SQL",
                        theme="aura",
                        on_change=lambda e: set_detection_value(
                            "query_sql", e.value
                        ),
                    ).classes("f2-editor w-full")
                    sql.mark("route-sql")
                    detection_label = ui.label().props(
                        'role="status" aria-live="polite"'
                    ).classes("sr-only")

                with section_card():
                    with ui.row().classes("w-full items-start gap-4 flex-wrap"):
                        with ui.column().classes("gap-1 grow"):
                            ui.label("Parâmetros").classes("text-lg font-semibold")
                            ui.label(
                                "Placeholders e binds são detectados automaticamente sem remover configurações."
                            ).classes("text-sm text-slate-400")

                        def add_parameter() -> None:
                            if not require_authenticated():
                                return
                            state["parameters"].append(
                                {
                                    "id": None,
                                    "name": "",
                                    "param_type": "string",
                                    "source": "query" if state["method"] == "GET" else "body",
                                    "default_value": None,
                                    "required": True,
                                    "description": None,
                                    "validation_rule": None,
                                    "detected": False,
                                }
                            )
                            state["dirty"] = True
                            parameter_cards.refresh()

                        add_parameter_button = ui.button(
                            "Adicionar parâmetro", icon="add", on_click=add_parameter
                        ).props("outline no-caps")
                        add_parameter_button.mark("parameter-add")

                    def update_parameter(item: dict[str, Any], key: str, value: Any) -> None:
                        item[key] = value
                        state["dirty"] = True

                    def move_parameter(index: int, delta: int) -> None:
                        if not require_authenticated():
                            return
                        target = index + delta
                        if 0 <= target < len(state["parameters"]):
                            state["parameters"][index], state["parameters"][target] = (
                                state["parameters"][target],
                                state["parameters"][index],
                            )
                            state["dirty"] = True
                            parameter_cards.refresh()

                    def delete_parameter(index: int) -> None:
                        if not require_authenticated():
                            return
                        item = state["parameters"].pop(index)
                        name = str(item.get("name") or "")
                        if name in state["detected_names"]:
                            state["suppressed"].add(name)
                        state["dirty"] = True
                        parameter_cards.refresh()

                    @ui.refreshable
                    def parameter_cards() -> None:
                        if not state["parameters"]:
                            empty_state(
                                "Nenhum parâmetro",
                                "Adicione manualmente ou informe placeholders no caminho e binds no SQL.",
                                icon="data_object",
                            )
                            return
                        with ui.column().classes("w-full gap-3"):
                            for index, item in enumerate(state["parameters"]):
                                with ui.card().classes(
                                    "f2-card f2-card-flat f2-param-card w-full p-4 gap-4"
                                ):
                                    with ui.row().classes(
                                        "w-full items-center gap-2 flex-wrap"
                                    ):
                                        ui.button(
                                            icon="arrow_upward",
                                            on_click=lambda _, i=index: move_parameter(i, -1),
                                        ).props(
                                            'flat dense round aria-label="Mover parâmetro para cima"'
                                        )
                                        ui.button(
                                            icon="arrow_downward",
                                            on_click=lambda _, i=index: move_parameter(i, 1),
                                        ).props(
                                            'flat dense round aria-label="Mover parâmetro para baixo"'
                                        )
                                        if not item.get("detected"):
                                            ui.badge("Revisar", color="warning").props("outline")
                                        ui.space()
                                        ui.button(
                                            icon="delete_outline",
                                            on_click=lambda _, i=index: delete_parameter(i),
                                        ).props(
                                            'flat dense round color=negative aria-label="Remover parâmetro"'
                                        )
                                    with ui.element("div").classes(
                                        "grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 w-full"
                                    ):
                                        ui.input(
                                            "Nome",
                                            value=item.get("name", ""),
                                            on_change=lambda e, row=item: update_parameter(
                                                row, "name", e.value
                                            ),
                                        ).props("outlined dense").classes("w-full")
                                        source = ui.select(
                                            ["path", "query", "body"],
                                            value=item.get("source", "query"),
                                            label="Origem",
                                            on_change=lambda e, row=item: update_parameter(
                                                row, "source", e.value
                                            ),
                                        ).props("outlined dense").classes("w-full")
                                        ui.select(
                                            [
                                                "string",
                                                "integer",
                                                "float",
                                                "boolean",
                                                "date",
                                                "datetime",
                                            ],
                                            value=item.get("param_type", "string"),
                                            label="Tipo",
                                            on_change=lambda e, row=item: update_parameter(
                                                row, "param_type", e.value
                                            ),
                                        ).props("outlined dense").classes("w-full")
                                        required = ui.switch(
                                            "Obrigatório",
                                            value=bool(item.get("required", False)),
                                            on_change=lambda e, row=item: update_parameter(
                                                row, "required", e.value
                                            ),
                                        ).classes("self-center")
                                    with ui.expansion(
                                        "Detalhes opcionais", icon="tune"
                                    ).classes("w-full"):
                                        with ui.element("div").classes(
                                            "grid grid-cols-1 md:grid-cols-3 gap-3 w-full p-3 pt-0"
                                        ):
                                            ui.input(
                                                "Valor padrão",
                                                value=item.get("default_value") or "",
                                                on_change=lambda e, row=item: update_parameter(
                                                    row, "default_value", e.value or None
                                                ),
                                            ).props("outlined dense").classes("w-full")
                                            ui.input(
                                                "Regex",
                                                value=item.get("validation_rule") or "",
                                                on_change=lambda e, row=item: update_parameter(
                                                    row, "validation_rule", e.value or None
                                                ),
                                            ).props("outlined dense").classes("w-full")
                                            ui.input(
                                                "Descrição",
                                                value=item.get("description") or "",
                                                on_change=lambda e, row=item: update_parameter(
                                                    row, "description", e.value or None
                                                ),
                                            ).props("outlined dense").classes("w-full")
                                    if (
                                        item.get("source") == "path"
                                        and item.get("name") in state["detected_names"]
                                    ):
                                        source.disable()
                                        required.disable()

                    parameter_cards()

            def synchronize_parameters() -> None:
                detected = QueryService.detect_parameters(
                    state["route_path"], state["query_sql"], state["method"]
                )
                state["detected_names"] = {item["name"] for item in detected}
                state["parameters"], state["suppressed"] = QueryService.merge_parameter_drafts(
                    state["parameters"], detected, state["suppressed"]
                )
                if detected:
                    tokens = [
                        f"{{{item['name']}}}"
                        if item["source"] == "path"
                        else f":{item['name']}"
                        for item in detected
                    ]
                    count = len(detected)
                    detection_label.set_text(
                        f"{count} parâmetro{'s' if count != 1 else ''} detectado"
                        f"{'s' if count != 1 else ''}: {', '.join(tokens)}"
                    )
                else:
                    detection_label.set_text(
                        "Digite binds como :CLIENTE_ID ou placeholders como {ID}."
                    )
                parameter_cards.refresh()

            def schedule_detection(*_args: Any) -> None:
                state["generation"] += 1
                generation = state["generation"]

                def apply_detection() -> None:
                    if generation == state["generation"]:
                        synchronize_parameters()

                ui.timer(0.12, apply_detection, once=True)

            synchronize_parameters()

        def routes_page() -> None:
            page_header(
                "Rotas",
                "Cadastre e teste os comandos Firebird expostos pela API.",
                action_label="Nova rota",
                on_action=lambda: start_editor(),
            )
            with ui.element("div").classes(
                "grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_160px_160px] gap-3 w-full mb-4"
            ):
                search = (
                    ui.input(
                        "Buscar",
                        placeholder="Caminho, método ou tag",
                    )
                    .props('outlined stack-label clearable aria-label="Buscar rotas"')
                    .classes("w-full")
                )
                method_filter = ui.select(
                    ["Todos", "GET", "POST", "PUT", "PATCH", "DELETE"],
                    value="Todos",
                    label="Método",
                ).props("outlined")
                status_filter = ui.select(
                    ["Todos", "Ativas", "Inativas"], value="Todos", label="Status"
                ).props("outlined")

            @ui.refreshable
            def route_list() -> None:
                term = (search.value or "").lower()
                items = [
                    item
                    for item in QueryService.get_all_queries()
                    if term in json.dumps(item, default=str).lower()
                    and (method_filter.value == "Todos" or item["method"] == method_filter.value)
                    and (
                        status_filter.value == "Todos"
                        or (status_filter.value == "Ativas" and item["is_active"])
                        or (status_filter.value == "Inativas" and not item["is_active"])
                    )
                ]
                ui.label(f"{len(items)} rota(s)").classes("text-xs text-slate-400 mb-2")
                if not items:
                    empty_state(
                        "Nenhuma rota encontrada",
                        "Ajuste os filtros ou crie uma nova rota.",
                        icon="route",
                    )
                    return
                with ui.column().classes("w-full gap-3"):
                    for item in items:
                        with ui.card().classes(
                            "f2-card f2-card-flat f2-route-card w-full p-4 sm:p-5 gap-3"
                        ):
                            with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                                method_badge(item["method"])
                                ui.label(item["route_path"]).classes(
                                    "font-mono text-base font-medium grow min-w-0 break-all"
                                )
                                ui.badge(
                                    "Ativa" if item["is_active"] else "Inativa",
                                    color="positive" if item["is_active"] else "grey",
                                ).props("outline")
                                ui.button(
                                    "Testar",
                                    icon="play_arrow",
                                    on_click=lambda _, q=item: test_dialog(q),
                                ).props("outline no-caps")
                                ui.button(
                                    "Editar",
                                    icon="edit",
                                    on_click=lambda _, q=item: start_editor(q),
                                ).props("flat no-caps")

                                def remove(selected: dict[str, Any] = item) -> None:
                                    if not require_authenticated():
                                        return
                                    QueryService.delete_query(selected["id"])
                                    record_admin_audit(
                                        "query.delete", "query", selected["id"]
                                    )
                                    dynamic_loader.reload_app(fastapi_app)
                                    ui.notify("Rota excluída", type="positive")
                                    route_list.refresh()

                                ui.button(
                                    icon="delete_outline",
                                    on_click=lambda _, q=item: confirm_dialog(
                                        "Excluir rota?",
                                        f"A rota {q['method']} {q['route_path']} será removida permanentemente.",
                                        confirm_label="Excluir rota",
                                        on_confirm=lambda: remove(q),
                                    ),
                                ).props(
                                    'flat round color=negative aria-label="Excluir rota"'
                                )
                            description = item.get("description") or "Sem descrição"
                            ui.label(description).classes("text-sm text-slate-400")
                            tags = [tag.strip() for tag in (item.get("tags") or "").split(",") if tag.strip()]
                            if tags:
                                with ui.row().classes("gap-2 flex-wrap"):
                                    for tag in tags:
                                        ui.badge(tag, color="grey").props("outline")

            search.on("update:model-value", lambda: route_list.refresh())
            method_filter.on("update:model-value", lambda: route_list.refresh())
            status_filter.on("update:model-value", lambda: route_list.refresh())
            route_list()

        def keys_page() -> None:
            items = AccessKeyService.list_keys()

            def show_secret(created: dict[str, Any]) -> None:
                with ui.dialog() as secret_dialog, ui.card().classes(
                    "f2-dialog w-[calc(100vw-2rem)] max-w-xl p-0 gap-0"
                ):
                    secret_dialog.props("persistent")
                    with ui.column().classes("w-full gap-4 p-6"):
                        ui.label("Access Key criada").classes("text-xl font-semibold")
                        alert(
                            "Copie a chave agora. Ela não poderá ser exibida novamente.",
                            kind="warning",
                            icon="key",
                        )
                        ui.code(created["plain_key"]).classes(
                            "w-full break-all text-sm bg-slate-950 p-4 rounded-lg"
                        )

                        def copy_key() -> None:
                            if not require_authenticated():
                                return
                            ui.clipboard.write(created["plain_key"])
                            ui.notify("Chave copiada", type="positive")

                        ui.button("Copiar chave", icon="content_copy", on_click=copy_key).props(
                            "outline no-caps"
                        ).classes("w-full sm:w-auto")
                    with ui.row().classes(
                        "w-full justify-end px-6 py-4 border-t border-slate-700"
                    ):
                        def close_secret() -> None:
                            secret_dialog.close()
                            main_content.refresh()

                        ui.button(
                            "Já armazenei a chave",
                            icon="check",
                            on_click=close_secret,
                        ).props("unelevated no-caps")
                secret_dialog.open()

            def create_key_dialog() -> None:
                if not require_authenticated():
                    return
                with ui.dialog() as dialog, ui.card().classes(
                    "f2-dialog w-[calc(100vw-2rem)] max-w-md p-0 gap-0"
                ):
                    with ui.column().classes("w-full gap-4 p-6"):
                        ui.label("Criar Access Key").classes("text-xl font-semibold")
                        ui.label(
                            "Use uma descrição que identifique o consumidor desta chave."
                        ).classes("text-sm text-slate-400")
                        description = ui.input(
                            "Descrição", placeholder="Ex.: Integração do ERP"
                        ).props("outlined stack-label autofocus").classes("w-full")
                        description.mark("key-description")
                    with ui.row().classes(
                        "w-full justify-end gap-2 px-6 py-4 border-t border-slate-700"
                    ):
                        ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")

                        def create() -> None:
                            if not require_authenticated():
                                return
                            create_button.disable()
                            create_button.props("loading")
                            try:
                                created = AccessKeyService.create_key(
                                    description=description.value or ""
                                )
                                record_admin_audit(
                                    "access_key.create", "access_key", created["id"]
                                )
                                dialog.close()
                                show_secret(created)
                            except ValueError as exc:
                                ui.notify(str(exc), type="negative")
                                create_button.enable()
                                create_button.props(remove="loading")
                            except Exception as exc:
                                logger.warning(
                                    "Falha ao criar Access Key error_type=%s",
                                    type(exc).__name__,
                                )
                                ui.notify("Nao foi possivel criar a chave.", type="negative")
                                create_button.enable()
                                create_button.props(remove="loading")

                        create_button = ui.button(
                            "Criar chave", icon="add", on_click=create
                        ).props("unelevated no-caps")
                        create_button.mark("key-create-confirm")
                dialog.open()

            page_header(
                "Access Keys",
                "Gerencie as credenciais usadas pelos consumidores das APIs.",
                action_label="Criar chave" if items else None,
                on_action=create_key_dialog if items else None,
            )
            if not items:
                empty_state(
                    "Nenhuma Access Key",
                    "Crie uma chave para permitir o acesso de consumidores às rotas publicadas.",
                    icon="key_off",
                )
                ui.button("Criar primeira chave", icon="add", on_click=create_key_dialog).props(
                    "unelevated no-caps"
                ).classes("mt-4")
                return

            def edit_key_dialog(key: dict[str, Any]) -> None:
                if not require_authenticated():
                    return
                with ui.dialog() as dialog, ui.card().classes(
                    "f2-dialog w-[calc(100vw-2rem)] max-w-md p-0 gap-0"
                ):
                    with ui.column().classes("w-full gap-4 p-6"):
                        ui.label("Editar Access Key").classes("text-xl font-semibold")
                        description = ui.input(
                            "Descrição", value=key["description"] or ""
                        ).props("outlined stack-label autofocus").classes("w-full")
                        active = ui.switch("Chave ativa", value=key["is_active"])
                    with ui.row().classes(
                        "w-full justify-end gap-2 px-6 py-4 border-t border-slate-700"
                    ):
                        ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")

                        def save_key() -> None:
                            if not require_authenticated():
                                return
                            AccessKeyService.update_key(
                                key["id"],
                                description=description.value or "",
                                is_active=active.value,
                            )
                            record_admin_audit("access_key.update", "access_key", key["id"])
                            dialog.close()
                            ui.notify("Access Key atualizada", type="positive")
                            main_content.refresh()

                        ui.button("Salvar", icon="save", on_click=save_key).props(
                            "unelevated no-caps"
                        )
                dialog.open()

            def set_key_active(key: dict[str, Any], is_active: bool) -> None:
                if not require_authenticated():
                    return
                AccessKeyService.update_key(
                    key["id"],
                    description=key["description"] or "",
                    is_active=is_active,
                )
                record_admin_audit("access_key.update", "access_key", key["id"])
                ui.notify(
                    "Access Key ativada" if is_active else "Access Key desativada",
                    type="positive",
                )
                main_content.refresh()

            def request_key_status(key: dict[str, Any]) -> None:
                if not require_authenticated():
                    return
                if not key["is_active"]:
                    set_key_active(key, True)
                    return
                confirm_dialog(
                    "Desativar Access Key?",
                    "A chave deixará de autenticar imediatamente, mas poderá ser ativada novamente.",
                    confirm_label="Desativar chave",
                    on_confirm=lambda: set_key_active(key, False),
                )

            with ui.column().classes("w-full gap-3"):
                for key in items:
                    with ui.card().classes(
                        "f2-card f2-card-flat f2-key-card w-full p-4 sm:p-5 gap-4"
                    ):
                        with ui.row().classes(
                            "w-full items-start justify-between gap-4 flex-wrap"
                        ):
                            with ui.row().classes(
                                "items-center gap-3 grow min-w-[220px] flex-nowrap"
                            ):
                                with ui.element("div").classes(
                                    "w-10 h-10 shrink-0 rounded-lg bg-blue-500/10 "
                                    "text-blue-400 flex items-center justify-center"
                                ):
                                    ui.icon("key").classes("text-xl")
                                with ui.column().classes("gap-1 min-w-0"):
                                    ui.label(
                                        key["description"] or "Sem descrição"
                                    ).classes("font-semibold truncate")
                                    ui.label(key["key_prefix"] + "…").classes(
                                        "f2-key-prefix"
                                    )
                            with ui.row().classes(
                                "items-center justify-end gap-1 sm:gap-2 flex-wrap"
                            ):
                                ui.badge(
                                    "Ativa" if key["is_active"] else "Inativa",
                                    color="positive" if key["is_active"] else "grey",
                                ).props("outline")
                                ui.button(
                                    "Desativar" if key["is_active"] else "Ativar",
                                    icon="lock" if key["is_active"] else "lock_open",
                                    on_click=lambda _, selected=key: request_key_status(
                                        selected
                                    ),
                                ).props("flat no-caps").classes("max-sm:hidden")
                                ui.button(
                                    icon="lock" if key["is_active"] else "lock_open",
                                    on_click=lambda _, selected=key: request_key_status(
                                        selected
                                    ),
                                ).props(
                                    'flat round aria-label="'
                                    + (
                                        "Desativar Access Key"
                                        if key["is_active"]
                                        else "Ativar Access Key"
                                    )
                                    + '"'
                                ).classes("sm:hidden")
                                ui.button(
                                    "Editar",
                                    icon="edit",
                                    on_click=lambda _, selected=key: edit_key_dialog(
                                        selected
                                    ),
                                ).props("flat no-caps").classes("max-sm:hidden")
                                ui.button(
                                    icon="edit",
                                    on_click=lambda _, selected=key: edit_key_dialog(
                                        selected
                                    ),
                                ).props(
                                    'flat round aria-label="Editar Access Key"'
                                ).classes("sm:hidden")

                                def remove_key(selected: dict[str, Any] = key) -> None:
                                    if not require_authenticated():
                                        return
                                    AccessKeyService.delete_key(selected["id"])
                                    record_admin_audit(
                                        "access_key.delete", "access_key", selected["id"]
                                    )
                                    ui.notify("Access Key excluída", type="positive")
                                    main_content.refresh()

                                ui.button(
                                    icon="delete_outline",
                                    on_click=lambda _, selected=key: confirm_dialog(
                                        "Excluir Access Key?",
                                        "A chave deixará de autenticar imediatamente e não poderá ser recuperada.",
                                        confirm_label="Excluir chave",
                                        on_confirm=lambda: remove_key(selected),
                                    ),
                                ).props(
                                    'flat round color=negative aria-label="Excluir Access Key"'
                                )
                        ui.separator().classes("bg-slate-800")
                        with ui.element("div").classes(
                            "grid grid-cols-1 sm:grid-cols-3 gap-4 w-full"
                        ):
                            metadata = [
                                ("analytics", "Requisições", str(key["usage_count"])),
                                (
                                    "schedule",
                                    "Último uso",
                                    format_timestamp(key["last_used_at"]),
                                ),
                                (
                                    "calendar_today",
                                    "Criada em",
                                    format_timestamp(key["created_at"]),
                                ),
                            ]
                            for icon, label, value in metadata:
                                with ui.row().classes(
                                    "items-center gap-3 min-w-0 rounded-lg "
                                    "bg-slate-950/40 px-3 py-2"
                                ):
                                    ui.icon(icon).classes("text-blue-400 text-lg")
                                    with ui.column().classes("gap-0 min-w-0"):
                                        ui.label(label).classes("text-xs text-slate-500")
                                        ui.label(value).classes(
                                            "text-sm font-medium truncate"
                                        )

        def executions_page() -> None:
            rows = execution_service.list_executions("admin", is_admin=True, limit=200)
            page_header(
                "Execuções",
                "Acompanhe o histórico e cancele operações em andamento diretamente na lista.",
                action_label="Atualizar",
                action_icon="refresh",
                on_action=main_content.refresh,
            )

            def cancel_execution(execution_id: str) -> None:
                if not require_authenticated():
                    return
                result = execution_service.request_cancel(
                    execution_id, owner_hash="admin-ui", is_admin=True
                )
                if result["ok"]:
                    record_admin_audit("execution.cancel", "execution", execution_id)
                    ui.notify("Cancelamento solicitado", type="positive")
                else:
                    ui.notify("Execução não encontrada ou finalizada", type="warning")
                main_content.refresh()

            if not rows:
                empty_state(
                    "Nenhuma execução",
                    "As execuções de rotas aparecerão aqui.",
                    icon="history",
                )
                return

            display_rows = []
            for row in rows:
                item = dict(row)
                item["created_display"] = format_timestamp(item.get("created_at"))
                display_rows.append(item)

            columns = [
                {"name": "execution_id", "label": "ID", "field": "execution_id", "align": "left"},
                {"name": "http_method", "label": "Método", "field": "http_method", "align": "left"},
                {"name": "route_ref", "label": "Rota", "field": "route_ref", "align": "left"},
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
                {"name": "affected_rows", "label": "Linhas", "field": "affected_rows", "align": "left"},
                {"name": "created_display", "label": "Criada em (UTC)", "field": "created_display", "align": "left"},
                {"name": "actions", "label": "", "field": "actions", "align": "right"},
            ]
            table = ui.table(
                columns=columns,
                rows=display_rows,
                row_key="execution_id",
                pagination=20,
            ).props("flat dense").classes("f2-table w-full max-md:hidden")
            table.add_slot(
                "body-cell-status",
                """
                <q-td :props="props">
                  <q-badge outline :color="({queued:'grey',running:'blue',cancel_requested:'orange',completed:'positive',failed:'negative',canceled:'grey',timeout:'orange'})[props.value] || 'grey'">
                    {{ ({queued:'Na fila',running:'Em execução',cancel_requested:'Cancelamento solicitado',completed:'Concluída',failed:'Falhou',canceled:'Cancelada',timeout:'Tempo esgotado'})[props.value] || props.value }}
                  </q-badge>
                </q-td>
                """,
            )
            table.add_slot(
                "body-cell-actions",
                """
                <q-td :props="props">
                  <q-btn v-if="['queued','running'].includes(props.row.status)"
                    flat dense no-caps icon="cancel" label="Cancelar" color="negative"
                    data-testid="execution-cancel"
                    :aria-label="'Cancelar execução ' + props.row.execution_id"
                    @click="$parent.$emit('cancel_execution', props.row.execution_id)" />
                </q-td>
                """,
            )
            table.on("cancel_execution", lambda e: confirm_dialog(
                "Cancelar execução?",
                f"Será solicitado o cancelamento de {e.args}.",
                confirm_label="Solicitar cancelamento",
                on_confirm=lambda: cancel_execution(str(e.args)),
            ))

            with ui.column().classes("w-full gap-3 md:hidden"):
                for row in display_rows:
                    with ui.card().classes("f2-card f2-card-flat w-full p-4 gap-3"):
                        with ui.row().classes("w-full items-center gap-2"):
                            method_badge(row["http_method"])
                            ui.label(row["route_ref"]).classes("font-mono grow truncate")
                            status_badge(row["status"])
                        ui.label(row["execution_id"]).classes(
                            "font-mono text-xs text-slate-400 break-all"
                        )
                        with ui.row().classes("w-full items-center gap-4 flex-wrap text-sm"):
                            ui.label(f"Linhas: {row['affected_rows']}")
                            ui.label(row["created_display"]).classes("text-slate-400")
                            if row["status"] in {"queued", "running"}:
                                ui.space()
                                cancel_button = ui.button(
                                    "Cancelar",
                                    icon="cancel",
                                    on_click=lambda _, selected=row: confirm_dialog(
                                        "Cancelar execução?",
                                        f"Será solicitado o cancelamento de {selected['execution_id']}.",
                                        confirm_label="Solicitar cancelamento",
                                        on_confirm=lambda: cancel_execution(
                                            selected["execution_id"]
                                        ),
                                    ),
                                ).props("flat no-caps color=negative")
                                cancel_button.mark("execution-cancel")

        def system_page() -> None:
            page_header(
                "Sistema",
                "Informações da aplicação e atalhos operacionais.",
            )
            with ui.element("div").classes(
                "grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 w-full"
            ):
                status_items = [
                    (
                        "info",
                        "Versão",
                        settings.api_version,
                        "Versão atual da aplicação",
                    ),
                    (
                        "schema",
                        "Migration",
                        current_revision() or "Não aplicada",
                        "Revisão atual do metastore",
                    ),
                    (
                        "storage",
                        "Firebird",
                        "Configurado"
                        if settings.firebird_configured
                        else "Não configurado",
                        "Estado da conexão principal",
                    ),
                ]
                for icon, label, value, description in status_items:
                    with section_card("f2-system-card justify-between"):
                        with ui.row().classes("w-full items-start justify-between gap-3"):
                            with ui.column().classes("gap-2 min-w-0"):
                                ui.label(label).classes("text-sm text-slate-400")
                                ui.label(value).classes(
                                    "text-lg font-semibold break-all"
                                )
                            with ui.element("div").classes(
                                "w-9 h-9 shrink-0 rounded-lg bg-blue-500/10 "
                                "text-blue-400 flex items-center justify-center"
                            ):
                                ui.icon(icon).classes("text-xl")
                        ui.label(description).classes("text-xs text-slate-500")

                with section_card("f2-system-card justify-between"):
                    with ui.row().classes("w-full items-start justify-between gap-3"):
                        with ui.column().classes("gap-2"):
                            ui.label("Documentação").classes("text-sm text-slate-400")
                            ui.label("Swagger").classes("text-lg font-semibold")
                        with ui.element("div").classes(
                            "w-9 h-9 shrink-0 rounded-lg bg-blue-500/10 "
                            "text-blue-400 flex items-center justify-center"
                        ):
                            ui.icon("api").classes("text-xl")
                    ui.link("Abrir Swagger", "/docs", new_tab=True).classes(
                        "inline-flex items-center text-blue-400 font-medium no-underline "
                        "hover:text-blue-300"
                    )

        @ui.refreshable
        def main_content() -> None:
            if not require_authenticated():
                return
            with ui.column().classes("f2-page gap-0 min-h-screen"):
                if view["section"] == "dashboard":
                    dashboard_page()
                elif view["section"] == "routes":
                    if view.get("editor"):
                        route_editor_page()
                    else:
                        routes_page()
                elif view["section"] == "keys":
                    keys_page()
                elif view["section"] == "executions":
                    executions_page()
                else:
                    system_page()

        main_content()

    storage_secret = hashlib.sha256(
        b"fire2api:nicegui:v1\0" + settings.admin_api_key.encode()
    ).hexdigest()
    ui.run_with(
        fastapi_app,
        storage_secret=storage_secret,
        title=settings.api_title,
        favicon=Path("app/assets/fire2api-logo.svg"),
        language="pt-BR",
    )
