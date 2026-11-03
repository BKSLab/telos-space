from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Result, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.agent_conversations import AgentConversation
from src.db.models.agent_messages import AgentMessage
from src.exceptions.agent_conversations import AgentMessagesRepositoryError


class AgentMessagesRepository:
    """Реплики и очередь ответов; каждый метод выполняет один statement."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_id(self, message_id: int) -> AgentMessage | None:
        """Читает реплику без блокировки: нужен только её диалог."""
        return (
            await self._execute(select(AgentMessage).where(AgentMessage.id == message_id))
        ).scalar_one_or_none()

    async def get_for_update(self, message_id: int) -> AgentMessage | None:
        """Блокирует реплику на короткое время действия или решения участника."""
        return (
            await self._execute(
                select(AgentMessage).where(AgentMessage.id == message_id).with_for_update()
            )
        ).scalar_one_or_none()

    async def resume(self, message_id: int) -> None:
        """Возвращает завершённый ответ в очередь после решения по его действиям."""
        await self._execute(
            update(AgentMessage)
            .where(
                AgentMessage.id == message_id,
                AgentMessage.role == "assistant",
                AgentMessage.status.in_(("completed", "failed")),
            )
            .values(status="queued", error=None, run_id=None, started_at=None, completed_at=None)
        )

    async def get_page(
        self, conversation_id: int, *, before_id: int | None, limit: int
    ) -> list[AgentMessage]:
        """Читает реплики от новых к старым.

        Args:
            conversation_id: Проверенный сервисом диалог.
            before_id: Исключительная верхняя граница страницы.
            limit: Максимум реплик.
        Returns:
            Страница в обратном хронологическом порядке.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        statement = select(AgentMessage).where(AgentMessage.conversation_id == conversation_id)
        if before_id is not None:
            statement = statement.where(AgentMessage.id < before_id)
        return list(
            (await self._execute(statement.order_by(AgentMessage.id.desc()).limit(limit))).scalars()
        )

    async def get_request(self, conversation_id: int, request_id: UUID) -> list[AgentMessage]:
        """Возвращает обе реплики одной отправки для идемпотентного повтора.

        Args:
            conversation_id: Проверенный диалог.
            request_id: Ключ отправки.
        Returns:
            Вопрос и ответ, если отправка уже сохранена.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        return list(
            (
                await self._execute(
                    select(AgentMessage)
                    .where(
                        AgentMessage.conversation_id == conversation_id,
                        AgentMessage.request_id == request_id,
                    )
                    .order_by(AgentMessage.id)
                )
            ).scalars()
        )

    async def get_answer_by_request(
        self, request_id: UUID, *, user_id: int | None = None, api_key_id: UUID | None = None
    ) -> AgentMessage | None:
        """Результат в области локального владельца и предъявленного ключа."""
        statement = select(AgentMessage).where(
            AgentMessage.request_id == request_id, AgentMessage.role == "assistant"
        )
        if user_id is not None:
            statement = statement.join(AgentConversation).where(
                AgentConversation.user_id == user_id,
                AgentConversation.channel == "api",
                AgentConversation.api_key_id.is_(None) if api_key_id is None
                else AgentConversation.api_key_id == api_key_id,
            )
        return (await self._execute(statement.order_by(AgentMessage.id).limit(1))).scalar_one_or_none()

    async def get_active(self, conversation_id: int) -> AgentMessage | None:
        """Находит незавершённый ответ диалога.

        Args:
            conversation_id: Проверенный диалог.
        Returns:
            Ожидающий ответ либо None.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        return (
            await self._execute(
                select(AgentMessage).where(
                    AgentMessage.conversation_id == conversation_id,
                    AgentMessage.status.in_(("queued", "processing")),
                )
            )
        ).scalar_one_or_none()

    async def save_pair(self, data: list[dict[str, Any]]) -> list[AgentMessage]:
        """Записывает вопрос и место ответа одним INSERT без commit.

        Args:
            data: Две реплики, подготовленные сервисом.
        Returns:
            Сохранённые реплики с серверными ID.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        return list(
            (
                await self._execute(insert(AgentMessage).values(data).returning(AgentMessage))
            ).scalars()
        )

    async def get_history(
        self, conversation_id: int, *, before_id: int, limit: int
    ) -> list[AgentMessage]:
        """Читает недавние завершённые реплики для модели.

        Args:
            conversation_id: Диалог задания.
            before_id: ID текущего вопроса, не входящего в историю.
            limit: Бюджет недавних реплик.
        Returns:
            Реплики от новых к старым.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        return list(
            (
                await self._execute(
                    select(AgentMessage)
                    .where(
                        AgentMessage.conversation_id == conversation_id,
                        AgentMessage.id < before_id,
                        AgentMessage.status == "completed",
                    )
                    .order_by(AgentMessage.id.desc())
                    .limit(limit)
                )
            ).scalars()
        )

    async def get_for_summary(
        self, conversation_id: int, *, after_id: int, before_id: int, limit: int
    ) -> list[AgentMessage]:
        """Читает следующую порцию ранней истории без повторного пересказа.

        Args:
            conversation_id: Диалог задания.
            after_id: Последняя уже пересказанная реплика.
            before_id: Начало недавней истории.
            limit: Размер порции.
        Returns:
            Завершённые реплики в хронологическом порядке.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        return list(
            (
                await self._execute(
                    select(AgentMessage)
                    .where(
                        AgentMessage.conversation_id == conversation_id,
                        AgentMessage.id > after_id,
                        AgentMessage.id < before_id,
                        AgentMessage.status == "completed",
                    )
                    .order_by(AgentMessage.id)
                    .limit(limit)
                )
            ).scalars()
        )

    async def claim_next(self, run_id: UUID) -> AgentMessage | None:
        """Атомарно захватывает один ожидающий ответ через SKIP LOCKED.

        Args:
            run_id: Уникальный идентификатор попытки worker-а.
        Returns:
            Захваченный ответ либо None при пустой очереди.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        candidate = (
            select(AgentMessage.id)
            .where(AgentMessage.status == "queued")
            .order_by(AgentMessage.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        message = (
            await self._execute(
                update(AgentMessage)
                .where(AgentMessage.id == candidate)
                .values(
                    status="processing",
                    run_id=run_id,
                    started_at=func.now(),
                    error=None,
                    progress=None,
                )
                .returning(AgentMessage)
            )
        ).scalar_one_or_none()

        return message

    async def finish(self, message_id: int, run_id: UUID, data: dict[str, Any]) -> bool:
        """Завершает только свою ещё активную попытку.

        Args:
            message_id: Ответ задания.
            run_id: Владелец попытки.
            data: Результат либо безопасное описание ошибки.
        Returns:
            True, если результат сохранён этой попыткой.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        result = await self._execute(
            update(AgentMessage)
            .where(
                AgentMessage.id == message_id,
                AgentMessage.run_id == run_id,
                AgentMessage.status == "processing",
            )
            .values(**data, progress=None, completed_at=func.now())
            .returning(AgentMessage.request_id)
        )
        request_id = result.scalar_one_or_none()
        return request_id is not None

    async def set_progress(self, message_id: int, run_id: UUID, progress: str) -> bool:
        """Пишет живой статус только своей ещё активной попытки.

        Завершённый ответ статус не получает: поздняя запись после ``finish``
        ничего не меняет.

        Args:
            message_id: Ответ задания.
            run_id: Владелец попытки.
            progress: Что Арис делает сейчас.
        Returns:
            True, если статус записан.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        result = await self._execute(
            update(AgentMessage)
            .where(
                AgentMessage.id == message_id,
                AgentMessage.run_id == run_id,
                AgentMessage.status == "processing",
            )
            .values(progress=progress)
        )
        return result.rowcount > 0

    async def fail_expired(self, timeout_seconds: float) -> None:
        """Завершает попытки, прерванные аварийной остановкой процесса.

        Args:
            timeout_seconds: Верхняя граница длительности попытки.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        await self._execute(
            update(AgentMessage)
            .where(
                AgentMessage.status == "processing",
                AgentMessage.started_at < func.now() - timedelta(seconds=timeout_seconds),
            )
            .values(
                status="failed",
                error="Подготовка ответа прервалась. Можно повторить запрос.",
                progress=None,
                completed_at=func.now(),
            )
            .returning(AgentMessage.request_id)
        )
    async def retry(self, message_id: int) -> AgentMessage:
        """Возвращает проверенный сервисом неудачный ответ в очередь.

        Args:
            message_id: ID последнего неудачного ответа диалога.
        Returns:
            Состояние той же реплики после постановки в очередь.
        Raises:
            AgentMessagesRepositoryError: При ошибке PostgreSQL.
        """
        return (
            await self._execute(
                update(AgentMessage)
                .where(AgentMessage.id == message_id, AgentMessage.status == "failed")
                .values(
                    status="queued",
                    error=None,
                    run_id=None,
                    started_at=None,
                    completed_at=None,
                )
                .returning(AgentMessage)
            )
        ).scalar_one()

    async def _execute(self, statement: Any) -> Result[Any]:
        try:
            return await self.db_session.execute(statement)
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise AgentMessagesRepositoryError(
                "Не удалось выполнить запрос к репликам агента."
            ) from error
