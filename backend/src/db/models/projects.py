from __future__ import annotations

import enum
import hashlib
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .board_stickers import BoardSticker
    from .boards import Board
    from .documents import Document
    from .project_members import ProjectMember
    from .project_milestones import ProjectMilestone
    from .project_risks import ProjectRisk
    from .project_stages import ProjectStage
    from .task_dependencies import TaskDependency
    from .tasks import Task
    from .users import User
    from .wbs_nodes import WbsNode


class ProjectStatus(str, enum.Enum):
    """Жизненный статус проекта."""

    PLANNING = "PLANNING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class Project(Base, TimestampMixin):
    """Проект — корневая сущность трекера."""

    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_search_vector", "search_vector", postgresql_using="gin"),
        CheckConstraint("context_revision >= 0", name="ck_projects_context_revision_nonnegative"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Идентификатор проекта.",
        comment="Уникальный идентификатор проекта.",
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Владелец проекта: единственный, кто может его удалить.",
        comment="Идентификатор пользователя-владельца проекта.",
    )
    key: Mapped[str] = mapped_column(
        String(length=10),
        unique=True,
        nullable=False,
        index=True,
        doc="Короткий код проекта, используемый как префикс номера задачи.",
        comment="Уникальный короткий код проекта в верхнем регистре.",
    )
    name: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        doc="Название проекта.",
        comment="Человекочитаемое название проекта.",
    )
    description_md: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Markdown-описание проекта.",
        comment="Описание проекта в формате Markdown.",
    )
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status"),
        nullable=False,
        default=ProjectStatus.PLANNING,
        doc="Текущий статус проекта.",
        comment="Жизненный статус проекта.",
    )
    color: Mapped[str] = mapped_column(
        String(length=20),
        nullable=False,
        doc="HEX-цвет проекта в интерфейсе.",
        comment="HEX-цвет, которым проект обозначается в интерфейсе.",
    )
    icon: Mapped[str | None] = mapped_column(
        String(length=64),
        nullable=True,
        doc="Эмодзи-иконка проекта.",
        comment="Необязательная эмодзи-иконка проекта.",
    )
    icon_image_key: Mapped[str | None] = mapped_column(
        String(length=2048),
        nullable=True,
        doc="Ключ своей картинки проекта в хранилище; важнее icon.",
        comment="Локальный ключ картинки проекта в uploads; наружу не отдаётся.",
    )
    icon_image_content_type: Mapped[str | None] = mapped_column(
        String(length=50),
        nullable=True,
        doc="MIME-тип своей картинки проекта.",
        comment="Проверенный MIME-тип картинки проекта: png, jpeg или webp.",
    )
    start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Дата старта проекта.",
        comment="Плановая дата начала проекта.",
    )
    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Плановая дата завершения проекта.",
        comment="Плановая дата завершения проекта.",
    )
    order_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Порядок проекта в списке.",
        comment="Порядок отображения проекта в списке и переключателе.",
    )
    description_sections: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    context_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Версия единого паспорта проекта.",
        comment="Серверная версия содержательных полей; прежние проекты имеют 0 до первого изменения.",
    )
    due_date_has_been_set: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    search_vector: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('russian', coalesce(name, '')), 'A') || "
            "setweight(to_tsvector('russian', coalesce(description_md, '')), 'B')",
            persisted=True,
        ),
        nullable=False,
        doc="Вычисляемый FTS-вектор проекта.",
        comment="Взвешенный FTS-вектор названия и описания проекта.",
    )

    boards: Mapped[list[Board]] = relationship(
        "Board",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    owner: Mapped[User] = relationship("User", back_populates="owned_projects")
    members: Mapped[list[ProjectMember]] = relationship(
        "ProjectMember",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    stages: Mapped[list[ProjectStage]] = relationship(
        "ProjectStage",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tasks: Mapped[list[Task]] = relationship(
        "Task",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    milestones: Mapped[list[ProjectMilestone]] = relationship(
        "ProjectMilestone",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    task_dependencies: Mapped[list[TaskDependency]] = relationship(
        "TaskDependency",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    wbs_nodes: Mapped[list[WbsNode]] = relationship(
        "WbsNode",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    documents: Mapped[list[Document]] = relationship(
        "Document",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    stickers: Mapped[list[BoardSticker]] = relationship(
        "BoardSticker",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    risks: Mapped[list[ProjectRisk]] = relationship(
        "ProjectRisk",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, key={self.key!r}, name={self.name!r})>"

    @property
    def icon_image_version(self) -> str | None:
        """Версия своей картинки для URL с кешем; сама ссылка наружу не уходит."""
        if not self.icon_image_key:
            return None
        return hashlib.sha256(self.icon_image_key.encode()).hexdigest()[:16]


class ProjectContextHistory(Base):
    """Неизменяемые снимки единственного паспорта; запись только добавляется."""

    __tablename__ = "project_context_history"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_project_context_history_revision_nonnegative"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Проект снимка.",
        comment="История сохраняется до удаления самого проекта.",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        doc="Версия паспорта.",
        comment="Уникальная версия внутри проекта; 0 — прежний baseline.",
    )
    description_sections: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Полные поля снимка.",
        comment="Сохранённые поля паспорта без ограничения превью.",
    )
    description_md: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Полный Markdown снимка.",
        comment="Прежний или составленный текст сохраняется полностью и без повторного разбора.",
    )
    changed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="Серверный автор снимка.",
        comment="Неизвестный исторический автор не выдумывается.",
    )
    changed_by_name: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Снимок имени автора.",
        comment="Определён сервером по текущей локальной identity, null для исходного baseline.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Время фиксации снимка.",
        comment="Для baseline — фактическое время сохранения, не историческая дата написания текста.",
    )
    is_initial: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
        doc="Исходное состояние.",
        comment="Создание нового проекта или прежний baseline версии 0.",
    )
    changed_fields: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        doc="Изменённые поля.",
        comment="Канонические имена полей паспорта либо description_md для свободного текста.",
    )
