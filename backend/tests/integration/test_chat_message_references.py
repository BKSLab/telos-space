"""Цитаты и переход к сообщению: актуальный UI и финальная ACL на PostgreSQL."""

# ruff: noqa: F811

import asyncio
from hashlib import sha256
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from src.db.models import AgentMessage, ApiKey, ChatAgentTurn, ChatMessage, Project, ProjectMember
from src.dependencies.chat_history import build_agent_chat_history_service
from src.dependencies.scopes import build_aris_chat_scope
from src.exceptions.agent_conversations import AgentConversationNotFoundError
from src.schemas.agent_conversations import AgentMessageCreateSchema, AgentPageContextSchema
from src.schemas.agent_requests import AgentAskSchema
from src.schemas.chat_references import ChatMessageReferenceSchema
from src.schemas.knowledge import KnowledgeAnswerSchema
from src.schemas.project_chats import ChatMessageCreate, ChatMessageEdit
from src.services.agent_requests import AgentRequestsService, RequestLinks
from src.services.agent_routing import AgentRoutingAnswer, AgentRoutingService
from src.services.aris_chat import ArisChatConfig, ArisChatService
from src.services.project_agent import ProjectAgentService
from tests.integration.repositories.test_agent_personal_conversations import agent_env  # noqa: F401
from tests.integration.test_agent_chat_files import reader


def history_reader(env, tmp_path):
    return build_agent_chat_history_service(
        session_factory=env.factory, chat_files=reader(env, tmp_path)
    )


def shared_worker(env, tmp_path):
    agent = AsyncMock(spec=ProjectAgentService)
    worker = ArisChatService(
        scope=build_aris_chat_scope(session_factory=env.factory),
        agent=agent,
        config=ArisChatConfig(turn_timeout_seconds=30),
        chat_history=history_reader(env, tmp_path),
    )
    return worker, agent


def reference(source, *, conversation_id):
    return ChatMessageReferenceSchema(
        kind="conversation",
        chat_id=conversation_id,
        message_id=source.id,
        project_id=None,
        author_id=1,
        author_name="Прежнее имя",
        created_at=source.created_at,
        revision=1,
        content_hash=sha256(source.content.encode("utf-8")).hexdigest(),
        quote=source.content,
        start=0,
        end=len(source.content),
    )


async def test_private_around_owns_target_and_hydrates_saved_references_in_one_conversation(
    agent_env,
):
    env = agent_env
    conversation = await env.service.create_conversation(user_id=1)
    foreign = await env.service.create_conversation(user_id=2)
    async with env.factory() as db:
        messages = [
            AgentMessage(
                conversation_id=conversation.id,
                request_id=uuid4(),
                role="user",
                status="completed",
                content=f"Реплика {n} 😀",
            )
            for n in range(18)
        ]
        other = AgentMessage(
            conversation_id=foreign.id,
            request_id=uuid4(),
            role="user",
            status="completed",
            content="Чужая приватная реплика",
        )
        db.add_all([*messages, other])
        await db.flush()
        refs = [
            reference(messages[0], conversation_id=conversation.id),
            reference(messages[1], conversation_id=conversation.id),
        ]
        cross_scope = reference(other, conversation_id=foreign.id)
        reply = AgentMessage(
            conversation_id=conversation.id,
            request_id=uuid4(),
            role="assistant",
            status="completed",
            content="Проверенный ответ",
            message_references=[r.model_dump(mode="json") for r in [*refs, cross_scope]],
        )
        db.add(reply)
        await db.commit()
        target_id, first_id, removed_id, reply_id, foreign_id = (
            messages[8].id,
            messages[0].id,
            messages[1].id,
            reply.id,
            other.id,
        )
    page = await env.service.get_messages(
        user_id=1,
        conversation_id=conversation.id,
        before_id=None,
        around_message_id=target_id,
        limit=5,
    )
    ids = [item.id for item in page.items]
    assert len(ids) == 5 and ids == sorted(ids) and ids[2] == target_id
    assert page.next_before_id == ids[0]
    earlier = await env.service.get_messages(
        user_id=1, conversation_id=conversation.id, before_id=page.next_before_id, limit=5
    )
    assert all(item.id < ids[0] for item in earlier.items)
    for target in (foreign_id, 999999):
        with pytest.raises(AgentConversationNotFoundError):
            await env.service.get_messages(
                user_id=1,
                conversation_id=conversation.id,
                before_id=None,
                around_message_id=target,
                limit=5,
            )
    with pytest.raises(AgentConversationNotFoundError):
        await env.service.get_messages(
            user_id=2,
            conversation_id=conversation.id,
            before_id=None,
            around_message_id=target_id,
            limit=5,
        )
    latest = await env.service.get_messages(
        user_id=1, conversation_id=conversation.id, before_id=None, limit=1
    )
    assert latest.items[0].id == reply_id
    assert [r.state for r in latest.items[0].message_references] == ["available", "available"]
    assert latest.items[0].message_references[0].author_name == "Тестовый Участник"
    async with env.factory() as db:
        await db.execute(
            update(AgentMessage)
            .where(AgentMessage.id == first_id)
            .values(content="Новая версия источника")
        )
        await db.execute(delete(AgentMessage).where(AgentMessage.id == removed_id))
        await db.commit()
    latest = await env.service.get_messages(
        user_id=1, conversation_id=conversation.id, before_id=None, limit=1
    )
    assert [r.state for r in latest.items[0].message_references] == ["edited", "unavailable"]
    assert all(r.quote == "" for r in latest.items[0].message_references)


