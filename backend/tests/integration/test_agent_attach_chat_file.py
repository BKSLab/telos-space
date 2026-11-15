"""Копирование sent-оригиналов в задачи: настоящий PostgreSQL, ACL и журнал действий."""

import asyncio
import threading
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from src.agent.action_catalog import PERSONAL_ACTIONS, PROJECT_ACTIONS
from src.agent.aris import ArisMode
from src.agent.tools import AgentToolContext, AgentToolRequest
from src.core.settings import get_settings
from src.db.models import (
    AgentFile,
    AgentMessage,
    AgentToolRun,
    ChatAttachment,
    ChatMessage,
    Document,
    ProjectStage,
    Task,
    TaskAttachment,
)
from src.dependencies.scopes import (
    build_agent_project_tool_scope,
    build_project_knowledge_query_scope,
)
from src.dependencies.services import build_project_tool_executor
from src.services.agent_actions import AgentActionsService
from src.services.project_query import ProjectQueryService
from tests.integration.test_agent_chat_files import (
    private_context,
    reader,
    save_private,
    save_shared,
    shared_context,
)

ORIGINAL = b"original bytes\x00\xff\xfe\r\n"


@pytest_asyncio.fixture
async def attachment_env(chat_env, tmp_path):
    settings = get_settings().model_copy(deep=True)
    settings.app.uploads_path = tmp_path
    files = reader(chat_env, tmp_path)
    actions = AgentActionsService(
        scope=build_agent_project_tool_scope(session_factory=chat_env.factory, settings=settings),
        actions=PROJECT_ACTIONS + PERSONAL_ACTIONS,
        chat_files=files,
    )
    query = ProjectQueryService(
        scope=build_project_knowledge_query_scope(session_factory=chat_env.factory)
    )
    async with chat_env.factory() as db:
        db.add_all(
            [
                ProjectStage(project_id=project, name="Работа", order_index=0, color="#334455")
                for project in (1, 2)
            ]
        )
        await db.commit()
    return SimpleNamespace(
        **(
            vars(chat_env)
            | {
                "files": files,
                "actions": actions,
                "executor": build_project_tool_executor(query, actions),
                "root": tmp_path,
            }
        )
    )


def execution(context, *, personal=False):
    return AgentToolContext(
        project_id=context.project_id,
        user_id=context.user_id,
        can_write=True,
        message_id=context.message_id,
        chat_turn_id=context.chat_turn_id,
        run_id=context.run_id,
        mode=ArisMode.PROJECT_CHAT
        if context.chat_turn_id
        else (ArisMode.WIDGET_PERSONAL if personal else ArisMode.WIDGET_PROJECT),
    )


async def call(env, context, name, arguments, *, project=None):
    if project is not None:
        arguments = {"project_key": f"C{project}", "name": name, "arguments": arguments}
        name = "telos_run_project_action"
    return await env.executor.execute(context, AgentToolRequest(name=name, arguments=arguments))


def result(response):
    assert "error" not in response, response
    assert response["action"]["status"] == "completed"
    return response["action"]["result"]


@pytest.mark.parametrize("kind", ["shared", "private", "global_project", "personal"])
async def test_task_create_then_attach_keeps_original_bytes_and_replays_once(attachment_env, kind):
    env = attachment_env
    if kind == "shared":
        source, chat, old, _, _ = await shared_context(env)
        file_id = await save_shared(env, env.files, chat, message_id=old, content=ORIGINAL)
        source_model = ChatAttachment
    else:
        source, conversation, question = await private_context(
            env, project_id=1 if kind == "private" else None
        )
        file_id = await save_private(
            env, env.files, conversation, message_id=question, content=ORIGINAL
        )
        source_model = AgentFile
    context = execution(source, personal=kind in {"global_project", "personal"})
    project = 2 if kind == "global_project" else None
    created = result(
        await call(
            env,
            context,
            "telos_create_personal_task" if kind == "personal" else "telos_create_task",
            {"title": f"Новая задача {kind}"},
            project=project,
        )
    )["task"]
    arguments = {"task_id": created["id"], "file_id": str(file_id)}
    first = await call(env, context, "telos_attach_uploaded_file", arguments, project=project)
    document = result(first)["document"]
    assert document["ownership"]["owner_task_id"] == created["id"]
    assert document["original"]["size"] == len(ORIGINAL)
    assert document["original"]["content_type"] == "text/plain"
    async with env.factory() as db:
        copied = (await db.execute(select(TaskAttachment))).scalar_one()
        assert copied.task_id == created["id"] and copied.uploaded_by_user_id == 1
        assert (env.root / copied.storage_key).read_bytes() == ORIGINAL
        assert (await db.get(Task, created["id"])).project_id == (
            None if kind == "personal" else 2 if kind == "global_project" else 1
        )
        assert await db.scalar(select(func.count()).select_from(Document)) == 0
        await db.execute(delete(source_model).where(source_model.id == file_id))
        await db.commit()
    assert (
        await call(env, context, "telos_attach_uploaded_file", arguments, project=project) == first
    )
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(TaskAttachment)) == 1
        receipts = list(
            (
                await db.execute(
                    select(AgentToolRun).where(
                        AgentToolRun.tool_name == "telos_attach_uploaded_file"
                    )
                )
            ).scalars()
        )
        assert len(receipts) == 1 and receipts[0].result["document"]["ref"] == document["ref"]


