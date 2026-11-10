"""Журнал действий агента, подтверждений и идемпотентных результатов."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class AgentToolRun(TimestampMixin, Base):
    """Один вызов изменяющего инструмента от имени пользователя.

    Проектное действие несёт ``project_id``; личное действие Ариса в виджете
    (разовая задача) — ``project_id IS NULL``. Ответ, в котором
    действие предложено: ``message_id`` (виджет) либо ``chat_turn_id`` (чат
    проекта); у MCP-вызова обе ссылки пусты.
    """

    __tablename__ = "agent_tool_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", "request_id", name="uq_agent_tool_request"),
        Index(
            "uq_agent_tool_personal_request",
            "user_id",
            "request_id",
            unique=True,
            postgresql_where=text("project_id IS NULL"),
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'rejected', 'failed')", name="ck_agent_tool_status"
        ),
        CheckConstraint(
            "message_id IS NULL OR chat_turn_id IS NULL", name="ck_agent_tool_single_origin"
        ),
        Index("ix_agent_tool_message", "message_id", "created_at"),
        Index("ix_agent_tool_chat_turn", "chat_turn_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, doc="ID действия.", comment="ID действия.")
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        doc="Проект действия; null — личное действие вне проекта.",
        comment="Проект действия; null — личное действие вне проекта.",
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        doc="Инициатор действия.",
        comment="Инициатор действия.",
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="CASCADE"),
        doc="Ответ Ариса в виджете; null для чата проекта и MCP.",
        comment="Ответ Ариса в виджете; null для чата проекта и MCP.",
    )
    chat_turn_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_agent_turns.id", ondelete="CASCADE"),
        doc="Ответ Ариса в чате проекта; null для виджета и MCP.",
        comment="Ответ Ариса в чате проекта; null для виджета и MCP.",
    )
    request_id: Mapped[UUID] = mapped_column(
        doc="Ключ идемпотентного вызова.", comment="Ключ идемпотентного вызова."
    )
    tool_name: Mapped[str] = mapped_column(
        String(64), doc="Имя инструмента.", comment="Имя инструмента."
    )
    title: Mapped[str] = mapped_column(
        String(255),
        doc="Название действия для участника.",
        comment="Название действия для участника.",
    )
    arguments: Mapped[dict] = mapped_column(
        JSONB, doc="Проверенные аргументы действия.", comment="Проверенные аргументы действия."
    )
    status: Mapped[str] = mapped_column(
        String(16), doc="Ожидание решения или результат.", comment="Ожидание решения или результат."
    )
    result: Mapped[dict] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
        doc="Сохранённый результат вызова.",
        comment="Сохранённый результат вызова.",
    )

    def __repr__(self) -> str:
        return f"<AgentToolRun(id={self.id}, tool_name={self.tool_name}, status={self.status})>"
