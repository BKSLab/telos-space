"""Загрузки и одноразовая привязка файлов к сообщению."""

from sqlalchemy import delete, func, insert, literal, select, update

from src.db.models.chat_attachments import ChatAttachment
from src.db.models.chat_file_texts import ChatFileText
from src.repositories.chat_base import ChatRepository


class ChatAttachmentsRepository(ChatRepository):
    """Права и допустимость изменения определяет сервис."""

    async def get(self, file_id):
        """Находит файл для последующей проверки области доступа."""
        return (
            await self._execute(select(ChatAttachment).where(ChatAttachment.id == file_id))
        ).scalar_one_or_none()

    async def get_many(self, message_ids: list[int]):
        """Пакетно читает вложения страницы."""
        return list(
            (
                await self._execute(
                    select(ChatAttachment)
                    .where(ChatAttachment.message_id.in_(message_ids))
                    .order_by(ChatAttachment.created_at)
                )
            ).scalars()
        )

    async def get_by_ids(self, file_ids: list):
        """Читает набор загрузок под последующую проверку владельца."""
        return list(
            (
                await self._execute(select(ChatAttachment).where(ChatAttachment.id.in_(file_ids)))
            ).scalars()
        )

    async def draft_count(self, chat_id: int, user_id: int) -> int:
        """Ограничивает число незавершённых загрузок участника."""
        return (
            await self._execute(
                select(func.count())
                .select_from(ChatAttachment)
                .where(
                    ChatAttachment.chat_id == chat_id,
                    ChatAttachment.uploader_id == user_id,
                    ChatAttachment.message_id.is_(None),
                )
            )
        ).scalar_one()

    async def save(self, data: dict):
        """Создаёт оригинал и durable очередь подготовки одним statement."""
        saved = (
            insert(ChatAttachment)
            .values(**data)
            .returning(*ChatAttachment.__table__.c)
            .cte("saved_file")
        )
        queued = (
            insert(ChatFileText)
            .from_select(["chat_attachment_id", "status"], select(saved.c.id, literal("pending")))
            .cte("queued_text")
        )
        return (
            await self._execute(
                select(ChatAttachment).from_statement(select(saved).add_cte(queued))
            )
        ).scalar_one()

    async def bind(self, file_ids: list, message_id: int) -> None:
        """Привязывает проверенные загрузки внутри транзакции отправки."""
        await self._execute(
            update(ChatAttachment)
            .where(ChatAttachment.id.in_(file_ids))
            .values(message_id=message_id)
        )

    async def delete(self, file_id) -> None:
        """Удаляет метаданные черновика."""
        await self._execute(delete(ChatAttachment).where(ChatAttachment.id == file_id))

    async def get_expired(self, cutoff, *, limit: int = 100):
        """Выбирает старые неотправленные загрузки для фоновой очистки."""
        return list(
            (
                await self._execute(
                    select(ChatAttachment)
                    .where(ChatAttachment.message_id.is_(None), ChatAttachment.created_at < cutoff)
                    .order_by(ChatAttachment.created_at)
                    .limit(limit)
                )
            ).scalars()
        )

    async def get_existing_keys(self, keys: list[str]) -> set[str]:
        """Проверяет пакет кандидатов файловой уборки одним запросом."""
        return set(
            (
                await self._execute(
                    select(ChatAttachment.storage_key).where(ChatAttachment.storage_key.in_(keys))
                )
            ).scalars()
        )