@pytest.mark.parametrize("change", [None, "edit"])
async def test_private_worker_passes_structured_context_and_checks_citations_before_finish(
    agent_env, tmp_path, change
):
    env = agent_env
    env.service.chat_history = history_reader(env, tmp_path)
    conversation = await env.service.create_conversation(user_id=1)
    accepted = await env.service.send_message(
        user_id=1,
        conversation_id=conversation.id,
        data=AgentMessageCreateSchema(
            request_id=uuid4(),
            content="Процитируй эту реплику 😀",
            page_context=AgentPageContextSchema(route="/", timezone="Asia/Tokyo"),
        ),
    )

    async def respond(**kwargs):
        assert kwargs["history"] == [] and "uploaded_files" not in kwargs
        context = kwargs["chat_context"]
        assert context["timezone"] == kwargs["execution"].timezone == "Asia/Tokyo"
        assert context["current_message"]["id"] == accepted.user_message.id
        assert context["messages"] == []
        verified = ChatMessageReferenceSchema.model_validate(
            context["current_message"]["reference"]
        )
        if change == "edit":
            async with env.factory() as db:
                await db.execute(
                    update(AgentMessage)
                    .where(AgentMessage.id == accepted.user_message.id)
                    .values(content="Изменено во время ответа")
                )
                await db.commit()
        return KnowledgeAnswerSchema(
            answer="Цитата: " + verified.quote, sources=[], message_references=[verified]
        )

    env.responder.ask_personal.side_effect = respond
    assert await env.service.process_next()
    async with env.factory() as db:
        reply = await db.get(AgentMessage, accepted.assistant_message.id)
        if change:
            assert (
                reply.status == "failed"
                and reply.message_references == []
                and "Процитируй" not in reply.content
            )
        else:
            assert reply.status == "completed" and len(reply.message_references) == 1
    page = await env.service.get_messages(
        user_id=1, conversation_id=conversation.id, before_id=None, limit=2
    )
    if change is None:
        assert page.items[-1].message_references[0].state == "available"


