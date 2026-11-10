"""MCP-записи с квитанциями, реальной аутентификацией и commit PostgreSQL."""

# ruff: noqa: F811 -- pytest внедряет импортированную фикстуру по имени параметра.

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.app_state import (
    RUNTIME_STATE_KEY,
    SESSION_FACTORY_STATE_KEY,
    SETTINGS_STATE_KEY,
)
from src.core.settings import get_settings
from src.db.models import AgentToolRun, ProjectMember, Task
from src.db.models.api_keys import ApiKey
from src.db.models.project_stages import ProjectStage
from src.db.models.task_activity import TaskActivity
from src.db.models.task_comments import TaskComment
from src.db.models.task_participants import TaskParticipant
from src.exceptions.agent_tools import AgentToolRunsRepositoryError
from src.mcp_server import write_tools
from src.mcp_server.server import build_mcp_app, mcp_server
from src.repositories.agent_tool_runs import AgentToolRunsRepository
from src.utils.api_keys import generate_api_key, hash_api_key
from src.utils.api_scopes import (
    SCOPE_COMMENTS_WRITE,
    SCOPE_TASKS_DELETE,
    SCOPE_TASKS_READ,
    SCOPE_TASKS_WRITE,
)
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)

TEST_SCOPES = [SCOPE_TASKS_READ, SCOPE_TASKS_WRITE, SCOPE_TASKS_DELETE, SCOPE_COMMENTS_WRITE]
WRITE_NAMES = ("telos_create_task", "telos_add_comment")


@pytest_asyncio.fixture
async def mcp_env(agent_env, tmp_path):
    """Синтетические ключи и ресурсы используют только отдельную тестовую БД."""
    settings = get_settings().model_copy(deep=True)
    settings.app.uploads_path = tmp_path
    settings.app.app_public_url = "http://testserver"
    settings.app.mcp_allowed_hosts = ["testserver", "testserver:*"]
    settings.knowledge.knowledge_enabled = False
    # У вызова одновременно живут сессия аутентификации и область действия.
    engine = create_async_engine(agent_env.engine.url, pool_size=8, max_overflow=0, pool_timeout=3)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    keys = {}
    async with factory() as session:
        session.add_all(
            ProjectStage(project_id=i, name="Бэклог", color="#334455", order_index=0)
            for i in (1, 2)
        )
        for user_id in (1, 2):
            raw_key, prefix = generate_api_key()
            row = ApiKey(
                id=uuid4(),
                label="Синтетическая MCP-проверка",
                key_hash=hash_api_key(raw_key),
                key_prefix=prefix,
                owner_user_id=user_id,
                kind="personal",
                scopes=TEST_SCOPES.copy(),
                is_active=True,
            )
            session.add(row)
            keys[user_id] = SimpleNamespace(raw=raw_key, id=row.id)
        await session.commit()
    state = {
        RUNTIME_STATE_KEY: SimpleNamespace(),
        SETTINGS_STATE_KEY: settings,
        SESSION_FACTORY_STATE_KEY: factory,
    }
    try:
        yield SimpleNamespace(factory=factory, state=state, keys=keys, settings=settings)
    finally:
        await engine.dispose()


def context(env, user_id=1):
    return SimpleNamespace(
        headers={"Authorization": f"Bearer {env.keys[user_id].raw}"},
        request_context=SimpleNamespace(
            request=SimpleNamespace(state=SimpleNamespace(**env.state))
        ),
    )


async def arguments(env, name):
    if name == "telos_create_task":
        return {
            "project_key": "P1",
            "title": "Проверить идемпотентную запись",
            "description": "Описание **задачи**",
            "stage": "Бэклог",
            "priority": "HIGH",
            "assignee": "user2",
            "observers": ["user1"],
            "start_date": "2026-11-07",
            "due_date": "2026-11-09",
        }
    parent = await write_tools.create_task(
        context=context(env), project_key="P1", title="Задача для комментария"
    )
    return {
        "task_key": parent["task_key"],
        "body": "Проверка **комментария**",
        "author": "Непроверенная подпись клиента",
    }


async def call(env, name, payload, request_id=None, user_id=1):
    # tool_context заново собирает AuthService и AgentActionsService на каждый вызов.
    return await mcp_server._tool_manager.get_tool(name).fn(
        context=context(env, user_id), request_id=request_id, **payload
    )


async def snapshot(env):
    async with env.factory() as session:
        return {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (Task, TaskComment, TaskActivity, TaskParticipant, AgentToolRun)
        }


