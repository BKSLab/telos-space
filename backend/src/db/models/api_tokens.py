from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .users import User


class ApiTokenScope(str, enum.Enum):
    """Права токена внешнего клиента."""

    READ = "READ"
    WRITE = "WRITE"


class ApiToken(Base, TimestampMixin):
    """Токен доступа внешнего клиента, прежде всего MCP.

    Секрет хранится только как SHA-256: восстановить его из базы нельзя,
    поэтому пользователю он показывается единственный раз при выпуске.
    ``prefix`` нужен, чтобы человек узнавал свой токен в списке.
    """

    __tablename__ = "api_tokens"
    __table_args__ = (Index("ix_api_tokens_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Идентификатор токена.",
        comment="Уникальный идентификатор токена.",
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Владелец токена.",
        comment="Пользователь, от имени которого работает токен.",
    )
    name: Mapped[str] = mapped_column(
        String(length=100),
        nullable=False,
        doc="Человекочитаемое имя токена.",
        comment="Имя, по которому владелец узнаёт токен в списке.",
    )
    token_hash: Mapped[str] = mapped_column(
        String(length=64),
        nullable=False,
        unique=True,
        index=True,
        doc="SHA-256 от секрета в шестнадцатеричном виде.",
        comment="Хеш секрета; сам секрет не хранится.",
    )
    prefix: Mapped[str] = mapped_column(
        String(length=8),
        nullable=False,
        doc="Первые восемь случайных символов после маркера токена.",
        comment="Публичная часть для узнавания токена; маркер формата не включён.",
    )
    scope: Mapped[ApiTokenScope] = mapped_column(
        Enum(ApiTokenScope, name="api_token_scope", native_enum=True),
        nullable=False,
        default=ApiTokenScope.READ,
        doc="Права токена.",
        comment="READ разрешает только чтение, WRITE — ещё и изменение.",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Момент истечения. NULL — бессрочный токен.",
        comment="Момент истечения токена; NULL означает отсутствие срока.",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Момент отзыва. Отозванный токен не восстанавливается.",
        comment="Момент отзыва токена владельцем.",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Последнее использование; обновляется не на каждый запрос.",
        comment="Приблизительное время последнего использования токена.",
    )

    user: Mapped[User] = relationship("User", back_populates="api_tokens")

    def __repr__(self) -> str:
        return f"<ApiToken(id={self.id}, user_id={self.user_id}, name={self.name!r})>"