async def test_shared_worker_persists_timezone_and_hydrates_edited_deleted_and_foreign_refs(
    chat_env, tmp_path
):
    env = chat_env
    worker, agent = shared_worker(env, tmp_path)
    source = await env.service.send(
        1, 1, ChatMessageCreate(content="Я исправлю завтра 😀", client_message_id=uuid4())
    )
    await env.service.send(
        1,
        2,
        ChatMessageCreate(
            content="Арис, кто это написал?",
            client_message_id=uuid4(),
            reply_to_message_id=source.id,
            timezone="Europe/Samara",
        ),
    )

    async def respond(**kwargs):
        assert kwargs["history"] == [] and "uploaded_files" not in kwargs
        context = kwargs["chat_context"]
        assert context["timezone"] == kwargs["execution"].timezone == "Europe/Samara"
        found = next(item for item in context["messages"] if item["id"] == source.id)
        verified = ChatMessageReferenceSchema.model_validate(found["reference"])
        return KnowledgeAnswerSchema(
            answer="Участник написал: " + verified.quote, sources=[], message_references=[verified]
        )

    agent.ask.side_effect = respond
    assert await worker.process_next()
    page = await env.service.history(1, 1)
    reply = page.messages[-1]
    assert (
        reply.agent.status == "completed" and reply.agent.message_references[0].state == "available"
    )
    assert reply.agent.message_references[0].quote == source.content
    async with env.factory() as db:
        turn = (
            await db.execute(
                select(ChatAgentTurn).where(ChatAgentTurn.reply_message_id == reply.id)
            )
        ).scalar_one()
        assert turn.timezone == "Europe/Samara"
        foreign_ref = dict(turn.message_references[0], kind="conversation")
        await db.execute(
            update(ChatAgentTurn)
            .where(ChatAgentTurn.id == turn.id)
            .values(message_references=[*turn.message_references, foreign_ref])
        )
        await db.execute(update(Project).where(Project.id == 1).values(key="NEWKEY"))
        await db.commit()
    current = (await env.service.history(1, 1)).messages[-1].agent.message_references
    assert len(current) == 1 and current[0].project_key == "NEWKEY"
    await env.service.edit(
        1, 1, source.id, ChatMessageEdit(content="Исправлю позже", expected_revision=1)
    )
    edited = (await env.service.history(1, 1)).messages[-1].agent.message_references[0]
    assert edited.state == "edited" and edited.quote == ""
    await env.service.delete(1, 1, source.id, 2)
    deleted_ref = (await env.service.history(1, 1)).messages[-1].agent.message_references[0]
    assert deleted_ref.state == "deleted" and deleted_ref.quote == ""


@pytest.mark.parametrize("change", ["edit", "revoke"])
async def test_shared_finish_never_publishes_cached_quote_after_source_or_access_changes(
    chat_env, tmp_path, change
):
    env = chat_env
    worker, agent = shared_worker(env, tmp_path)
    source = await env.service.send(
        1, 1, ChatMessageCreate(content="Исходная цитата 😀", client_message_id=uuid4())
    )
    await env.service.send(
        1, 2, ChatMessageCreate(content="Арис, процитируй решение", client_message_id=uuid4())
    )

    async def respond(**kwargs):
        found = next(item for item in kwargs["chat_context"]["messages"] if item["id"] == source.id)
        verified = ChatMessageReferenceSchema.model_validate(found["reference"])
        async with env.factory() as db:
            if change == "edit":
                await db.execute(
                    update(ChatMessage)
                    .where(ChatMessage.id == source.id)
                    .values(content="Иное решение", revision=2)
                )
            else:
                await db.execute(
                    delete(ProjectMember).where(
                        ProjectMember.project_id == 1, ProjectMember.user_id == 2
                    )
                )
            await db.commit()
        return KnowledgeAnswerSchema(
            answer="Цитата: " + verified.quote, sources=[], message_references=[verified]
        )

    agent.ask.side_effect = respond
    assert await worker.process_next()
    reply = (await env.service.history(1, 1)).messages[-1]
    assert reply.agent.status == "failed" and reply.agent.message_references == []
    assert "Исходная цитата" not in reply.content


async def test_shared_source_edit_waits_until_verified_answer_is_published(chat_env, tmp_path):
    env = chat_env
    worker, agent = shared_worker(env, tmp_path)
    source = await env.service.send(
        1, 1, ChatMessageCreate(content="Решение до правки 😀", client_message_id=uuid4())
    )
    await env.service.send(
        1, 2, ChatMessageCreate(content="Арис, процитируй решение", client_message_id=uuid4())
    )

    async def respond(**kwargs):
        found = next(item for item in kwargs["chat_context"]["messages"] if item["id"] == source.id)
        return KnowledgeAnswerSchema(
            answer="Решение проверено.", sources=[], message_references=[found["reference"]]
        )

    agent.ask.side_effect = respond
    verified, release = asyncio.Event(), asyncio.Event()
    original_verify = worker.chat_history.verify_reference

    async def pause_after_verify(**kwargs):
        reference = await original_verify(**kwargs)
        verified.set()
        await release.wait()
        return reference

    worker.chat_history.verify_reference = pause_after_verify
    processing = asyncio.create_task(worker.process_next())
    editing = None
    try:
        await asyncio.wait_for(verified.wait(), timeout=5)
        editing = asyncio.create_task(
            env.service.edit(
                1,
                1,
                source.id,
                ChatMessageEdit(content="Решение после публикации", expected_revision=1),
            )
        )
        _, pending = await asyncio.wait([editing], timeout=0.05)
        assert editing in pending
    finally:
        release.set()
        assert await asyncio.wait_for(processing, timeout=5)
        if editing is not None:
            await asyncio.wait_for(editing, timeout=5)
    reply = (await env.service.history(1, 1)).messages[-1]
    assert reply.agent.status == "completed"
    assert reply.agent.message_references[0].state == "edited"
    assert reply.agent.message_references[0].quote == ""


