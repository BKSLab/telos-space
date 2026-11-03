"""Диалог виджета Ариса: переписка, очередь, приватность и проект страницы — на PostgreSQL."""

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
from src.db.models.agent_conversations import AgentConversation
from src.db.models.agent_messages import AgentMessage
from src.dependencies.scopes import build_agent_conversation_scope
from src.exceptions.agent_conversations import (
    AgentConversationBusyError,
    AgentConversationNotFoundError,
    AgentMessageConflictError,
)
from src.repositories.agent_conversations import AgentConversationsRepository
from src.repositories.agent_messages import AgentMessagesRepository
from src.schemas.agent_conversations import AgentMessageCreateSchema, TimPageContextSchema
from src.schemas.knowledge import KnowledgeAnswerSchema, KnowledgeSourceSchema
from src.services.project_agent import ProjectAgentService
from src.services.tim_widget import (
    AgentConversationConfig,
    ConversationMemory,
    TimWidgetService,
)


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
                    first_name="Участник",
                    last_name="Тестовый",
                    password_hash="unused",
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
    responder.ask_personal.return_value = KnowledgeAnswerSchema(
        answer="Ваши задачи вне проекта.", sources=[]
    )
    service = TimWidgetService(
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


PAGE_P1 = TimPageContextSchema(route="/projects/P1/board", project_key="P1")


async def start(env, user_id=1):
    return await env.service.create_conversation(user_id=user_id)


async def send(
    env,
    conversation_id,
    content="Есть задача на проверку окна создания заказа?",
    request_id=None,
    page_context=PAGE_P1,
    user_id=1,
):
    return await env.service.send_message(
        user_id=user_id,
        conversation_id=conversation_id,
        data=AgentMessageCreateSchema(
            content=content, request_id=request_id or uuid4(), page_context=page_context
        ),
    )


async def read(env, conversation_id, **kwargs):
    return await env.service.get_messages(
        user_id=1, conversation_id=conversation_id, before_id=None, limit=50, **kwargs
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


async def test_dialogues_are_private_and_recheck_active_user(agent_env):
    env = agent_env
    conversation = await start(env)
    await send(env, conversation.id)
    assert not (await env.service.list_conversations(user_id=2, offset=0, limit=30)).items
    for user_id in (2, 999):
        with pytest.raises(AgentConversationNotFoundError):
            await env.service.get_messages(
                user_id=user_id, conversation_id=conversation.id, before_id=None, limit=50
            )
        with pytest.raises(AgentConversationNotFoundError):
            await env.service.send_message(
                user_id=user_id,
                conversation_id=conversation.id,
                data=AgentMessageCreateSchema(content="чужой вопрос", request_id=uuid4()),
            )
    async with env.factory() as session:
        await session.execute(update(User).where(User.id == 1).values(is_active=False))
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


async def test_page_context_picks_project_only_within_user_access(agent_env):
    """Проект страницы — подсказка: недоступный проект Арису не показывается."""
    env = agent_env
    newest = None
    for user_id, page, expected_project in (
        (1, PAGE_P1, 1),
        (1, TimPageContextSchema(route="/projects/P2", project_key="P2"), 2),
        # user2 не участник P2: ответ идёт в личном режиме без проекта.
        (2, TimPageContextSchema(route="/projects/P2", project_key="P2"), None),
        (1, TimPageContextSchema(route="/my/tasks"), None),
        (1, None, None),
    ):
        env.responder.ask.reset_mock()
        env.responder.ask_personal.reset_mock()
        conversation = await start(env, user_id=user_id)
        await send(env, conversation.id, page_context=page, user_id=user_id)
        await env.service.process_next()
        if expected_project is None:
            env.responder.ask.assert_not_awaited()
            kwargs = env.responder.ask_personal.await_args.kwargs
            assert kwargs["execution"].project_id is None
            assert kwargs["page_context"] == (page.model_dump(mode="json") if page else {})
        else:
            env.responder.ask_personal.assert_not_awaited()
            kwargs = env.responder.ask.await_args.kwargs
            assert kwargs["project_id"] == expected_project
            assert kwargs["extra_context"]["page_context"]["project_key"] == page.project_key
        newest = conversation
    listed = await env.service.list_conversations(user_id=1, offset=0, limit=30)
    assert listed.items[0].id == newest.id
    message = (
        await env.service.get_messages(
            user_id=1, conversation_id=newest.id, before_id=None, limit=50
        )
    ).items[0]
    assert message.page_context is None


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
                user_id=1, conversation_id=conversation.id, message_id=message.id
            )
    assert len((await read(env, conversation.id)).items) == 2


async def test_old_history_is_summarized_and_pagination_retains_the_full_transcript(agent_env):
    env = agent_env
    conversation = await start(env)
    for i in range(5):
        await send(env, conversation.id, f"Вопрос об окне заказа номер {i}")
        await env.service.process_next()
        if i < 4:
            await asyncio.gather(*env.service._memory_tasks.values())
    assert env.llm.get_structured_response.await_count == 2
    last = env.responder.ask.await_args.kwargs
    assert last["memory"] == "Обсуждается проверка окна создания заказа."
    assert len(last["history"]) == 4
    args = dict(user_id=1, conversation_id=conversation.id, limit=3)
    page = await env.service.get_messages(**args, before_id=None)
    earlier = await env.service.get_messages(**args, before_id=page.next_before_id)
    assert earlier.items[-1].id < page.items[0].id
    assert len((await read(env, conversation.id)).items) == 10
    other = await start(env)
    await send(env, other.id)
    await env.service.process_next()
    assert env.responder.ask.await_args.kwargs["history"] == []
    assert env.responder.ask.await_args.kwargs["memory"] == ""


async def test_memory_for_the_next_question_is_prepared_after_the_answer(agent_env):
    env = agent_env
    conversation = await start(env)
    for i in range(2):
        await send(env, conversation.id, f"Вопрос об окне заказа номер {i}")
        await env.service.process_next()
        await asyncio.gather(*env.service._memory_tasks.values())
    # Четыре реплики — ровно недавняя история: пересказывать нечего.
    env.llm.get_structured_response.assert_not_awaited()
    await send(env, conversation.id, "Вопрос об окне заказа номер 2")
    await env.service.process_next()
    # Пересказ случился после ответа, в фоне, а не перед ним.
    env.llm.get_structured_response.assert_not_awaited()
    await asyncio.gather(*env.service._memory_tasks.values())
    env.llm.get_structured_response.assert_awaited_once()
    async with env.factory() as session:
        saved = await session.get(AgentConversation, conversation.id)
        assert saved.summary == "Обсуждается проверка окна создания заказа."
        through_id = saved.summary_through_id
    # Следующий вопрос берёт готовую память и модель пересказа не ждёт.
    await send(env, conversation.id, "Вопрос об окне заказа номер 3")
    await env.service.process_next()
    env.llm.get_structured_response.assert_awaited_once()
    assert env.responder.ask.await_args.kwargs["memory"] == saved.summary
    assert len(env.responder.ask.await_args.kwargs["history"]) == 4
    # Память продвигается только от той границы, с которой её начинали считать.
    async with env.factory() as session:
        stale = AgentConversationsRepository(session)
        assert not await stale.save_summary(
            conversation.id, expected_through_id=0, summary="устарело", through_id=1
        )
        await session.rollback()
        assert (await session.get(AgentConversation, conversation.id)).summary_through_id >= (
            through_id
        )


async def test_pending_memory_does_not_block_reply_or_hide_earlier_decisions(agent_env):
    env = agent_env
    env.service.MEMORY_FALLBACK_MESSAGES = 2
    conversation = await start(env)
    first = await send(env, conversation.id, "Первый этап P1 завершён, переходим к выпуску.")
    await env.service.process_next()
    await asyncio.gather(*env.service._memory_tasks.values())
    stored_summary = "Первый этап P1 завершён; обсуждается выпуск."
    async with env.factory() as session:
        await session.execute(
            update(AgentConversation)
            .where(AgentConversation.id == conversation.id)
            .values(summary=stored_summary, summary_through_id=first.assistant_message.id)
        )
        await session.commit()

    decision = "Согласовали выпуск P1-7 на 2026-11-03. Ответственная — Анна."
    await send(env, conversation.id, decision)
    await env.service.process_next()
    await asyncio.gather(*env.service._memory_tasks.values())
    report = "Подробности проверки окна заказа.\n" * 300
    env.responder.ask.return_value = KnowledgeAnswerSchema(answer=report, sources=[])
    await send(env, conversation.id, "Подготовь подробный отчёт о проверке.")
    await env.service.process_next()
    await asyncio.gather(*env.service._memory_tasks.values())
    env.llm.get_structured_response.assert_not_awaited()

    entered, release = asyncio.Event(), asyncio.Event()

    async def summarize(**kwargs):
        entered.set()
        await release.wait()
        return ConversationMemory(summary=stored_summary + " " + decision)

    env.llm.get_structured_response.side_effect = summarize
    env.responder.ask.return_value = KnowledgeAnswerSchema(answer="Хорошо.", sources=[])
    await send(env, conversation.id, "Спасибо за отчёт.")
    await env.service.process_next()
    pending = env.service._memory_tasks[conversation.id]
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        for question in (
            "Напомни согласованную дату выпуска и ответственную.",
            "Покажи ещё раз дату и ответственную за выпуск.",
            "Что мы решили о выпуске?",
        ):
            await send(env, conversation.id, question)
            assert await asyncio.wait_for(env.service.process_next(), timeout=5)
            assert not pending.done()
            last = env.responder.ask.await_args.kwargs
            assert last["memory"] == stored_summary
            assert decision in "\n".join(item.content for item in last["history"])
            env.llm.get_structured_response.assert_awaited_once()
        assert env.engine.pool.checkedout() == 0
        assert len(last["history"]) > env.service.config.history_messages
        assert "часть переписки пропущена" in last["history"][0].content
        transcript = (await read(env, conversation.id)).items
        assert transcript[-1].status == "completed"
        assert decision in [item.content for item in transcript]
        assert report in [item.content for item in transcript]
    finally:
        release.set()
        await pending


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


async def test_user_deletion_cascades_dialogues_and_messages(agent_env):
    env = agent_env
    conversation = await start(env, user_id=2)
    await send(env, conversation.id, user_id=2)
    async with env.factory() as session:
        await session.execute(delete(ProjectMember).where(ProjectMember.user_id == 2))
        await session.execute(delete(User).where(User.id == 2))
        await session.commit()
        assert not (await session.scalars(select(AgentMessage))).all()
    assert not await env.service.process_next()


async def test_live_progress_is_visible_while_answering_and_cleared_after(agent_env):
    env = agent_env
    conversation = await start(env)
    await send(env, conversation.id)
    entered, release = asyncio.Event(), asyncio.Event()
    progress = {}

    async def respond(**kwargs):
        progress["write"] = kwargs["on_progress"]
        await kwargs["on_progress"]("Читаю задачу P1-1…")
        entered.set()
        await release.wait()
        return KnowledgeAnswerSchema(answer="Задача P1-1 в работе.", sources=[])

    env.responder.ask.side_effect = respond
    running = asyncio.create_task(env.service.process_next())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        live = (await read(env, conversation.id)).items[-1]
        assert (live.status, live.progress) == ("processing", "Читаю задачу P1-1…")
        # Статус не держит соединение с БД между записями.
        assert env.engine.pool.checkedout() == 0
    finally:
        release.set()
        await running
    done = (await read(env, conversation.id)).items[-1]
    assert (done.status, done.content, done.progress) == (
        "completed",
        "Задача P1-1 в работе.",
        None,
    )
    # Запоздалая запись после ответа ничего не меняет.
    await progress["write"]("Ищу задачи…")
    assert (await read(env, conversation.id)).items[-1].progress is None


async def test_failed_answer_clears_progress(agent_env):
    env = agent_env
    conversation = await start(env)
    await send(env, conversation.id)

    async def fail(**kwargs):
        await kwargs["on_progress"]("Смотрю календарь…")
        raise RuntimeError("модель упала")

    env.responder.ask.side_effect = fail
    await env.service.process_next()
    failed = (await read(env, conversation.id)).items[-1]
    assert failed.status == "failed" and failed.progress is None