async def assert_single_write(env, name, request_id, baseline):
    model = Task if name == "telos_create_task" else TaskComment
    current = await snapshot(env)
    assert current[model.__tablename__] == baseline[model.__tablename__] + 1
    assert current[AgentToolRun.__tablename__] == baseline[AgentToolRun.__tablename__] + 1
    async with env.factory() as session:
        receipt = (
            await session.scalars(select(AgentToolRun).where(AgentToolRun.request_id == request_id))
        ).one()
        assert receipt.tool_name == name
        assert receipt.status == "completed"
        assert receipt.message_id is None and receipt.chat_turn_id is None


@pytest.mark.parametrize("name", WRITE_NAMES)
async def test_lost_response_retry_uses_fresh_services_and_unkeyed_calls_still_create(
    mcp_env, name
):
    env = mcp_env
    payload = await arguments(env, name)
    baseline = await snapshot(env)
    request_id = uuid4()
    lost_response = await call(env, name, payload, request_id)
    committed = await snapshot(env)
    repeated = await call(env, name, payload, request_id)
    assert repeated == lost_response
    assert await snapshot(env) == committed
    await assert_single_write(env, name, request_id, baseline)
    if name == "telos_add_comment":
        assert repeated["author"] == "1 Участник"
        assert repeated["comment_id"] > 0 and repeated["created_at"]
    else:
        assert repeated["created"] is True and repeated["task_key"].startswith("P1-")
    first_unkeyed = await call(env, name, payload)
    second_unkeyed = await call(env, name, payload)
    identity = "task_key" if name == "telos_create_task" else "comment_id"
    assert len({repeated[identity], first_unkeyed[identity], second_unkeyed[identity]}) == 3
    assert (await snapshot(env))["agent_tool_runs"] == 1


@pytest.mark.parametrize("name", WRITE_NAMES)
async def test_concurrent_same_key_writes_exactly_one_object_and_receipt(
    mcp_env, name, monkeypatch
):
    env = mcp_env
    payload = await arguments(env, name)
    baseline = await snapshot(env)
    request_id = uuid4()
    both_arrived = asyncio.Event()
    arrivals = 0
    original_lock = AgentToolRunsRepository.lock_request

    async def synchronized_lock(self, project_id, user_id, incoming_id):
        nonlocal arrivals
        if incoming_id == request_id:
            arrivals += 1
            if arrivals == 2:
                both_arrived.set()
            await asyncio.wait_for(both_arrived.wait(), timeout=5)
        await original_lock(self, project_id, user_id, incoming_id)

    monkeypatch.setattr(AgentToolRunsRepository, "lock_request", synchronized_lock)
    first, duplicate = await asyncio.wait_for(
        asyncio.gather(call(env, name, payload, request_id), call(env, name, payload, request_id)),
        timeout=15,
    )
    assert arrivals == 2 and first == duplicate
    await assert_single_write(env, name, request_id, baseline)


@pytest.mark.parametrize("name", WRITE_NAMES)
async def test_same_key_with_different_arguments_is_a_tool_error(mcp_env, name):
    env = mcp_env
    payload = await arguments(env, name)
    request_id = uuid4()
    await call(env, name, payload, request_id)
    committed = await snapshot(env)
    changed = {**payload, "title" if name == "telos_create_task" else "body": "Другой текст"}
    with pytest.raises(ToolError, match="Ключ повтора уже использован.*request_id"):
        await call(env, name, changed, request_id)
    assert await snapshot(env) == committed


async def test_create_receipt_replays_original_names_after_stage_rename_and_member_removal(mcp_env):
    env = mcp_env
    payload = {**await arguments(env, "telos_create_task"), "observers": ["user2"]}
    baseline = await snapshot(env)
    request_id = uuid4()
    original = await call(env, "telos_create_task", payload, request_id)
    async with env.factory() as session:
        await session.execute(
            update(ProjectStage)
            .where(ProjectStage.project_id == 1, ProjectStage.name == payload["stage"])
            .values(name="Переименованная стадия")
        )
        await session.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 2)
        )
        await session.commit()
    committed = await snapshot(env)
    assert await call(env, "telos_create_task", payload, request_id) == original
    registered = mcp_server._tool_manager.get_tool("telos_create_task").fn
    assert await registered(context=context(env), request_id=request_id, **payload) == original
    changed = {**payload, "stage": "Несуществующая новая стадия"}
    for tool in (write_tools.create_task, registered):
        with pytest.raises(ToolError, match="Ключ повтора уже использован.*request_id"):
            await tool(context=context(env), request_id=request_id, **changed)
    await assert_single_write(env, "telos_create_task", request_id, baseline)
    assert await snapshot(env) == committed


