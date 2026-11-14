"""Единая scoped-история private/shared чатов и проверка источников ответов."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.exceptions.agent_files import AgentFileNotFoundError
from src.exceptions.base import RepositoryError
from src.exceptions.project_chats import (
    ChatConflictError,
    ChatNotFoundError,
    ChatServiceError,
    ChatValidationError,
)
from src.repositories.agent_chat_history import AgentChatHistoryRepository
from src.schemas.chat_files import ChatFileSchema
from src.schemas.chat_history import (
    ChatHistoryEntityReferenceSchema,
    ChatHistoryFiltersSchema,
    ChatHistoryMessageSchema,
    ChatHistoryPageSchema,
)
from src.schemas.chat_references import ChatMessageReferenceSchema
from src.services.agent_chat_files import AgentChatFilesService, ChatFileContext, ChatFileLocation
from src.utils.document_refs import DOCUMENT_SOURCE_KINDS, decode_document_ref, encode_document_ref


@dataclass(frozen=True, slots=True)
class ChatHistoryScope:
    """Чтение нескольких SQL-проекций без внешнего IO и удержания долгой сессии."""

    history: AgentChatHistoryRepository


ChatHistoryScopeFactory = Callable[[], AbstractAsyncContextManager[ChatHistoryScope]]


class AgentChatHistoryService:
    """Читает ровно текущую переписку до вопроса активного run, без OCR и enqueue."""

    def __init__(self, *, files: AgentChatFilesService, scope: ChatHistoryScopeFactory) -> None:
        self.files = files
        self.scope = scope

    @asynccontextmanager
    async def _scope(self):
        try:
            async with self.scope() as db:
                yield db
        except RepositoryError as error:
            raise ChatServiceError("Не удалось прочитать историю переписки.") from error

    async def _location(
        self, context: ChatFileContext, chat_id: int | None = None
    ) -> ChatFileLocation:
        try:
            location = await self.files.location(context)
        except AgentFileNotFoundError as error:
            raise ChatNotFoundError("Текущая переписка недоступна.") from error
        if chat_id is not None and chat_id != location.scope_id:
            raise ChatNotFoundError("Другая переписка недоступна в этом контексте.")
        return location

    async def _recheck(self, context: ChatFileContext, location: ChatFileLocation) -> None:
        if await self._location(context) != location:
            raise ChatNotFoundError("Контекст переписки изменился во время чтения.")

    @staticmethod
    def _timezone(context: ChatFileContext) -> ZoneInfo:
        try:
            return ZoneInfo(context.timezone)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ChatValidationError("Неизвестный часовой пояс переписки.") from error

    @classmethod
    def _period(
        cls, context: ChatFileContext, date_from: date | None, date_to: date | None
    ) -> tuple[datetime | None, datetime | None]:
        zone = cls._timezone(context)
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ChatValidationError("Начало периода должно быть не позже его конца.")
        try:
            start = (
                datetime.combine(date_from, time.min, tzinfo=zone).astimezone(UTC)
                if date_from
                else None
            )
            end = (
                datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
                if date_to
                else None
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise ChatValidationError("Проверьте календарные даты периода.") from error
        return start, end

    @staticmethod
    async def _get(db: ChatHistoryScope, location: ChatFileLocation, message_id: int) -> dict:
        row = await db.history.get_message(**location.query(), message_id=message_id)
        if row is None:
            raise ChatNotFoundError("Сообщение недоступно в текущей истории.")
        return row

    @staticmethod
    def _reference(
        row: dict,
        location: ChatFileLocation,
        *,
        quote: str | None = None,
        offset: int = 0,
        max_chars: int = 1000,
    ) -> ChatMessageReferenceSchema:
        raw = row["content"] or ""
        if quote is not None:
            if not quote or len(quote) > 2000:
                raise ChatValidationError("Цитата должна содержать от 1 до 2000 символов.")
            start = raw.find(quote)
            if start < 0:
                raise ChatValidationError("Цитата не совпадает с исходным текстом сообщения.")
        else:
            start = min(offset, len(raw))
            quote = raw[start : start + min(max_chars, 1000)]
        return ChatMessageReferenceSchema(
            kind=location.kind,
            chat_id=location.scope_id,
            message_id=row["id"],
            project_id=location.project_id,
            project_key=location.project_key,
            author_id=row["author_id"],
            author_name=row["author_name"],
            created_at=row["created_at"],
            revision=row["revision"],
            content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            quote=quote,
            start=start,
            end=start + len(quote),
        )

    @classmethod
    def _entity_hint(cls, source: dict) -> ChatHistoryEntityReferenceSchema | None:
        """Whitelist сохраняет identity, а старые nonce/цитаты/fullcard остаются вне DTO."""
        source_id = source.get("source_id")
        parsed = (
            re.fullmatch(r"([a-z][a-z_]{0,31}):([1-9][0-9]{0,18})", source_id)
            if isinstance(source_id, str)
            else None
        )
        entity_type = source.get("entity_type")
        entity_type = (
            entity_type.lower() if isinstance(entity_type, str) else parsed[1] if parsed else ""
        )
        if re.fullmatch(r"[a-z][a-z_]{0,31}", entity_type) is None:
            return None

        def identifier(value):
            return value if type(value) is int and 0 < value <= 2**63 - 1 else None

        def short(value, limit):
            return value.strip()[:limit] or None if isinstance(value, str) else None

        entity_id = identifier(source.get("entity_id"))
        if entity_id is None and parsed and parsed[1] == entity_type:
            entity_id = identifier(int(parsed[2]))
        document_ref = source.get("document_ref")
        if isinstance(document_ref, str):
            try:
                entity_type, entity_id = decode_document_ref(document_ref)
            except ValueError:
                return None
        elif entity_type in DOCUMENT_SOURCE_KINDS and entity_id is not None:
            try:
                document_ref = encode_document_ref(entity_type, entity_id)
            except ValueError:
                return None
        else:
            document_ref = None
        if entity_id is None:
            return None
        card = source.get("card")
        card = card if isinstance(card, dict) else {}
        return ChatHistoryEntityReferenceSchema(
            entity_type=entity_type,
            entity_id=None if document_ref else entity_id,
            document_ref=document_ref,
            source_id=f"{entity_type}:{entity_id}",
            task_id=identifier(source.get("task_id")) or identifier(card.get("task_id")),
            key=short(source.get("key"), 64)
            or short(card.get("key"), 64)
            or short(source.get("task_key"), 64),
            title=short(source.get("title"), 160),
        )

    @classmethod
    def _entity_hints(cls, sources) -> list[ChatHistoryEntityReferenceSchema]:
        result = []
        seen = set()
        for source in sources[:20] if isinstance(sources, list) else []:
            hint = cls._entity_hint(source) if isinstance(source, dict) else None
            if hint is not None and hint.source_id not in seen:
                result.append(hint)
                seen.add(hint.source_id)
        return result

    @classmethod
    def _message(
        cls,
        row: dict,
        location: ChatFileLocation,
        *,
        offset: int = 0,
        max_chars: int = 2000,
        quote: str | None = None,
    ) -> ChatHistoryMessageSchema:
        raw = row["content"] or ""
        offset = min(offset, len(raw))
        content = raw[offset : offset + max_chars]
        end = offset + len(content)
        return ChatHistoryMessageSchema(
            id=row["id"],
            chat_id=location.scope_id,
            kind=location.kind,
            project_id=location.project_id,
            project_key=location.project_key,
            author_id=row["author_id"],
            author_name=row["author_name"],
            author_type=row["author_type"],
            created_at=row["created_at"],
            revision=row["revision"],
            content=content,
            text_truncated=offset > 0 or end < len(raw),
            content_offset=offset,
            content_total_chars=len(raw),
            next_content_offset=end if end < len(raw) else None,
            reply_to_message_id=row["reply_to_message_id"],
            entity_references=cls._entity_hints(row.get("entity_sources"))
            if row["author_type"] == "AGENT"
            else [],
            reference=cls._reference(
                row, location, quote=quote, offset=offset, max_chars=max_chars
            ),
        )

    @staticmethod
    def _entity_links(messages: list[ChatHistoryMessageSchema], rows: list[dict]) -> None:
        by_id = {message.id: message for message in messages}
        for row in rows:
            message = by_id.get(row["message_id"])
            if message is None or len(message.entity_references) >= 20:
                continue
            hint = AgentChatHistoryService._entity_hint(row)
            if hint is not None and hint.source_id not in {
                reference.source_id for reference in message.entity_references
            }:
                message.entity_references.append(hint)

    @staticmethod
    def _attachments(
        messages: list[ChatHistoryMessageSchema], rows: list[dict], priority: list[int]
    ) -> None:
        """Метаданные не теряются при исчерпании общего бюджета текстовых префиксов."""
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["message_id"]].append(row)
        by_id = {message.id: message for message in messages}
        budget = 6000
        for message_id in dict.fromkeys(priority):
            message = by_id[message_id]
            for row in grouped[message_id]:
                excerpt = row.get("excerpt") or ""
                excerpt = excerpt[: min(2000, budget)] if row["status"] == "ready" else ""
                budget -= len(excerpt)
                message.attachments.append(ChatFileSchema(**{**row, "excerpt": excerpt or None}))

    async def initial_context(self, *, context: ChatFileContext) -> dict:
        """Текущий вопрос отдельно; ровно 15 предыдущих подходящих сообщений при наличии."""
        location = await self._location(context)
        zone = self._timezone(context)
        async with self._scope() as db:
            current = await self._get(db, location, location.question_id)
            recent = await db.history.list_messages(
                **location.query(), before_order=location.upper_bound, limit=15
            )
            recent.sort(key=lambda row: row["ordinal"])
            quote_row = None
            reply_to = current["reply_to_message_id"]
            if reply_to is not None and reply_to not in {row["id"] for row in recent}:
                quote_row = await db.history.get_message(**location.query(), message_id=reply_to)
            rows = [current, *recent, *([quote_row] if quote_row else [])]
            attachments = await db.history.attachments(
                **location.query(), message_ids=[row["id"] for row in rows]
            )
            entity_links = (
                await db.history.entity_links(
                    scope_id=location.scope_id,
                    upper_bound=location.upper_bound,
                    message_ids=[row["id"] for row in rows],
                )
                if location.kind == "project_chat"
                else []
            )
        current_message = self._message(current, location)
        messages = [self._message(row, location) for row in recent]
        quoted_message = self._message(quote_row, location) if quote_row else None
        all_messages = [current_message, *messages, *([quoted_message] if quoted_message else [])]
        priority = [
            current_message.id,
            *([quoted_message.id] if quoted_message else []),
            *[message.id for message in reversed(messages)],
        ]
        self._attachments(all_messages, attachments, priority)
        self._entity_links(all_messages, entity_links)
        await self._recheck(context, location)
        return {
            "current_message": current_message.model_dump(mode="json"),
            "messages": [message.model_dump(mode="json") for message in messages],
            "quoted_message": quoted_message.model_dump(mode="json") if quoted_message else None,
            "chat_id": location.scope_id,
            "kind": location.kind,
            "timezone": context.timezone,
            "current_datetime": datetime.now(zone).isoformat(timespec="seconds"),
        }

    async def list_messages(
        self,
        *,
        context: ChatFileContext,
        chat_id: int | None = None,
        limit: int = 15,
        before_message_id: int | None = None,
        after_message_id: int | None = None,
        query: str | None = None,
        author_user_id: int | Literal["me"] | None = None,
        author_type: Literal["USER", "AGENT"] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> ChatHistoryPageSchema:
        """Курсоры, FTS, автор и локальные календарные дни применяются до LIMIT."""
        if not 1 <= limit <= 50 or (before_message_id is not None and after_message_id is not None):
            raise ChatValidationError("limit от 1 до 50; выберите только before или after.")
        query = query.strip() if query else None
        if query and len(query) > 200:
            raise ChatValidationError("Поисковая строка должна быть не длиннее 200 символов.")
        if author_user_id == "me":
            author_user_id = context.user_id
        if author_user_id is not None and (type(author_user_id) is not int or author_user_id <= 0):
            raise ChatValidationError("author_user_id должен быть положительным ID либо me.")
        if author_type not in (None, "USER", "AGENT"):
            raise ChatValidationError("author_type должен быть USER либо AGENT.")
        start, end = self._period(context, date_from, date_to)
        location = await self._location(context, chat_id)
        async with self._scope() as db:
            before_order = (
                (await self._get(db, location, before_message_id))["ordinal"]
                if before_message_id is not None
                else None
            )
            after_order = (
                (await self._get(db, location, after_message_id))["ordinal"]
                if after_message_id is not None
                else None
            )
            rows = await db.history.list_messages(
                **location.query(),
                limit=limit + 1,
                before_order=before_order,
                after_order=after_order,
                query=query,
                author_user_id=author_user_id,
                author_type=author_type,
                start_at=start,
                end_before=end,
            )
            has_more = len(rows) > limit
            rows = sorted(rows[:limit], key=lambda row: row["ordinal"])
            attachments = await db.history.attachments(
                **location.query(), message_ids=[row["id"] for row in rows]
            )
            entity_links = (
                await db.history.entity_links(
                    scope_id=location.scope_id,
                    upper_bound=location.upper_bound,
                    message_ids=[row["id"] for row in rows],
                )
                if location.kind == "project_chat"
                else []
            )
        items = [self._message(row, location) for row in rows]
        self._attachments(items, attachments, [message.id for message in reversed(items)])
        self._entity_links(items, entity_links)
        await self._recheck(context, location)
        return ChatHistoryPageSchema(
            items=items,
            has_more=has_more,
            next_before_message_id=items[0].id if has_more and after_message_id is None else None,
            next_after_message_id=items[-1].id
            if has_more and after_message_id is not None
            else None,
            applied_filters=ChatHistoryFiltersSchema(
                chat_id=location.scope_id,
                kind=location.kind,
                query=query,
                author_user_id=author_user_id,
                author_type=author_type,
                date_from=date_from,
                date_to=date_to,
                timezone=context.timezone,
                before_message_id=before_message_id,
                after_message_id=after_message_id,
            ),
        )

    async def get_message(
        self,
        *,
        context: ChatFileContext,
        message_id: int,
        chat_id: int | None = None,
        before_count: int = 0,
        after_count: int = 0,
        quote: str | None = None,
        offset: int = 0,
        max_chars: int = 8000,
    ) -> dict:
        """Дочитывает длинный ответ и до пяти соседей с каждой стороны, без будущих сообщений."""
        if (
            not 0 <= before_count <= 5
            or not 0 <= after_count <= 5
            or offset < 0
            or not 1 <= max_chars <= 8000
        ):
            raise ChatValidationError("Соседи от 0 до 5; offset >= 0; max_chars от 1 до 8000.")
        location = await self._location(context, chat_id)
        async with self._scope() as db:
            row = await self._get(db, location, message_id)
            before = (
                await db.history.list_messages(
                    **location.query(), limit=before_count, before_order=row["ordinal"]
                )
                if before_count
                else []
            )
            after = (
                await db.history.list_messages(
                    **location.query(), limit=after_count, after_order=row["ordinal"]
                )
                if after_count
                else []
            )
            neighboring = sorted([*before, *after], key=lambda item: item["ordinal"])
            attachments = await db.history.attachments(
                **location.query(), message_ids=[row["id"], *[item["id"] for item in neighboring]]
            )
            entity_links = (
                await db.history.entity_links(
                    scope_id=location.scope_id,
                    upper_bound=location.upper_bound,
                    message_ids=[row["id"], *[item["id"] for item in neighboring]],
                )
                if location.kind == "project_chat"
                else []
            )
        message = self._message(row, location, offset=offset, max_chars=max_chars, quote=quote)
        neighbors = [self._message(item, location) for item in neighboring]
        self._attachments(
            [message, *neighbors],
            attachments,
            [message.id, *[item.id for item in reversed(neighbors)]],
        )
        self._entity_links([message, *neighbors], entity_links)
        await self._recheck(context, location)
        return {
            "message": message.model_dump(mode="json"),
            "neighbors": [item.model_dump(mode="json") for item in neighbors],
        }

    async def verify_reference(
        self, *, context: ChatFileContext, reference: ChatMessageReferenceSchema
    ) -> ChatMessageReferenceSchema:
        """Повторно читает оригинал; произвольные автор/project metadata модели не доверяются."""
        location = await self._location(context, reference.chat_id)
        if reference.kind != location.kind or reference.project_id != location.project_id:
            raise ChatNotFoundError("Ссылка принадлежит другой переписке или проекту.")
        async with self._scope() as db:
            row = await self._get(db, location, reference.message_id)
        raw = row["content"] or ""
        if (
            reference.state != "available"
            or reference.revision != row["revision"]
            or reference.content_hash != hashlib.sha256(raw.encode("utf-8")).hexdigest()
            or reference.end < reference.start
            or reference.end > len(raw)
            or raw[reference.start : reference.end] != reference.quote
            or reference.end - reference.start != len(reference.quote)
        ):
            raise ChatConflictError("Исходное сообщение или цитата изменились после чтения.")
        verified = self._reference(row, location, offset=reference.start, max_chars=0)
        verified = verified.model_copy(
            update={"quote": reference.quote, "start": reference.start, "end": reference.end}
        )
        await self._recheck(context, location)
        return verified
