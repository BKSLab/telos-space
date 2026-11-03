"""Переписка, очередь и изоляция проверяются на настоящем PostgreSQL с commit."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.clients.llm import LlmClient
from src.db.models import Base, Project, ProjectMember, User
from src.db.models.agent_messages import AgentMessage
from src.dependencies.scopes import build_agent_conversation_scope
from src.exceptions.agent_conversations import (
    AgentConversationBusyError,
    AgentConversationNotFoundError,
    AgentMessageConflictError,
)
from src.repositories.agent_messages import AgentMessagesRepository
from src.schemas.agent_conversations import AgentMessageCreateSchema
from src.schemas.knowledge import KnowledgeAnswerSchema, KnowledgeSourceSchema
from src.services.agent_conversations import (
    AgentConversationConfig,
    AgentConversationsService,
    ConversationMemory,
)
from src.services.project_agent import ProjectAgentService


@pytest_asyncio.fixture
async def agent_env(postgres_container):
    """Отдельная БД позволяет проверять реальные commit и несколько соединений."""
    database = f"agent_test_{uuid4().hex}"
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database}"'))
    engine = create_async_engine(
        admin.url.set(database=database), pool_size=2, max_overflow=0, pool_timeout=2
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add_all(
            [
                User(
                    id=i,
                    username=f"user{i}",
                    password_hash="!",
                    first_name="Участник",
                    last_name=str(i),
                    is_active=True,
                )
                for i in (1, 2)
            ]
        )
        await session.flush()
        session.add_all(
            [
                Project(id=i, owner_id=1, key=f"P{i}", name=f"Проект {i}", color="#334455")
                for i in (1, 2)
            ]
        )
        await session.flush()
        session.add_all(
            [
                ProjectMember(project_id=p, user_id=u, role="OWNER" if u == 1 else "MEMBER")
                for p, u in ((1, 1), (1, 2), (2, 1))
            ]
        )
        await session.commit()
    responder = AsyncMock(spec=ProjectAgentService)
    responder.ask.return_value = KnowledgeAnswerSchema(
        answer="Проверка окна заказа обсуждается.",
        sources=[
            KnowledgeSourceSchema(
                source_id="project:1", entity_type="project", entity_id=1, title="Проект 1"
            )
        ],
    )
    llm = AsyncMock(spec=LlmClient)
    llm.get_structured_response.return_value = ConversationMemory(
        summary="Обсуждается проверка окна создания заказа."
    )
    service = AgentConversationsService(
        scope=build_agent_conversation_scope(session_factory=factory),
        agent=responder,
        llm_client=llm,
        config=AgentConversationConfig(
            turn_timeout_seconds=30, history_messages=4, summary_batch_size=2
        ),
    )
    try:
        yield SimpleNamespace(
            service=service, responder=responder, llm=llm, factory=factory, engine=engine
        )
    finally:
        await service.drain()
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()


async def start(env, user_id=1):
    return await env.service.create_conversation(project_id=1, user_id=user_id)


async def send(
    env, conversation_id, content="Есть задача на проверку окна создания заказа?", request_id=None
):
    return await env.service.send_message(
        project_id=1,
        user_id=1,
        conversation_id=conversation_id,
        data=AgentMessageCreateSchema(content=content, request_id=request_id or uuid4()),
    )


async def read(env, conversation_id, **kwargs):
    return await env.service.get_messages(
        project_id=1, user_id=1, conversation_id=conversation_id, before_id=None, limit=50, **kwargs
    )


async def test_saved_turn_survives_reopening_and_does_not_hold_database_during_model(agent_env):
    env = agent_env
    conversation = await start(env)
    accepted = await send(env, conversation.id)
    assert accepted.assistant_message.status == "queued"
    assert (await read(env, conversation.id)).items[0].content.startswith("Есть задача")
    env.responder.ask.assert_not_awaited()
    entered, release = asyncio.Event(), asyncio.Event()

    async def respond(**kwargs):
        assert kwargs["project_id"] == 1 and kwargs["actor"]["user_id"] == 1
        assert kwargs["history"] == []
        entered.set()
        await release.wait()
        return KnowledgeAnswerSchema(answer="Подходящая задача не найдена.", sources=[])

    env.responder.ask.side_effect = respond
    running = asyncio.create_task(env.service.process_next())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert env.engine.pool.checkedout() == 0
        restored = await asyncio.wait_for(read(env, conversation.id), timeout=2)
        assert restored.items[-1].status == "processing"
    finally:
        release.set()
        await running
    assert (await read(env, conversation.id)).items[-1].content == "Подходящая задача не найдена."
    env.responder.ask.side_effect = None
    await send(env, conversation.id, "Давай уточним её описание")
    await env.service.process_next()
    history = env.responder.ask.await_args.kwargs["history"]
    assert [item.role for item in history] == ["user", "assistant"]
    assert "окна создания заказа" in history[0].content
    restored = await read(env, conversation.id)
    assert restored.items[-1].sources[0].source_id == "project:1"


async def test_dialogues_are_private_project_scoped_and_recheck_membership(agent_env):
    env = agent_env
    conversation = await start(env)
    await send(env, conversation.id)
    assert not (
        await env.service.list_conversations(project_id=1, user_id=2, offset=0, limit=30)
    ).items
    for project_id, user_id in ((1, 2), (2, 1), (1, 999)):
        with pytest.raises(AgentConversationNotFoundError):
            await env.service.get_messages(
                project_id=project_id,
                user_id=user_id,
                conversation_id=conversation.id,
                before_id=None,
                limit=50,
            )
        with pytest.raises(AgentConversationNotFoundError):
            await env.service.send_message(
                project_id=project_id,
                user_id=user_id,
                conversation_id=conversation.id,
                data=AgentMessageCreateSchema(content="чужой вопрос", request_id=uuid4()),
            )
    async with env.factory() as session:
        await session.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 1)
        )
        await session.commit()
    with pytest.raises(AgentConversationNotFoundError):
        await read(env, conversation.id)
    await env.service.process_next()
    env.responder.ask.assert_not_awaited()
    async with env.factory() as session:
        response = await session.scalar(
            select(AgentMessage).where(AgentMessage.role == "assistant")
        )
        assert response.status == "failed"


async def test_submission_is_atomic_idempotent_and_serializes_concurrent_requests(agent_env):
    env = agent_env
    conversation = await start(env)
    request_id = uuid4()
    first, duplicate = await asyncio.gather(
        send(env, conversation.id, request_id=request_id),
        send(env, conversation.id, request_id=request_id),
    )
    assert first == duplicate
    assert len((await read(env, conversation.id)).items) == 2
    with pytest.raises(AgentMessageConflictError):
        await send(env, conversation.id, "Другой вопрос", request_id=request_id)
    with pytest.raises(AgentConversationBusyError):
        await send(env, conversation.id, "Второй вопрос")
    assert len((await read(env, conversation.id)).items) == 2
    await asyncio.gather(env.service.process_next(), env.service.process_next())
    env.responder.ask.assert_awaited_once()
    assert (
        await send(env, conversation.id, request_id=request_id)
    ).assistant_message.status == "completed"


async def test_failed_answer_can_retry_without_duplicates_and_cannot_rewrite_past_turn(agent_env):
    env = agent_env
    conversation = await start(env)
    accepted = await send(env, conversation.id)
    env.responder.ask.side_effect = RuntimeError("секрет upstream")
    await env.service.process_next()
    failure = (await read(env, conversation.id)).items[-1]
    assert failure.status == "failed" and "секрет" not in failure.error
    retry_args = dict(
        project_id=1,
        user_id=1,
        conversation_id=conversation.id,
        message_id=accepted.assistant_message.id,
    )
    assert (await env.service.retry_message(**retry_args)).status == "queued"
    assert (await env.service.retry_message(**retry_args)).status == "queued"
    env.responder.ask.side_effect = None
    await env.service.process_next()
    assert len((await read(env, conversation.id)).items) == 2
    with pytest.raises(AgentMessageConflictError):
        await env.service.retry_message(**retry_args)
    await send(env, conversation.id, "Продолжим обсуждение")
    with pytest.raises(AgentMessageConflictError):
        await env.service.retry_message(**retry_args)


async def test_server_shutdown_persists_interruption_and_database_failure_does_not_mask_cancellation(
    agent_env, monkeypatch
):
    env = agent_env
    conversation = await start(env)
    await send(env, conversation.id)
    entered = asyncio.Event()

    async def respond(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    env.responder.ask.side_effect = respond
    for storage_available in (True, False):
        entered.clear()
        task = asyncio.create_task(env.service.process_next())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            if not storage_available:
                monkeypatch.setattr(
                    env.service, "_finish", AsyncMock(side_effect=RuntimeError("БД выключена"))
                )
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        if storage_available:
            message = (await read(env, conversation.id)).items[-1]
            assert message.status == "failed" and "прервалась" in message.error
            await env.service.retry_message(
                project_id=1, user_id=1, conversation_id=conversation.id, message_id=message.id
            )
    assert len((await read(env, conversation.id)).items) == 2


async def test_old_history_is_summarized_and_pagination_retains_the_full_transcript(agent_env):
    env = agent_env
    conversation = await start(env)
    for i in range(5):
        # Сводка сохраняется фоново: проверяем короткую историю после её commit.
        memory_task = env.service._memory_tasks.get(conversation.id)
        if memory_task is not None:
            await asyncio.wait_for(asyncio.shield(memory_task), timeout=5)
        await send(env, conversation.id, f"Вопрос об окне заказа номер {i}")
        await env.service.process_next()
    assert env.llm.get_structured_response.await_count == 2
    last = env.responder.ask.await_args.kwargs
    assert last["memory"] == "Обсуждается проверка окна создания заказа."
    assert len(last["history"]) == 4
    args = dict(project_id=1, user_id=1, conversation_id=conversation.id, limit=3)
    page = await env.service.get_messages(**args, before_id=None)
    earlier = await env.service.get_messages(**args, before_id=page.next_before_id)
    assert earlier.items[-1].id < page.items[0].id
    assert len((await read(env, conversation.id)).items) == 10
    other = await start(env)
    await send(env, other.id)
    await env.service.process_next()
    assert env.responder.ask.await_args.kwargs["history"] == []
    assert env.responder.ask.await_args.kwargs["memory"] == ""


async def test_timeout_and_expired_attempts_fail_visibly_and_old_worker_cannot_finish(agent_env):
    env = agent_env
    conversation = await start(env)
    accepted = await send(env, conversation.id)
    env.service.config = replace(env.service.config, turn_timeout_seconds=0.05)

    async def stalled(**kwargs):
        await asyncio.Event().wait()

    env.responder.ask.side_effect = stalled
    await env.service.process_next()
    assert (await read(env, conversation.id)).items[-1].status == "failed"
    await env.service.retry_message(
        project_id=1,
        user_id=1,
        conversation_id=conversation.id,
        message_id=accepted.assistant_message.id,
    )
    old_run = uuid4()
    async with env.factory() as session:
        repository = AgentMessagesRepository(session)
        message = await repository.claim_next(old_run)
        await session.execute(
            update(AgentMessage)
            .where(AgentMessage.id == message.id)
            .values(started_at=text("now() - interval '1 hour'"))
        )
        await session.commit()
    async with env.factory() as session:
        repository = AgentMessagesRepository(session)
        await repository.fail_expired(30)
        assert not await repository.finish(
            message.id, old_run, {"status": "completed", "content": "Поздний ответ"}
        )
        await session.commit()
    assert (await read(env, conversation.id)).items[-1].status == "failed"


async def test_project_deletion_cascades_dialogues_and_messages(agent_env):
    env = agent_env
    conversation = await start(env)
    await send(env, conversation.id)
    async with env.factory() as session:
        await session.execute(delete(Project).where(Project.id == 1))
        await session.commit()
        assert not (await session.scalars(select(AgentMessage))).all()
    assert not await env.service.process_next()
