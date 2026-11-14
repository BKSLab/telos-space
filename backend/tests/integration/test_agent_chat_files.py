"""Реальный PostgreSQL: private/shared ACL, draft lifecycle и конкурентное извлечение."""

import asyncio
import hashlib
import os
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select, update

from src.clients.vision import DisabledVisionCapability
from src.db.models import (
    AgentConversation,
    AgentFile,
    AgentMessage,
    ApiKey,
    ApiTokenScope,
    ChatAgentTurn,
    ChatFileText,
    ChatMessage,
    ProjectChat,
    ProjectMember,
    User,
)
from src.dependencies.chat_files import build_agent_chat_files_service
from src.exceptions.agent_files import AgentFileNotFoundError
from src.repositories.agent_chat_files import AgentChatFilesRepository
from src.repositories.agent_files import AgentFilesRepository
from src.repositories.chat_attachments import ChatAttachmentsRepository
from src.repositories.chat_file_texts import ChatFileTextsRepository
from src.services.agent_chat_files import ChatFileContext
from src.services.auth import ApiKeyGrant, Principal


def reader(env, tmp_path, vision=None, *, lease=180, poll=0.03, max_chars=1_000_000):
    settings = SimpleNamespace(
        app=SimpleNamespace(
            uploads_path=tmp_path,
            chat_file_text_max_chars=max_chars,
            chat_file_text_lease_seconds=lease,
            chat_file_text_poll_seconds=poll,
            chat_file_text_wait_seconds=120,
        ),
        auth=SimpleNamespace(registration_invite_code=SecretStr("synthetic")),
    )
    return build_agent_chat_files_service(
        session_factory=env.factory,
        settings=settings,
        vision=vision if vision is not None else DisabledVisionCapability(),
    )