async def test_external_finish_uses_current_key_grant_without_rewriting_question_snapshot(
    agent_env,
):
    env = agent_env
    key_id = uuid4()
    original_scopes = ["agent:ask", "tasks:write"]
    async with env.factory() as db:
        db.add(
            ApiKey(
                id=key_id,
                key_prefix=uuid4().hex[:12],
                key_hash="synthetic",
                label="Test grant",
                owner_user_id=1,
                kind="personal",
                scopes=original_scopes,
            )
        )
        await db.commit()
    requests = AgentRequestsService(
        scope=env.service.scope, links=RequestLinks("http://routing.test")
    )
    request_id = await requests.ask_external(
        user_id=1,
        api_key_id=key_id,
        data=AgentAskSchema(text="Покажи мои задачи"),
        key_scopes=original_scopes,
    )
    async with env.factory() as db:
        await db.execute(update(ApiKey).where(ApiKey.id == key_id).values(scopes=["agent:ask"]))
        await db.commit()
    routing = AsyncMock(spec=AgentRoutingService)
    routing.ask.return_value = AgentRoutingAnswer(
        answer=KnowledgeAnswerSchema(answer="Проверено без записи.", sources=[]),
        project_id=None,
        project_key=None,
        mode="overview",
    )
    env.service.routing = routing
    assert await env.service.process_next()
    execution = routing.ask.await_args.kwargs["execution"]
    assert not execution.can_write and not execution.allows("telos_create_task")
    assert execution.principal.api_key.scopes == frozenset({"agent:ask"})
    async with env.factory() as db:
        pair = (
            (await db.execute(select(AgentMessage).where(AgentMessage.request_id == request_id)))
            .scalars()
            .all()
        )
        assert next(row for row in pair if row.role == "user").key_scopes == original_scopes
        assert next(row for row in pair if row.role == "assistant").status == "completed"


async def test_shared_citation_hides_quote_when_unchanged_source_answer_is_resumed(
    chat_env, tmp_path
):
    env = chat_env
    worker, agent = shared_worker(env, tmp_path)
    agent.ask.return_value = KnowledgeAnswerSchema(answer="Первый завершённый ответ 😀", sources=[])
    await env.service.send(
        1, 1, ChatMessageCreate(content="Арис, первый ответ", client_message_id=uuid4())
    )
    assert await worker.process_next()
    source = (await env.service.history(1, 1)).messages[-1]

    async def respond(**kwargs):
        found = next(item for item in kwargs["chat_context"]["messages"] if item["id"] == source.id)
        return KnowledgeAnswerSchema(
            answer="Первый ответ процитирован.", sources=[], message_references=[found["reference"]]
        )

    agent.ask.side_effect = respond
    await env.service.send(
        1, 1, ChatMessageCreate(content="Арис, процитируй первый ответ", client_message_id=uuid4())
    )
    assert await worker.process_next()
    assert (await env.service.history(1, 1)).messages[-1].agent.message_references[
        0
    ].state == "available"
    async with env.factory() as db:
        await db.execute(
            update(ChatAgentTurn)
            .where(ChatAgentTurn.reply_message_id == source.id)
            .values(status="queued")
        )
        await db.commit()
    reference = (await env.service.history(1, 1)).messages[-1].agent.message_references[0]
    assert reference.state == "unavailable" and reference.quote == ""