@pytest.mark.parametrize("name", WRITE_NAMES)
async def test_receipt_failure_rolls_back_domain_commit_and_allows_clean_retry(
    mcp_env, name, monkeypatch
):
    env = mcp_env
    payload = await arguments(env, name)
    baseline = await snapshot(env)
    request_id = uuid4()
    original_save = AgentToolRunsRepository.save

    async def fail_after_insert(self, data):
        await original_save(self, data)
        raise AgentToolRunsRepositoryError("Синтетический сбой сохранения квитанции")

    with monkeypatch.context() as faults:
        faults.setattr(AgentToolRunsRepository, "save", fail_after_insert)
        with pytest.raises(ToolError):
            await call(env, name, payload, request_id)
    assert await snapshot(env) == baseline
    await call(env, name, payload, request_id)
    await assert_single_write(env, name, request_id, baseline)


@pytest.mark.parametrize("name", WRITE_NAMES)
async def test_retry_rechecks_project_membership_before_returning_receipt(mcp_env, name):
    env = mcp_env
    payload = await arguments(env, name)
    request_id = uuid4()
    await call(env, name, payload, request_id, user_id=2)
    async with env.factory() as session:
        await session.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 2)
        )
        await session.commit()
    committed = await snapshot(env)
    with pytest.raises(ToolError, match="Проект недоступен"):
        await call(env, name, payload, request_id, user_id=2)
    assert await snapshot(env) == committed


@pytest.mark.parametrize("user_id", (1, 2))
async def test_create_retry_does_not_return_receipt_for_transferred_task(mcp_env, user_id):
    env = mcp_env
    payload = await arguments(env, "telos_create_task")
    request_id = uuid4()
    created = await call(env, "telos_create_task", payload, request_id, user_id=user_id)
    moved = await write_tools.transfer_task(
        context=context(env),
        task_key=created["task_key"],
        target_project_key="P2",
        remove_outsiders=True,
    )
    assert moved["task_key"].startswith("P2-")
    committed = await snapshot(env)
    with pytest.raises(ToolError):
        await call(env, "telos_create_task", payload, request_id, user_id=user_id)
    assert await snapshot(env) == committed


async def test_create_retry_does_not_return_receipt_for_deleted_task(mcp_env):
    env = mcp_env
    payload = await arguments(env, "telos_create_task")
    request_id = uuid4()
    created = await call(env, "telos_create_task", payload, request_id)
    await write_tools.delete_task(context=context(env), task_key=created["task_key"], confirm=True)
    committed = await snapshot(env)
    assert committed["tasks"] == 0 and committed["agent_tool_runs"] == 1
    with pytest.raises(ToolError):
        await call(env, "telos_create_task", payload, request_id)
    assert await snapshot(env) == committed


def rpc_body(response):
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("application/json"):
        return response.json()
    messages = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert len(messages) == 1, response.text
    return messages[0]


def tool_result(envelope):
    result = envelope["result"]
    assert not result.get("isError"), result
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


