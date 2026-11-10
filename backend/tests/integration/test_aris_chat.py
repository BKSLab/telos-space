"""Арис в общем чате проекта: адресация, очередь, ответ всем, ошибки и решения по действиям.

Проверки идут на PostgreSQL с настоящими commit: ответ Ариса — обычное
сообщение чата, поэтому важны порядок событий outbox и права решающего.
"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from src.agent.action_catalog import PERSONAL_ACTIONS, PROJECT_ACTIONS
from src.agent.aris import ArisMode
from src.agent.tools import AgentToolContext
from src.core.settings import get_settings
from src.db.models import ChatAgentTurn, ChatEvent, ChatMessage, ProjectMember
from src.dependencies.scopes import build_agent_project_tool_scope, build_aris_chat_scope
from src.exceptions.agent_tools import AgentToolAccessError, AgentToolConflictError
from src.exceptions.ai import AiProviderError
from src.repositories.chat_messages import ChatMessagesRepository
from src.schemas.knowledge import KnowledgeAnswerSchema, KnowledgeSourceSchema
from src.schemas.project_chats import ChatMessageCreate
from src.services.agent_actions import AgentActionsService
from src.services.aris_chat import ArisChatConfig, ArisChatService
from src.services.project_agent import ProjectAgentService


def message(content, **kwargs):
    return ChatMessageCreate(content=content, client_message_id=uuid4(), **kwargs)


@pytest.fixture
def aris(chat_env, tmp_path):
    settings = get_settings().model_copy(deep=True)
    settings.app.uploads_path = tmp_path
    agent = AsyncMock(spec=ProjectAgentService)
    agent.ask.return_value = KnowledgeAnswerSchema(answer="В проекте две задачи.", sources=[])
    chat_env.agent = agent
    chat_env.aris = ArisChatService(
        scope=build_aris_chat_scope(session_factory=chat_env.factory),
        agent=agent,
        config=ArisChatConfig(turn_timeout_seconds=30),
    )
    chat_env.actions = AgentActionsService(
        scope=build_agent_project_tool_scope(session_factory=chat_env.factory, settings=settings),
        actions=PROJECT_ACTIONS + PERSONAL_ACTIONS,
    )
    return chat_env


async def events(env, message_id):
    async with env.factory() as db:
        rows = (await db.execute(select(ChatEvent).order_by(ChatEvent.seq))).scalars()
        return [row.event_type for row in rows if row.payload.get("message_id") == message_id]


async def test_only_addressed_message_queues_aris_and_placeholder_is_visible_to_all(aris):
    env = aris
    for text in (
        "Аристотель, привет",
        "Арис2, сводка",
        "ARIS2, сводка",
        "коллеги, Арис сказал",
        "Вера, сводка",
        "Тим, сводка",
    ):
        ignored = await env.service.send(1, 2, message(text))
        assert ignored.agent is None
    assert not await env.aris.process_next()
    question = message("ArIs, какие задачи в проекте?")
    asked = await env.service.send(1, 2, question)
    page = await env.service.history(1, 1)
    placeholder = page.messages[-1]
    assert placeholder.author_type == "AGENT" and placeholder.author is None
    assert placeholder.reply.id == asked.id and placeholder.content == ""
    assert placeholder.agent.status == "queued" and placeholder.agent.name == "Арис"
    assert placeholder.agent.requester.id == 2
    assert await events(env, placeholder.id) == ["message.created"]
    # Повтор той же отправки не зовёт Ариса второй раз.
    repeated = await asyncio.gather(
        env.service.send(1, 2, question), env.service.send(1, 2, question)
    )
    assert all(item.id == asked.id for item in repeated)
    async with env.factory() as db:
        count = len((await db.execute(select(ChatAgentTurn))).scalars().all())
        saved_reply = await db.get(ChatMessage, placeholder.id)
        assert saved_reply.reply_to_message_id == asked.id
        replies = await db.scalars(
            select(ChatMessage).where(ChatMessage.reply_to_message_id == asked.id)
        )
        assert len(replies.all()) == 1
    assert count == 1
    assert await events(env, placeholder.id) == ["message.created"]


async def test_answer_uses_requester_rights_chat_history_and_quote(aris):
    env = aris
    quoted = await env.service.send(1, 1, message("Задача по складу зависла"))
    await env.service.send(1, 2, message("АРИС перенеси срок", reply_to_message_id=quoted.id))
    assert await env.aris.process_next()
    kwargs = env.agent.ask.await_args.kwargs
    assert kwargs["project_id"] == 1 and kwargs["mode"] is ArisMode.PROJECT_CHAT
    assert kwargs["question"] == "перенеси срок"
    execution = kwargs["execution"]
    assert (execution.user_id, execution.project_id, execution.can_write) == (2, 1, True)
    assert execution.chat_turn_id is not None and execution.message_id is None
    assert kwargs["actor"]["user_id"] == 2
    assert kwargs["extra_context"]["quoted_message"]["content"] == "Задача по складу зависла"
    assert [item.content for item in kwargs["history"]] == ["1 Участник: Задача по складу зависла"]
    reply = (await env.service.history(1, 2)).messages[-1]
    assert reply.content == "В проекте две задачи." and reply.agent.status == "completed"
    assert await events(env, reply.id) == ["message.created", "message.updated"]
    assert env.engine.sync_engine.pool.checkedout() == 0


@pytest.mark.parametrize("outcome", ["completed", "failed", "card_only"])
async def test_history_refreshes_terminal_reply_committed_after_reading_placeholder(
    aris, monkeypatch, outcome
):
    env = aris
    if outcome == "failed":
        env.agent.ask.side_effect = AiProviderError("synthetic provider failure")
    elif outcome == "card_only":
        env.agent.ask.return_value = KnowledgeAnswerSchema(
            answer="",
            sources=[
                KnowledgeSourceSchema(
                    source_id="project:1", entity_type="project", entity_id=1, title="Чат 1"
                )
            ],
        )
    await env.service.send(1, 1, message("Арис, что с проектом?"))
    original_get_page = ChatMessagesRepository.get_page
    interleaved = False

    async def finish_after_page(repository, chat_id, **kwargs):
        nonlocal interleaved
        rows = await original_get_page(repository, chat_id, **kwargs)
        placeholder = next((row for row in rows if row.author_kind == "agent"), None)
        if placeholder is not None and not interleaved:
            interleaved = True
            assert placeholder.content == "" and placeholder.revision == 1
            # Второй scope завершает ход после чтения страницы, до загрузки turns.
            assert await asyncio.wait_for(env.aris.process_next(), timeout=10)
            async with env.factory() as db:
                saved_reply = await db.get(ChatMessage, placeholder.id)
                assert saved_reply.revision == 2
            # Читающая сессия по-прежнему держит старый объект в identity map.
            assert placeholder.content == "" and placeholder.revision == 1
        return rows

    monkeypatch.setattr(ChatMessagesRepository, "get_page", finish_after_page)
    reply = (await asyncio.wait_for(env.service.history(1, 1), timeout=10)).messages[-1]

    assert interleaved
    assert reply.agent.status == ("failed" if outcome == "failed" else "completed")
    assert reply.revision == 2
    if outcome == "failed":
        assert reply.content.startswith("Не получилось ответить") and reply.agent.sources == []
    elif outcome == "card_only":
        assert reply.content == ""
        assert [source.source_id for source in reply.agent.sources] == ["project:1"]
    else:
        assert reply.content == "В проекте две задачи." and reply.agent.sources == []


async def test_model_failure_becomes_visible_error_message_not_silence(aris):
    env = aris
    await env.service.send(1, 1, message("Арис, что горит?"))
    env.agent.ask.side_effect = AiProviderError("upstream 500 секрет")
    await env.aris.process_next()
    reply = (await env.service.history(1, 1)).messages[-1]
    assert reply.agent.status == "failed"
    assert reply.content.startswith("Не получилось ответить") and "секрет" not in reply.content
    # Позвавший ушёл из проекта до ответа — ошибка тоже видна, модель не вызывается.
    env.agent.ask.reset_mock(side_effect=True)
    await env.service.send(1, 2, message("Арис, что горит?"))
    async with env.factory() as db:
        await db.execute(delete(ProjectMember).where(ProjectMember.user_id == 2))
        await db.commit()
    await env.aris.process_next()
    env.agent.ask.assert_not_awaited()
    async with env.factory() as db:
        turn = (
            (await db.execute(select(ChatAgentTurn).order_by(ChatAgentTurn.id.desc())))
            .scalars()
            .first()
        )
        reply = await db.get(ChatMessage, turn.reply_message_id)
    assert turn.status == "failed" and "не участник" in reply.content


async def test_live_progress_reaches_the_whole_chat_and_is_cleared_by_the_answer(aris):
    env = aris
    await env.service.send(1, 2, message("Арис, что с задачей P1-1?"))
    entered, release = asyncio.Event(), asyncio.Event()
    progress = {}

    async def respond(**kwargs):
        progress["write"] = kwargs["on_progress"]
        await kwargs["on_progress"]("Читаю задачу P1-1…")
        entered.set()
        await release.wait()
        return KnowledgeAnswerSchema(answer="Задача в работе.", sources=[])

    env.agent.ask.side_effect = respond
    running = asyncio.create_task(env.aris.process_next())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        # Статус видят все участники: и позвавший, и владелец.
        for user_id in (1, 2):
            live = (await env.service.history(1, user_id)).messages[-1]
            assert live.agent.status == "processing"
            assert live.agent.progress == "Читаю задачу P1-1…" and live.content == ""
        # Событие несёт сообщение с новым статусом — так его получат открытые чаты.
        replay = await env.service.events(1, 1, after=0)
        updated = [item for item in replay.events if item.type == "message.updated"]
        assert updated and updated[-1].data["message"]["agent"]["progress"] == (
            "Читаю задачу P1-1…"
        )
    finally:
        release.set()
        await running
    reply = (await env.service.history(1, 1)).messages[-1]
    assert reply.agent.status == "completed" and reply.agent.progress is None
    assert await events(env, reply.id) == [
        "message.created",
        "message.updated",
        "message.updated",
    ]
    # Запоздалая запись после ответа не меняет ход и не шлёт событий.
    await progress["write"]("Ищу задачи…")
    reply = (await env.service.history(1, 1)).messages[-1]
    assert reply.agent.progress is None
    assert len(await events(env, reply.id)) == 3


async def test_timeout_and_stalled_attempt_fail_visibly(aris):
    env = aris
    await env.service.send(1, 1, message("Арис, сводка"))

    async def stalled(**kwargs):
        await asyncio.Event().wait()

    env.agent.ask.side_effect = stalled
    env.aris.config = replace(env.aris.config, turn_timeout_seconds=0.05)
    await env.aris.process_next()
    reply = (await env.service.history(1, 1)).messages[-1]
    assert reply.agent.status == "failed" and "не уложился" in reply.content


async def test_proposed_action_is_decided_by_requester_or_member_with_rights(aris):
    env = aris
    owner = AgentToolContext(project_id=1, user_id=1, can_write=True)
    await env.actions.execute(
        context=replace(owner, request_id=uuid4()),
        name="telos_create_stage",
        arguments={"name": "Бэклог", "color": "#334455"},
    )
    created = await env.actions.execute(
        context=replace(owner, request_id=uuid4()),
        name="telos_create_task",
        arguments={"title": "Проверить окно заказа"},
    )
    task = created["action"]["result"]["task"]

    async def propose(**kwargs):
        # Арис в чате не видит личных действий: режим чата их не пускает.
        assert kwargs["execution"].mode is not ArisMode.WIDGET_PERSONAL
        await env.actions.execute(
            context=replace(kwargs["execution"], mode=ArisMode.PROJECT_CHAT),
            name="telos_delete_task",
            arguments={"task_id": task["id"], "expected_updated_at": task["updated_at"]},
        )
        return KnowledgeAnswerSchema(answer="Подтвердите удаление в карточке.", sources=[])

    env.agent.ask.side_effect = propose
    await env.service.send(1, 2, message("Арис, удали задачу про окно заказа"))
    await env.aris.process_next()
    reply = (await env.service.history(1, 1)).messages[-1]
    action = reply.agent.actions[0]
    assert action.status == "pending" and action.tool_name == "telos_delete_task"

    def decide(user_id, decision, project_id=1):
        return env.actions.decide_in_chat(
            context=AgentToolContext(project_id=project_id, user_id=user_id, can_write=True),
            action_id=UUID(str(action.id)),
            decision=decision,
        )

    for user_id, project_id in ((3, 1), (1, 2)):
        with pytest.raises(AgentToolAccessError):
            await decide(user_id, "approve", project_id)
    # Через виджет или MCP чатовое действие не решается.
    with pytest.raises(AgentToolAccessError):
        await env.actions.decide(context=owner, action_id=action.id, decision="approve")
    done = await decide(1, "approve")
    assert done.status == "completed" and done.result["decided_by"]["user_id"] == 1
    assert (await decide(1, "approve")).status == "completed"
    with pytest.raises(AgentToolConflictError):
        await decide(2, "reject")
    refreshed = (await env.service.history(1, 2)).messages[-1]
    assert refreshed.agent.actions[0].status == "completed"
    assert (await events(env, refreshed.id))[-1] == "message.updated"


async def test_owner_only_action_needs_owner_rights_and_requester_can_reject(aris):
    """Участник без прав на само действие (не владелец) подтвердить его не может."""
    env = aris

    async def propose(**kwargs):
        await env.actions.execute(
            context=kwargs["execution"], name="telos_remove_member", arguments={"user_id": 2}
        )
        return KnowledgeAnswerSchema(answer="Жду решения.", sources=[])

    env.agent.ask.side_effect = propose
    await env.service.send(1, 1, message("Арис, убери второго участника"))
    await env.aris.process_next()
    action = (await env.service.history(1, 1)).messages[-1].agent.actions[0]
    assert action.status == "pending"
    with pytest.raises(AgentToolAccessError):
        await env.actions.decide_in_chat(
            context=AgentToolContext(project_id=1, user_id=2, can_write=True),
            action_id=action.id,
            decision="approve",
        )
    rejected = await env.actions.decide_in_chat(
        context=AgentToolContext(project_id=1, user_id=1, can_write=True),
        action_id=action.id,
        decision="reject",
    )
    assert rejected.status == "rejected"
    async with env.factory() as db:
        members = (await db.execute(select(ProjectMember.user_id))).scalars().all()
    assert 2 in members
