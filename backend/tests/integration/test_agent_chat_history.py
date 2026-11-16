"""Реальная история PostgreSQL: границы run, сообщения/вложения и проверяемые цитаты."""

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from src.db.models import (
    AgentConversation,
    AgentFile,
    AgentMessage,
    ApiKey,
    ApiTokenScope,
    ChatAgentTurn,
    ChatAttachment,
    ChatFileText,
    ChatMessage,
    ChatMessageEntity,
    ProjectChat,
    ProjectStage,
    Task,
)
from src.dependencies.chat_history import build_agent_chat_history_service
from src.exceptions.project_chats import ChatConflictError, ChatNotFoundError, ChatValidationError
from src.schemas.chat_references import ChatMessageReferenceSchema
from src.services.agent_chat_files import ChatFileContext
from src.services.auth import ApiKeyGrant, Principal
from src.services.context_evidence import HIDDEN_CONTEXT_ANSWER
from src.utils.document_refs import encode_document_ref
from tests.integration.test_agent_chat_files import reader


async def shared_history(env):
    """Смешивает ID/seq, незавершённые ответы и удалённый текст рядом с верхней границей."""
    async with env.factory() as db:
        chat = (
            await db.execute(select(ProjectChat).where(ProjectChat.project_id == 1))
        ).scalar_one()
        older = [
            ChatMessage(
                chat_id=chat.id,
                project_id=1,
                seq=(index + 1) * 10,
                author_user_id=1 if index % 2 == 0 else 2,
                content=f"Решение {index + 1}",
                client_message_id=uuid4(),
            )
            for index in range(20)
        ]
        db.add_all(older)
        await db.flush()
        answers = []
        for index, status in enumerate(("failed", "processing", "queued", "completed")):
            answer = ChatMessage(
                chat_id=chat.id,
                project_id=1,
                seq=195 + index,
                author_kind="agent",
                content=f"Ответ Ариса {status}",
                client_message_id=uuid4(),
            )
            db.add(answer)
            await db.flush()
            db.add(
                ChatAgentTurn(
                    chat_id=chat.id,
                    project_id=1,
                    request_message_id=older[index + 10].id,
                    reply_message_id=answer.id,
                    requester_id=older[index + 10].author_user_id,
                    status=status,
                    run_id=uuid4(),
                )
            )
            answers.append(answer)
        deleted = ChatMessage(
            chat_id=chat.id,
            project_id=1,
            seq=199,
            author_user_id=2,
            content="Удалённый текст",
            client_message_id=uuid4(),
            deleted_at=datetime.now(UTC),
        )
        question = ChatMessage(
            chat_id=chat.id,
            project_id=1,
            seq=210,
            author_user_id=1,
            content="",
            client_message_id=uuid4(),
            reply_to_message_id=older[0].id,
        )
        reply = ChatMessage(
            chat_id=chat.id,
            project_id=1,
            seq=211,
            author_kind="agent",
            content="Текущее незавершённое",
            client_message_id=uuid4(),
        )
        future = ChatMessage(
            chat_id=chat.id,
            project_id=1,
            seq=220,
            author_user_id=2,
            content="Будущее сообщение",
            client_message_id=uuid4(),
        )
        db.add_all([deleted, question, reply, future])
        await db.flush()
        run = uuid4()
        turn = ChatAgentTurn(
            chat_id=chat.id,
            project_id=1,
            request_message_id=question.id,
            reply_message_id=reply.id,
            requester_id=1,
            status="processing",
            run_id=run,
        )
        db.add(turn)
        await db.flush()
        data = {
            "context": ChatFileContext(
                user_id=1, project_id=1, chat_turn_id=turn.id, run_id=run, timezone="Europe/Samara"
            ),
            "older": [row.id for row in older],
            "answers": [row.id for row in answers],
            "question": question.id,
            "reply": reply.id,
            "future": future.id,
            "deleted": deleted.id,
            "chat_id": chat.id,
        }
        await db.commit()
        return data