async def test_streamable_http_uuid_retries_scopes_and_revoked_synthetic_key(mcp_env):
    env = mcp_env
    app = build_mcp_app(settings=env.settings)

    async def with_resources(scope, receive, send):
        scope["state"] = {**scope.get("state", {}), **env.state}
        await app(scope, receive, send)

    headers = {
        "Authorization": f"Bearer {env.keys[1].raw}",
        "Accept": "application/json, text/event-stream",
    }
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=with_resources),
            base_url="http://testserver",
            headers=headers,
            timeout=15,
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
                        "clientInfo": {"name": "synthetic-idempotency-test", "version": "1"},
                    },
                },
            )
            initialization = rpc_body(initialized)["result"]
            client.headers["MCP-Session-Id"] = initialized.headers["mcp-session-id"]
            client.headers["MCP-Protocol-Version"] = initialization["protocolVersion"]
            notified = await client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
            )
            assert notified.status_code == 202
            sequence = 1

            async def invoke(name, payload):
                nonlocal sequence
                sequence += 1
                response = await client.post(
                    "/",
                    json={
                        "jsonrpc": "2.0",
                        "id": sequence,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": payload},
                    },
                )
                return rpc_body(response)

            listing = rpc_body(await client.post("/", json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/list",
            }))["result"]["tools"]
            names = {tool["name"] for tool in listing}
            assert len(names) == 46 and all(name.startswith("telos_") for name in names)
            removed_names = {"tt_" + name.removeprefix("telos_") for name in names}
            removed_names -= {"tt_ask_aris", "tt_get_aris_result"}
            removed_names |= {"tt_projects_ask", "tt_projects_result"}
            removed_names |= {
                "list_project_tools", "execute_project_tool", "list_project_risks", "get_project_risk",
                "create_project_risk", "update_project_risk", "list_projects", "get_project", "list_tasks",
                "get_task", "list_comments", "search_tasks", "search_project_knowledge", "read_project_knowledge",
                "get_calendar_range", "list_tasks_without_due_date", "list_milestones", "create_task", "update_task",
                "move_task", "delete_task", "add_comment", "set_task_dates", "create_milestone",
            }
            untouched = await snapshot(env)
            assert len(removed_names) == 70 and not names & removed_names
            for removed in sorted(removed_names):
                refused = await invoke(removed, {})
                assert refused["result"]["isError"] is True
                assert "Unknown tool" in refused["result"]["content"][0]["text"]
            assert await snapshot(env) == untouched

            long_description = (
                "# Полный исходный текст\n\n"
                + "Кириллица, **Markdown**, emoji 🔎 и строки таблицы | A | Б |.\n" * 120
                + "\nПоследняя строка должна сохраниться дословно."
            )
            create_payload = {
                "project_key": "P1",
                "title": "Задача через MCP HTTP",
                "description": long_description,
                "request_id": str(uuid4()),
            }
            created = tool_result(await invoke("telos_create_task", create_payload))
            assert tool_result(await invoke("telos_create_task", create_payload)) == created
            lookup = {"task_key": created["task_key"]}
            preview = tool_result(await invoke("telos_get_task", lookup))
            assert preview["description_meta"]["truncated"] is True
            assert preview["description_meta"]["length"] == len(long_description)
            full = tool_result(await invoke("telos_get_task", {**lookup, "full_text": True}))
            assert full["description"].encode("utf-8") == long_description.encode("utf-8")
            assert full["description_meta"]["truncated"] is False
            revised_description = long_description + "\nСогласованное дополнение."
            updated = tool_result(
                await invoke(
                    "telos_update_task",
                    {
                        **lookup,
                        "description": revised_description,
                        "expected_revision": full["revision"],
                    },
                )
            )
            assert updated["revision"] == full["revision"] + 1
            stale = await invoke(
                "telos_update_task",
                {
                    **lookup,
                    "description": "Устаревшая замена",
                    "expected_revision": full["revision"],
                },
            )
            assert stale["result"]["isError"] is True
            fresh = tool_result(await invoke("telos_get_task", {**lookup, "full_text": True}))
            assert fresh["description"] == revised_description
            assert fresh["revision"] == updated["revision"]
            comment_payload = {
                "task_key": created["task_key"],
                "body": "Комментарий через MCP HTTP",
                "request_id": str(uuid4()),
            }
            comment = tool_result(await invoke("telos_add_comment", comment_payload))
            assert tool_result(await invoke("telos_add_comment", comment_payload)) == comment
            assert comment["comment_id"] > 0 and comment["author"] == "1 Участник"
            temporary = tool_result(await invoke("telos_create_task", {
                "project_key": "P1", "title": "Явно подтверждаемое удаление",
            }))
            before_delete = await snapshot(env)
            no_confirmation = await invoke("telos_delete_task", {"task_key": temporary["task_key"]})
            assert no_confirmation["result"]["isError"] is True
            assert "confirm=true" in no_confirmation["result"]["content"][0]["text"]
            assert await snapshot(env) == before_delete
            deleted = tool_result(await invoke("telos_delete_task", {
                "task_key": temporary["task_key"], "confirm": True,
            }))
            assert deleted == {"task_key": temporary["task_key"], "deleted": True}
            committed = await snapshot(env)
            assert committed["tasks"] == 1 and committed["task_comments"] == 1
            assert committed["agent_tool_runs"] == 2
            conflict = await invoke("telos_create_task", {**create_payload, "title": "Другой текст"})
            assert conflict["result"]["isError"] is True
            invalid_uuid = await invoke(
                "telos_create_task", {**create_payload, "request_id": "invalid"}
            )
            assert invalid_uuid["result"]["isError"] is True
            async with env.factory() as session:
                await session.execute(
                    update(ApiKey)
                    .where(ApiKey.id == env.keys[1].id)
                    .values(scopes=[SCOPE_TASKS_READ])
                )
                await session.commit()
            denied = await invoke("telos_create_task", create_payload)
            assert denied["result"]["isError"] is True
            async with env.factory() as session:
                await session.execute(
                    update(ApiKey).where(ApiKey.id == env.keys[1].id).values(is_active=False)
                )
                await session.commit()
            revoked = await invoke("telos_add_comment", comment_payload)
            assert revoked["result"]["isError"] is True
            assert await snapshot(env) == committed
