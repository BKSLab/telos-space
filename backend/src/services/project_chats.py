"""Один сценарий записи для REST и WS, с короткими транзакциями."""

import logging
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from src.agent.aris import is_addressed_to_aris
from src.agent.wakeup import wake_agent_worker
from src.exceptions.api_keys import ApiKeyRouteNotAllowedError
from src.exceptions.base import RepositoryError
from src.exceptions.project_chats import (
    ChatConflictError,
    ChatForbiddenError,
    ChatNotFoundError,
    ChatServiceError,
    ChatValidationError,
)
from src.schemas.project_chats import (
    ChatEventPage,
    ChatEventView,
    ChatMessageBody,
    ChatMessageCreate,
    ChatMessageEdit,
    ChatMessagePage,
    ProjectChatView,
)
from src.services.chat_presenter import ChatPresenter, entity_view, public_user
from src.services.chat_scope import ChatScope, ChatScopeFactory

logger = logging.getLogger(__name__)


class ProjectChatService:
    """Сервис принимает фабрику коротких областей, а не удерживаемую сессию."""

    def __init__(self, scope: ChatScopeFactory, presenter: ChatPresenter) -> None:
        self.scope = scope
        self.presenter = presenter
        self.metrics = Counter()

    @asynccontextmanager
    async def operation(self):
        """Скрывает ошибки хранилища; сессия закрывается до сетевой доставки."""
        started = monotonic()
        try:
            async with self.scope() as db:
                yield db
        except RepositoryError as error:
            logger.exception("❌ Ошибка операции чата.")
            raise ChatServiceError(str(error)) from error
        finally:
            self.metrics["db_scope_count"] += 1
            self.metrics["db_scope_ms_sum"] += (monotonic() - started) * 1000

    async def require(self, db: ChatScope, project_id: int, user_id: int, *, lock: bool = False):
        """Повторно проверяет активность и membership в каждой операции."""
        chat = await db.chats.get(project_id, lock=lock)
        if chat is None or await db.participants.get_member(project_id, user_id) is None:
            raise ChatNotFoundError("Нет доступа к чату проекта.")
        return chat

    async def authenticate(
        self, project_id: int, *, session_token: str | None, bearer_secret: str | None
    ):
        """Handshake/heartbeat проверяет сессию в собственном коротком scope."""
        async with self.operation() as db:
            principal = await db.auth.resolve_principal(
                session_token=session_token, bearer_secret=bearer_secret
            )
            if getattr(principal, "api_key", None) is not None:
                raise ApiKeyRouteNotAllowedError('project chat')
            await self.require(db, project_id, principal.user_id)
            return principal

    async def get_chat(self, project_id: int, user_id: int) -> ProjectChatView:
        """Возвращает персональную границу прочтения и публичный состав чата."""
        async with self.operation() as db:
            chat = await self.require(db, project_id, user_id)
            last_read = await db.reads.get(chat.id, user_id)
            return ProjectChatView(
                id=chat.id,
                project_id=project_id,
                event_cursor=chat.last_event_seq,
                last_read_seq=last_read,
                unread_count=await db.messages.unread_count(chat.id, user_id, last_read),
                members=[public_user(row) for row in await db.participants.get_members(project_id)],
            )

    async def history(
        self,
        project_id: int,
        user_id: int,
        *,
        before: int | None = None,
        after: int | None = None,
        around: int | None = None,
        limit: int = 50,
        query: str | None = None,
    ) -> ChatMessagePage:
        """Последние/старые сообщения, поиск и переход к окружению результата."""
        if sum(value is not None for value in (before, after, around)) > 1:
            raise ChatValidationError("Задайте только один курсор истории.")
        async with self.operation() as db:
            chat = await self.require(db, project_id, user_id)
            cursor = chat.last_event_seq
            if around is not None:
                target = await self._message(db, chat.id, around)
                older = await db.messages.get_page(chat.id, before=target.seq, limit=limit // 2 + 1)
                newer = await db.messages.get_page(chat.id, after=target.seq, limit=limit // 2)
                rows = [*reversed(older[: limit // 2]), target, *newer]
                more = len(older) > limit // 2
            else:
                rows = await db.messages.get_page(
                    chat.id, before=before, after=after, limit=limit + 1, query=query
                )
                more = len(rows) > limit
                rows = rows[:limit]
                if after is None:
                    rows.reverse()
            return ChatMessagePage(
                messages=await self.presenter.messages(db, rows), has_more=more, event_cursor=cursor
            )

    async def lookup_entities(self, project_id: int, user_id: int, query: str = ""):
        """Ищет объекты текущего проекта для # picker."""
        async with self.operation() as db:
            await self.require(db, project_id, user_id)
            return [
                entity_view(row) for row in await db.entity_lookup.find(project_id, query=query)
            ]

    async def hydrate_entities(self, project_id: int, user_id: int, refs: list):
        """Обновляет уже показанные карточки после событий доменного объекта."""
        async with self.operation() as db:
            await self.require(db, project_id, user_id)
            rows = await db.entity_lookup.find(
                project_id,
                refs=[(ref.entity_type, ref.entity_id) for ref in refs],
                limit=max(len(refs), 1),
            )
            return [entity_view(row) for row in rows]

    async def send(self, project_id: int, user_id: int, data: ChatMessageCreate):
        """Повторный UUID возвращает исходное сообщение без второго события."""
        started = monotonic()
        async with self.operation() as db:
            chat = await self.require(db, project_id, user_id, lock=True)
            duplicate = await db.messages.get_duplicate(chat.id, user_id, data.client_message_id)
            if duplicate:
                return (await self.presenter.messages(db, [duplicate]))[0]
            await self._validate_body(db, chat, user_id, data)
            if data.reply_to_message_id:
                await self._message(db, chat.id, data.reply_to_message_id)
            seq = await db.chats.next_seq(chat.id)
            row = await db.messages.save(
                dict(
                    chat_id=chat.id,
                    project_id=project_id,
                    seq=seq,
                    author_user_id=user_id,
                    content=data.content,
                    client_message_id=data.client_message_id,
                    reply_to_message_id=data.reply_to_message_id,
                )
            )
            await self._save_links(db, row.id, data)
            await db.events.save(chat.id, seq, "message.created", {"message_id": row.id})
            aris_called = is_addressed_to_aris(data.content)
            if aris_called:
                await self._call_aris(db, chat, project_id, user_id, row)
            result = (await self.presenter.messages(db, [row]))[0]
            await db.unit_of_work.commit()
            if aris_called:
                wake_agent_worker()
            self.metrics["db_message_write_count"] += 1
            self.metrics["db_message_write_ms_sum"] += (monotonic() - started) * 1000
            logger.info(
                "chat.message_created project_id=%s actor_id=%s message_id=%s seq=%s chat_id=%s client_message_id=%s duration_ms=%.1f",
                project_id,
                user_id,
                row.id,
                seq,
                chat.id,
                data.client_message_id,
                (monotonic() - started) * 1000,
            )
            return result

    async def _call_aris(self, db: ChatScope, chat, project_id: int, user_id: int, request) -> None:
        """Ставит ответ Ариса в очередь: пустое сообщение Ариса видно всем как «печатает».

        Ответ-заглушка и ход сохраняются в транзакции вопроса, поэтому вопрос
        без ответа (или ответ без вопроса) невозможен даже при сбое процесса.
        """
        seq = await db.chats.next_seq(chat.id)
        reply = await db.messages.save(
            dict(
                chat_id=chat.id,
                project_id=project_id,
                seq=seq,
                author_user_id=None,
                author_kind="agent",
                content="",
                client_message_id=uuid4(),
                reply_to_message_id=request.id,
            )
        )
        await db.turns.save(
            dict(
                chat_id=chat.id,
                project_id=project_id,
                request_message_id=request.id,
                reply_message_id=reply.id,
                requester_id=user_id,
                status="queued",
            )
        )
        await db.events.save(chat.id, seq, "message.created", {"message_id": reply.id})
        logger.info(
            "aris.chat_turn_queued project_id=%s actor_id=%s request_id=%s reply_id=%s",
            project_id,
            user_id,
            request.id,
            reply.id,
        )

    async def edit(self, project_id: int, user_id: int, message_id: int, data: ChatMessageEdit):
        """Меняет свою реплику и её ссылки в одной транзакции."""
        async with self.operation() as db:
            chat = await self.require(db, project_id, user_id, lock=True)
            row = await self._own_message(db, chat.id, message_id, user_id, data.expected_revision)
            await self._validate_body(db, chat, user_id, data, message_id=message_id)
            current_files = await db.attachments.get_many([message_id])
            if {row.id for row in current_files} != set(data.attachment_ids):
                raise ChatValidationError(
                    "Правка сохраняет исходные вложения. Новые файлы отправьте отдельным сообщением."
                )
            row = await db.messages.update(
                message_id,
                dict(content=data.content, revision=row.revision + 1, edited_at=datetime.now(UTC)),
            )
            await db.entities.delete(message_id)
            await db.mentions.delete(message_id)
            await self._save_links(db, message_id, data)
            await self._emit(db, chat.id, "message.updated", {"message_id": message_id})
            result = (await self.presenter.messages(db, [row]))[0]
            await db.unit_of_work.commit()
            return result

    async def delete(self, project_id: int, user_id: int, message_id: int, revision: int):
        """Оставляет tombstone; содержимое и ссылки больше не выдаются."""
        async with self.operation() as db:
            chat = await self.require(db, project_id, user_id, lock=True)
            row = await self._message(db, chat.id, message_id)
            if row.author_user_id != user_id:
                raise ChatForbiddenError("Удалить можно только своё сообщение.")
            if row.deleted_at is None:
                if row.revision != revision:
                    raise ChatConflictError("Версия сообщения изменилась.")
                row = await db.messages.update(
                    message_id,
                    dict(content="", deleted_at=datetime.now(UTC), revision=row.revision + 1),
                )
                await self._emit(db, chat.id, "message.deleted", {"message_id": message_id})
            result = (await self.presenter.messages(db, [row]))[0]
            await db.unit_of_work.commit()
            return result

    async def react(
        self, project_id: int, user_id: int, message_id: int, reaction: str, active: bool
    ):
        """Установка/снятие реакции с идемпотентной повторной доставкой."""
        async with self.operation() as db:
            chat = await self.require(db, project_id, user_id, lock=True)
            row = await self._message(db, chat.id, message_id)
            if row.deleted_at:
                raise ChatConflictError("Сообщение удалено.")
            changed = await (
                db.reactions.add(message_id, user_id, reaction)
                if active
                else db.reactions.remove(message_id, user_id, reaction)
            )
            if changed:
                await self._emit(db, chat.id, "reaction.updated", {"message_id": message_id})
            result = (await self.presenter.messages(db, [row]))[0]
            await db.unit_of_work.commit()
            return result

    async def mark_read(self, project_id: int, user_id: int, message_id: int):
        """Продвигает границу только до существующего сообщения того же чата."""
        async with self.operation() as db:
            chat = await self.require(db, project_id, user_id, lock=True)
            row = await self._message(db, chat.id, message_id)
            previous = await db.reads.get(chat.id, user_id)
            seq = previous
            if row.seq > previous:
                seq = await db.reads.advance(chat.id, user_id, row.seq)
                await self._emit(
                    db, chat.id, "read.updated", {"user_id": user_id, "last_read_seq": seq}
                )
            unread = await db.messages.unread_count(chat.id, user_id, seq)
            await db.unit_of_work.commit()
            return {"user_id": user_id, "last_read_seq": seq, "unread_count": unread}

    async def events(
        self, project_id: int, user_id: int, after: int, limit: int = 200
    ) -> ChatEventPage:
        """Восстанавливает все изменения, не раскрывая удалённый текст из outbox."""
        async with self.operation() as db:
            chat = await self.require(db, project_id, user_id)
            rows = await db.events.get_page(chat.id, after, limit=limit + 1)
            more = len(rows) > limit
            rows = rows[:limit]
            return ChatEventPage(
                events=await self.present_events(db, project_id, rows),
                cursor=rows[-1].seq if rows else after,
                has_more=more,
            )

    async def present_events(
        self, db: ChatScope, project_id: int, rows: list
    ) -> list[ChatEventView]:
        """Hydration общая для resync и фонового publisher, без старых снимков текста."""
        if not rows:
            return []
        message_ids = list(
            {row.payload["message_id"] for row in rows if "message_id" in row.payload}
        )
        messages = await db.messages.get_many(rows[0].chat_id, message_ids) if message_ids else []
        views = {
            row.id: row.model_dump(mode="json")
            for row in await self.presenter.messages(db, messages)
        }
        return [
            ChatEventView(
                type=row.event_type,
                project_id=project_id,
                seq=row.seq,
                data={
                    **row.payload,
                    **(
                        {"message": views[row.payload["message_id"]]}
                        if row.payload.get("message_id") in views
                        else {}
                    ),
                },
            )
            for row in rows
        ]

    async def _validate_body(self, db, chat, user_id, data, *, message_id=None):
        members = {row["id"] for row in await db.participants.get_members(chat.project_id)}
        if not set(data.mentions).issubset(members):
            raise ChatValidationError("Упоминать можно только участников проекта.")
        refs = [(ref.entity_type, ref.entity_id) for ref in data.entities]
        if refs:
            found = await db.entity_lookup.find(chat.project_id, refs=refs, limit=len(refs))
            if len(found) != len(refs):
                raise ChatValidationError("Одна из ссылок не принадлежит проекту.")
        if data.attachment_ids:
            files = await db.attachments.get_by_ids(data.attachment_ids)
            if len(files) != len(data.attachment_ids) or any(
                row.chat_id != chat.id
                or row.uploader_id != user_id
                or row.message_id not in (None, message_id)
                for row in files
            ):
                raise ChatValidationError("Вложение недоступно или уже отправлено.")

    @staticmethod
    async def _save_links(db, message_id, data: ChatMessageBody):
        if data.entities:
            await db.entities.save_many(
                [
                    dict(
                        message_id=message_id,
                        entity_type=ref.entity_type,
                        entity_id=ref.entity_id,
                        position=index,
                    )
                    for index, ref in enumerate(data.entities)
                ]
            )
        if data.mentions:
            await db.mentions.save_many(
                [dict(message_id=message_id, user_id=user_id) for user_id in data.mentions]
            )
        if data.attachment_ids:
            await db.attachments.bind(data.attachment_ids, message_id)

    @staticmethod
    async def _message(db, chat_id, message_id):
        row = await db.messages.get(chat_id, message_id)
        if row is None:
            raise ChatNotFoundError("Сообщение отсутствует в этом чате.")
        return row

    async def _own_message(self, db, chat_id, message_id, user_id, revision):
        row = await self._message(db, chat_id, message_id)
        if row.author_user_id != user_id:
            raise ChatForbiddenError("Изменить можно только своё сообщение.")
        if row.deleted_at or row.revision != revision:
            raise ChatConflictError("Сообщение удалено или изменено.")
        return row

    @staticmethod
    async def _emit(db, chat_id, kind, payload):
        seq = await db.chats.next_seq(chat_id)
        await db.events.save(chat_id, seq, kind, payload)
