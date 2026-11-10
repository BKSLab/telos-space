from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .projects import Project

DEFAULT_BOARD_TITLE = "Основная"


class Board(Base, TimestampMixin):
    """Доска: отдельный холст со стикерами и фигурами.

    ``project_id IS NULL`` — свободная (личная) доска из раздела «Доски»:
    не привязана к проекту, видна только автору (``created_by_user_id``).
    """

    __tablename__ = "project_boards"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 120",
            name="ck_project_boards_title_length",
        ),
        CheckConstraint("revision >= 1", name="ck_project_boards_revision_positive"),
        CheckConstraint(
            "project_id IS NOT NULL OR created_by_user_id IS NOT NULL",
            name="ck_project_boards_free_has_owner",
        ),
        Index("ix_project_boards_project_active", "project_id", "archived_at", "position"),
        Index(
            "ix_project_boards_owner_active",
            "created_by_user_id",
            "archived_at",
            "position",
            postgresql_where=column("project_id").is_(None),
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Идентификатор доски.",
        comment="Уникальный идентификатор доски проекта.",
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc="Проект, которому принадлежит доска; NULL у свободной доски.",
        comment="Идентификатор проекта-владельца доски; NULL — личная доска без проекта.",
    )
    title: Mapped[str] = mapped_column(
        String(length=120),
        nullable=False,
        doc="Название доски на вкладке.",
        comment="Название доски проекта.",
    )
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc="Порядок вкладки: по возрастанию, затем по id.",
        comment="Порядок доски среди досок проекта.",
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="Пользователь, создавший доску, если профиль ещё существует.",
        comment="Идентификатор автора доски.",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        doc="Версия для optimistic concurrency.",
        comment="Монотонная ревизия доски.",
    )
    editor_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {"version": 1, "groups": [], "locked": [], "templates": []},
        server_default='{"version":1,"groups":[],"locked":[],"templates":[]}',
        doc="Явные группы, locks редактора и автономные шаблоны выделения.",
        comment="Версионированная metadata редактора; CAS использует revision доски.",
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Момент мягкого удаления; архивная доска не отдаётся.",
        comment="Дата мягкого удаления доски; NULL у активной.",
    )

    project: Mapped[Project | None] = relationship("Project", back_populates="boards")

    def __repr__(self) -> str:
        return (
            f"<Board(id={self.id}, project_id={self.project_id}, "
            f"title={self.title!r}, revision={self.revision})>"
        )