async def private_history(env):
    async with env.factory() as db:
        conversation = AgentConversation(user_id=2, project_id=None, title="Личная история")
        db.add(conversation)
        await db.flush()
        request = uuid4()
        first = AgentMessage(
            conversation_id=conversation.id,
            request_id=request,
            role="user",
            status="completed",
            content="Подтверждено расписание Review",
        )
        long_text = "А🙂" * 6000 + "УНИКАЛЬНАЯ ЦИТАТА" + "Б🙂" * 3000
        completed = AgentMessage(
            conversation_id=conversation.id,
            request_id=request,
            role="assistant",
            status="completed",
            content=long_text,
        )
        db.add_all([first, completed])
        await db.flush()
        request, run = uuid4(), uuid4()
        question = AgentMessage(
            conversation_id=conversation.id,
            request_id=request,
            role="user",
            status="completed",
            content="Текущий вопрос",
        )
        reply = AgentMessage(
            conversation_id=conversation.id,
            request_id=request,
            role="assistant",
            status="processing",
            run_id=run,
        )
        db.add_all([question, reply])
        await db.flush()
        future = AgentMessage(
            conversation_id=conversation.id,
            request_id=uuid4(),
            role="user",
            status="completed",
            content="Будущее",
        )
        db.add(future)
        await db.flush()
        data = {
            "context": ChatFileContext(user_id=2, project_id=None, message_id=reply.id, run_id=run),
            "conversation": conversation.id,
            "first": first.id,
            "completed": completed.id,
            "question": question.id,
            "future": future.id,
            "long_text": long_text,
        }
        await db.commit()
        return data


@pytest.mark.parametrize("kind", ["project_chat", "conversation"])
async def test_author_filters_resolve_me_and_exclude_lexical_agent_distractors_before_limit(
    chat_env, tmp_path, kind
):
    files = reader(chat_env, tmp_path)
    history = build_agent_chat_history_service(session_factory=chat_env.factory, chat_files=files)
    data = await (shared_history(chat_env) if kind == "project_chat" else private_history(chat_env))
    context = replace(data["context"], timezone="Europe/Samara")
    timestamp = datetime(2026, 11, 15, 9, tzinfo=UTC)
    own_ids = data["older"][:3:2] if kind == "project_chat" else [data["first"]]
    agent_id = data["answers"][-1] if kind == "project_chat" else data["completed"]
    model = ChatMessage if kind == "project_chat" else AgentMessage
    targets = [*own_ids, agent_id, data["question"], data["future"]]
    async with chat_env.factory() as db:
        await db.execute(
            update(model)
            .where(model.id.in_(targets))
            .values(content="Обсуждали смету", created_at=timestamp)
        )
        if kind == "project_chat":
            other_id = data["older"][1]
            await db.execute(
                update(ChatMessage)
                .where(ChatMessage.id == other_id)
                .values(content="Обсуждали смету", created_at=timestamp)
            )
            await db.execute(
                update(ChatMessage)
                .where(ChatMessage.id == data["future"])
                .values(author_user_id=context.user_id)
            )
            await db.execute(
                update(ChatMessage)
                .where(ChatMessage.id == data["older"][4])
                .values(content="Обсуждали смету", created_at=timestamp - timedelta(days=1))
            )
            foreign_scope = (
                await db.execute(select(ProjectChat.id).where(ProjectChat.project_id == 2))
            ).scalar_one()
            db.add(
                ChatMessage(
                    chat_id=foreign_scope,
                    project_id=2,
                    seq=1,
                    author_user_id=context.user_id,
                    content="Обсуждали смету",
                    client_message_id=uuid4(),
                    created_at=timestamp,
                )
            )
        else:
            foreign = AgentConversation(user_id=context.user_id, title="Другая личная переписка")
            db.add(foreign)
            await db.flush()
            foreign_scope = foreign.id
            db.add(
                AgentMessage(
                    conversation_id=foreign_scope,
                    request_id=uuid4(),
                    role="user",
                    status="completed",
                    content="Обсуждали смету",
                    created_at=timestamp,
                )
            )
        await db.commit()

    filters = {
        "context": context,
        "query": "смета",
        "before_message_id": data["question"],
        "date_from": date(2026, 11, 15),
        "date_to": date(2026, 11, 15),
    }
    page = await history.list_messages(**filters, author_user_id="me", author_type="USER", limit=1)
    assert [message.id for message in page.items] == own_ids[-1:]
    assert page.applied_filters.author_user_id == context.user_id
    assert page.applied_filters.author_type == "USER"
    assert all(
        message.author_id == context.user_id and message.author_type == "USER"
        for message in page.items
    )
    assert page.has_more == (len(own_ids) > 1)
    if page.has_more:
        previous = await history.list_messages(
            **(filters | {"before_message_id": page.next_before_message_id}),
            author_user_id="me",
            author_type="USER",
            limit=1,
        )
        assert [message.id for message in previous.items] == own_ids[:1] and not previous.has_more
    explicit = await history.list_messages(
        **filters, author_user_id=context.user_id, author_type="USER"
    )
    assert [message.id for message in explicit.items] == own_ids
    for author_id, author_type in ((99999999, "USER"), ("me", "AGENT")):
        empty = await history.list_messages(
            **filters, author_user_id=author_id, author_type=author_type
        )
        assert empty.items == []
        assert empty.applied_filters.author_user_id == (
            context.user_id if author_id == "me" else author_id
        )
        assert empty.applied_filters.author_type == author_type
    humans = await history.list_messages(**filters, author_type="USER", limit=1)
    assert [message.id for message in humans.items] == own_ids[-1:]
    agents = await history.list_messages(**filters, author_type="AGENT", limit=3)
    assert [message.id for message in agents.items] == [agent_id]
    assert not agents.has_more
    assert agents.items[0].author_type == "AGENT" and agents.items[0].author_id is None
    if kind == "project_chat":
        other = await history.list_messages(**filters, author_user_id=2, author_type="USER")
        assert [message.id for message in other.items] == [other_id]
    else:
        other = await history.list_messages(**filters, author_user_id=1, author_type="USER")
        assert other.items == []
    unbounded_author = await history.list_messages(context=context, query="смета")
    assert data["question"] in {message.id for message in unbounded_author.items}
    assert data["future"] not in {message.id for message in unbounded_author.items}
    with pytest.raises(ChatNotFoundError):
        await history.list_messages(
            **filters, chat_id=foreign_scope, author_user_id="me", author_type="USER"
        )


