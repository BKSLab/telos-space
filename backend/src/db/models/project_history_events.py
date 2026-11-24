"""Неизменяемые проектные снимки публичных доменных действий."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ProjectHistoryEvent(Base):
    """Событие принадлежит проекту независимо от дальнейшей судьбы источника."""

    __tablename__ = "project_history_events"
    __table_args__ = (
        UniqueConstraint("project_id", "public_id", name="uq_project_history_public_id"),
        Index("ix_project_history_project_created", "project_id", "created_at", "id"),
        Index("ix_project_history_project_id", "project_id", "id"),
        Index("ix_project_history_project_task", "project_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        doc="Номер события.",
        comment="Монотонный номер проектного события.",
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        doc="Проект события.",
        comment="Неизменяемая проектная область события.",
    )
    public_id: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        doc="Стабильный публичный ID.",
        comment="Канонический ID источника либо project_event:<id>.",
    )
    kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        doc="Группа событий.",
        comment="Публичная группа фильтра истории.",
    )
    event_type: Mapped[str | None] = mapped_column(
        String(80), doc="Тип действия.", comment="Семантический тип доменного действия."
    )
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Заголовок на момент действия.",
        comment="Снимок заголовка; не читается из текущего объекта.",
    )
    detail: Mapped[str | None] = mapped_column(
        String(500),
        doc="Краткое содержание.",
        comment="Публичные детали длиной не более 500 символов.",
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer,
        doc="Автор действия.",
        comment="Снимок ID автора без FK; неизвестный автор остаётся NULL.",
    )
    actor_name: Mapped[str | None] = mapped_column(
        String(350), doc="Имя автора.", comment="Снимок имени автора действия."
    )
    task_id: Mapped[int | None] = mapped_column(
        Integer,
        doc="Исходная задача.",
        comment="Исторический ID задачи без FK; сохраняется после переноса и удаления.",
    )
    entity_type: Mapped[str | None] = mapped_column(
        String(80),
        doc="Тип источника.",
        comment="Тип публичного источника для проверки доступности перехода.",
    )
    entity_id: Mapped[int | None] = mapped_column(
        Integer, doc="Исходный объект.", comment="ID публичного источника без FK."
    )
    document_ref: Mapped[str | None] = mapped_column(
        String(160),
        doc="Ссылка на документ.",
        comment="Каноническая ссылка источника, требующая текущей проверки доступа.",
    )
    source_table: Mapped[str | None] = mapped_column(
        String(80), doc="Таблица источника.", comment="Несекретный ключ дедупликации источника."
    )
    source_id: Mapped[int | None] = mapped_column(
        Integer, doc="Строка источника.", comment="Исторический ID строки источника без FK."
    )
    comment_id: Mapped[int | None] = mapped_column(
        Integer, doc="Комментарий источника.", comment="Исторический ID комментария без FK."
    )
    report_id: Mapped[int | None] = mapped_column(
        Integer, doc="Отчёт источника.", comment="Исторический ID публикации отчёта без FK."
    )
    report_revision: Mapped[int | None] = mapped_column(
        Integer, doc="Ревизия отчёта.", comment="Номер опубликованной ревизии отчёта."
    )
    from_value: Mapped[str | None] = mapped_column(
        String(500),
        doc="Прежнее значение.",
        comment="Ограниченное публичное значение до изменения.",
    )
    to_value: Mapped[str | None] = mapped_column(
        String(500),
        doc="Новое значение.",
        comment="Ограниченное публичное значение после изменения.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Время действия.",
        comment="Время действия либо доказанное время исторического источника.",
    )
    transaction_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=func.txid_current(),
        doc="Транзакция фиксации.",
        comment="Транзакция PostgreSQL для атомарной дедупликации действий.",
    )
