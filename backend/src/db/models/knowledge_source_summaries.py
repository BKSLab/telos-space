"""Кэш кратких описаний документов и проектных файлов."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class KnowledgeSourceSummary(Base):
    __tablename__ = "knowledge_source_summaries"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(document_id, attachment_id, project_file_id) = 1",
            name="ck_knowledge_summary_owner",
        ),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    attachment_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_attachments.id", ondelete="CASCADE"), nullable=True
    )
    project_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_files.id", ondelete="CASCADE"),
        nullable=True,
        doc="Файл проекта.",
        comment="Исходный самостоятельный файл проекта.",
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