async def attachment(db, *, kind, scope_id, message_id, text=None, name="Решения.txt"):
    file_id = uuid4()
    if kind == "project_chat":
        db.add(
            ChatAttachment(
                id=file_id,
                chat_id=scope_id,
                message_id=message_id,
                uploader_id=1,
                storage_key=f"synthetic/{file_id}.txt",
                original_name=name,
                content_type="text/plain",
                size_bytes=10,
            )
        )
        cache_key = "chat_attachment_id"
    else:
        db.add(
            AgentFile(
                id=file_id,
                conversation_id=scope_id,
                message_id=message_id,
                storage_key=f"synthetic/{file_id}.txt",
                original_name=name,
                content_type="text/plain",
                size=10,
            )
        )
        cache_key = "agent_file_id"
    await db.flush()
    if text is not None:
        db.add(
            ChatFileText(
                **{cache_key: file_id},
                status="ready",
                content=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                original_chars=len(text),
                total_chars=len(text),
            )
        )
        await db.flush()
    return str(file_id)


async def test_initial_has_exact_15_eligible_messages_and_prioritized_nested_file_context(
    chat_env, tmp_path
):
    files = reader(chat_env, tmp_path)
    history = build_agent_chat_history_service(session_factory=chat_env.factory, chat_files=files)
    data = await shared_history(chat_env)
    async with chat_env.factory() as db:
        current_files = [
            await attachment(
                db,
                kind="project_chat",
                scope_id=data["chat_id"],
                message_id=data["question"],
                text="Т" * 2400,
            )
            for _ in range(2)
        ]
        old_file = await attachment(
            db,
            kind="project_chat",
            scope_id=data["chat_id"],
            message_id=data["older"][0],
            text="Ц" * 2400,
        )
        recent_file = await attachment(
            db,
            kind="project_chat",
            scope_id=data["chat_id"],
            message_id=data["older"][-1],
            text="И" * 2400,
        )
        legacy = await attachment(
            db, kind="project_chat", scope_id=data["chat_id"], message_id=data["question"]
        )
        draft = await attachment(
            db, kind="project_chat", scope_id=data["chat_id"], message_id=None, text="Черновик"
        )
        await db.execute(
            update(ChatMessage).where(ChatMessage.id == data["older"][-1]).values(content="")
        )
        await db.commit()
    result = await history.initial_context(context=data["context"])
    assert (
        result["current_message"]["id"] == data["question"]
        and result["current_message"]["content"] == ""
    )
    assert len(result["messages"]) == 15
    expected = [*data["older"][6:19], data["answers"][-1], data["older"][-1]]
    assert [message["id"] for message in result["messages"]] == expected
    assert result["quoted_message"]["id"] == data["older"][0]
    assert (
        result["messages"][-1]["content"] == ""
        and result["messages"][-1]["attachments"][0]["file_id"] == recent_file
    )
    all_messages = [result["current_message"], result["quoted_message"], *result["messages"]]
    files_by_id = {
        file["file_id"]: file for message in all_messages for file in message["attachments"]
    }
    assert all(len(files_by_id[file]["excerpt"]) == 2000 for file in current_files)
    assert (
        len(files_by_id[old_file]["excerpt"]) == 2000
        and files_by_id[recent_file]["excerpt"] is None
    )
    assert files_by_id[legacy]["status"] == "pending" and draft not in files_by_id
    assert sum(len(file["excerpt"] or "") for file in files_by_id.values()) == 6000
    assert result["timezone"] == "Europe/Samara" and result["current_datetime"].endswith("+04:00")
    async with chat_env.factory() as db:
        assert (
            await db.execute(select(ChatFileText).where(ChatFileText.chat_attachment_id == legacy))
        ).scalar_one_or_none() is None


