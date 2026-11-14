from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .projects import Project


class Document(Base, TimestampMixin):
    """Редактируемый markdown-документ проекта."""

    __tablename__ = "documents"
    origin_attachment_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_attachments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    __table_args__ = (
        CheckConstraint("num_nonnulls(project_id, owner_task_id) = 1", name="ck_documents_owner"),
        UniqueConstraint("project_id", "slug", name="uq_documents_project_slug"),
        Index("ix_documents_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Идентификатор документа.",
        comment="Уникальный идентификатор документа.",
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc="Проект, которому принадлежит документ.",
        comment="Идентификатор проекта-владельца документа.",
    )
    owner_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
        doc="Задача-владелец текста.",
        comment="Задача-владелец; её текущий доступ определяет доступ к тексту.",
    )
    authored_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        doc="Автор документа.",
        comment="Локальный автор; у прежних документов может отсутствовать.",
    )
    caption: Mapped[str | None] = mapped_column(
        Text,
        doc="Примечание документа.",
        comment="Необязательное примечание пользователя.",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        server_default="1",
        doc="Версия записи.",
        comment="Монотонная версия редактируемых полей документа.",
    )
    slug: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        index=True,
        doc="URL-идентификатор документа, уникальный в пределах проекта.",
        comment="URL-идентификатор документа внутри проекта.",
    )
    title: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        doc="Заголовок для списка документов.",
        comment="Человекочитаемый заголовок документа.",
    )
    content_md: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Markdown-содержимое документа.",
        comment="Содержимое документа в формате Markdown.",
    )
    search_vector: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('russian', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('russian', coalesce(caption, '')), 'B') || "
            "setweight(to_tsvector('russian', coalesce(content_md, '')), 'B')",
            persisted=True,
        ),
        nullable=False,
        doc="Автоматически вычисляемый FTS-вектор заголовка и содержимого документа.",
        comment="Взвешенный FTS-вектор заголовка и содержимого документа.",
    )

    project: Mapped[Project | None] = relationship("Project", back_populates="documents")

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, project_id={self.project_id}, slug={self.slug!r})>"
