from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AgentMessage(Base):
    """Реплика диалога и постоянное состояние подготовки ответа агента."""

    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "request_id", "role", name="uq_agent_message_request_role"
        ),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_agent_message_role"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_agent_message_status",
        ),
        CheckConstraint(
            "role = 'assistant' OR status = 'completed'", name="ck_agent_user_message_complete"
        ),
        Index("ix_agent_messages_conversation", "conversation_id", "id"),
        Index("ix_agent_messages_queue", "status", "id"),
        Index("ix_agent_messages_request_result", "request_id", "role"),
        Index(
            "uq_agent_conversation_active",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'processing')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, doc="ID реплики.", comment="ID реплики.")
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        doc="Диалог реплики.",
        comment="Диалог реплики.",
    )
    request_id: Mapped[UUID] = mapped_column(
        doc="Ключ повторной отправки вопроса.", comment="Ключ повторной отправки вопроса."
    )
    role: Mapped[str] = mapped_column(
        String(16), doc="Автор: участник или агент.", comment="Автор: участник или агент."
    )
    content: Mapped[str] = mapped_column(
        Text, server_default="", doc="Текст реплики.", comment="Текст реплики."
    )
    sources: Mapped[list] = mapped_column(
        JSONB,
        server_default=text("'[]'::jsonb"),
        doc="Проверенные источники ответа.",
        comment="Проверенные источники ответа.",
    )
    files: Mapped[list] = mapped_column(
        JSONB,
        server_default=text("'[]'::jsonb"),
        doc="Метаданные файлов, выбранных участником при отправке.",
        comment="Метаданные файлов, выбранных участником при отправке.",
    )
    page_context: Mapped[dict | None] = mapped_column(
        JSONB,
        doc="Страница, с которой пользователь написал Арису: route, project_key, task_id, board_id.",
        comment="Контекст страницы виджета: route, project_key, task_id, board_id.",
    )
    verbatim: Mapped[str | None] = mapped_column(
        Text,
        doc="Дословная реплика пользователя; только она разрешает агенту запись.",
        comment="Дословная реплика пользователя; только она разрешает агенту запись.",
    )
    key_scopes: Mapped[list | None] = mapped_column(
        JSONB,
        doc="Права сервисного ключа на момент вопроса; None — вопрос из интерфейса.",
        comment="Права сервисного ключа на момент вопроса; NULL — вопрос из интерфейса.",
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        doc="Проект, в котором агент ответил в беседе по трекеру.",
        comment="Проект, в котором агент ответил в беседе по трекеру; NULL — обзор.",
    )
    status: Mapped[str] = mapped_column(
        String(16), doc="Состояние подготовки ответа.", comment="Состояние подготовки ответа."
    )
    progress: Mapped[str | None] = mapped_column(
        String(200),
        doc="Что Арис делает сейчас, пока готовит ответ; None — статуса нет или ответ завершён.",
        comment="Что Арис делает сейчас, пока готовит ответ; NULL — статуса нет или ответ завершён.",
    )
    error: Mapped[str | None] = mapped_column(
        Text, doc="Безопасное описание ошибки.", comment="Безопасное описание ошибки."
    )
    run_id: Mapped[UUID | None] = mapped_column(
        doc="Владелец текущей попытки исполнения.", comment="Владелец текущей попытки исполнения."
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), doc="Начало попытки.", comment="Начало попытки."
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), doc="Завершение попытки.", comment="Завершение попытки."
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        doc="Время отправки.",
        comment="Время отправки.",
    )

    def __repr__(self) -> str:
        return f"<AgentMessage(id={self.id}, conversation_id={self.conversation_id}, status={self.status})>"
