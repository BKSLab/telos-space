"""Ссылки комментария на Markdown и загруженные документы проекта."""

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TaskCommentDocument(Base):
    """Сохраняет оригинал; снятие связи с задачей убирает ссылку комментария."""

    __tablename__ = "task_comment_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["comment_id", "task_id"],
            ["task_comments.id", "task_comments.task_id"],
            name="fk_comment_documents_comment_task",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "num_nonnulls(document_id, project_file_id) = 1", name="ck_comment_documents_source"
        ),
        UniqueConstraint("comment_id", "document_id", name="uq_comment_documents_document"),
        UniqueConstraint("comment_id", "project_file_id", name="uq_comment_documents_file"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id: Mapped[int] = mapped_column(index=True)
    task_id: Mapped[int] = mapped_column(index=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    project_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_files.id", ondelete="CASCADE"), index=True
    )
    document_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_links.id", ondelete="CASCADE"), index=True
    )