@pytest.mark.parametrize("kind", ["shared", "private"])
async def test_attach_refuses_drafts_foreign_future_deleted_and_missing_active_scope(
    attachment_env, kind
):
    env = attachment_env
    invalid = []
    if kind == "shared":
        source, chat, old, _, future = await shared_context(env)
        valid = await save_shared(env, env.files, chat, message_id=old)
        invalid += [
            await save_shared(env, env.files, chat),
            await save_shared(env, env.files, chat, message_id=future),
        ]
        foreign, other_chat, other_old, _, _ = await shared_context(env, project_id=2)
        invalid.append(await save_shared(env, env.files, other_chat, message_id=other_old))
        async with env.factory() as db:
            removed = ChatMessage(
                chat_id=chat,
                project_id=1,
                seq=105,
                author_user_id=2,
                content="Удалённый файл",
                client_message_id=uuid4(),
                deleted_at=datetime.now(UTC),
            )
            db.add(removed)
            await db.flush()
            removed_id = removed.id
            await db.commit()
        invalid.append(await save_shared(env, env.files, chat, message_id=removed_id))
    else:
        source, conversation, question = await private_context(env)
        valid = await save_private(env, env.files, conversation, message_id=question)
        invalid.append(await save_private(env, env.files, conversation))
        async with env.factory() as db:
            future = AgentMessage(
                conversation_id=conversation,
                request_id=uuid4(),
                role="user",
                status="completed",
                content="Будущий файл",
            )
            db.add(future)
            await db.flush()
            future_id = future.id
            await db.commit()
        invalid.append(await save_private(env, env.files, conversation, message_id=future_id))
        for user in (1, 2):
            _, foreign_conversation, foreign_question = await private_context(env, user_id=user)
            invalid.append(
                await save_private(
                    env, env.files, foreign_conversation, message_id=foreign_question
                )
            )
    context = execution(source)
    task = result(await call(env, context, "telos_create_task", {"title": "Целевая"}))["task"]
    for file_id in invalid:
        denied = await call(
            env,
            context,
            "telos_attach_uploaded_file",
            {
                "task_id": task["id"],
                "file_id": str(file_id),
            },
        )
        assert "error" in denied
    arguments = {"task_id": task["id"], "file_id": str(valid)}
    for unavailable in (
        replace(context, message_id=None, chat_turn_id=None, request_id=uuid4()),
        replace(context, run_id=uuid4()),
        replace(context, can_write=False),
    ):
        assert "error" in await call(env, unavailable, "telos_attach_uploaded_file", arguments)
    async with env.factory() as db:
        stage = await db.scalar(select(ProjectStage).where(ProjectStage.project_id == 2))
        other = Task(project_id=2, stage_id=stage.id, number=1, title="Другая область")
        db.add(other)
        await db.flush()
        other_id = other.id
        await db.commit()
    assert "error" in await call(
        env, context, "telos_attach_uploaded_file", arguments | {"task_id": other_id}
    )
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(TaskAttachment)) == 0
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AgentToolRun)
                .where(AgentToolRun.tool_name == "telos_attach_uploaded_file")
            )
            == 0
        )


async def test_explicit_private_project_cannot_copy_to_another_project(attachment_env):
    env = attachment_env
    source, conversation, question = await private_context(env, project_id=1)
    file_id = await save_private(env, env.files, conversation, message_id=question)
    context = execution(source)
    task = result(
        await call(env, context, "telos_create_task", {"title": "Второй проект"}, project=2)
    )["task"]
    denied = await call(
        env,
        context,
        "telos_attach_uploaded_file",
        {
            "task_id": task["id"],
            "file_id": str(file_id),
        },
        project=2,
    )
    assert "error" in denied
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(TaskAttachment)) == 0


