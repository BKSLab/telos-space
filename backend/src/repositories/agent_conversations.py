import uuid
from typing import Any

from sqlalchemy import Result, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.agent_conversations import AgentConversation
from src.exceptions.agent_conversations import AgentConversationsRepositoryError


class AgentConversationsRepository:
    """Хранение диалогов; транзакцией владеет сервис."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def lock_external(self, user_id: int, api_key_id: uuid.UUID | None, key: str) -> None:
        """Сериализует создание внешнего диалога или запроса до появления строки."""
        value = f"agent-request:{user_id}:{api_key_id}:{key}"
        await self._execute(select(func.pg_advisory_xact_lock(func.hashtextextended(value, 0))))

    async def get_page(self, project_or_user_id: int, user_id: int | None = None, *, offset: int, limit: int, project_id: int | None = None) -> list[AgentConversation]:
        """Возвращает страницу диалогов пользователя с Арисом в виджете.

        Беседы внешнего клиента (канал ``api``) в список виджета не входят.

        Args:
            user_id: Владелец переписки.
            offset: Смещение страницы.
            limit: Максимум строк.
        Returns:
            Диалоги от недавних к старым.
        Raises:
            AgentConversationsRepositoryError: При ошибке PostgreSQL.
        """
        if user_id is None:
            user_id = project_or_user_id
        else:
            project_id = project_or_user_id
        result = await self._execute(
            select(AgentConversation)
            .where(AgentConversation.user_id == user_id, AgentConversation.channel == "web",
                   AgentConversation.project_id.is_(None) if project_id is None
                   else AgentConversation.project_id == project_id)
            .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars())

    async def get_owned(
        self,
        conversation_id: int,
        project_or_user_id: int,
        user_id: int | None = None,
        *,
        project_id: int | None = None,
        channel: str | None = "web",
        lock: bool = False,
    ) -> AgentConversation | None:
        """Находит диалог только у его владельца и только в своём канале.

        Args:
            conversation_id: ID диалога.
            user_id: Пользователь из серверного контекста.
            channel: ``web`` — виджет Ариса; ``api`` — беседа внешнего клиента;
                None — любой канал (решение по действию и попытка ответа worker-а).
                Маршруты виджета беседу SD не находят.
            lock: Сериализовать изменение диалога до конца транзакции.
        Returns:
            Доступный диалог либо None.
        Raises:
            AgentConversationsRepositoryError: При ошибке PostgreSQL.
        """
        fixed_project = user_id is not None or project_id is not None
        if user_id is None:
            user_id = project_or_user_id
        else:
            project_id = project_or_user_id
        statement = select(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.user_id == user_id,
        )
        if fixed_project:
            statement = statement.where(AgentConversation.project_id == project_id)
        if channel is not None:
            statement = statement.where(AgentConversation.channel == channel)
        if lock:
            statement = statement.with_for_update()
        return (await self._execute(statement)).scalar_one_or_none()

    async def get_external(
        self,
        *,
        user_id: int,
        api_key_id: uuid.UUID | None,
        external_key: str,
        lock: bool = False,
    ) -> AgentConversation | None:
        """Находит беседу внешнего канала по её ключу, владельцу и ключу доступа.

        Args:
            user_id: Сотрудник, от имени которого идёт беседа.
            api_key_id: Сервисный ключ; None — вход через локальная сессия или личный токен.
            external_key: Ключ беседы вызывающей системы, например sd:chat:123.
            lock: Сериализовать постановку вопросов в одну беседу.
        Returns:
            Беседа либо None.
        Raises:
            AgentConversationsRepositoryError: При ошибке PostgreSQL.
        """
        statement = select(AgentConversation).where(
            AgentConversation.user_id == user_id,
            AgentConversation.api_key_id.is_(None)
            if api_key_id is None
            else AgentConversation.api_key_id == api_key_id,
            AgentConversation.external_key == external_key,
            AgentConversation.channel == "api",
        )
        if lock:
            statement = statement.with_for_update()
        return (await self._execute(statement)).scalar_one_or_none()

    async def get_by_id(self, conversation_id: int) -> AgentConversation | None:
        """Читает диалог задания для worker-а, который затем проверяет владельца.

        Args:
            conversation_id: ID из сохранённого задания.
        Returns:
            Диалог либо None после каскадного удаления.
        Raises:
            AgentConversationsRepositoryError: При ошибке PostgreSQL.
        """
        return (
            await self._execute(
                select(AgentConversation).where(AgentConversation.id == conversation_id)
            )
        ).scalar_one_or_none()

    async def save(self, data: dict[str, Any]) -> AgentConversation:
        """Сохраняет диалог в текущей транзакции.

        Args:
            data: Поля нового диалога.
        Returns:
            Запись с серверными значениями.
        Raises:
            AgentConversationsRepositoryError: При ошибке PostgreSQL.
        """
        return (
            await self._execute(
                insert(AgentConversation).values(**data).returning(AgentConversation)
            )
        ).scalar_one()

    async def update(self, conversation_id: int, data: dict[str, Any]) -> None:
        """Обновляет название или память диалога без промежуточного commit.

        Args:
            conversation_id: ID изменяемого диалога.
            data: Проверенные сервисом поля.
        Raises:
            AgentConversationsRepositoryError: При ошибке PostgreSQL.
        """
        await self._execute(
            update(AgentConversation).where(AgentConversation.id == conversation_id).values(**data)
        )

    async def save_summary(
        self, conversation_id: int, *, expected_through_id: int, summary: str, through_id: int
    ) -> bool:
        """Продвигает память диалога, только если её никто не продвинул раньше.

        Args:
            conversation_id: ID диалога.
            expected_through_id: С какой границы начинался пересказ.
            summary: Новая сводка.
            through_id: Последняя пересказанная реплика.
        Returns:
            True, если сводка записана этим вызовом.
        Raises:
            AgentConversationsRepositoryError: При ошибке PostgreSQL.
        """
        result = await self._execute(
            update(AgentConversation)
            .where(
                AgentConversation.id == conversation_id,
                AgentConversation.summary_through_id == expected_through_id,
            )
            .values(summary=summary, summary_through_id=through_id)
            .returning(AgentConversation.id)
        )
        return result.scalar_one_or_none() is not None

    async def _execute(self, statement: Any) -> Result[Any]:
        try:
            return await self.db_session.execute(statement)
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise AgentConversationsRepositoryError(
                "Не удалось выполнить запрос к диалогам."
            ) from error
