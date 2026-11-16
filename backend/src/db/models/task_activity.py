from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .tasks import Task


class TaskActivityEventType(str, enum.Enum):
    """Тип события в истории изменений задачи."""

    STAGE_CHANGED = "STAGE_CHANGED"
    DUE_DATE_CHANGED = "DUE_DATE_CHANGED"
    START_DATE_CHANGED = "START_DATE_CHANGED"
    DATES_CHANGED = "DATES_CHANGED"
    BASELINE_CHANGED = "BASELINE_CHANGED"
    DESCRIPTION_CHANGED = "DESCRIPTION_CHANGED"
    PRIORITY_CHANGED = "PRIORITY_CHANGED"
    ASSIGNEE_CHANGED = "ASSIGNEE_CHANGED"
    WBS_NODE_CHANGED = "WBS_NODE_CHANGED"
    COMMENT_ADDED = "COMMENT_ADDED"
    CHECKLIST_CHANGED = "CHECKLIST_CHANGED"
    TASK_MOVED = "TASK_MOVED"
    LINKS_REMOVED = "LINKS_REMOVED"


class TaskActivity(Base):
    """Неизменяемая запись истории изменений задачи."""

    __tablename__ = "task_activity"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Идентификатор события истории задачи.",
        comment="Уникальный идентификатор события.",
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        doc="Задача события.",
        comment="Идентификатор задачи, к которой относится событие.",
    )
    event_type: Mapped[TaskActivityEventType] = mapped_column(
        Enum(TaskActivityEventType, name="task_activity_event_type"),
        nullable=False,
        doc="Тип события.",
        comment="Тип изменения задачи.",
    )
    from_value: Mapped[str | None] = mapped_column(
        String(length=255),
        nullable=True,
        doc="Предыдущее значение.",
        comment="Текстовое представление предыдущего значения.",
    )
    to_value: Mapped[str | None] = mapped_column(
        String(length=255),
        nullable=True,
        doc="Новое значение.",
        comment="Текстовое представление нового значения.",
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Причина изменения плановых дат.",
        comment="Общая причина изменения или снятия назначенных дат задачи.",
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="Кто выполнил изменение.",
        comment="Пользователь, от имени которого сделано изменение; NULL — неизвестен.",
    )
    actor_name: Mapped[str | None] = mapped_column(
        String(length=255),
        nullable=True,
        doc="Имя автора изменения.",
        comment="Снимок имени автора изменения на момент события.",
    )
    via_api_key_label: Mapped[str | None] = mapped_column(
        String(length=160),
        nullable=True,
        doc="Сервисный ключ, через который сделано изменение.",
        comment="Имя API-ключа: изменение сделано агентом «через ключ»; NULL — напрямую.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        doc="Дата события.",
        comment="Дата и время фиксации события.",
    )

    task: Mapped[Task] = relationship("Task", back_populates="activity")

    def __repr__(self) -> str:
        return f"<TaskActivity(id={self.id}, task_id={self.task_id}, event_type={self.event_type})>"
