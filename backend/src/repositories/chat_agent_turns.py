"""Постоянная очередь ответов Ариса в чатах проектов."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, text, update

from src.db.models.chat_agent_turns import ChatAgentTurn
from src.repositories.chat_base import ChatRepository


class ChatAgentTurnsRepository(ChatRepository):
    """Каждый метод — один statement в транзакции сервиса."""

    async def save(self, data: dict[str, Any]) -> ChatAgentTurn:
        """Сохраняет ход вместе с вопросом и ответом-заглушкой."""
        return (
            await self._execute(insert(ChatAgentTurn).values(**data).returning(ChatAgentTurn))
        ).scalar_one()

    async def get(self, turn_id: int, *, lock: bool = False) -> ChatAgentTurn | None:
        """Читает ход; lock сериализует решение по действию с воркером."""
        statement = select(ChatAgentTurn).where(ChatAgentTurn.id == turn_id)
        if lock:
            statement = statement.with_for_update()
        return (await self._execute(statement)).scalar_one_or_none()

    async def get_by_replies(self, message_ids: list[int]) -> list[ChatAgentTurn]:
        """Пакетно читает ходы по сообщениям Ариса для витрины чата."""
        return list(
            (
                await self._execute(
                    select(ChatAgentTurn).where(ChatAgentTurn.reply_message_id.in_(message_ids))
                )
            ).scalars()
        )

    async def claim_next(self, run_id: UUID) -> ChatAgentTurn | None:
        """Атомарно захватывает один ожидающий ход через SKIP LOCKED."""
        candidate = (
            select(ChatAgentTurn.id)
            .where(ChatAgentTurn.status == "queued")
            .order_by(ChatAgentTurn.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        return (
            await self._execute(
                update(ChatAgentTurn)
                .where(ChatAgentTurn.id == candidate)
                .values(
                    status="processing",
                    run_id=run_id,
                    started_at=func.now(),
                    error=None,
                    progress=None,
                )
                .returning(ChatAgentTurn)
            )
        ).scalar_one_or_none()

    async def claim_expired(self, timeout_seconds: float) -> ChatAgentTurn | None:
        """Забирает одну попытку, прерванную аварийной остановкой процесса."""
        candidate = (
            select(ChatAgentTurn.id)
            .where(
                ChatAgentTurn.status == "processing",
                ChatAgentTurn.started_at < func.now() - timedelta(seconds=timeout_seconds),
            )
            .order_by(ChatAgentTurn.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        return (
            await self._execute(
                update(ChatAgentTurn)
                .where(ChatAgentTurn.id == candidate)
                .values(
                    status="failed",
                    error="Подготовка ответа прервалась.",
                    progress=None,
                    completed_at=func.now(),
                )
                .returning(ChatAgentTurn)
            )
        ).scalar_one_or_none()

    async def finish(self, turn_id: int, run_id: UUID, data: dict[str, Any]) -> bool:
        """Завершает только свою ещё активную попытку."""
        result = await self._execute(
            update(ChatAgentTurn)
            .where(
                ChatAgentTurn.id == turn_id,
                ChatAgentTurn.run_id == run_id,
                ChatAgentTurn.status == "processing",
            )
            .values(**data, progress=None, completed_at=func.now())
        )
        return result.rowcount > 0

    async def set_progress(self, turn_id: int, run_id: UUID, progress: str) -> ChatAgentTurn | None:
        """Пишет живой статус только своей ещё активной попытки.

        Returns:
            Ход, если статус записан; None — попытка уже завершена или чужая.
        """
        return (
            await self._execute(
                update(ChatAgentTurn)
                .where(
                    ChatAgentTurn.id == turn_id,
                    ChatAgentTurn.run_id == run_id,
                    ChatAgentTurn.status == "processing",
                )
                .values(progress=progress)
                .returning(ChatAgentTurn)
            )
        ).scalar_one_or_none()

    async def emit(self, project_id: int, kind: str, message_id: int) -> None:
        """Пишет событие чата той же функцией, что и триггеры, — без блокировки строки чата
        в сервисе, который работает не через ChatScope (решение по действию)."""
        await self._execute(
            select(
                func.chat_emit(
                    project_id, kind, func.jsonb_build_object(text("'message_id'"), message_id)
                )
            )
        )