async def test_filters_and_cursors_apply_before_limit_with_calendar_dst_boundaries(
    chat_env, tmp_path
):
    files = reader(chat_env, tmp_path)
    history = build_agent_chat_history_service(session_factory=chat_env.factory, chat_files=files)
    data = await shared_history(chat_env)
    context = replace(data["context"], timezone="America/New_York")
    timestamps = [
        datetime(2026, 3, 8, 4, 59, 59, tzinfo=UTC),
        datetime(2026, 3, 8, 5, tzinfo=UTC),
        datetime(2026, 3, 9, 3, 59, 59, tzinfo=UTC),
        datetime(2026, 3, 9, 4, tzinfo=UTC),
    ]
    async with chat_env.factory() as db:
        for message_id, timestamp in zip(data["older"][:4], timestamps, strict=True):
            await db.execute(
                update(ChatMessage)
                .where(ChatMessage.id == message_id)
                .values(created_at=timestamp, content="Решение диаграммы", author_user_id=2)
            )
        await db.execute(
            update(ChatMessage)
            .where(ChatMessage.id == data["future"])
            .values(created_at=timestamps[1], content="Решение диаграммы", author_user_id=2)
        )
        await db.commit()
    found = await history.list_messages(
        context=context,
        query="диаграмма",
        author_user_id=2,
        date_from=date(2026, 3, 8),
        date_to=date(2026, 3, 8),
        limit=1,
    )
    assert [message.id for message in found.items] == [data["older"][2]] and found.has_more
    prior = await history.list_messages(
        context=context,
        before_message_id=found.next_before_message_id,
        query="диаграмма",
        author_user_id=2,
        date_from=date(2026, 3, 8),
        date_to=date(2026, 3, 8),
        limit=1,
    )
    assert [message.id for message in prior.items] == [data["older"][1]] and not prior.has_more
    assert prior.applied_filters.timezone == "America/New_York"
    latest = await history.list_messages(context=context, limit=2)
    assert [message.id for message in latest.items] == [data["older"][-1], data["question"]]
    newer = await history.list_messages(
        context=context, after_message_id=data["older"][-1], limit=1
    )
    assert [message.id for message in newer.items] == [data["question"]] and not newer.has_more
    following = await history.list_messages(
        context=context, after_message_id=data["older"][0], limit=2
    )
    assert [message.id for message in following.items] == data["older"][
        1:3
    ] and following.next_after_message_id == data["older"][2]
    neighborhood = await history.get_message(
        context=context, message_id=data["older"][2], before_count=2, after_count=2
    )
    assert [message["id"] for message in neighborhood["neighbors"]] == [
        *data["older"][:2],
        *data["older"][3:5],
    ]
    for message_id in (data["future"], data["deleted"], data["reply"], *data["answers"][:3]):
        with pytest.raises(ChatNotFoundError):
            await history.get_message(context=context, message_id=message_id)


