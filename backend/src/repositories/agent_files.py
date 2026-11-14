from typing import Any
from uuid import UUID

from sqlalchemy import Result, delete, func, insert, literal, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.agent_files import AgentFile
from src.db.models.chat_file_texts import ChatFileText
from src.exceptions.agent_files import AgentFilesRepositoryError


class AgentFilesRepository:
    """Метаданные файлов; физическим хранилищем управляет сценарий."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get(self, file_id: UUID) -> AgentFile | None:
        """Читает загрузку; сервис обязан проверить её диалог."""
        return (
            await self._execute(select(AgentFile).where(AgentFile.id == file_id))
        ).scalar_one_or_none()

    async def count(self, conversation_id: int) -> int:
        """Возвращает число загрузок диалога."""
        return (
            await self._execute(
                select(func.count())
                .select_from(AgentFile)
                .where(AgentFile.conversation_id == conversation_id)
            )
        ).scalar_one()

    async def save(self, data: dict) -> AgentFile:
        """Атомарно сохраняет оригинал и очередь подготовки, без скрытого commit."""
        saved = insert(AgentFile).values(**data).returning(*AgentFile.__table__.c).cte("saved_file")
        queued = (
            insert(ChatFileText)
            .from_select(["agent_file_id", "status"], select(saved.c.id, literal("pending")))
            .cte("queued_text")
        )
        return (
            await self._execute(select(AgentFile).from_statement(select(saved).add_cte(queued)))
        ).scalar_one()

    async def bind_message(self, file_id: UUID, message_id: int) -> None:
        """Защищает отправленный файл от удаления как черновика."""
        await self._execute(
            update(AgentFile)
            .where(AgentFile.id == file_id, AgentFile.message_id.is_(None))
            .values(message_id=message_id)
        )

    async def delete(self, file_id: UUID) -> None:
        """Удаляет метаданные проверенного черновика."""
        await self._execute(delete(AgentFile).where(AgentFile.id == file_id))

    async def _execute(self, statement: Any) -> Result[Any]:
        try:
            return await self.db_session.execute(statement)
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise AgentFilesRepositoryError("Ошибка доступа к загрузкам диалога.") from error
