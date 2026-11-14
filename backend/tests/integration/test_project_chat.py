"""Ключевые инварианты общего чата на PostgreSQL с настоящими commit."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from src.db.models import (
    ChatAttachment,
    ChatEvent,
    ProjectChat,
    ProjectMember,
    ProjectStage,
    Task,
    User,
)
from src.db.models.documents import Document
from src.db.models.project_files import ProjectFile
from src.db.models.task_attachments import TaskAttachment
from src.dependencies.scopes import build_chat_scope
from src.exceptions.project_chats import (
    ChatConflictError,
    ChatForbiddenError,
    ChatNotFoundError,
    ChatValidationError,
)
from src.schemas.project_chats import ChatMessageCreate, ChatMessageEdit
from src.services.chat_presenter import ChatPresenter
from src.services.project_chats import ProjectChatService
from src.utils.document_refs import encode_document_ref


def message(content="Проверим создание заказа", **kwargs):
    """Новая реплика по публичному контракту."""
    return ChatMessageCreate(content=content, client_message_id=uuid4(), **kwargs)


async def test_lifecycle_access_and_history_survive_new_service(chat_env):
    env = chat_env
    async with env.factory() as db:
        assert len(list((await db.execute(select(ProjectChat))).scalars())) == 2
    sent = await env.service.send(1, 1, message())
    other_service = ProjectChatService(
        build_chat_scope(session_factory=env.factory, invite_code="unused"), ChatPresenter()
    )
    assert (await other_service.history(1, 2)).messages[0].id == sent.id
    assert (await other_service.get_chat(1, 2)).unread_count == 1
    with pytest.raises(ChatNotFoundError):
        await env.service.history(1, 3)
    with pytest.raises(ChatNotFoundError):
        await env.service.history(2, 2)
    assert env.engine.sync_engine.pool.checkedout() == 0


async def test_concurrent_retry_has_one_message_and_one_event(chat_env):
    data = message()
    left, right = await asyncio.gather(
        chat_env.service.send(1, 1, data), chat_env.service.send(1, 1, data)
    )
    assert left.id == right.id
    assert len((await chat_env.service.history(1, 1)).messages) == 1
    events = await chat_env.service.events(1, 2, 0)
    assert len([event for event in events.events if event.type == "message.created"]) == 1


async def test_reconnect_replays_edits_reactions_and_tombstones(chat_env):
    chat = chat_env.service
    row = await chat.send(1, 1, message())
    cursor = (await chat.get_chat(1, 2)).event_cursor
    edited = await chat.edit(
        1, 1, row.id, ChatMessageEdit(content="Решение изменилось", expected_revision=1)
    )
    await chat.react(1, 2, row.id, "👍", True)
    await chat.delete(1, 1, row.id, edited.revision)
    page = await chat.events(1, 2, cursor)
    assert [event.type for event in page.events] == [
        "message.updated",
        "reaction.updated",
        "message.deleted",
    ]
    assert all(event.data["message"]["content"] == "" for event in page.events)
    assert all(event.data["message"]["deleted_at"] for event in page.events)


async def test_foreign_edits_stale_versions_and_cross_project_reply_rejected(chat_env):
    chat = chat_env.service
    row = await chat.send(1, 1, message())
    with pytest.raises(ChatForbiddenError):
        await chat.edit(1, 2, row.id, ChatMessageEdit(content="Чужая правка", expected_revision=1))
    await chat.edit(1, 1, row.id, ChatMessageEdit(content="Новая версия", expected_revision=1))
    with pytest.raises(ChatConflictError):
        await chat.delete(1, 1, row.id, 1)
    with pytest.raises(ChatNotFoundError):
        await chat.send(2, 1, message(reply_to_message_id=row.id))
    with pytest.raises(ChatValidationError):
        await chat.send(1, 1, message(mentions=[3]))


async def test_read_boundary_is_monotonic_and_membership_is_revoked(chat_env):
    chat = chat_env.service
    first = await chat.send(1, 1, message("Первое сообщение"))
    second = await chat.send(1, 1, message("Второе сообщение"))
    await chat.mark_read(1, 2, second.id)
    await chat.mark_read(1, 2, first.id)
    info = await chat.get_chat(1, 2)
    assert info.last_read_seq == second.seq and info.unread_count == 0
    async with chat_env.factory() as db:
        await db.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 2)
        )
        await db.commit()
    with pytest.raises(ChatNotFoundError):
        await chat.send(1, 2, message())
    events = await chat.events(1, 1, 0)
    assert events.events[-1].type == "member.removed"


async def test_new_member_sees_history_without_historical_unread(chat_env):
    chat = chat_env.service
    row = await chat.send(1, 1, message())
    async with chat_env.factory() as db:
        db.add(ProjectMember(project_id=1, user_id=3, role="MEMBER"))
        await db.commit()
    assert (await chat.get_chat(1, 3)).unread_count == 0
    assert (await chat.history(1, 3)).messages[0].id == row.id


async def test_files_are_private_until_sent_and_cannot_cross_project(chat_env):
    file = await chat_env.files.upload(1, 1, "решение.txt", "Согласовано".encode())
    with pytest.raises(ChatValidationError):
        await chat_env.service.send(1, 2, message(attachment_ids=[file.id]))
    with pytest.raises(ChatValidationError):
        await chat_env.service.send(2, 1, message(attachment_ids=[file.id]))
    sent = await chat_env.service.send(1, 1, message("", attachment_ids=[file.id]))
    assert sent.attachments[0].id == file.id
    with pytest.raises(ChatConflictError):
        await chat_env.files.delete_draft(1, 1, file.id)
    with pytest.raises(ChatValidationError):
        await chat_env.service.send(1, 1, message(attachment_ids=[file.id]))


async def test_keyset_search_and_reply_to_deleted_message(chat_env):
    chat = chat_env.service
    rows = [await chat.send(1, 1, message(f"Обсуждение интерфейса {i}")) for i in range(5)]
    page = await chat.history(1, 2, limit=2)
    assert [row.id for row in page.messages] == [rows[3].id, rows[4].id]
    previous = await chat.history(1, 2, before=page.messages[0].seq, limit=2)
    assert [row.id for row in previous.messages] == [rows[1].id, rows[2].id]
    search = await chat.history(1, 2, query="интерфейс", limit=10)
    assert len(search.messages) == 5
    reply = await chat.send(1, 2, message("Ответ", reply_to_message_id=rows[0].id))
    await chat.delete(1, 1, rows[0].id, 1)
    current = await chat.history(1, 1)
    assert next(row for row in current.messages if row.id == reply.id).reply.deleted


async def test_rollback_does_not_publish_entity_or_message_events(chat_env):
    cursor = (await chat_env.service.get_chat(1, 1)).event_cursor
    with pytest.raises(ChatValidationError):
        await chat_env.service.send(
            1, 1, message(entities=[{"entity_type": "TASK", "entity_id": 99999}])
        )
    assert (await chat_env.service.events(1, 1, cursor)).events == []
    async with chat_env.factory() as db:
        assert all(
            event.published_at is None for event in (await db.execute(select(ChatEvent))).scalars()
        )


async def test_disabled_user_loses_access_and_emits_revocation(chat_env):
    async with chat_env.factory() as db:
        await db.execute(update(User).where(User.id == 2).values(is_active=False))
        await db.commit()
    with pytest.raises(ChatNotFoundError):
        await chat_env.service.history(1, 2)
    assert (await chat_env.service.events(1, 1, 0)).events[-1].type == "member.removed"


async def test_entity_cards_follow_changes_and_reject_other_project(chat_env):
    async with chat_env.factory() as db:
        db.add_all(
            [ProjectStage(id=i, project_id=i, name="В работе", color="#334455") for i in (1, 2)]
        )
        await db.flush()
        db.add_all(
            [Task(id=i, project_id=i, stage_id=i, number=1, title=f"Проверка {i}") for i in (1, 2)]
        )
        await db.commit()
    chat = chat_env.service
    with pytest.raises(ChatValidationError):
        await chat.send(1, 1, message(entities=[{"entity_type": "TASK", "entity_id": 2}]))
    row = await chat.send(1, 1, message(entities=[{"entity_type": "TASK", "entity_id": 1}]))
    assert row.entities[0].status == "В работе"
    cursor = (await chat.get_chat(1, 1)).event_cursor
    async with chat_env.factory() as db:
        await db.execute(update(ProjectStage).where(ProjectStage.id == 1).values(name="Проверено"))
        await db.execute(update(Task).where(Task.id == 1).values(title="Уточнённая задача"))
        await db.commit()
    current = (await chat.history(1, 2)).messages[0].entities[0]
    assert current.title == "Уточнённая задача" and current.status == "Проверено"
    assert {event.type for event in (await chat.events(1, 2, cursor)).events} == {
        "entities.refresh",
        "entity.updated",
    }
    async with chat_env.factory() as db:
        await db.execute(delete(Task).where(Task.id == 1))
        await db.commit()
    deleted = (await chat.history(1, 2)).messages[0].entities[0]
    assert deleted.available is False and deleted.href is None


async def test_document_cards_keep_opaque_identity_across_storage_and_current_task_project(chat_env):
    async with chat_env.factory() as db:
        db.add_all([ProjectStage(id=i, project_id=i, name="В работе", color="#334455") for i in (1, 2)])
        await db.flush()
        db.add(Task(id=1, project_id=1, stage_id=1, number=1, title="Задача документа"))
        await db.flush()
        db.add_all([
            Document(id=1, project_id=1, title="Общий Markdown", slug="shared", content_md="Текст"),
            Document(id=2, project_id=2, title="Чужой Markdown", slug="foreign", content_md="Чужой текст"),
            Document(id=3, owner_task_id=1, title="Markdown задачи", slug="task", content_md="Условия"),
            ProjectFile(id=1, project_id=1, original_name="План.pdf", storage_key="projects/1/plan.pdf", content_type="application/pdf", size=20, content_hash="a" * 64),
            TaskAttachment(id=1, task_id=1, original_name="Условия.md", storage_key="tasks/1/spec.md", content_type="text/markdown", size=15),
        ])
        await db.commit()
    refs = [encode_document_ref(kind, identifier) for kind, identifier in [("document", 1), ("document", 3), ("project_file", 1), ("attachment", 1)]]
    body = [{"entity_type": "DOCUMENT", "document_ref": ref} for ref in refs]
    sent = await chat_env.service.send(1, 1, message(entities=body))
    assert [item.document_ref for item in sent.entities] == refs
    assert all(item.entity_id is None and item.href.endswith(item.document_ref) for item in sent.entities)
    with pytest.raises(ChatValidationError):
        await chat_env.service.send(1, 1, message(entities=[{"entity_type": "DOCUMENT", "document_ref": encode_document_ref("document", 2)}]))
    found = await chat_env.service.lookup_entities(1, 2)
    assert {item.document_ref for item in found if item.entity_type == "DOCUMENT"} == set(refs)
    cursor = (await chat_env.service.get_chat(1, 1)).event_cursor
    async with chat_env.factory() as db:
        await db.execute(update(ProjectFile).where(ProjectFile.id == 1).values(display_title="Уточнённый план"))
        await db.commit()
    changes = (await chat_env.service.events(1, 2, cursor)).events
    assert any(item.data.get("document_ref") == refs[2] and "entity_id" not in item.data for item in changes)
    async with chat_env.factory() as db:
        await db.execute(update(Task).where(Task.id == 1).values(project_id=2, stage_id=2))
        await db.commit()
    current = (await chat_env.service.history(1, 2)).messages[0].entities
    assert [item.document_ref for item in current] == refs
    assert [item.available for item in current] == [True, False, True, False]


async def test_cleanup_removes_expired_drafts_and_orphans_but_keeps_sent_files(chat_env):
    files = chat_env.files
    draft = await files.upload(1, 1, "draft.txt", b"draft")
    sent = await files.upload(1, 1, "sent.txt", b"keep")
    await chat_env.service.send(1, 1, message(attachment_ids=[sent.id]))
    old_orphan = await files.storage.save(1, ".txt", b"orphan")
    fresh_orphan = await files.storage.save(1, ".txt", b"pending upload")
    cutoff = datetime.now(UTC) - timedelta(days=2)
    async with chat_env.factory() as db:
        await db.execute(update(ChatAttachment).values(created_at=cutoff))
        rows = list((await db.execute(select(ChatAttachment))).scalars())
        keys = {row.id: row.storage_key for row in rows}
        await db.commit()
    for key in [*keys.values(), old_orphan]:
        os.utime(files.storage.resolve(key), (cutoff.timestamp(), cutoff.timestamp()))
    assert await files.cleanup() == 2
    assert files.storage.resolve(keys[sent.id]).read_bytes() == b"keep"
    assert files.storage.resolve(fresh_orphan).is_file()
    async with chat_env.factory() as db:
        assert (await db.get(ChatAttachment, draft.id)) is None
        assert (await db.get(ChatAttachment, sent.id)) is not None
    assert await files.cleanup() == 0