async def test_private_full_text_pages_references_and_precise_scope(chat_env, tmp_path):
    files = reader(chat_env, tmp_path)
    history = build_agent_chat_history_service(session_factory=chat_env.factory, chat_files=files)
    data = await private_history(chat_env)
    context = replace(data["context"], project_id=1)
    initial = await history.initial_context(context=context)
    assert [message["id"] for message in initial["messages"]] == [data["first"], data["completed"]]
    assert (
        initial["messages"][1]["text_truncated"]
        and initial["messages"][1]["next_content_offset"] == 2000
    )
    found = await history.list_messages(context=context, query="расписание", author_user_id=2)
    assert [message.id for message in found.items] == [data["first"]]
    collected = []
    offset = 0
    while offset is not None:
        page = (
            await history.get_message(context=context, message_id=data["completed"], offset=offset)
        )["message"]
        assert len(page["content"]) <= 8000 and page["content_offset"] == offset
        collected.append(page["content"])
        offset = page["next_content_offset"]
    assert "".join(collected) == data["long_text"]
    quoted = await history.get_message(
        context=context, message_id=data["completed"], offset=11000, quote="🙂УНИКАЛЬНАЯ ЦИТАТА"
    )
    reference = ChatMessageReferenceSchema.model_validate(quoted["message"]["reference"])
    assert reference.start == 11999 and reference.end - reference.start == len(reference.quote)
    assert reference.content_hash == hashlib.sha256(data["long_text"].encode()).hexdigest()
    verified = await history.verify_reference(
        context=context,
        reference=reference.model_copy(
            update={"author_name": "Непроверенное имя", "project_key": "FORGED"}
        ),
    )
    assert (
        verified.author_name == "Арис"
        and verified.project_id is None
        and verified.project_key is None
    )
    other = await private_history(chat_env)
    with pytest.raises(ChatNotFoundError):
        await history.get_message(context=other["context"], message_id=data["completed"])
    for bad in (
        replace(context, user_id=1),
        replace(context, project_id=2),
        replace(context, run_id=uuid4()),
    ):
        with pytest.raises(ChatNotFoundError):
            await history.initial_context(context=bad)
    with pytest.raises(ChatNotFoundError):
        await history.get_message(context=context, message_id=data["future"])
    with pytest.raises(ChatNotFoundError):
        await history.list_messages(context=context, chat_id=data["conversation"] + 1)
    with pytest.raises(ChatNotFoundError):
        await history.verify_reference(
            context=context, reference=reference.model_copy(update={"kind": "project_chat"})
        )
    with pytest.raises(ChatValidationError):
        await history.get_message(
            context=context, message_id=data["completed"], quote="Отсутствующая цитата"
        )
    async with chat_env.factory() as db:
        await db.execute(
            update(AgentMessage)
            .where(AgentMessage.id == data["completed"])
            .values(content="Изменённый текст")
        )
        await db.commit()
    with pytest.raises(ChatConflictError):
        await history.verify_reference(context=context, reference=reference)