async def private_context(env, *, project_id=1, user_id=1):
    async with env.factory() as db:
        conversation = AgentConversation(user_id=user_id, project_id=project_id, title="Synthetic")
        db.add(conversation)
        await db.flush()
        request, run = uuid4(), uuid4()
        question = AgentMessage(
            conversation_id=conversation.id,
            request_id=request,
            role="user",
            content="Читай файл",
            status="completed",
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
        context = ChatFileContext(
            user_id=user_id,
            project_id=project_id,
            message_id=reply.id,
            run_id=run,
        )
        result = (context, conversation.id, question.id)
        await db.commit()
        return result


async def save_private(
    env, service, conversation_id, *, message_id=None, name="file.txt", content=b"text"
):
    key = await service.agent_storage.save(conversation_id, ".txt", content)
    async with env.factory() as db:
        row = await AgentFilesRepository(db).save(
            dict(
                id=uuid4(),
                conversation_id=conversation_id,
                message_id=message_id,
                original_name=name,
                content_type="text/plain",
                size=len(content),
                storage_key=key,
            )
        )
        file_id = row.id
        assert row.created_at is not None
        await db.commit()
    return file_id


async def shared_context(env, *, project_id=1, user_id=1):
    async with env.factory() as db:
        chat = (
            await db.execute(select(ProjectChat).where(ProjectChat.project_id == project_id))
        ).scalar_one()
        old = ChatMessage(
            chat_id=chat.id,
            project_id=project_id,
            seq=100,
            author_user_id=2,
            content="Ранний файл",
            client_message_id=uuid4(),
        )
        question = ChatMessage(
            chat_id=chat.id,
            project_id=project_id,
            seq=110,
            author_user_id=user_id,
            content="Читай файл",
            client_message_id=uuid4(),
        )
        reply = ChatMessage(
            chat_id=chat.id,
            project_id=project_id,
            seq=111,
            author_kind="agent",
            content="",
            client_message_id=uuid4(),
        )
        future = ChatMessage(
            chat_id=chat.id,
            project_id=project_id,
            seq=112,
            author_user_id=2,
            content="Будущее",
            client_message_id=uuid4(),
        )
        db.add_all([old, question, reply, future])
        await db.flush()
        run = uuid4()
        turn = ChatAgentTurn(
            chat_id=chat.id,
            project_id=project_id,
            request_message_id=question.id,
            reply_message_id=reply.id,
            requester_id=user_id,
            status="processing",
            run_id=run,
        )
        db.add(turn)
        await db.flush()
        context = ChatFileContext(
            user_id=user_id, project_id=project_id, chat_turn_id=turn.id, run_id=run
        )
        result = context, chat.id, old.id, question.id, future.id
        await db.commit()
        return result


async def save_shared(
    env, service, chat_id, *, message_id=None, uploader=2, name="file.txt", content=b"text"
):
    key = await service.chat_storage.save(chat_id, ".txt", content)
    async with env.factory() as db:
        row = await ChatAttachmentsRepository(db).save(
            dict(
                id=uuid4(),
                chat_id=chat_id,
                message_id=message_id,
                uploader_id=uploader,
                original_name=name,
                content_type="text/plain",
                size_bytes=len(content),
                storage_key=key,
            )
        )
        file_id = row.id
        await db.commit()
    return file_id


async def test_private_draft_is_prepared_once_then_visible_only_after_send(chat_env, tmp_path):
    service = reader(chat_env, tmp_path)
    context, conversation, question = await private_context(chat_env)
    text = "🙂Заголовок\n" + "a" * 14000 + "\nНужная строка [A.]"
    file_id = await save_private(chat_env, service, conversation, content=text.encode())
    assert (await service.list_files(context=context)).total == 0
    with pytest.raises(AgentFileNotFoundError):
        await service.read_text(context=context, file_id=file_id)
    state = await service.draft_status(
        kind="conversation", file_id=file_id, user_id=1, conversation_id=conversation, project_id=1
    )
    assert state.status == "pending"
    assert await service.process_next()
    assert not await service.process_next()
    with pytest.raises(AgentFileNotFoundError):
        await service.draft_status(
            kind="conversation",
            file_id=file_id,
            user_id=2,
            conversation_id=conversation,
            project_id=1,
        )
    async with chat_env.factory() as db:
        await AgentFilesRepository(db).bind_message(file_id, question)
        await db.commit()
    listing = await service.list_files(context=context)
    assert [file.file_id for file in listing.items] == [file_id]
    assert listing.items[0].excerpt == text[:600]
    assert listing.items[0].status == "ready"
    page = await service.read_text(context=context, file_id=file_id, max_chars=100)
    assert page.text == text[:100] and page.next_offset == 100
    assert page.content_hash == hashlib.sha256(text.encode()).hexdigest()
    found = await service.read_text(context=context, file_id=file_id, q="[a.]", max_chars=80)
    assert found.total_matches == 1 and found.matches[0].start == text.index("[A.]")
    assert len(found.text) <= 80
    # Повторное чтение использует производный cache, даже если диск стал недоступен.
    async with chat_env.factory() as db:
        original = await db.get(AgentFile, file_id)
        key = original.storage_key
    await service.agent_storage.delete_if_exists(key)
    assert (await service.read_text(context=context, file_id=file_id, max_chars=100)).text == text[
        :100
    ]


async def test_scope_filters_history_before_pagination_and_denies_foreign_future_deleted(
    chat_env, tmp_path
):
    service = reader(chat_env, tmp_path)
    context, chat_id, old_message, question, future = await shared_context(chat_env)
    old_file = await save_shared(chat_env, service, chat_id, message_id=old_message, name="old.txt")
    current_file = await save_shared(
        chat_env, service, chat_id, message_id=question, name="current.txt"
    )
    future_file = await save_shared(
        chat_env, service, chat_id, message_id=future, name="future.txt"
    )
    draft = await save_shared(chat_env, service, chat_id, name="draft.txt")
    page = await service.list_files(context=context, limit=1)
    assert page.total == 2 and page.next_offset == 1 and page.items[0].file_id == current_file
    page2 = await service.list_files(context=context, offset=1, limit=1)
    assert page2.items[0].file_id == old_file and page2.next_offset is None
    assert (await service.list_files(context=context, q="old")).total == 1
    for file_id in (draft, future_file):
        with pytest.raises(AgentFileNotFoundError):
            await service.read_text(context=context, file_id=file_id)
    for denied in (
        replace(context, user_id=2),
        replace(context, project_id=2),
        replace(context, run_id=uuid4()),
        replace(context, chat_turn_id=None),
    ):
        with pytest.raises(AgentFileNotFoundError):
            await service.list_files(context=denied)
    async with chat_env.factory() as db:
        await db.execute(
            update(ChatMessage)
            .where(ChatMessage.id == old_message)
            .values(deleted_at=datetime.now(UTC))
        )
        await db.commit()
    assert (await service.list_files(context=context)).total == 1
    with pytest.raises(AgentFileNotFoundError):
        await service.read_text(context=context, file_id=old_file)


async def test_private_and_personal_context_never_cross_conversations(chat_env, tmp_path):
    service = reader(chat_env, tmp_path)
    first, first_conversation, first_question = await private_context(chat_env, project_id=None)
    second, second_conversation, second_question = await private_context(chat_env, project_id=None)
    own = await save_private(chat_env, service, first_conversation, message_id=first_question)
    other = await save_private(chat_env, service, second_conversation, message_id=second_question)
    assert (await service.list_files(context=first)).items[0].file_id == own
    with pytest.raises(AgentFileNotFoundError):
        await service.read_text(context=first, file_id=other)
    with pytest.raises(AgentFileNotFoundError):
        await service.list_files(context=replace(first, user_id=2))
    assert (await service.list_files(context=replace(first, project_id=1))).items[0].file_id == own
    assert (await service.list_files(context=second)).total == 1


async def test_global_private_context_allows_accessible_page_project_but_fixed_project_is_exact(
    chat_env, tmp_path
):
    service = reader(chat_env, tmp_path)
    global_context, conversation, question = await private_context(
        chat_env, project_id=None, user_id=2
    )
    file_id = await save_private(chat_env, service, conversation, message_id=question)
    assert await service.process_next()
    assert (
        await service.read_text(context=replace(global_context, project_id=1), file_id=file_id)
    ).text == "text"
    with pytest.raises(AgentFileNotFoundError):
        await service.read_text(context=replace(global_context, project_id=2), file_id=file_id)
    fixed_context, fixed_conversation, fixed_question = await private_context(
        chat_env, project_id=1
    )
    fixed_file = await save_private(
        chat_env, service, fixed_conversation, message_id=fixed_question
    )
    with pytest.raises(AgentFileNotFoundError):
        await service.read_text(context=replace(fixed_context, project_id=2), file_id=fixed_file)


class BarrierVision:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = 0

    async def extract_image_text(self, *, filename, content):
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return "Изображение: 🙂данные"


async def test_lease_heartbeat_prevents_duplicate_ocr_and_delete_discards_late_result(
    chat_env, tmp_path
):
    vision = BarrierVision()
    service = reader(chat_env, tmp_path, vision, lease=0.15, poll=0.025)
    context, conversation, question = await private_context(chat_env)
    file_id = await save_private(
        chat_env, service, conversation, name="image.png", content=b"image"
    )
    first = asyncio.create_task(service.process_next())
    await asyncio.wait_for(vision.started.wait(), 2)
    assert chat_env.engine.pool.checkedout() == 0
    # DB-pool освобождён, lease живёт дольше исходного срока при медленном vision.
    await asyncio.sleep(0.25)
    assert not await service.process_next()
    assert vision.calls == 1
    with pytest.raises(AgentFileNotFoundError):
        await service.read_text(context=context, file_id=file_id)
    async with chat_env.factory() as db:
        await db.execute(delete(AgentFile).where(AgentFile.id == file_id))
        await db.commit()
    assert await asyncio.wait_for(first, 2)
    assert vision.cancelled.is_set()
    vision.release.set()
    async with chat_env.factory() as db:
        assert (
            await db.execute(select(ChatFileText).where(ChatFileText.agent_file_id == file_id))
        ).scalar_one_or_none() is None


async def test_send_during_ocr_preserves_cache_and_rechecks_reader_acl(chat_env, tmp_path):
    vision = BarrierVision()
    service = reader(chat_env, tmp_path, vision)
    context, conversation, question = await private_context(chat_env)
    file_id = await save_private(
        chat_env, service, conversation, name="image.png", content=b"image"
    )
    running = asyncio.create_task(service.process_next())
    await asyncio.wait_for(vision.started.wait(), 2)
    async with chat_env.factory() as db:
        await AgentFilesRepository(db).bind_message(file_id, question)
        await db.commit()
    assert (await service.read_text(context=context, file_id=file_id)).status == "processing"
    vision.release.set()
    assert await running
    assert (
        await service.read_text(context=context, file_id=file_id)
    ).text == "Изображение: 🙂данные"
    async with chat_env.factory() as db:
        await db.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 1)
        )
        await db.commit()
    with pytest.raises(AgentFileNotFoundError):
        await service.read_text(context=context, file_id=file_id)


