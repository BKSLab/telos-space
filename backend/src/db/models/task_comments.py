from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Computed, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .task_attachments import TaskAttachment
    from .tasks import Task
    from .users import User


class TaskComment(Base):
    """Комментарий к задаче канбана."""

    __tablename__ = "task_comments"
    __table_args__ = (
        Index("ix_task_comments_search_vector", "search_vector", postgresql_using="gin"),
        UniqueConstraint("id", "task_id", name="uq_task_comments_id_task"),
        Index("ix_task_comments_task_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Идентификатор комментария.",
        comment="Уникальный идентификатор комментария.",
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        doc="Задача комментария.",
        comment="Идентификатор задачи, к которой относится комментарий.",
    )
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Автор комментария — локальный пользователь.",
        comment="Автор комментария; NULL — пользователь удалён, подпись осталась в author_name.",
    )
    author_name: Mapped[str | None] = mapped_column(
        String(length=255),
        nullable=True,
        doc="Полное имя автора на момент написания.",
        comment="Снимок полного имени автора: для полнотекстового поиска и удалённых авторов.",
    )
    body_md: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Текст комментария.",
        comment="Текст комментария в формате Markdown.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        doc="Дата создания комментария.",
        comment="Дата и время создания комментария.",
    )
    search_vector: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('russian', coalesce(body_md, '')), 'A') || "
            "setweight(to_tsvector('russian', coalesce(author_name, '')), 'B')",
            persisted=True,
        ),
        nullable=False,
        doc="Вычисляемый FTS-вектор комментария.",
        comment="Взвешенный FTS-вектор текста и автора комментария.",
    )

    task: Mapped[Task] = relationship("Task", back_populates="comments")
    author: Mapped[User | None] = relationship("User", lazy="raise_on_sql")
    attachments: Mapped[list[TaskAttachment]] = relationship(
        "TaskAttachment",
        secondary="task_comment_attachments",
        primaryjoin="TaskComment.id == TaskCommentAttachment.comment_id",
        secondaryjoin="TaskAttachment.id == TaskCommentAttachment.attachment_id",
        viewonly=True,
        lazy="raise_on_sql",
        order_by="TaskAttachment.created_at, TaskAttachment.id",
    )

    def __repr__(self) -> str:
        return f"<TaskComment(id={self.id}, task_id={self.task_id})>"
