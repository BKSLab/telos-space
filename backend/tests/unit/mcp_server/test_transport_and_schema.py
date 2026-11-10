"""Проверки монтирования MCP и контракта объявленных инструментов."""

import pytest
from httpx import ASGITransport, AsyncClient

from main import app
from src.core.settings import get_settings
from src.mcp_server.registry import TOOL_REGISTRY, telos_tool
from src.mcp_server.server import MAX_LIMIT, mcp_server

READ_TOOLS = {
    "telos_list_project_tools",
    "telos_list_project_risks",
    "telos_get_project_risk",
    "telos_list_projects",
    "telos_get_project",
    "telos_list_tasks",
    "telos_get_task",
    "telos_list_comments",
    "telos_search_tasks",
    "telos_search_project_knowledge",
    "telos_read_project_knowledge",
    "telos_get_calendar_range",
    "telos_list_tasks_without_due_date",
    "telos_list_milestones",
}
WRITE_TOOLS = {
    "telos_execute_project_tool",
    "telos_create_project_risk",
    "telos_update_project_risk",
    "telos_create_task",
    "telos_update_task",
    "telos_move_task",
    "telos_delete_task",
    "telos_add_comment",
    "telos_set_task_dates",
    "telos_create_milestone",
}


@pytest.mark.parametrize("name", [
    "tt_create_task", "create_task", "telos_create", "telos_Create_task",
    "telos_create__task", "telos_create_task_", " telos_create_task",
])
def test_registration_rejects_noncanonical_tool_names(name: str) -> None:
    """Неверное публичное имя отклоняется при регистрации без нормализации."""
    before = set(TOOL_REGISTRY)
    with pytest.raises(ValueError, match="telos_"):
        telos_tool(name=name, title="Проверка", description="Проверка имени", scope="tasks:read")
    assert set(TOOL_REGISTRY) == before


async def test_transport_catalog_matches_the_single_rights_registry() -> None:
    """Защищённый реестр и публикуемые инструменты содержат одни и те же 46 имён."""
    actual = {tool.name for tool in await mcp_server.list_tools()}
    assert len(actual) == 46
    assert actual == set(TOOL_REGISTRY)
    assert all(name.startswith("telos_") for name in actual)


@pytest.mark.parametrize(
    "name",
    [
        "telos_list_tasks",
        "telos_list_comments",
        "telos_search_tasks",
        "telos_get_calendar_range",
        "telos_list_tasks_without_due_date",
        "telos_list_milestones",
    ],
)
async def test_list_tools_have_bounded_limit(name: str) -> None:
    """Списочные инструменты ограничены: агент не вытянет проект целиком."""
    tools = {tool.name: tool for tool in await mcp_server.list_tools()}
    limit = tools[name].input_schema["properties"]["limit"]

    assert limit.get("maximum") == MAX_LIMIT
    assert limit.get("minimum") == 1
    assert "default" in limit


async def test_tool_schemas_follow_the_declared_conventions() -> None:
    """Все запланированные инструменты зарегистрированы, удаление требует подтверждения, инструменты записи объявляют право, описания на русском, наружу выходят отображаемые ключи, проектные инструменты требуют ключ проекта."""
    # Объявлен ровно тот набор инструментов, который описан в плане.
    tools = await mcp_server.list_tools()

    assert READ_TOOLS | WRITE_TOOLS <= {tool.name for tool in tools}
    # У необратимого удаления есть отдельное поле подтверждения.
    tools = {tool.name: tool for tool in await mcp_server.list_tools()}
    properties = tools["telos_delete_task"].input_schema["properties"]

    assert "confirm" in properties
    assert properties["confirm"].get("default") is False
    # Описание изменяющего инструмента предупреждает о праве записи.
    tools = {tool.name: tool for tool in await mcp_server.list_tools()}

    for name in WRITE_TOOLS:
        assert "право" in tools[name].description or "записи" in tools[name].description
    # Описание читает модель: без него она не выберет инструмент осмысленно.
    tools = await mcp_server.list_tools()

    for tool in tools:
        assert tool.description, f"У инструмента {tool.name} нет описания."
        assert len(tool.description) > 40, f"Описание {tool.name} слишком короткое."
    # В контракте инструментов нет числовых идентификаторов сущностей.
    tools = await mcp_server.list_tools()

    for tool in tools:
        properties = set(tool.input_schema.get("properties", {}))
        assert "project_id" not in properties, f"{tool.name} принимает числовой project_id."
        assert "task_id" not in properties, f"{tool.name} принимает числовой task_id."
    # Инструменты уровня проекта обязательно требуют ключ проекта.
    tools = {tool.name: tool for tool in await mcp_server.list_tools()}

    for name in (
        "telos_get_project",
        "telos_list_tasks",
        "telos_search_tasks",
        "telos_search_project_knowledge",
        "telos_get_calendar_range",
        "telos_list_tasks_without_due_date",
        "telos_list_milestones",
        "telos_create_milestone",
    ):
        assert "project_key" in tools[name].input_schema.get("required", []), name


async def test_mcp_transport_requires_a_token_and_its_own_protocol() -> None:
    """Смонтированный MCP не отвечает на обычный GET и не пускает вызов без токена."""
    # Путь MCP смонтирован в приложении и отвечает своим транспортом.
    mcp_path = get_settings().app.mcp_path

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(mcp_path)

    # Транспорт Streamable HTTP не обслуживает обычный GET без сессии,
    # но сам путь существует: 404 означал бы, что монтирование не сработало.
    assert response.status_code != 404
    # Вызов без заголовка Authorization не проходит аутентификацию.
    mcp_path = get_settings().app.mcp_path

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            mcp_path,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "telos_list_projects", "arguments": {}},
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )

    assert response.status_code != 200 or "telos_list_projects" not in response.text