async def test_shared_reference_rejects_edit_delete_and_foreign_cursors(chat_env, tmp_path):
    files = reader(chat_env, tmp_path)
    history = build_agent_chat_history_service(session_factory=chat_env.factory, chat_files=files)
    data = await shared_history(chat_env)
    context = data["context"]
    result = await history.get_message(
        context=context, message_id=data["older"][0], quote="Решение 1"
    )
    reference = ChatMessageReferenceSchema.model_validate(result["message"]["reference"])
    assert await history.verify_reference(context=context, reference=reference) == reference
    async with chat_env.factory() as db:
        other_chat = (
            await db.execute(select(ProjectChat).where(ProjectChat.project_id == 2))
        ).scalar_one()
        other_message = ChatMessage(
            chat_id=other_chat.id,
            project_id=2,
            seq=1,
            author_user_id=1,
            content="Другая область того же владельца",
            client_message_id=uuid4(),
        )
        db.add(other_message)
        await db.flush()
        other_id = other_message.id
        await db.commit()
    with pytest.raises(ChatNotFoundError):
        await history.list_messages(context=context, before_message_id=other_id)
    with pytest.raises(ChatNotFoundError):
        await history.get_message(context=context, chat_id=other_chat.id, message_id=other_id)
    foreign = await private_history(chat_env)
    with pytest.raises(ChatNotFoundError):
        await history.list_messages(context=foreign["context"], before_message_id=99999999)
    with pytest.raises(ChatNotFoundError):
        await history.verify_reference(
            context=context, reference=reference.model_copy(update={"project_id": 2})
        )
    with pytest.raises(ChatConflictError):
        await history.verify_reference(
            context=context, reference=reference.model_copy(update={"end": 2})
        )
    async with chat_env.factory() as db:
        await db.execute(
            update(ChatMessage)
            .where(ChatMessage.id == reference.message_id)
            .values(content="Новое решение", revision=2)
        )
        await db.commit()
    with pytest.raises(ChatConflictError):
        await history.verify_reference(context=context, reference=reference)
    async with chat_env.factory() as db:
        await db.execute(
            update(ChatMessage)
            .where(ChatMessage.id == reference.message_id)
            .values(deleted_at=datetime.now(UTC))
        )
        await db.commit()
    with pytest.raises(ChatNotFoundError):
        await history.verify_reference(context=context, reference=reference)


async def test_fresh_credential_check_prevents_return_after_key_revocation(
    chat_env, tmp_path, monkeypatch
):
    files = reader(chat_env, tmp_path)
    history = build_agent_chat_history_service(session_factory=chat_env.factory, chat_files=files)
    data = await private_history(chat_env)
    key_id = uuid4()
    async with chat_env.factory() as db:
        db.add(
            ApiKey(
                id=key_id,
                label="Synthetic history",
                key_hash="!",
                key_prefix="history1",
                owner_user_id=2,
                kind="personal",
                scopes=["agent:ask", "projects:read"],
                is_active=True,
            )
        )
        await db.commit()
    principal = Principal(
        user_id=2,
        username="chatuser2",
        first_name="Участник",
        last_name="2",
        middle_name=None,
        scope=ApiTokenScope.READ,
        via_api_token=True,
        api_key=ApiKeyGrant(
            key_id=key_id,
            label="Synthetic history",
            key_prefix="history1",
            scopes=frozenset({"agent:ask", "projects:read"}),
            owner_user_id=2,
        ),
    )
    context = replace(data["context"], project_id=1, principal=principal)
    entered, release = asyncio.Event(), asyncio.Event()
    recheck = history._recheck

    async def barrier(ctx, location):
        entered.set()
        await release.wait()
        await recheck(ctx, location)

    monkeypatch.setattr(history, "_recheck", barrier)
    pending = asyncio.create_task(history.list_messages(context=context))
    await asyncio.wait_for(entered.wait(), 2)
    assert chat_env.engine.pool.checkedout() == 0
    async with chat_env.factory() as db:
        await db.execute(update(ApiKey).where(ApiKey.id == key_id).values(is_active=False))
        await db.commit()
    release.set()
    with pytest.raises(ChatNotFoundError):
        await pending


