"""Личные и сервисные API-ключи локальных пользователей."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ApiKey(Base, TimestampMixin):
    """API-ключ сервисного клиента."""

    __tablename__ = "api_keys"
    __table_args__ = (CheckConstraint("kind IN ('personal', 'service')", name="ck_api_keys_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        doc="UUID ключа.",
        comment="Первичный ключ.",
    )
    label: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        doc="Человекочитаемое имя ключа.",
        comment="Отображаемое имя ключа; попадает в историю задач как «через ключ».",
    )
    key_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="SHA-256 полного ключа.",
        comment="Хеш секрета; сам ключ не хранится.",
    )
    key_prefix: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        unique=True,
        doc="Публичный идентификатор ключа: восемь hex-символов.",
        comment="Публичный идентификатор для узнавания и поиска ключа, без секрета.",
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Сотрудник, на которого выдан ключ.",
        comment="Локальный владелец ключа; доступ ограничен его правами.",
    )
    owner_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Имя владельца на момент выдачи.",
        comment="Снимок имени владельца для списка ключей.",
    )
    kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="service",
        server_default="service",
        index=True,
        doc="personal — личный ключ; service — интеграция локального владельца.",
        comment="personal|service; права ограничены доступом владельца.",
    )
    scopes: Mapped[list] = mapped_column(
        JSON,
        default=list,
        nullable=False,
        doc="Права ключа из реестра api_scopes.",
        comment="Список строк вида tasks:read. Пустой список — ключ не открывает ничего.",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Срок действия.",
        comment="NULL — бессрочный ключ.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        doc="Активен ли ключ.",
        comment="False — ключ отозван и не принимается.",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Когда ключ отозван.",
        comment="Заполняется вместе с is_active=false.",
    )
    revoked_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="Кто отозвал ключ.",
        comment="Пользователь, отозвавший ключ.",
    )
    revoked_by_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Имя отозвавшего.",
        comment="Снимок имени на момент отзыва.",
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="Кто выдал ключ.",
        comment="Пользователь, выпустивший ключ.",
    )
    created_by_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Имя выдавшего.",
        comment="Снимок имени на момент выдачи.",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Последнее успешное использование.",
        comment="Обновляется не чаще раза в несколько минут.",
    )

    def __repr__(self) -> str:
        return f"<ApiKey(id={self.id}, prefix={self.key_prefix!r}, label={self.label!r})>"
