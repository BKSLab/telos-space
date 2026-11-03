from typing import Any
from uuid import UUID

from sqlalchemy import Result, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.agent_tool_runs import AgentToolRun
from src.exceptions.agent_tools import AgentToolRunsRepositoryError


class AgentToolRunsRepository:
    """Одна SQL-операция на метод; транзакцией владеет сценарий инструмента."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def lock_request(self, project_id: int | None, user_id: int, request_id: UUID) -> None:
        """Сериализует повтор запроса до commit, в том числе до создания строки."""
        key = f"agent-tool:{project_id or 'personal'}:{user_id}:{request_id}"
        await self._execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))

    async def get_request(
        self, project_id: int | None, user_id: int, request_id: UUID
    ) -> AgentToolRun | None:
        """Читает прежний результат только в области владельца и проекта (или вне его)."""
        return (
            await self._execute(
                select(AgentToolRun).where(
                    AgentToolRun.project_id.is_(None)
                    if project_id is None
                    else AgentToolRun.project_id == project_id,
                    AgentToolRun.user_id == user_id,
                    AgentToolRun.request_id == request_id,
                )
            )
        ).scalar_one_or_none()

    async def get_owned(
        self, run_id: UUID, project_id: int | None, user_id: int
    ) -> AgentToolRun | None:
        """Возвращает действие инициатора для решения с блокировкой строки."""
        return (
            await self._execute(
                select(AgentToolRun)
                .where(
                    AgentToolRun.id == run_id,
                    AgentToolRun.project_id.is_(None)
                    if project_id is None
                    else AgentToolRun.project_id == project_id,
                    AgentToolRun.user_id == user_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def get_for_decision(self, run_id: UUID, user_id: int) -> AgentToolRun | None:
        """Действие инициатора в любом проекте или вне проекта, с блокировкой строки."""
        return (
            await self._execute(
                select(AgentToolRun)
                .where(AgentToolRun.id == run_id, AgentToolRun.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def get_in_chat(
        self, run_id: UUID, project_id: int, *, lock: bool = True
    ) -> AgentToolRun | None:
        """Действие, предложенное Арисом в чате проекта, с блокировкой строки.

        Решение в общем чате принимает не только инициатор, поэтому владелец
        здесь не фильтруется: права решающего проверяет сервис.
        """
        statement = select(AgentToolRun).where(
            AgentToolRun.id == run_id,
            AgentToolRun.project_id == project_id,
            AgentToolRun.chat_turn_id.is_not(None),
        )
        if lock:
            statement = statement.with_for_update()
        return (await self._execute(statement)).scalar_one_or_none()

    async def get_for_turns(self, turn_ids: list[int]) -> list[AgentToolRun]:
        """Действия ходов Ариса в чате проекта по порядку выполнения."""
        return list(
            (
                await self._execute(
                    select(AgentToolRun)
                    .where(AgentToolRun.chat_turn_id.in_(turn_ids))
                    .order_by(AgentToolRun.created_at, AgentToolRun.id)
                )
            ).scalars()
        )

    async def get_for_user(self, run_id: UUID, user_id: int) -> AgentToolRun | None:
        """Читает действие только его инициатора: чужое неотличимо от несуществующего."""
        return (
            await self._execute(
                select(AgentToolRun).where(
                    AgentToolRun.id == run_id, AgentToolRun.user_id == user_id
                )
            )
        ).scalar_one_or_none()

    async def get_for_messages(self, message_ids: list[int]) -> list[AgentToolRun]:
        """Читает действия уже проверенных сервисом реплик по порядку выполнения."""
        return list(
            (
                await self._execute(
                    select(AgentToolRun)
                    .where(AgentToolRun.message_id.in_(message_ids))
                    .order_by(AgentToolRun.created_at, AgentToolRun.id)
                )
            ).scalars()
        )

    async def save(self, data: dict[str, Any]) -> AgentToolRun:
        """Записывает действие в общей транзакции с изменением проекта."""
        return (
            await self._execute(insert(AgentToolRun).values(**data).returning(AgentToolRun))
        ).scalar_one()

    async def reject_pending(self, message_id: int) -> None:
        """Закрывает неподтверждённые предложения при новом вопросе участника."""
        await self._execute(
            update(AgentToolRun)
            .where(
                AgentToolRun.message_id == message_id,
                AgentToolRun.status == "pending",
            )
            .values(
                status="rejected",
                result={
                    "message": "Обсуждение продолжилось новым вопросом; действие не выполнено."
                },
            )
        )

    async def update(self, run_id: UUID, data: dict[str, Any]) -> AgentToolRun:
        """Сохраняет решение или результат выполнения."""
        return (
            await self._execute(
                update(AgentToolRun)
                .where(AgentToolRun.id == run_id)
                .values(**data)
                .returning(AgentToolRun)
            )
        ).scalar_one()

    async def _execute(self, statement: Any) -> Result[Any]:
        try:
            return await self.db_session.execute(statement)
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise AgentToolRunsRepositoryError(
                "Не удалось сохранить или прочитать действие агента."
            ) from error
