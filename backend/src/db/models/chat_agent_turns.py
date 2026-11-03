"""Ход Ариса в чате проекта: вопрос участника, ответ-заглушка и очередь подготовки."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ChatAgentTurn(TimestampMixin, Base):
    """Одно обращение к Арису в общем чате.

    Ответ живёт обычным сообщением чата (``author_kind='agent'``), которое
    создаётся сразу пустым — это и есть индикатор «Арис печатает» для всей
    команды. Строка хода хранит постоянное состояние подготовки: воркер
    переживает перезапуск, а зависшая попытка завершается ошибкой по таймауту.
    """

    __tablename__ = "chat_agent_turns"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chat_id", "project_id"],
            ["project_chats.id", "project_chats.project_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_chat_agent_turn_status",
        ),
        Index("ix_chat_agent_turns_queue", "status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, doc="ID хода.", comment="ID хода.")
    chat_id: Mapped[int] = mapped_column(doc="Чат хода.", comment="Чат хода.")
    project_id: Mapped[int] = mapped_column(doc="Проект хода.", comment="Проект хода.")
    request_message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        unique=True,
        doc="Сообщение участника, обращённое к Арису.",
        comment="Сообщение участника, обращённое к Арису.",
    )
    reply_message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        unique=True,
        doc="Сообщение Ариса с ответом.",
        comment="Сообщение Ариса с ответом.",
    )
    requester_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        doc="Кто позвал Ариса; его права действуют в ответе.",
        comment="Кто позвал Ариса; его права действуют в ответе.",
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
    sources: Mapped[list] = mapped_column(
        JSONB,
        server_default=text("'[]'::jsonb"),
        doc="Проверенные источники ответа (карточки сущностей).",
        comment="Проверенные источники ответа (карточки сущностей).",
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

    def __repr__(self) -> str:
        return f"<ChatAgentTurn(id={self.id}, project_id={self.project_id}, status={self.status})>"