async def test_two_active_copies_share_their_own_uow_without_extra_pool_connections(
    attachment_env, monkeypatch
):
    env = attachment_env
    assert env.engine.pool.size() == 2
    calls = []
    expected = {}
    for suffix in (b"first", b"second"):
        source, conversation, question = await private_context(env)
        file_id = await save_private(
            env, env.files, conversation, message_id=question, content=ORIGINAL + suffix
        )
        context = execution(source)
        task = result(await call(env, context, "telos_create_task", {"title": "Параллельная"}))[
            "task"
        ]
        calls.append((context, {"task_id": task["id"], "file_id": str(file_id)}))
        expected[task["id"]] = ORIGINAL + suffix
    ready = asyncio.Event()
    entered = 0
    invoke = env.actions._invoke

    async def both_uow_locked(definition, db, actor, parameters):
        nonlocal entered
        if definition.name == "telos_attach_uploaded_file":
            entered += 1
            if entered == 2:
                assert env.engine.pool.checkedout() == 2
                ready.set()
            await asyncio.wait_for(ready.wait(), 3)
            bound = db.bind_chat_files(env.files)
            assert bound.agent_storage is env.files.agent_storage
            assert bound.chat_storage is env.files.chat_storage and bound.vision is env.files.vision
            assert (
                bound.max_text_chars,
                bound.lease_seconds,
                bound.poll_seconds,
                bound.wait_seconds,
            ) == (
                env.files.max_text_chars,
                env.files.lease_seconds,
                env.files.poll_seconds,
                env.files.wait_seconds,
            )
        return await invoke(definition, db, actor, parameters)

    monkeypatch.setattr(env.actions, "_invoke", both_uow_locked)
    responses = await asyncio.wait_for(
        asyncio.gather(
            *[
                call(env, context, "telos_attach_uploaded_file", arguments)
                for context, arguments in calls
            ]
        ),
        6,
    )
    assert entered == 2
    assert {result(item)["document"]["ownership"]["owner_task_id"] for item in responses} == set(
        expected
    )
    async with env.factory() as db:
        copies = list((await db.execute(select(TaskAttachment))).scalars())
        assert len(copies) == 2
        for copied in copies:
            assert (env.root / copied.storage_key).read_bytes() == expected[copied.task_id]
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AgentToolRun)
                .where(AgentToolRun.tool_name == "telos_attach_uploaded_file")
            )
            == 2
        )


async def test_source_deleted_during_original_read_is_discarded_without_receipt(
    attachment_env, monkeypatch
):
    from src.services import agent_chat_files

    env = attachment_env
    source, conversation, question = await private_context(env)
    file_id = await save_private(env, env.files, conversation, message_id=question)
    context = execution(source)
    task = result(await call(env, context, "telos_create_task", {"title": "Проверка гонки"}))[
        "task"
    ]
    entered, release = threading.Event(), threading.Event()
    original_read = agent_chat_files._read_original

    def blocked_read(storage, file):
        entered.set()
        if not release.wait(5):
            raise AssertionError("Controlled original read was not released")
        return original_read(storage, file)

    monkeypatch.setattr(agent_chat_files, "_read_original", blocked_read)
    pending = asyncio.create_task(
        call(
            env,
            context,
            "telos_attach_uploaded_file",
            {
                "task_id": task["id"],
                "file_id": str(file_id),
            },
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        async with env.factory() as db:
            await db.execute(delete(AgentFile).where(AgentFile.id == file_id))
            await db.commit()
    finally:
        release.set()
    assert "error" in await pending
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(TaskAttachment)) == 0
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AgentToolRun)
                .where(AgentToolRun.tool_name == "telos_attach_uploaded_file")
            )
            == 0
        )


async def test_write_gate_refuses_attachment_before_read_or_mutation(attachment_env):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from src.agent.context import AgentContextBudget
    from src.agent.runtime import ArisDeps, ArisRuntime, WriteGate

    env = attachment_env
    source, conversation, question = await private_context(env)
    file_id = await save_private(env, env.files, conversation, message_id=question)
    context = execution(source)
    task = result(await call(env, context, "telos_create_task", {"title": "Без поручения"}))["task"]
    env.files.read_original = AsyncMock(wraps=env.files.read_original)
    step = 0

    async def decline_write():
        return False

    async def respond(_messages, _info):
        nonlocal step
        step += 1
        if step == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "telos_attach_uploaded_file",
                        {
                            "task_id": task["id"],
                            "file_id": str(file_id),
                        },
                    )
                ]
            )
        return ModelResponse(
            parts=[ToolCallPart("telos_submit_answer", {"answer": "Изменение не выполнено."})]
        )

    deps = ArisDeps(
        execution=context,
        executor=env.executor,
        system_prompt="Не изменяй данные без поручения.",
        content={"question": "Обсудим файл."},
        budget=AgentContextBudget(
            system_prompt="Не изменяй данные без поручения.",
            max_tokens=12000,
            history_tokens=1000,
        ),
        tool_rounds=2,
        write_gate_factory=lambda: WriteGate(decline_write()),
    )
    try:
        completed = await ArisRuntime(model=FunctionModel(respond), executor=env.executor).run(
            deps, metadata={}
        )
    finally:
        if deps.write_gate is not None:
            deps.write_gate.close()
    assert completed.output is not None and completed.tool_calls[0]["refused"] == "write_intent"
    env.files.read_original.assert_not_awaited()
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(TaskAttachment)) == 0
