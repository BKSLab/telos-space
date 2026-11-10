"""Внешний личный помощник на настоящем PostgreSQL.

Проверяется то, что нельзя проверить дублёрами: беседа без проекта в общей
очереди, выбор проекта строго среди проектов сотрудника, права ключа и
дословная реплика до проектного агента, видимость результата только автору
и решение по действию из беседы без проекта.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.clients.llm import LlmClient
from src.core.settings import get_settings
from src.db.models import Base, Project, ProjectMember, ProjectStage, Task, User
from src.db.models.agent_conversations import AgentConversation
from src.db.models.agent_messages import AgentMessage
from src.db.models.agent_tool_runs import AgentToolRun
from src.db.models.api_keys import ApiKey
from src.dependencies.scopes import build_agent_conversation_scope, build_agent_project_tool_scope
from src.exceptions.agent_conversations import AgentRequestBusyError, AgentRequestNotFoundError
from src.repositories.agent_conversations import AgentConversationsRepository
from src.schemas.agent_conversations import TimPageContextSchema
from src.schemas.agent_requests import AgentAskSchema
from src.schemas.knowledge import KnowledgeAnswerSchema, KnowledgeSourceSchema
from src.services.agent_requests import AgentRequestsService, RequestLinks
from src.services.project_agent import ProjectAgentService
from src.services.tim_widget import AgentConversationConfig, ConversationMemory, TimWidgetService
from src.services.tracker_agent import TrackerAgentService, TrackerRoute

KEY_A, KEY_B = uuid4(), uuid4()


@pytest_asyncio.fixture
async def tracker_env(postgres_container):
    database_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    database = f"agent_tracker_{uuid4().hex}"
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database}"'))
    engine = create_async_engine(admin.url.set(database=database), pool_size=3, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add_all(
            [
                User(id=i, username=f"user{i}", first_name="Участник", last_name=str(i), password_hash="!", is_active=True)
                for i in (1, 2)
            ]
        )
        await session.flush()
        session.add_all([ApiKey(id=key, label="Личный клиент", key_hash="unused",
            key_prefix=key.hex[:12], owner_user_id=1, kind="personal",
            scopes=["agent:ask", "tasks:write"], is_active=True) for key in (KEY_A, KEY_B)])
        session.add_all(
            [
                Project(id=1, owner_id=1, key="CRM", name="Внедрение CRM", color="#334455"),
                Project(id=2, owner_id=2, key="HR", name="Кадры", color="#334455"),
            ]
        )
        await session.flush()
        # Сотрудник 1 — только в CRM, сотрудник 2 — только в HR.
        session.add_all(
            [
                ProjectMember(project_id=1, user_id=1, role="OWNER"),
                ProjectMember(project_id=2, user_id=2, role="OWNER"),
            ]
        )
        await session.commit()
    responder = AsyncMock(spec=ProjectAgentService)
    responder.task_link_template.return_value = "https://tt.test/?task={key}"
    responder.ask_personal.return_value = KnowledgeAnswerSchema(answer="Открытых задач нет.", sources=[])
    responder.ask.return_value = KnowledgeAnswerSchema(
        answer="[CRM-5](https://tt.test/?task=CRM-5) в работе.",
        sources=[
            KnowledgeSourceSchema(
                source_id="task:5",
                entity_type="task",
                entity_id=5,
                title="Настроить воронку",
                card={"key": "CRM-5"},
            )
        ],
    )
    llm = AsyncMock(spec=LlmClient)
    llm.get_structured_response.return_value = ConversationMemory(summary="Раньше говорили о CRM.")
    settings = get_settings()
    tracker = TrackerAgentService(
        scope=build_agent_project_tool_scope(session_factory=factory, settings=settings),
        agent=responder,
        llm_client=llm,
    )
    conversations = TimWidgetService(
        scope=build_agent_conversation_scope(session_factory=factory),
        agent=responder,
        llm_client=llm,
        config=AgentConversationConfig(
            turn_timeout_seconds=30, history_messages=4, summary_batch_size=2
        ),
        tracker=tracker,
    )
    requests = AgentRequestsService(
        scope=build_agent_conversation_scope(session_factory=factory),
        links=RequestLinks("https://tt.test/"),
    )
    try:
        yield SimpleNamespace(
            conversations=conversations,
            requests=requests,
            responder=responder,
            llm=llm,
            factory=factory,
        )
    finally:
        await conversations.drain()
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()


async def ask(
    env,
    text_,
    *,
    user_id=1,
    key=KEY_A,
    user_text=None,
    conversation="local:chat:1",
    scopes=("agent:ask", "tasks:write"),
    page_context=None,
):
    return await env.requests.ask_external(
        user_id=user_id,
        api_key_id=key,
        data=AgentAskSchema(
            text=text_,
            user_text=user_text,
            conversation_id=conversation,
            page_context=page_context,
        ),
        key_scopes=list(scopes),
    )


async def test_task_key_routes_to_project_with_verbatim_and_key_scopes(tracker_env):
    env = tracker_env
    request_id = await ask(env, "Что с задачей CRM-5?")
    pending = await env.requests.get_external_result(
        user_id=1, api_key_id=KEY_A, request_id=request_id
    )
    assert pending.status == "queued" and pending.write_allowed is False
    assert await env.conversations.process_next()
    call = env.responder.ask.await_args.kwargs
    assert call["project_id"] == 1 and call["channel"] == "api"
    assert call["verbatim"] is None
    # Права ключа: tasks:write открывает изменения задач, но не комментарии и удаление.
    allowed = call["execution"].allowed_tools
    assert {"telos_create_task", "telos_update_task"} <= allowed
    assert not {"telos_add_comment", "telos_delete_task"} & allowed
    assert call["execution"].project_id == 1 and call["execution"].can_write is True
    # Ключ задачи дал проект без обращения к модели выбора.
    env.llm.get_structured_response.assert_not_awaited()
    result = await env.requests.get_external_result(
        user_id=1, api_key_id=KEY_A, request_id=request_id
    )
    assert result.status == "completed" and result.project_key == "CRM"
    assert result.links[0].key == "CRM-5"
    assert result.links[0].url == "https://tt.test/?task=CRM-5"
    # Дословная реплика доходит до проектного агента — только она разрешает запись.
    second = await ask(env, "Перенести CRM-5", user_text="Перенеси CRM-5 на пятницу")
    await env.conversations.process_next()
    assert env.responder.ask.await_args.kwargs["verbatim"] == "Перенеси CRM-5 на пятницу"
    assert env.responder.ask.await_args.kwargs["history"]  # та же беседа — та же память
    done = await env.requests.get_external_result(user_id=1, api_key_id=KEY_A, request_id=second)
    assert done.write_allowed is True and done.conversation_id == "local:chat:1"


async def test_project_key_as_a_word_routes_without_the_model(tracker_env):
    env = tracker_env
    await ask(env, "Что просрочено в проекте CRM?")
    await env.conversations.process_next()
    assert env.responder.ask.await_args.kwargs["project_id"] == 1
    # Голый ключ проекта однозначен так же, как ключ задачи: модель выбора не зовём.
    env.llm.get_structured_response.assert_not_awaited()
    # Ключ внутри слова или строчными — не ключ: решает модель выбора.
    env.llm.get_structured_response.return_value = TrackerRoute(
        mode="overview", answer="Открытых задач нет."
    )
    for index, text_ in enumerate(
        ("Как дела у CRMщиков?", "что в crm?", "Что горит, кроме CRM?", "Всё, кроме задачи CRM-5")
    ):
        env.llm.get_structured_response.reset_mock()
        await ask(env, text_, conversation=f"local:word:{index}")
        await env.conversations.process_next()
        env.llm.get_structured_response.assert_awaited_once()


async def test_foreign_project_is_invisible_and_ambiguity_is_clarified(tracker_env):
    env = tracker_env
    seen = []

    async def route(*, schema, content, **_kwargs):
        seen.append(content)
        # Модель пытается выбрать проект, которого нет у сотрудника.
        return TrackerRoute(mode="project", project_key="CRM", answer="")

    env.llm.get_structured_response.side_effect = route
    # Сотрудник 2 называет задачу чужого проекта: ключ не относится к его проектам.
    request_id = await ask(env, "Что с CRM-5?", user_id=2, key=None)
    await env.conversations.process_next()
    env.responder.ask.assert_not_awaited()
    assert '"CRM"' not in seen[0] and "Внедрение CRM" not in seen[0]
    assert '"HR"' in seen[0]
    result = await env.requests.get_external_result(
        user_id=2, api_key_id=None, request_id=request_id
    )
    assert result.status == "completed" and result.project_key is None
    assert "Уточните, о каком проекте речь" in result.answer
    assert "HR — Кадры" in result.answer and "CRM" not in result.answer.replace("CRM-5", "")


async def test_result_is_visible_only_to_its_key_and_employee(tracker_env):
    env = tracker_env
    request_id = await ask(env, "Что у меня горит?")
    for user_id, key in ((2, KEY_A), (1, KEY_B), (1, None)):
        with pytest.raises(AgentRequestNotFoundError):
            await env.requests.get_external_result(
                user_id=user_id, api_key_id=key, request_id=request_id
            )
    with pytest.raises(AgentRequestNotFoundError):
        await env.requests.get_external_result(user_id=1, api_key_id=KEY_A, request_id=uuid4())
    # Пока ответ готовится, второй вопрос в ту же беседу не встаёт, а в другую — встаёт.
    with pytest.raises(AgentRequestBusyError) as busy:
        await ask(env, "И ещё вопрос")
    assert str(request_id) in busy.value.detail
    other = await ask(env, "Другой чат", conversation="local:chat:2")
    assert other != request_id
    # Тот же ключ беседы у другого ключа доступа — отдельная беседа.
    await ask(env, "Чужой ключ", key=KEY_B)
    async with env.factory() as session:
        rows = (await session.scalars(select(AgentConversation))).all()
    assert len(rows) == 3
    assert {row.channel for row in rows} == {"api"}


async def test_pending_action_links_and_decision_in_conversation_without_project(tracker_env):
    env = tracker_env
    request_id = await ask(env, "Удали CRM-5", user_text="Удали CRM-5")
    await env.conversations.process_next()
    async with env.factory() as session:
        message = await session.scalar(
            select(AgentMessage).where(
                AgentMessage.request_id == request_id, AgentMessage.role == "assistant"
            )
        )
        action = AgentToolRun(
            id=uuid4(),
            project_id=1,
            user_id=1,
            message_id=message.id,
            request_id=uuid4(),
            tool_name="telos_delete_task",
            title="Удалить задачу",
            arguments={"task_id": 5},
            status="pending",
            result={"preview": {"task": {"key": "CRM-5", "title": "Настроить воронку"}}},
        )
        session.add(action)
        await session.commit()
        conversation_id = message.conversation_id
    result = await env.requests.get_external_result(
        user_id=1, api_key_id=KEY_A, request_id=request_id
    )
    assert [item.id for item in result.pending] == [action.id]
    assert result.pending[0].confirm_url == f"https://tt.test/?confirm={action.id}"
    assert result.pending[0].task_key == "CRM-5" and result.done == []
    # Решение по ссылке находит внешнюю беседу у её владельца, чужому — нет.
    async with env.factory() as session:
        repository = AgentConversationsRepository(session)
        assert await repository.get_owned(conversation_id, 1, channel=None) is not None
        assert await repository.get_owned(conversation_id, 2, channel=None) is None
        # Маршруты виджета внешнюю беседу не видят и в списке её нет.
        assert await repository.get_owned(conversation_id, 1) is None
        assert await repository.get_page(1, offset=0, limit=10) == []


async def add_tasks(env):
    """Задача CRM-5 (id 5) в проекте сотрудника 1 и HR-6 (id 6) — в чужом для него HR."""
    async with env.factory() as session:
        stages = [
            ProjectStage(project_id=project_id, name="Работа", color="#334455", order_index=0)
            for project_id in (1, 2)
        ]
        session.add_all(stages)
        await session.flush()
        session.add_all(
            [
                Task(
                    id=task_id,
                    project_id=stage.project_id,
                    stage_id=stage.id,
                    number=task_id,
                    title=title,
                    priority="MEDIUM",
                    position=float(task_id),
                )
                for task_id, stage, title in ((5, stages[0], "Воронка"), (6, stages[1], "Отпуск"))
            ]
        )
        await session.commit()


async def test_this_task_from_page_context_routes_to_its_project_without_key(tracker_env):
    env = tracker_env
    await add_tasks(env)
    page = TimPageContextSchema(route="/projects/CRM/board", task_id=5)
    request_id = await ask(
        env, "Перенести эту задачу на пятницу", user_text="перенеси эту задачу", page_context=page
    )
    async with env.factory() as session:
        question = await session.scalar(
            select(AgentMessage).where(
                AgentMessage.request_id == request_id, AgentMessage.role == "user"
            )
        )
    assert question.page_context == page.model_dump(mode="json")
    await env.conversations.process_next()
    call = env.responder.ask.await_args.kwargs
    # Проект — из задачи страницы, без модели выбора; «эта задача» — task_id.
    env.llm.get_structured_response.assert_not_awaited()
    assert call["project_id"] == 1 and call["execution"].project_id == 1
    assert call["extra_context"] == {"page_context": {"route": "/projects/CRM/board", "task_id": 5}}
    # Запись по-прежнему решает дословная реплика, а не контекст.
    assert call["verbatim"] == "перенеси эту задачу" and call["execution"].can_write is True
    result = await env.requests.get_external_result(
        user_id=1, api_key_id=KEY_A, request_id=request_id
    )
    assert result.project_key == "CRM"


async def test_foreign_page_context_gives_neither_read_nor_write(tracker_env):
    env = tracker_env
    await add_tasks(env)
    seen = []

    async def route(*, schema, content, **_kwargs):
        seen.append(content)
        return TrackerRoute(mode="clarify", answer="Уточните проект.")

    env.llm.get_structured_response.side_effect = route
    # Сотрудник 1 не в HR: ни проект, ни задача HR-6 из контекста не открываются.
    page = TimPageContextSchema(route="/projects/HR/board", project_key="HR", task_id=6)
    request_id = await ask(env, "Удали эту задачу", user_text="удали эту задачу", page_context=page)
    await env.conversations.process_next()
    env.responder.ask.assert_not_awaited()
    assert '"HR"' not in seen[0] and '"task_id"' not in seen[0]
    result = await env.requests.get_external_result(
        user_id=1, api_key_id=KEY_A, request_id=request_id
    )
    assert result.project_key is None and result.done == [] and result.pending == []
    # Ключ задачи в тексте главнее страницы: CRM-5 при контексте чужого HR идёт в CRM,
    # а чужой контекст до проектного агента не доходит.
    await ask(env, "Что с CRM-5?", page_context=page, conversation="local:chat:2")
    await env.conversations.process_next()
    call = env.responder.ask.await_args.kwargs
    assert call["project_id"] == 1
    assert call["extra_context"] == {"page_context": {"route": "/projects/HR/board"}}


async def test_missing_task_in_page_context_is_ignored(tracker_env):
    env = tracker_env
    seen = []

    async def route(*, schema, content, **_kwargs):
        seen.append(content)
        return TrackerRoute(mode="overview", answer="Открытых задач нет.")

    env.llm.get_structured_response.side_effect = route
    page = TimPageContextSchema(route="/my-tasks", task_id=999999)
    request_id = await ask(env, "Что с этой задачей?", page_context=page)
    await env.conversations.process_next()
    env.responder.ask.assert_not_awaited()
    assert '"task_id"' not in seen[0] and '"route": "/my-tasks"' in seen[0]
    result = await env.requests.get_external_result(
        user_id=1, api_key_id=KEY_A, request_id=request_id
    )
    assert result.status == "completed" and result.answer == "Открытых задач нет."