async def test_revoked_owner_during_preparation_never_publishes_text(chat_env, tmp_path):
    vision = BarrierVision()
    service = reader(chat_env, tmp_path, vision)
    context, conversation, question = await private_context(chat_env)
    file_id = await save_private(
        chat_env, service, conversation, message_id=question, name="image.png", content=b"image"
    )
    running = asyncio.create_task(service.process_next())
    await asyncio.wait_for(vision.started.wait(), 2)
    async with chat_env.factory() as db:
        await db.execute(update(User).where(User.id == 1).values(is_active=False))
        await db.commit()
    vision.release.set()
    assert await running
    async with chat_env.factory() as db:
        cache = (
            await db.execute(select(ChatFileText).where(ChatFileText.agent_file_id == file_id))
        ).scalar_one()
        assert cache.status == "failed" and cache.content == ""
    with pytest.raises(AgentFileNotFoundError):
        await service.read_text(context=context, file_id=file_id)


async def test_legacy_files_enqueue_without_sync_extraction_and_ttl_preserves_sent(
    chat_env, tmp_path
):
    service = reader(chat_env, tmp_path)
    context, conversation, question = await private_context(chat_env)
    file_id = await save_private(chat_env, service, conversation, message_id=question)
    draft = await save_private(chat_env, service, conversation)
    orphan = await service.agent_storage.save(conversation, ".txt", b"orphan")
    fresh = await service.agent_storage.save(conversation, ".txt", b"pending upload")
    old_time = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(service.agent_storage.resolve(orphan), (old_time, old_time))
    async with chat_env.factory() as db:
        await db.execute(delete(ChatFileText).where(ChatFileText.agent_file_id == file_id))
        await db.execute(
            update(AgentFile)
            .where(AgentFile.id.in_([file_id, draft]))
            .values(created_at=datetime.now(UTC) - timedelta(days=2))
        )
        await db.commit()
    listing = await service.list_files(context=context)
    assert listing.items[0].status == "pending"
    async with chat_env.factory() as db:
        assert (
            await db.execute(select(ChatFileText).where(ChatFileText.agent_file_id == file_id))
        ).scalar_one_or_none() is None
    page = await service.read_text(context=context, file_id=file_id)
    assert page.status == "pending" and page.text == ""
    assert await service.cleanup_agent_drafts(retention_hours=24) == 2
    assert service.agent_storage.resolve(fresh).read_bytes() == b"pending upload"
    async with chat_env.factory() as db:
        assert await db.get(AgentFile, draft) is None
        assert await db.get(AgentFile, file_id) is not None
        assert (
            await db.execute(select(ChatFileText).where(ChatFileText.agent_file_id == file_id))
        ).scalar_one().status == "pending"
    assert await service.process_next()
    assert (await service.read_text(context=context, file_id=file_id)).text == "text"


