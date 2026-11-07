from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.schemas.enums import TaskPriority, TaskRole, TaskStatus

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .project_stages import ProjectStage
    from .projects import Project
    from .task_activity import TaskActivity
    from .task_attachments import TaskAttachment
    from .task_comments import TaskComment
    from .task_dependencies import TaskDependency
    from .task_participants import TaskParticipant
    from .wbs_nodes import WbsNode


class Task(Base, TimestampMixin):
    """Задача проекта.

    Одна и та же задача отображается в канбане, списке задач и ИСР. Стадия
    отвечает за состояние работы, ``wbs_node_id`` — за место в структуре
    проекта; эти признаки независимы друг от друга.

    Нераспределённая задача существует в двух состояниях: в списке-пуле
    (координат нет) либо выложенной на холст ИСР (``canvas_x``/``canvas_y``
    заполнены). Привязка к разделу очищает координаты: место задачи в
    структуре считает раскладка, а не пользователь.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_tasks_project_number"),
        CheckConstraint("revision >= 1", name="ck_tasks_revision_positive"),
        CheckConstraint("project_id IS NOT NULL OR (wbs_node_id IS NULL AND wbs_position IS NULL AND canvas_x IS NULL AND canvas_y IS NULL)", name="ck_tasks_personal_no_wbs"),
        Index("ix_tasks_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_tasks_project_due_date", "project_id", "due_date"),
        CheckConstraint("(project_id IS NULL) = (stage_id IS NULL)", name="ck_tasks_stage_in_project"),
        CheckConstraint("(project_id IS NULL) = (status IS NOT NULL)", name="ck_tasks_status_outside_project"),
        Index("uq_tasks_personal_number", "number", unique=True, postgresql_where=text("project_id IS NULL")),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Идентификатор задачи.",
        comment="Уникальный идентификатор задачи.",
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc="Проект, которому принадлежит задача.",
        comment="Идентификатор проекта-владельца задачи.",
    )
    stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_stages.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        doc="Текущая стадия задачи. Стадия принадлежит тому же проекту.",
        comment="Идентификатор текущей стадии задачи.",
    )
    status: Mapped[TaskStatus | None] = mapped_column(
        Enum(TaskStatus, name="task_status"),
        nullable=True,
        doc="Состояние задачи вне проекта; у проектной задачи его задаёт стадия.",
        comment="Статус разовой задачи; NULL у проектной задачи.",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
        default=1,
        doc="Текущая версия задачи для защиты от устаревших изменений.",
        comment="Монотонная версия общих полей и назначений задачи.",
    )
    attention_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
        default=1,
        doc="Версия содержимого задачи для персонального просмотра.",
        comment="Монотонная версия; обновляется триггерами вместе с содержимым.",
    )
    attention_deadline_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="1",
        default=1,
        doc="Версия срока и перехода между открытым и закрытым состоянием.",
        comment="Отдельная версия нарушения срока; обычные комментарии её не меняют.",
    )
    wbs_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("wbs_nodes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Раздел ИСР. NULL — задача не распределена по структуре.",
        comment="Идентификатор раздела ИСР; NULL для нераспределённой задачи.",
    )
    number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Порядковый номер задачи внутри проекта.",
        comment="Номер задачи внутри проекта для отображения вида KEY-42.",
    )
    title: Mapped[str] = mapped_column(
        String(length=512),
        nullable=False,
        doc="Заголовок задачи.",
        comment="Заголовок задачи.",
    )
    description_md: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Markdown-описание задачи.",
        comment="Описание задачи в формате Markdown.",
    )
    checklist: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True, doc="Чек-лист задачи с упорядоченными пунктами.",
        comment="Название, стабильные ID, тексты и отметки пунктов; NULL — чек-листа нет.",
    )
    checklist_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0,
        doc="Версия чек-листа для защиты от потери параллельных правок.",
        comment="Монотонная версия, сохраняется и после удаления чек-листа.",
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, name="task_priority"),
        nullable=False,
        default=TaskPriority.MEDIUM,
        doc="Приоритет задачи.",
        comment="Приоритет выполнения задачи.",
    )
    role: Mapped[TaskRole | None] = mapped_column(
        Enum(TaskRole, name="task_role"),
        nullable=True,
        doc="Роль, ответственная за задачу.",
        comment="Ответственная за выполнение задачи роль.",
    )
    assignee: Mapped[str | None] = mapped_column(
        String(length=255),
        nullable=True,
        doc="Свободная подпись исполнителя. Авторизации в проекте нет.",
        comment="Необязательное имя исполнителя задачи.",
    )
    start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Плановое начало выполнения.",
        comment="Плановая дата начала задачи.",
    )
    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Срок исполнения.",
        comment="Плановая дата завершения задачи.",
    )
    baseline_start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Зафиксированное исходное начало.",
        comment="Дата начала утверждённого baseline.",
    )
    baseline_due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Зафиксированный исходный дедлайн.",
        comment="Дата завершения утверждённого baseline.",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Момент фактического завершения.",
        comment="Момент последнего перехода задачи в завершающую стадию.",
    )
    position: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        doc="Позиция внутри стадии.",
        comment="Позиция сортировки задачи внутри стадии.",
    )
    wbs_position: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Разреженная позиция среди задач одного раздела ИСР. "
            "NULL — задача не размещена в структуре."
        ),
        comment="Позиция сортировки задачи внутри раздела ИСР.",
    )
    canvas_x: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Координата X карточки на холсте ИСР. Заполнена только у задачи, "
            "выложенной на холст вне структуры."
        ),
        comment="Координата X карточки задачи на холсте ИСР.",
    )
    canvas_y: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Координата Y карточки на холсте ИСР. Заполнена только у задачи, "
            "выложенной на холст вне структуры."
        ),
        comment="Координата Y карточки задачи на холсте ИСР.",
    )
    search_vector: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('russian', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('russian', coalesce(description_md, '')), 'B')",
            persisted=True,
        ),
        nullable=False,
        doc="Вычисляемый FTS-вектор задачи.",
        comment="Взвешенный FTS-вектор заголовка и описания задачи.",
    )

    project: Mapped[Project | None] = relationship("Project", back_populates="tasks")
    stage: Mapped[ProjectStage | None] = relationship("ProjectStage", back_populates="tasks")
    wbs_node: Mapped[WbsNode | None] = relationship("WbsNode", back_populates="tasks")
    comments: Mapped[list[TaskComment]] = relationship(
        "TaskComment",
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    activity: Mapped[list[TaskActivity]] = relationship(
        "TaskActivity",
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    attachments: Mapped[list[TaskAttachment]] = relationship(
        "TaskAttachment",
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    successor_links: Mapped[list[TaskDependency]] = relationship(
        "TaskDependency",
        foreign_keys="TaskDependency.predecessor_task_id",
        back_populates="predecessor",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    predecessor_links: Mapped[list[TaskDependency]] = relationship(
        "TaskDependency",
        foreign_keys="TaskDependency.successor_task_id",
        back_populates="successor",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    participants: Mapped[list[TaskParticipant]] = relationship(
        "TaskParticipant",
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Task(id={self.id}, project_id={self.project_id}, title={self.title!r})>"
