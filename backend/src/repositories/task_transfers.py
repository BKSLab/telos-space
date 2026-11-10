"""Проектные и разовые задачи."""

import json
import logging
from dataclasses import dataclass

from sqlalchemy import Result, delete, func, or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.board_stickers import BoardStickerTaskLink
from src.db.models.document_links import DocumentLink
from src.db.models.project_risks import ProjectRisk
from src.db.models.task_dependencies import TaskDependency
from src.db.models.tasks import Task
from src.exceptions.task_transfers import TaskTransfersRepositoryError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TaskProjectLinks:
    """Сколько проектных связей у задачи."""

    dependencies: int = 0
    document_links: int = 0
    risks: int = 0
    board_stickers: int = 0
    wbs_placements: int = 0

    @property
    def total(self) -> int:
        """Всего связей, которые снимет перенос."""
        return self.dependencies + self.document_links + self.risks + self.board_stickers + self.wbs_placements


class TaskTransfersRepository:
    """Проектные связи задачи при переносе."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def get_project_links(self, task_id: int) -> TaskProjectLinks:
        """Считает проектные связи задачи одним запросом.

        Args:
            task_id: Идентификатор задачи.

        Returns:
            Число зависимостей, связей с документами, рисков и стикеров.

        Raises:
            TaskTransfersRepositoryError: Если запрос к БД завершился ошибкой.
        """
        dependencies = (
            select(func.count())
            .select_from(TaskDependency)
            .where(
                or_(
                    TaskDependency.predecessor_task_id == task_id,
                    TaskDependency.successor_task_id == task_id,
                )
            )
            .scalar_subquery()
        )
        links = (
            select(func.count())
            .select_from(DocumentLink)
            .where(DocumentLink.task_id == task_id)
            .scalar_subquery()
        )
        risks = (
            select(func.count())
            .select_from(ProjectRisk)
            .where(ProjectRisk.task_id == task_id)
            .scalar_subquery()
        )
        stickers = (
            select(func.count())
            .select_from(BoardStickerTaskLink)
            .where(BoardStickerTaskLink.task_id == task_id)
            .scalar_subquery()
        )
        placements = (
            select(func.count()).select_from(Task).where(
                Task.id == task_id,
                or_(Task.wbs_node_id.is_not(None), Task.wbs_position.is_not(None),
                    Task.canvas_x.is_not(None), Task.canvas_y.is_not(None)),
            ).scalar_subquery()
        )
        try:
            result: Result = await self.db_session.execute(
                select(dependencies, links, risks, stickers, placements)
            )
            row = result.one()
            return TaskProjectLinks(
                dependencies=int(row[0]),
                document_links=int(row[1]),
                risks=int(row[2]),
                board_stickers=int(row[3]),
                wbs_placements=int(row[4]),
            )
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось посчитать связи задачи id=%s.", task_id, exc_info=True)
            raise TaskTransfersRepositoryError(f"Ошибка чтения связей задачи id={task_id}.") from error

    async def delete_dependencies(self, task_id: int) -> None:
        """Удаляет зависимости задачи в обе стороны.

        Raises:
            TaskTransfersRepositoryError: Если удалить не удалось.
        """
        await self._write(
            delete(TaskDependency).where(
                or_(
                    TaskDependency.predecessor_task_id == task_id,
                    TaskDependency.successor_task_id == task_id,
                )
            ),
            operation="удаления зависимостей",
            task_id=task_id,
        )

    async def delete_document_links(self, task_id: int) -> None:
        """Удаляет связи задачи с документами проекта.

        Raises:
            TaskTransfersRepositoryError: Если удалить не удалось.
        """
        await self._write(
            delete(DocumentLink).where(DocumentLink.task_id == task_id),
            operation="удаления связей с документами",
            task_id=task_id,
        )

    async def unlink_risks(self, task_id: int) -> None:
        """Отвязывает риски проекта от задачи; сами риски остаются в проекте.

        Raises:
            TaskTransfersRepositoryError: Если отвязать не удалось.
        """
        await self._write(
            update(ProjectRisk).where(ProjectRisk.task_id == task_id).values(task_id=None),
            operation="отвязки рисков",
            task_id=task_id,
        )

    async def delete_sticker_links(self, task_id: int) -> None:
        """Снимает задачу со стикеров доски проекта; стикеры остаются.

        Raises:
            TaskTransfersRepositoryError: Если снять не удалось.
        """
        await self._write(
            delete(BoardStickerTaskLink).where(BoardStickerTaskLink.task_id == task_id),
            operation="снятия задачи со стикеров",
            task_id=task_id,
        )


    async def emit_chat_event(self, *, project_id: int, event_type: str, payload: dict) -> None:
        """Кладёт событие в outbox чата проекта в текущей транзакции.

        Тот же ``chat_emit``, которым пишут триггеры: событие получает следующий
        ``seq`` чата и уходит в Redis доставщиком outbox после commit. У проекта
        без чата событие молча не пишется — как и у триггеров.

        Args:
            project_id: Проект, чату которого адресовано событие.
            event_type: Тип события (``task.moved``).
            payload: Идентификаторы и подписи без текста переписки.

        Raises:
            TaskTransfersRepositoryError: Если записать событие не удалось.
        """
        await self._write(
            text("SELECT chat_emit(:project_id, :event_type, CAST(:payload AS jsonb))").bindparams(
                project_id=project_id,
                event_type=event_type,
                payload=json.dumps(payload, ensure_ascii=False),
            ),
            operation="записи события чата",
            task_id=int(payload.get("task_id") or 0),
        )

    async def _write(self, statement, *, operation: str, task_id: int) -> None:
        """Выполняет одну операцию записи в текущей транзакции."""
        try:
            await self.db_session.execute(statement.execution_options(synchronize_session=False))
            await self.db_session.flush()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Ошибка %s задачи id=%s.", operation, task_id, exc_info=True)
            raise TaskTransfersRepositoryError(f"Ошибка {operation} задачи id={task_id}.") from error