async def test_filename_search_finds_file_only_messages_literally_in_both_scopes(
    chat_env, tmp_path
):
    files = reader(chat_env, tmp_path)
    history = build_agent_chat_history_service(session_factory=chat_env.factory, chat_files=files)
    for kind in ("project_chat", "conversation"):
        if kind == "project_chat":
            data = await shared_history(chat_env)
            model = ChatMessage
            target, other = data["older"][:2]
            scope_id = data["chat_id"]
            hidden = [data["future"], data["deleted"], data["reply"]]
            async with chat_env.factory() as db:
                foreign_chat = (
                    await db.execute(select(ProjectChat).where(ProjectChat.project_id == 2))
                ).scalar_one()
                foreign_message = ChatMessage(
                    chat_id=foreign_chat.id,
                    project_id=2,
                    seq=1,
                    author_user_id=1,
                    content="",
                    client_message_id=uuid4(),
                )
                db.add(foreign_message)
                await db.flush()
                foreign_scope, foreign_id = foreign_chat.id, foreign_message.id
                await db.commit()
        else:
            # Чужой здесь — другой собственный диалог, причём его IDs уже ниже horizon.
            foreign = await private_history(chat_env)
            data = await private_history(chat_env)
            model = AgentMessage
            target, other = data["first"], data["question"]
            scope_id = data["conversation"]
            foreign_scope, foreign_id = foreign["conversation"], foreign["first"]
            hidden = [data["future"], data["context"].message_id]
        context = data["context"]
        async with chat_env.factory() as db:
            await db.execute(update(model).where(model.id == target).values(content=""))
            await db.execute(
                update(model).where(model.id == other).values(content="Решение диаграммы")
            )
            expected_file = await attachment(
                db, kind=kind, scope_id=scope_id, message_id=target, name="only-file.txt"
            )
            await attachment(
                db, kind=kind, scope_id=scope_id, message_id=target, name="price%_v1.txt"
            )
            await attachment(
                db, kind=kind, scope_id=scope_id, message_id=target, name="slash\\literal.txt"
            )
            await attachment(
                db, kind=kind, scope_id=scope_id, message_id=other, name="priceANYZv1.txt"
            )
            await attachment(
                db, kind=kind, scope_id=scope_id, message_id=other, name="slashliteral.txt"
            )
            for message_id in hidden:
                await attachment(
                    db, kind=kind, scope_id=scope_id, message_id=message_id, name="price%_v1.txt"
                )
            await attachment(
                db, kind=kind, scope_id=scope_id, message_id=None, name="price%_v1.txt"
            )
            await attachment(
                db, kind=kind, scope_id=foreign_scope, message_id=foreign_id, name="price%_v1.txt"
            )
            await db.commit()
        for query in ("only-file.txt", "PRICE%_V1.TXT", "%", "_", "slash\\literal.txt", "\\"):
            result = await history.list_messages(context=context, query=query, limit=1)
            assert [message.id for message in result.items] == [target], (kind, query)
            assert not result.has_more
            assert result.items[0].content == ""
            assert expected_file in {str(file.file_id) for file in result.items[0].attachments}
        # FTS тела остаётся частью OR; EXISTS не дублирует родителя при нескольких файлах.
        body = await history.list_messages(context=context, query="диаграмма")
        assert [message.id for message in body.items] == [other]
        newest = await history.list_messages(context=context, query="txt", limit=1)
        assert [message.id for message in newest.items] == [other] and newest.has_more
        older = await history.list_messages(
            context=context, query="txt", limit=1, before_message_id=newest.next_before_message_id
        )
        assert [message.id for message in older.items] == [target] and not older.has_more