async def test_cache_claim_singleflight_expired_reclaim_and_stale_token_cannot_publish(
    chat_env, tmp_path
):
    service = reader(chat_env, tmp_path)
    _, conversation, _ = await private_context(chat_env)
    file_id = await save_private(chat_env, service, conversation)

    async def claim(token):
        async with chat_env.factory() as db:
            row = await ChatFileTextsRepository(db).claim_next(token=token, lease_seconds=180)
            await db.commit()
            return row

    old_token, competitor = uuid4(), uuid4()
    rows = await asyncio.gather(claim(old_token), claim(competitor))
    claimed = [row for row in rows if row is not None]
    assert len(claimed) == 1 and claimed[0]["agent_file_id"] == file_id
    old_token = claimed[0]["lease_token"]
    async with chat_env.factory() as db:
        await db.execute(
            update(ChatFileText)
            .where(ChatFileText.agent_file_id == file_id)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
    new_token = uuid4()
    assert (await claim(new_token))["lease_token"] == new_token
    async with chat_env.factory() as db:
        repository = ChatFileTextsRepository(db)
        assert (
            await repository.finish(
                kind="conversation",
                file_id=file_id,
                token=old_token,
                values={"status": "ready", "content": "stale"},
            )
            is None
        )
        assert (
            await repository.finish(
                kind="conversation",
                file_id=file_id,
                token=new_token,
                values={"status": "ready", "content": "fresh"},
            )
            is not None
        )
        await db.commit()
    async with chat_env.factory() as db:
        assert (await ChatFileTextsRepository(db).get(kind="conversation", file_id=file_id))[
            "content"
        ] == "fresh"


async def test_ttl_and_send_lock_race_preserves_sent_file_and_its_prepared_text(chat_env, tmp_path):
    service = reader(chat_env, tmp_path)
    context, conversation, question = await private_context(chat_env)
    file_id = await save_private(chat_env, service, conversation)
    assert await service.process_next()
    cutoff = datetime.now(UTC) - timedelta(days=1)
    async with chat_env.factory() as db:
        await db.execute(
            update(AgentFile)
            .where(AgentFile.id == file_id)
            .values(created_at=cutoff - timedelta(hours=1))
        )
        await db.commit()
    ready = asyncio.Event()

    async def expire():
        ready.set()
        async with chat_env.factory() as db:
            deleted = await AgentChatFilesRepository(db).delete_expired_conversation_draft(
                file_id=file_id, cutoff=cutoff
            )
            await db.commit()
            return deleted

    async with chat_env.factory() as send:
        await send.execute(
            select(AgentConversation).where(AgentConversation.id == conversation).with_for_update()
        )
        expiring = asyncio.create_task(expire())
        await ready.wait()
        await AgentFilesRepository(send).bind_message(file_id, question)
        await send.commit()
    assert await expiring is None
    assert (await service.read_text(context=context, file_id=file_id)).text == "text"


async def test_cached_text_rechecks_active_context_and_key_after_cpu_phase(
    chat_env, tmp_path, monkeypatch
):
    import src.services.agent_chat_files as module

    service = reader(chat_env, tmp_path)
    context, conversation, question = await private_context(chat_env)
    file_id = await save_private(chat_env, service, conversation, message_id=question)
    assert await service.process_next()
    key_id = uuid4()
    async with chat_env.factory() as db:
        db.add(
            ApiKey(
                id=key_id,
                label="Synthetic",
                key_hash="!",
                key_prefix="cache123",
                owner_user_id=1,
                kind="personal",
                scopes=["agent:ask", "projects:read"],
                is_active=True,
            )
        )
        await db.commit()
    principal = Principal(
        user_id=1,
        username="chatuser1",
        first_name="Участник",
        last_name="1",
        middle_name=None,
        scope=ApiTokenScope.READ,
        via_api_token=True,
        api_key=ApiKeyGrant(
            key_id=key_id,
            label="Synthetic",
            key_prefix="cache123",
            scopes=frozenset({"agent:ask", "projects:read"}),
            owner_user_id=1,
        ),
    )
    context = replace(context, principal=principal)
    assert (await service.read_text(context=context, file_id=file_id)).text == "text"
    entered, release = threading.Event(), threading.Event()
    original_page = module._page

    def slow_page(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Synthetic barrier")
        return original_page(*args, **kwargs)

    monkeypatch.setattr(module, "_page", slow_page)
    pending = asyncio.create_task(service.read_text(context=context, file_id=file_id))
    await asyncio.wait_for(asyncio.to_thread(entered.wait), 2)
    async with chat_env.factory() as db:
        await db.execute(update(ApiKey).where(ApiKey.id == key_id).values(is_active=False))
        await db.commit()
    release.set()
    with pytest.raises(AgentFileNotFoundError):
        await pending
    monkeypatch.setattr(module, "_page", original_page)
    async with chat_env.factory() as db:
        await db.execute(
            update(AgentMessage)
            .where(AgentMessage.id == context.message_id)
            .values(status="completed")
        )
        await db.commit()
    with pytest.raises(AgentFileNotFoundError):
        await service.read_text(context=replace(context, principal=None), file_id=file_id)


async def test_cached_resource_limit_is_explicit_in_list_read_and_search(chat_env, tmp_path):
    service = reader(chat_env, tmp_path, max_chars=8000)
    context, conversation, question = await private_context(chat_env)
    text = "🙂" * 9000 + "beyond-resource-cap"
    file_id = await save_private(
        chat_env, service, conversation, message_id=question, content=text.encode()
    )
    assert await service.process_next()
    listing = await service.list_files(context=context)
    assert listing.items[0].truncated and listing.items[0].original_chars == len(text)
    page = await service.read_text(context=context, file_id=file_id)
    assert page.truncated and page.total_chars == 8000 and page.original_chars == len(text)
    assert page.detail and page.text == text[:8000] and page.next_offset is None
    found = await service.read_text(context=context, file_id=file_id, q="beyond-resource-cap")
    assert found.total_matches == 0 and found.truncated and found.text == ""
