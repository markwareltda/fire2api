from __future__ import annotations

import pytest
from nicegui import app as nicegui_app
from nicegui import ui
from nicegui.testing import User

from tests.conftest import TEST_ADMIN_KEY

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.nicegui_main_file("tests/ui_main.py"),
]


async def _login(user: User) -> None:
    await user.open("/")
    user.find(marker="login-key").type(TEST_ADMIN_KEY)
    user.find(marker="login-submit").click()
    await user.should_see("Dashboard")


async def test_ui_login_validation_and_navigation(user: User) -> None:
    await user.open("/")
    await user.should_see("Acesse o painel")
    await user.should_see("By Markware")

    user.find(marker="login-key").type("chave-incorreta")
    user.find(marker="login-submit").click()
    await user.should_see("A chave administrativa é inválida.")

    user.find(marker="login-key").clear().type(TEST_ADMIN_KEY)
    user.find(marker="login-submit").click()
    await user.should_see("Dashboard")
    await user.should_see(marker="nav-routes")
    await user.should_see(marker="nav-keys")
    await user.should_see(marker="nav-executions")


async def test_ui_open_page_cannot_act_after_session_expiration(user: User) -> None:
    await _login(user)
    with user:
        nicegui_app.storage.user["admin_marker"] = "1.invalid"
    user.find(marker="nav-routes").click()
    await user.should_see("Acesse o painel", retries=10)


async def test_ui_route_workspace_and_discard_confirmation(user: User) -> None:
    await _login(user)
    user.find(marker="nav-routes").click()
    await user.should_see("Nova rota")

    user.find(kind=ui.button, content="Nova rota").click()
    await user.should_see("Configuração geral")
    await user.should_see(marker="route-path")
    await user.should_see(marker="route-sql")
    await user.should_not_see(marker="route-delete")

    user.find(marker="parameter-add").click()
    await user.should_see("Detalhes opcionais")
    user.find(kind=ui.button, content="Cancelar").click()
    await user.should_see("Descartar alterações?")
    user.find(kind=ui.button, content="Descartar").click()
    await user.should_see("Cadastre e teste os comandos Firebird expostos pela API.")


async def test_ui_sql_parameter_detection_is_live(user: User) -> None:
    await _login(user)
    user.find(marker="nav-routes").click()
    await user.should_see("Nova rota")
    user.find(kind=ui.button, content="Nova rota").click()
    await user.should_see(marker="route-sql")

    user.find(marker="route-sql").type(":cliente_id ")
    await user.should_see("1 parâmetro detectado: :CLIENTE_ID", retries=10)
    await user.should_see("cliente_id")


async def test_ui_route_list_management_and_delete(user: User) -> None:
    from app.core.query_service import QueryService

    query_id = QueryService.save_query_configuration(
        None,
        {
            "route_path": "/ui-list-route",
            "method": "GET",
            "query_sql": "select 1 from rdb$database",
            "description": "Rota sintética para validar a nova listagem",
            "tags": "agenda, interno, leitura",
            "is_active": True,
        },
        [],
    )
    try:
        await _login(user)
        user.find(marker="nav-routes").click()

        await user.should_see("/ui-list-route")
        await user.should_see("Rota sintética para validar a nova listagem")
        await user.should_see("Ativa")
        await user.should_see("agenda")
        await user.should_see("+2")
        await user.should_not_see("interno")

        user.find(marker="route-search").type("rota-que-nao-existe")
        await user.should_see("Nenhuma rota encontrada")
        user.find(marker="route-search").clear()
        await user.should_see("/ui-list-route")

        user.find(marker=f"route-test-{query_id}").click()
        await user.should_see("Testar rota")
        user.find(marker="route-test-close").click()

        user.find(marker=f"route-manage-{query_id}").click()
        await user.should_see("Editar rota")
        await user.should_see(marker="route-delete")

        user.find(marker="route-delete").click()
        await user.should_see("Excluir rota?")
        user.find(marker="confirm-cancel").click()
        assert QueryService.get_query_by_id(query_id) is not None

        user.find(marker="route-delete").click()
        user.find(marker="confirm-apply").click()
        await user.should_see("Cadastre e teste os comandos Firebird expostos pela API.")
        await user.should_not_see("/ui-list-route")
        assert QueryService.get_query_by_id(query_id) is None
    finally:
        QueryService.delete_query(query_id)


async def test_ui_access_key_creation_shows_secret_once(user: User) -> None:
    await _login(user)
    user.find(marker="nav-keys").click()
    await user.should_see("Gerencie as credenciais usadas pelos consumidores das APIs.")

    try:
        create_button = user.find(kind=ui.button, content="Criar primeira chave")
    except AssertionError:
        create_button = user.find(kind=ui.button, content="Criar chave")
    create_button.click()
    await user.should_see("Use uma descrição que identifique o consumidor desta chave.")
    user.find(marker="key-description").type("Teste da interface")
    user.find(marker="key-create-confirm").click()

    await user.should_see("Access Key criada")
    await user.should_see("Ela não poderá ser exibida novamente.")
    user.find(kind=ui.button, content="Já armazenei a chave").click()
    await user.should_see("Teste da interface")
    await user.should_see("Requisições")
    await user.should_see("Desativar")


async def test_ui_execution_can_be_canceled_from_list(user: User) -> None:
    from app.core.database import get_db_connection

    execution_id = "ui-running-execution-000000000001"
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO execution_history
                (execution_id, owner_hash, route_type, route_ref, http_method,
                 statement_type, status, affected_rows)
            VALUES (?, 'ui-test', 'api', '/rota-em-execucao', 'GET',
                    'select', 'running', 0)
            """,
            (execution_id,),
        )
        conn.commit()

    await _login(user)
    user.find(marker="nav-executions").click()
    await user.should_see(
        "Acompanhe o histórico e cancele operações em andamento diretamente na lista."
    )
    await user.should_see(marker="execution-cancel")

    user.find(marker="execution-cancel").click()
    await user.should_see("Cancelar execução?")
    user.find(kind=ui.button, content="Solicitar cancelamento").click()
    await user.should_see("Cancelamento solicitado")
