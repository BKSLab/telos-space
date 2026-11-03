"""Курсорная история и полнотекстовый поиск сообщений."""

from sqlalchemy import func, insert, or_, select, update

from src.db.models.chat_messages import ChatMessage
from src.repositories.chat_base import ChatRepository


class ChatMessagesRepository(ChatRepository):
    """Каждый метод выполняет один statement в транзакции сервиса."""

    async def get(self, chat_id: int, message_id: int):
        """Сообщение всегда ищется в указанном чате."""
        return (
            await self._execute(
                select(ChatMessage).where(
                    ChatMessage.chat_id == chat_id, ChatMessage.id == message_id
                )
            )
        ).scalar_one_or_none()

    async def get_many(
        self, chat_id: int, message_ids: list[int], *, populate_existing: bool = False
    ):
        """Пакетно читает сообщения; по запросу обновляет объекты текущей сессии."""
        return list(
            (
                await self._execute(
                    select(ChatMessage)
                    .where(ChatMessage.chat_id == chat_id, ChatMessage.id.in_(message_ids))
                    .execution_options(populate_existing=populate_existing)
                )
            ).scalars()
        )

    async def get_duplicate(self, chat_id: int, user_id: int, client_message_id):
        """Возвращает результат ранее подтверждённой отправки."""
        return (
            await self._execute(
                select(ChatMessage).where(
                    ChatMessage.chat_id == chat_id,
                    ChatMessage.author_user_id == user_id,
                    ChatMessage.client_message_id == client_message_id,
                )
            )
        ).scalar_one_or_none()

    async def get_page(
        self,
        chat_id: int,
        *,
        before: int | None = None,
        after: int | None = None,
        limit: int = 51,
        query: str | None = None,
    ):
        """Keyset по серверному порядку; поиск использует GIN-индекс."""
        statement = select(ChatMessage).where(ChatMessage.chat_id == chat_id)
        if before is not None:
            statement = statement.where(ChatMessage.seq < before)
        if after is not None:
            statement = statement.where(ChatMessage.seq > after)
        if query:
            statement = statement.where(
                ChatMessage.deleted_at.is_(None),
                or_(
                    ChatMessage.search_vector.op("@@")(func.websearch_to_tsquery("russian", query)),
                    ChatMessage.search_vector.op("@@")(func.websearch_to_tsquery("simple", query)),
                ),
            )
        statement = statement.order_by(
            ChatMessage.seq.asc() if after is not None else ChatMessage.seq.desc()
        ).limit(limit)
        return list((await self._execute(statement)).scalars())

    async def unread_count(self, chat_id: int, user_id: int, after: int) -> int:
        """Считает только неудалённые сообщения других участников."""
        return (
            await self._execute(
                select(func.count())
                .select_from(ChatMessage)
                .where(
                    ChatMessage.chat_id == chat_id,
                    ChatMessage.seq > after,
                    ChatMessage.deleted_at.is_(None),
                    ChatMessage.author_user_id.is_distinct_from(user_id),
                )
            )
        ).scalar_one()

    async def save(self, data: dict):
        """Сохраняет сообщение и получает серверные поля через RETURNING."""
        return (
            await self._execute(insert(ChatMessage).values(**data).returning(ChatMessage))
        ).scalar_one()

    async def update(self, message_id: int, data: dict):
        """Обновляет проверенную сервисом запись под блокировкой чата."""
        return (
            await self._execute(
                update(ChatMessage)
                .where(ChatMessage.id == message_id)
                .values(**data)
                .returning(ChatMessage)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
