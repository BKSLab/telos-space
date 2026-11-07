"""Связь комментария с единственным оригиналом в файлах той же задачи."""

from sqlalchemy import ForeignKeyConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TaskCommentAttachment(Base):
    """Оба составных FK закрепляют принадлежность одной задаче в PostgreSQL."""

    __tablename__ = "task_comment_attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["comment_id", "task_id"],
            ["task_comments.id", "task_comments.task_id"],
            name="fk_comment_attachments_comment_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["attachment_id", "task_id"],
            ["task_attachments.id", "task_attachments.task_id"],
            name="fk_comment_attachments_attachment_task",
            ondelete="CASCADE",
        ),
        Index("ix_comment_attachments_attachment", "attachment_id", "task_id"),
    )

    comment_id: Mapped[int] = mapped_column(
        primary_key=True, doc="Комментарий.", comment="Комментарий, показывающий файл."
    )
    attachment_id: Mapped[int] = mapped_column(
        primary_key=True, doc="Оригинал задачи.", comment="Единый оригинал в файлах задачи."
    )
    task_id: Mapped[int] = mapped_column(
        doc="Общая задача.", comment="Общая задача комментария и оригинала."
    )

    def __repr__(self) -> str:
        return f"<TaskCommentAttachment(comment_id={self.comment_id}, attachment_id={self.attachment_id})>"