async def test_card_only_and_entity_only_messages_preserve_bounded_followup_identity(
    chat_env, tmp_path
):
    files = reader(chat_env, tmp_path)
    history = build_agent_chat_history_service(session_factory=chat_env.factory, chat_files=files)
    private = await private_history(chat_env)
    shared = await shared_history(chat_env)
    document_ref = encode_document_ref("project_file", 77)
    async with chat_env.factory() as db:
        stage = ProjectStage(project_id=1, name="История карточек", color="#334455", order_index=0)
        db.add(stage)
        await db.flush()
        task = Task(project_id=1, stage_id=stage.id, number=501, title="Заголовок задачи " * 25)
        db.add(task)
        await db.flush()
        task_id, task_key = task.id, f"C1-{task.number}"
        sources = [
            {
                "entity_type": "task",
                "entity_id": task_id,
                "source_id": f"task:{task_id}",
                "title": task.title,
                "excerpt": "LEAK_EXCERPT",
                "source_handle": "SRC_OLD_NONCE_1",
                "fragment_handle": "FRAG_OLD_NONCE_1",
                "message_reference_handle": "MSG_OLD_NONCE_1",
                "reference": {"quote": "LEAK_QUOTE"},
                "card": {"key": task_key, "status": "LEAK_STATUS", "summary": "LEAK_CARD_SUMMARY"},
            },
            {
                "entity_type": "task_report",
                "entity_id": 888,
                "source_id": "task_report:888",
                "task_id": task_id,
                "title": "Итог задачи",
            },
            {
                "entity_type": "project_file",
                "entity_id": 77,
                "source_id": "project_file:77",
                "document_ref": document_ref,
                "title": "Документ из ответа",
            },
            *[
                {
                    "entity_type": "risk",
                    "entity_id": 10000 + index,
                    "title": "Р" * 300,
                    "card": {"key": "K" * 120},
                }
                for index in range(25)
            ],
        ]
        await db.execute(
            update(AgentMessage)
            .where(AgentMessage.id == private["completed"])
            .values(content="", sources=sources)
        )
        await db.execute(
            update(AgentMessage)
            .where(AgentMessage.id == private["question"])
            .values(content="Измени эту задачу")
        )
        await db.execute(
            update(ChatAgentTurn)
            .where(ChatAgentTurn.reply_message_id == shared["answers"][-1])
            .values(sources=sources)
        )
        await db.execute(
            update(ChatMessage)
            .where(ChatMessage.id.in_([shared["answers"][-1], shared["older"][0]]))
            .values(content="")
        )
        db.add_all(
            [
                ChatMessageEntity(
                    message_id=shared["question"], entity_type="TASK", entity_id=task_id, position=0
                ),
                ChatMessageEntity(
                    message_id=shared["older"][0],
                    entity_type="DOCUMENT",
                    document_ref=document_ref,
                    position=0,
                ),
                ChatMessageEntity(
                    message_id=shared["answers"][-1],
                    entity_type="TASK",
                    entity_id=task_id,
                    position=0,
                ),
            ]
        )
        await db.commit()
    for data, card_id in ((private, private["completed"]), (shared, shared["answers"][-1])):
        initial = await history.initial_context(context=data["context"])
        card_only = next(message for message in initial["messages"] if message["id"] == card_id)
        assert card_only["content"] == HIDDEN_CONTEXT_ANSWER and not card_only["entity_references"]
        # Pure bounding of metadata is distinct from proof of current source ACL.
        hints = [hint.model_dump() for hint in history._entity_hints(sources)]
        assert len(hints) == 20
        assert hints[0]["entity_type"] == "task" and hints[0]["entity_id"] == task_id
        assert hints[0]["source_id"] == f"task:{task_id}" and hints[0]["key"] == task_key
        assert hints[1]["task_id"] == task_id
        assert (
            hints[2]["document_ref"] == document_ref and hints[2]["source_id"] == "project_file:77"
        )
        assert all(
            len(hint["title"] or "") <= 160 and len(hint["key"] or "") <= 64 for hint in hints
        )
        assert all(
            set(hint)
            == {"entity_type", "entity_id", "document_ref", "source_id", "task_id", "key", "title"}
            for hint in hints
        )
        detail = await history.get_message(context=data["context"], message_id=card_id)
        assert detail["message"]["entity_references"] == []
        page = await history.list_messages(context=data["context"])
        listed = next(message for message in page.items if message.id == card_id)
        assert not listed.entity_references
        serialized = json.dumps(initial, ensure_ascii=False)
        assert all(
            marker not in serialized
            for marker in ("LEAK_", "SRC_OLD_NONCE", "FRAG_OLD_NONCE", "MSG_OLD_NONCE")
        )
        if data is shared:
            current = initial["current_message"]
            assert (
                current["content"] == "" and current["entity_references"][0]["entity_id"] == task_id
            )
            assert initial["quoted_message"]["entity_references"][0]["document_ref"] == document_ref
        else:
            assert initial["current_message"]["content"] == "Измени эту задачу"
