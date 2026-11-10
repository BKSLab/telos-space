"""Создание проекта: PostgreSQL, атомарные квитанции и настоящий MCP HTTP."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, HTTPError
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.v1.endpoints.projects import router as projects_router
from src.core.app_state import RUNTIME_STATE_KEY, SESSION_FACTORY_STATE_KEY, SETTINGS_STATE_KEY
from src.core.settings import get_settings
from src.db.models import AgentToolRun, Base, Project, ProjectMember, User
from src.db.models.api_keys import ApiKey
from src.db.models.api_tokens import ApiToken, ApiTokenScope
from src.db.models.knowledge_index_jobs import KnowledgeIndexJob
from src.db.models.project_boards import ProjectBoard
from src.db.models.project_deadline_changes import ProjectDeadlineChange
from src.db.models.project_stages import ProjectStage
from src.dependencies.services import get_access_service, get_auth_service, get_projects_service
from src.exceptions.agent_tools import AgentToolRunsRepositoryError
from src.mcp_server import project_tools
from src.mcp_server.server import build_mcp_app
from src.mcp_server.services import build_access_service, build_auth_service
from src.repositories.agent_tool_runs import AgentToolRunsRepository
from src.repositories.knowledge_index_jobs import KnowledgeIndexJobsRepository
from src.repositories.unit_of_work import AgentToolUnitOfWork
from src.utils.api_keys import generate_api_key, hash_api_key
from src.utils.api_scopes import (
    SCOPE_PROJECTS_READ,
    SCOPE_PROJECTS_WRITE,
    SCOPE_TASKS_READ,
    SCOPE_TASKS_WRITE,
)
from src.utils.api_tokens import generate_token_secret, hash_token_secret

SCOPES = [SCOPE_PROJECTS_READ, SCOPE_PROJECTS_WRITE, SCOPE_TASKS_READ, SCOPE_TASKS_WRITE]
BUSINESS_MODELS = (Project, ProjectMember, ProjectStage, ProjectBoard, ProjectDeadlineChange, KnowledgeIndexJob, AgentToolRun)


@pytest_asyncio.fixture
async def project_creation_env(postgres_container, tmp_path):
    """Отдельная пустая БД; никакие пользователи или проекты вне теста не читаются."""
    database = f"project_creation_{uuid4().hex}"
    admin = create_async_engine(
        postgres_container.get_connection_url().replace("psycopg2", "asyncpg"),
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database}"'))
    engine = create_async_engine(admin.url.set(database=database), pool_size=8, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings().model_copy(deep=True)
    settings.app.uploads_path = tmp_path
    settings.app.app_public_url = "http://testserver"
    settings.app.mcp_allowed_hosts = ["testserver", "testserver:*", "127.0.0.1", "127.0.0.1:*"]
    settings.knowledge.knowledge_enabled = True
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    keys = {}
    legacy = {}
    async with factory() as session:
        for username in ("creator", "teammate"):
            user = User(username=username, password_hash="!", first_name=username, last_name="Тестовый", is_active=True)
            session.add(user)
            await session.flush()
            raw, prefix = generate_api_key()
            key = ApiKey(id=uuid4(), label="Изолированная MCP-проверка", key_hash=hash_api_key(raw), key_prefix=prefix,
                         owner_user_id=user.id, kind="personal", scopes=SCOPES.copy(), is_active=True)
            session.add(key)
            keys[username] = SimpleNamespace(raw=raw, id=key.id, user_id=user.id)
        for scope in (ApiTokenScope.READ, ApiTokenScope.WRITE):
            raw = generate_token_secret()
            session.add(ApiToken(user_id=keys["creator"].user_id, name="Проверка прежнего токена", token_hash=hash_token_secret(raw),
                                 prefix=raw[:8], scope=scope))
            legacy[scope] = raw
        await session.commit()
    state = {RUNTIME_STATE_KEY: SimpleNamespace(), SETTINGS_STATE_KEY: settings, SESSION_FACTORY_STATE_KEY: factory}
    try:
        yield SimpleNamespace(engine=engine, factory=factory, settings=settings, keys=keys, legacy=legacy, state=state)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()


def context(env, *, username="creator", secret=None):
    return SimpleNamespace(
        headers={"Authorization": f"Bearer {secret or env.keys[username].raw}"},
        request_context=SimpleNamespace(request=SimpleNamespace(state=SimpleNamespace(**env.state))),
    )


async def create(env, key="NEW", *, request_id=None, secret=None, **kwargs):
    return await project_tools.create_project(
        context(env, secret=secret), key=key, name="Проверочный проект", request_id=request_id or uuid4(), **kwargs
    )


async def counts(env):
    async with env.factory() as session:
        return {model.__tablename__: await session.scalar(select(func.count()).select_from(model)) for model in BUSINESS_MODELS}


async def project_id(env, key):
    async with env.factory() as session:
        return await session.scalar(select(Project.id).where(Project.key == key))


async def test_minimal_defaults_match_rest_and_owner_does_not_need_previous_membership(project_creation_env):
    env = project_creation_env
    assert (await counts(env))["projects"] == 0
    result = await create(env)
    assert result == {"project_key": "NEW", "name": "Проверочный проект", "status": "PLANNING", "created": True,
                      "url": "http://testserver/projects/NEW"}
    async with env.factory() as session:
        stored = (await session.scalars(select(Project))).one()
        assert stored.owner_id == env.keys["creator"].user_id
        assert stored.start_date == date.today() and stored.color == "#58a6ff" and stored.icon is None
        assert stored.description_md is None and stored.due_date is None
        members = list((await session.scalars(select(ProjectMember))).all())
        assert [(member.user_id, member.role.value) for member in members] == [(stored.owner_id, "OWNER")]
        stages = list((await session.scalars(select(ProjectStage).order_by(ProjectStage.order_index))).all())
        assert [item.name for item in stages] == ["Бэклог", "В работе", "Ревью", "Тестирование", "Готово"]
        board = (await session.scalars(select(ProjectBoard))).one()
        assert board.project_id == stored.id and board.created_by_user_id == stored.owner_id
        receipt = (await session.scalars(select(AgentToolRun))).one()
        assert receipt.project_id is None and receipt.tool_name == "create_project" and receipt.status == "completed"
        assert (await counts(env))["knowledge_index_jobs"] == 1
    # HTTP вызывает тот же доменный сервис: сохраняем и сравниваем его defaults.
    async with rest_client(env) as client:
        response = await client.post("/api/v1/projects", json={"key": "REST", "name": "Из REST"})
        assert response.status_code == 201
        saved = response.json()
        assert saved["status"] == result["status"] and saved["start_date"] == date.today().isoformat()
        assert saved["color"] == "#58a6ff" and saved["owner_id"] == env.keys["creator"].user_id


@asynccontextmanager
async def rest_client(env):
    """Настоящие HTTP routes и доменные сервисы с изолированной сессией БД."""
    from src.mcp_server.services import build_tool_services

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/v1")

    async def auth():
        async with env.factory() as session:
            yield build_auth_service(session, env.settings)

    async def projects():
        async with env.factory() as session:
            services = build_tool_services(session=session, session_factory=env.factory, settings=env.settings)
            async with services.project_creation.scope() as scope:
                # Этот HTTP сервис сохраняет собственный commit. Внешняя область
                # теста фиксируется явно только потому, что использует SAVEPOINT.
                yield scope.projects
                await scope.unit_of_work.commit()

    async def access():
        async with env.factory() as session:
            yield build_access_service(session)

    app.dependency_overrides[get_auth_service] = auth
    app.dependency_overrides[get_projects_service] = projects
    app.dependency_overrides[get_access_service] = access
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver",
                           headers={"Authorization": f"Bearer {env.keys['creator'].raw}"}) as client:
        yield client


async def test_replay_lost_response_payload_conflict_and_current_key_scope_access(project_creation_env):
    env = project_creation_env
    request_id = uuid4()
    result = await create(env, request_id=request_id)
    committed = await counts(env)
    assert await create(env, request_id=request_id) == result
    assert await counts(env) == committed
    with pytest.raises(ToolError, match="Ключ повтора"):
        await create(env, key="OTHER", request_id=request_id)
    # Повтор приходит с новым ключом того же владельца: квитанция привязана к человеку.
    async with env.factory() as session:
        raw, prefix = generate_api_key()
        session.add(ApiKey(id=uuid4(), label="Другой ключ", key_hash=hash_api_key(raw), key_prefix=prefix,
                           owner_user_id=env.keys["creator"].user_id, kind="personal", scopes=SCOPES.copy(), is_active=True))
        await session.commit()
    assert await create(env, request_id=request_id, secret=raw) == result
    async with env.factory() as session:
        await session.execute(update(ApiKey).where(ApiKey.id == env.keys["creator"].id).values(scopes=[SCOPE_PROJECTS_READ]))
        await session.commit()
    with pytest.raises(ToolError, match="projects:write"):
        await create(env, request_id=request_id)
    async with env.factory() as session:
        await session.execute(update(ApiKey).where(ApiKey.id == env.keys["creator"].id).values(scopes=SCOPES, is_active=False))
        await session.commit()
    with pytest.raises(ToolError, match="действующий токен"):
        await create(env, request_id=request_id)
    async with env.factory() as session:
        await session.execute(update(ApiKey).where(ApiKey.id == env.keys["creator"].id).values(is_active=True))
        await session.execute(delete(ProjectMember).where(ProjectMember.project_id == await project_id(env, "NEW")))
        await session.commit()
    with pytest.raises(ToolError, match="не найден"):
        await create(env, request_id=request_id)
    assert (await counts(env))["projects"] == committed["projects"]
    assert (await counts(env))["agent_tool_runs"] == 1


async def test_concurrent_same_request_serializes_and_returns_one_project(project_creation_env, monkeypatch):
    env = project_creation_env
    request_id = uuid4()
    arrivals, both_arrived = 0, asyncio.Event()
    original = AgentToolRunsRepository.lock_request

    async def rendezvous(self, project_id, user_id, incoming):
        nonlocal arrivals
        if incoming == request_id:
            arrivals += 1
            if arrivals == 2:
                both_arrived.set()
            await asyncio.wait_for(both_arrived.wait(), 10)
        await original(self, project_id, user_id, incoming)

    monkeypatch.setattr(AgentToolRunsRepository, "lock_request", rendezvous)
    first, second = await asyncio.wait_for(asyncio.gather(create(env, request_id=request_id), create(env, request_id=request_id)), 20)
    assert arrivals == 2 and first == second
    saved = await counts(env)
    assert saved["projects"] == saved["project_boards"] == saved["agent_tool_runs"] == 1


async def test_credentials_are_rechecked_after_waiting_for_request_lock(project_creation_env, monkeypatch):
    env = project_creation_env
    original = AgentToolRunsRepository.lock_request

    async def revoke_after_wait(self, *args):
        await original(self, *args)
        async with env.factory() as session:
            await session.execute(update(ApiKey).where(ApiKey.id == env.keys["creator"].id).values(scopes=[SCOPE_PROJECTS_READ]))
            await session.commit()

    monkeypatch.setattr(AgentToolRunsRepository, "lock_request", revoke_after_wait)
    baseline = await counts(env)
    with pytest.raises(ToolError, match="projects:write"):
        await create(env)
    assert await counts(env) == baseline


async def test_create_and_replay_need_only_one_database_connection(project_creation_env):
    env = project_creation_env
    engine = create_async_engine(
        env.engine.url, pool_size=1, max_overflow=0, pool_timeout=0.3
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    env.state[SESSION_FACTORY_STATE_KEY] = factory
    request_id = uuid4()
    try:
        original = await create(env, request_id=request_id)
        assert await create(env, request_id=request_id) == original
        assert engine.pool.checkedout() == 0
        stored = await counts(env)
        assert stored["projects"] == stored["agent_tool_runs"] == 1
    finally:
        await engine.dispose()


@pytest.mark.parametrize("failure", ["receipt", "commit", "outbox"])
async def test_failure_rolls_back_passport_team_stages_board_outbox_and_receipt(project_creation_env, monkeypatch, failure):
    env = project_creation_env

    async def fail(*args, **kwargs):
        raise AgentToolRunsRepositoryError("Внедрённый сбой изолированной проверки")

    if failure == "receipt":
        monkeypatch.setattr(AgentToolRunsRepository, "save", fail)
    elif failure == "commit":
        monkeypatch.setattr(AgentToolUnitOfWork, "commit", fail)
    else:
        monkeypatch.setattr(KnowledgeIndexJobsRepository, "add_project_change", fail)
    baseline = await counts(env)
    with pytest.raises(ToolError):
        await create(env, member_usernames=["teammate"], start_date=date(2026, 11, 10), due_date=date(2026, 12, 1))
    assert await counts(env) == baseline


async def test_validation_unknown_members_duplicate_key_and_legacy_token_contract(project_creation_env):
    env = project_creation_env
    for arguments in ({"member_usernames": ["missing"]}, {"stages": [{"name": "A"}, {"name": "a"}]},
                      {"start_date": date(2026, 12, 2), "due_date": date(2026, 12, 1)}, {"icon": "lucide:Invalid"},
                      {"member_usernames": ["bad login"]}):
        baseline = await counts(env)
        with pytest.raises(ToolError):
            await create(env, **arguments)
        assert await counts(env) == baseline
    with pytest.raises(ToolError):
        await create(env, secret=env.legacy[ApiTokenScope.READ])
    saved = await create(env, secret=env.legacy[ApiTokenScope.WRITE])
    assert saved["project_key"] == "NEW"
    baseline = await counts(env)
    with pytest.raises(ToolError, match="уже существует"):
        await create(env)
    with pytest.raises(ToolError, match="владельца ключа"):
        await create(env, key="IMPOSTOR", actor="teammate")
    assert await counts(env) == baseline


def rpc_body(response):
    assert response.status_code == 200
    if "text/event-stream" in response.headers.get("content-type", ""):
        return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
    return response.json()


def tool_result(body):
    assert not body["result"].get("isError"), body
    return json.loads(body["result"]["content"][0]["text"])


@asynccontextmanager
async def mcp_client(env, *, base_url="http://testserver", transport=None):
    async with AsyncClient(base_url=base_url, transport=transport, headers={
        "Authorization": f"Bearer {env.keys['creator'].raw}", "Accept": "application/json, text/event-stream",
    }) as client:
        response = await client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "project-create-test", "version": "1"}}})
        negotiated = rpc_body(response)["result"]
        client.headers["MCP-Session-Id"] = response.headers["mcp-session-id"]
        client.headers["MCP-Protocol-Version"] = negotiated["protocolVersion"]
        await client.post("/", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        sequence = 1

        async def invoke(name, arguments):
            nonlocal sequence
            sequence += 1
            return rpc_body(await client.post("/", json={"jsonrpc": "2.0", "id": sequence, "method": "tools/call", "params": {"name": name, "arguments": arguments}}))

        yield client, invoke


async def test_real_mcp_minimal_full_passport_read_rest_unicode_and_create_task(project_creation_env):
    env = project_creation_env
    app = build_mcp_app(settings=env.settings)

    async def with_resources(scope, receive, send):
        scope["state"] = {**scope.get("state", {}), **env.state}
        await app(scope, receive, send)

    long_text = "# Проект 🚀\n\n" + "Полное **Unicode** описание — 漢字 и é.\n" * 160
    sections = {"problem": "Разрозненные данные", "goal": "Единое пространство", "expected_result": "Рабочий трекер", "additional": long_text}
    async with app.router.lifespan_context(app):
        async with mcp_client(env, transport=ASGITransport(app=with_resources)) as (client, invoke):
            catalog = rpc_body(await client.post("/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))["result"]["tools"]
            schema = next(tool["inputSchema"] for tool in catalog if tool["name"] == "tt_create_project")
            assert set(schema["required"]) == {"key", "name", "request_id"}
            assert schema["additionalProperties"] is False
            simple = tool_result(await invoke("tt_create_project", {"key": "MIN", "name": "Минимальный", "request_id": str(uuid4())}))
            assert simple["status"] == "PLANNING" and simple["url"].endswith("/projects/MIN")
            payload = {"key": "FULL", "name": "Полный паспорт", "request_id": str(uuid4()), "description_sections": sections,
                       "description_md": "Этот текст уступает блокам", "member_usernames": ["TEAMMATE", "creator", "teammate"],
                       "stages": [{"name": " Идея ", "color": "#123456"}, {"name": "Принято", "is_done_stage": True}],
                       "status": "ACTIVE", "color": "#abcdef", "icon": "lucide:rocket", "start_date": "2026-11-10", "due_date": "2026-12-01"}
            full = tool_result(await invoke("tt_create_project", payload))
            assert full["project_key"] == "FULL" and full["status"] == "ACTIVE"
            read = tool_result(await invoke("tt_get_project", {"project_key": full["project_key"], "full_text": True}))
            expected = "## Проблема\n\nРазрозненные данные\n\n## Цель\n\nЕдиное пространство\n\n## Ожидаемый результат\n\nРабочий трекер\n\n## Дополнительно\n\n" + long_text.strip()
            assert read["description"] == expected and not read["description_meta"]["truncated"]
            raw = tool_result(await invoke("tt_create_project", {"key": "RAW", "name": "Без блоков", "request_id": str(uuid4()), "description_md": long_text}))
            raw_read = tool_result(await invoke("tt_get_project", {"project_key": raw["project_key"], "full_text": True}))
            assert raw_read["description"] == long_text
            task = tool_result(await invoke("tt_create_task", {"project_key": full["project_key"], "title": "Первая задача", "request_id": str(uuid4())}))
            assert task["task_key"] == "FULL-1"
            assert tool_result(await invoke("tt_get_task", {"task_key": task["task_key"]}))["stage"] == "Идея"
            baseline = await counts(env)
            assert (await invoke("tt_create_project", {**payload, "owner_id": env.keys["teammate"].user_id}))["result"]["isError"]
            assert (await invoke("tt_create_project", {**payload, "request_id": "invalid"}))["result"]["isError"]
            assert await counts(env) == baseline
            assert tool_result(await invoke("tt_create_project", payload)) == full
            async with rest_client(env) as rest:
                result = await rest.get(f"/api/v1/projects/{await project_id(env, 'FULL')}")
                assert result.status_code == 200
                saved = result.json()
                assert saved["description_sections"] == {**sections, "additional": long_text.strip()}
                assert saved["description_md"] == expected and saved["icon"] == "lucide:rocket"
            async with env.factory() as session:
                pid = await project_id(env, "FULL")
                stages = list((await session.scalars(select(ProjectStage).where(ProjectStage.project_id == pid).order_by(ProjectStage.order_index))).all())
                assert [stage.name for stage in stages] == ["Идея", "Принято"] and stages[-1].is_done_stage
                team = list((await session.scalars(select(ProjectMember).where(ProjectMember.project_id == pid))).all())
                assert len(team) == 2
                assert await session.scalar(select(func.count()).select_from(ProjectDeadlineChange).where(ProjectDeadlineChange.project_id == pid)) == 1


TCP_SERVER = '''
import os
from types import SimpleNamespace
import uvicorn
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.core.app_state import RUNTIME_STATE_KEY, SESSION_FACTORY_STATE_KEY, SETTINGS_STATE_KEY
from src.core.settings import get_settings
from src.mcp_server.server import build_mcp_app
settings = get_settings().model_copy(deep=True)
settings.app.app_public_url = "http://testserver"
settings.app.mcp_allowed_hosts = ["127.0.0.1", "127.0.0.1:*"]
settings.app.uploads_path = __import__("pathlib").Path(os.environ["PROJECT_CREATION_UPLOADS"])
settings.knowledge.knowledge_enabled = True
engine = create_async_engine(os.environ["PROJECT_CREATION_TEST_DB"])
factory = async_sessionmaker(engine, expire_on_commit=False)
app = build_mcp_app(settings=settings)
state = {RUNTIME_STATE_KEY: SimpleNamespace(), SESSION_FACTORY_STATE_KEY: factory, SETTINGS_STATE_KEY: settings}
dropped = False
async def with_resources(scope, receive, send):
    global dropped
    scope["state"] = {**scope.get("state", {}), **state}
    async def deliver(message):
        global dropped
        if os.environ.get("PROJECT_CREATION_DROP_RESPONSE") and not dropped and message["type"] == "http.response.body" and b"DURABLE" in message.get("body", b""):
            dropped = True
            raise RuntimeError("Controlled loss of committed MCP response")
        await send(message)
    await app(scope, receive, deliver)
uvicorn.run(with_resources, host="127.0.0.1", port=int(os.environ["PROJECT_CREATION_TEST_PORT"]), log_level="critical")
'''


@asynccontextmanager
async def fresh_mcp_process(env, *, drop_response=False):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process_env = {**os.environ, "PROJECT_CREATION_TEST_DB": env.engine.url.render_as_string(hide_password=False),
                   "PROJECT_CREATION_UPLOADS": str(env.settings.app.uploads_path), "PROJECT_CREATION_TEST_PORT": str(port),
                   "PROJECT_CREATION_DROP_RESPONSE": "1" if drop_response else ""}
    process = await asyncio.to_thread(
        subprocess.Popen, [sys.executable, "-c", TCP_SERVER],
        cwd=Path(__file__).resolve().parents[3], env=process_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with AsyncClient(timeout=0.5) as probe:
            for _ in range(160):
                assert process.poll() is None, "Изолированный MCP-процесс завершился до готовности"
                try:
                    await probe.get(base_url)
                    break
                except Exception:
                    await asyncio.sleep(0.1)
            else:
                pytest.fail("Изолированный MCP-процесс не запустился за 16 секунд")
        yield base_url
    finally:
        process.terminate()
        await asyncio.to_thread(process.wait, 10)


async def test_lost_http_response_new_session_and_process_restart_return_one_durable_result(project_creation_env):
    env = project_creation_env
    payload = {"key": "DURABLE", "name": "Переживает перезапуск", "request_id": str(uuid4())}
    async with fresh_mcp_process(env, drop_response=True) as base_url:
        async with mcp_client(env, base_url=base_url) as (_, invoke):
            # Сервис уже сделал commit, но сервер разрывает ответ до доставки клиенту.
            with pytest.raises(HTTPError):
                await invoke("tt_create_project", payload)
        assert (await counts(env))["projects"] == (await counts(env))["agent_tool_runs"] == 1
        async with mcp_client(env, base_url=base_url) as (_, invoke):
            original = tool_result(await invoke("tt_create_project", payload))
            assert original["project_key"] == "DURABLE"
    async with fresh_mcp_process(env) as base_url:
        async with mcp_client(env, base_url=base_url) as (_, invoke):
            assert tool_result(await invoke("tt_create_project", payload)) == original
            assert (await invoke("tt_create_project", {**payload, "name": "Другие аргументы"}))["result"]["isError"]
            assert tool_result(await invoke("tt_get_project", {"project_key": "DURABLE"}))["project_key"] == "DURABLE"
    stored = await counts(env)
    assert stored["projects"] == stored["project_boards"] == stored["agent_tool_runs"] == 1
