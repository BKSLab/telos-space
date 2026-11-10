from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .projects import Project
    from .tasks import Task
    from .users import User


DEFAULT_STICKER_COLOR = "#fff19a"
"""Жёлтая бумага стикера по умолчанию (светлый --sticker-yellow-bg)."""


class BoardSticker(Base, TimestampMixin):
    """Короткая общая заметка на доске проекта."""

    __tablename__ = "project_stickers"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(body)) BETWEEN 1 AND 2000",
            name="ck_project_stickers_body_length",
        ),
        CheckConstraint("revision >= 1", name="ck_project_stickers_revision_positive"),
        CheckConstraint(
            "canvas_x BETWEEN -1000000.0 AND 1000000.0",
            name="ck_project_stickers_canvas_x_range",
        ),
        CheckConstraint(
            "canvas_y BETWEEN -1000000.0 AND 1000000.0",
            name="ck_project_stickers_canvas_y_range",
        ),
        CheckConstraint("color ~ '^#[0-9a-f]{6}$'", name="ck_project_stickers_color_hex"),
        Index("ix_project_stickers_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Идентификатор стикера.",
        comment="Уникальный идентификатор стикера Project Board.",
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc="Проект, которому принадлежит стикер; NULL у стикера свободной доски.",
        comment="Идентификатор проекта-владельца стикера; NULL — доска без проекта.",
    )
    board_id: Mapped[int] = mapped_column(
        ForeignKey("project_boards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Доска, на которой лежит стикер.",
        comment="Идентификатор доски; стикер удаляется вместе с доской.",
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Текст стикера без пользовательского HTML.",
        comment="Текст общего стикера проекта.",
    )
    color: Mapped[str] = mapped_column(
        String(length=7),
        nullable=False,
        default=DEFAULT_STICKER_COLOR,
        doc="Свободный цвет стикера: hex #rrggbb в нижнем регистре.",
        comment="Цвет стикера: hex #rrggbb в нижнем регистре.",
    )
    canvas_x: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=40.0,
        doc="Координата X левого верхнего угла стикера на холсте.",
        comment="Координата X стикера на Project Board.",
    )
    canvas_y: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=40.0,
        doc="Координата Y левого верхнего угла стикера на холсте.",
        comment="Координата Y стикера на Project Board.",
    )
    width: Mapped[float] = mapped_column(
        Float, nullable=False, default=230.0, doc="Ширина стикера на холсте."
    )
    height: Mapped[float] = mapped_column(
        Float, nullable=False, default=230.0, doc="Высота стикера на холсте."
    )
    z_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc="Слой: общая шкала с фигурами той же доски.",
        comment="Порядок наложения стикера на доске.",
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Пользователь-создатель, если профиль ещё существует.",
        comment="Идентификатор автора стикера.",
    )
    created_by_username_snapshot: Mapped[str] = mapped_column(
        String(length=50),
        nullable=False,
        doc="Неизменяемый логин автора на момент создания.",
        comment="Fallback-логин автора стикера.",
    )
    created_by_display_name_snapshot: Mapped[str] = mapped_column(
        String(length=302),
        nullable=False,
        doc="Неизменяемое имя автора на момент создания.",
        comment="Fallback-имя автора стикера.",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        doc="Версия для optimistic concurrency.",
        comment="Монотонная ревизия стикера.",
    )

    project: Mapped[Project | None] = relationship("Project", back_populates="stickers")
    created_by: Mapped[User | None] = relationship("User")
    task_links: Mapped[list[BoardStickerTaskLink]] = relationship(
        "BoardStickerTaskLink",
        back_populates="sticker",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="BoardStickerTaskLink.task_id",
    )

    def __repr__(self) -> str:
        return (
            f"<BoardSticker(id={self.id}, project_id={self.project_id}, "
            f"revision={self.revision})>"
        )


class BoardStickerTaskLink(Base):
    """Связь стикера с задачей того же проекта."""

    __tablename__ = "project_sticker_task_links"

    sticker_id: Mapped[int] = mapped_column(
        ForeignKey("project_stickers.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Связанный стикер.",
        comment="Идентификатор стикера.",
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
        doc="Связанная задача.",
        comment="Идентификатор задачи.",
    )

    sticker: Mapped[BoardSticker] = relationship(
        "BoardSticker",
        back_populates="task_links",
    )
    task: Mapped[Task] = relationship("Task")

    def __repr__(self) -> str:
        return f"<BoardStickerTaskLink(sticker_id={self.sticker_id}, task_id={self.task_id})>"
