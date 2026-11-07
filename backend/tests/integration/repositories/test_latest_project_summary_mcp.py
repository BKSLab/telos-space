"""Полный сохранённый обзор через настоящий MCP Streamable HTTP и scoped-ключ."""

# ruff: noqa: F811 -- pytest внедряет импортированную общую фикстуру.

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update

from src.db.models import AgentToolRun, ProjectMember
from src.db.models.analytics_reports import AnalyticsReport
from src.db.models.api_keys import ApiKey
from src.mcp_server.server import build_mcp_app
from src.utils.api_scopes import SCOPE_PROJECTS_READ, SCOPE_TASKS_READ
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_latest_project_summary import report_data
from tests.integration.repositories.test_mcp_idempotent_writes import (
    mcp_env as mcp_env,
)
from tests.integration.repositories.test_mcp_idempotent_writes import (
    rpc_body,
    tool_result,
)


async def test_summary_transport_reads_full_latest_report_and_rechecks_access(mcp_env):
    env = mcp_env
    async with env.factory() as session:
        await session.execute(update(ApiKey).values(scopes=[SCOPE_PROJECTS_READ]))
        session.add(AnalyticsReport(**report_data(project_id=None, headline="Личный портфель")))
        session.add(AnalyticsReport(**report_data(project_id=2, headline="Чужой проект")))
        await session.commit()
    app = build_mcp_app(settings=env.settings)

    async def with_resources(scope, receive, send):
        scope["state"] = {**scope.get("state", {}), **env.state}
        await app(scope, receive, send)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=with_resources),
            base_url="http://testserver",
            headers={
                "Authorization": f"Bearer {env.keys[2].raw}",
                "Accept": "application/json, text/event-stream",
            },
        ) as client:
            initialized = await client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "saved-summary-test", "version": "1"},
                    },
                },
            )
            negotiated = rpc_body(initialized)["result"]
            client.headers["MCP-Session-Id"] = initialized.headers["mcp-session-id"]
            client.headers["MCP-Protocol-Version"] = negotiated["protocolVersion"]
            await client.post("/", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            sequence = 1

            async def invoke(name, arguments):
                nonlocal sequence
                sequence += 1
                return rpc_body(
                    await client.post(
                        "/",
                        json={
                            "jsonrpc": "2.0",
                            "id": sequence,
                            "method": "tools/call",
                            "params": {"name": name, "arguments": arguments},
                        },
                    )
                )

            empty = tool_result(
                await invoke("tt_get_latest_project_summary", {"project_key": "P1"})
            )
            assert empty["found"] is False and empty["report"] is None
            assert (await invoke("tt_get_latest_project_summary", {"project_key": "P2"}))["result"][
                "isError"
            ]

            async with env.factory() as session:
                session.add(AnalyticsReport(**report_data(headline="Предыдущий обзор")))
                latest = AnalyticsReport(**report_data(headline="Последний обзор 🔎"))
                session.add(latest)
                await session.commit()
                latest_id = latest.id
            result = tool_result(
                await invoke("tt_get_latest_project_summary", {"project_key": "P1"})
            )
            assert (
                result["found"] is True and result["source_id"] == f"analytics_report:{latest_id}"
            )
            assert result["report"]["headline"] == "Последний обзор 🔎"
            assert result["report"]["created_by"] == "Автор сохранённого анализа"
            assert (
                result["report"]["recommendations"][-1]["detail"]
                == report_data()["payload"]["recommendations"][-1]["detail"]
            )
            generic = tool_result(
                await invoke(
                    "tt_execute_project_tool",
                    {
                        "project_key": "P1",
                        "name": "get_latest_project_summary",
                        "arguments": {},
                    },
                )
            )
            assert generic == result
            async with env.factory() as session:
                assert await session.scalar(select(func.count()).select_from(AnalyticsReport)) == 4
                assert await session.scalar(select(func.count()).select_from(AgentToolRun)) == 0
                await session.execute(
                    update(ApiKey)
                    .where(ApiKey.id == env.keys[2].id)
                    .values(scopes=[SCOPE_TASKS_READ])
                )
                await session.commit()
            assert (await invoke("tt_get_latest_project_summary", {"project_key": "P1"}))["result"][
                "isError"
            ]
            async with env.factory() as session:
                await session.execute(
                    update(ApiKey)
                    .where(ApiKey.id == env.keys[2].id)
                    .values(scopes=[SCOPE_PROJECTS_READ], is_active=False)
                )
                await session.commit()
            assert (await invoke("tt_get_latest_project_summary", {"project_key": "P1"}))["result"][
                "isError"
            ]
            async with env.factory() as session:
                await session.execute(
                    update(ApiKey).where(ApiKey.id == env.keys[2].id).values(is_active=True)
                )
                await session.execute(
                    delete(ProjectMember).where(
                        ProjectMember.project_id == 1, ProjectMember.user_id == 2
                    )
                )
                await session.commit()
            assert (await invoke("tt_get_latest_project_summary", {"project_key": "P1"}))["result"][
                "isError"
            ]
