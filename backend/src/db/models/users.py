from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from .api_tokens import ApiToken
    from .project_members import ProjectMember
    from .projects import Project


class User(Base, TimestampMixin):
    """Пользователь платформы.

    Вход выполняется по логину: подтверждения почты в текущем контуре нет,
    поэтому email остаётся необязательным контактом в профиле.
    """

    __tablename__ = "users"

    github_token_ciphertext: Mapped[str | None] = mapped_column(Text, deferred=True)
    github_account_id: Mapped[int | None] = mapped_column(BigInteger)
    github_login: Mapped[str | None] = mapped_column(String(255))

    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Идентификатор пользователя.",
        comment="Уникальный идентификатор пользователя.",
    )
    username: Mapped[str] = mapped_column(
        String(length=50),
        unique=True,
        nullable=False,
        index=True,
        doc="Логин для входа.",
        comment="Уникальный логин пользователя.",
    )
    password_hash: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        doc="Bcrypt-хеш пароля.",
        comment="Хеш пароля пользователя.",
    )
    last_name: Mapped[str] = mapped_column(
        String(length=100),
        nullable=False,
        doc="Фамилия.",
        comment="Фамилия пользователя.",
    )
    first_name: Mapped[str] = mapped_column(
        String(length=100),
        nullable=False,
        doc="Имя.",
        comment="Имя пользователя.",
    )
    middle_name: Mapped[str | None] = mapped_column(
        String(length=100),
        nullable=True,
        doc="Отчество. Необязательно: есть его нет у многих.",
        comment="Отчество пользователя; может отсутствовать.",
    )
    email: Mapped[str | None] = mapped_column(
        String(length=255),
        nullable=True,
        doc="Электронная почта как контакт, не как идентификатор входа.",
        comment="Необязательная электронная почта пользователя.",
    )
    phone: Mapped[str | None] = mapped_column(
        String(length=32),
        nullable=True,
        doc="Телефон.",
        comment="Необязательный телефон пользователя.",
    )
    telegram: Mapped[str | None] = mapped_column(
        String(length=64),
        nullable=True,
        doc="Ник в Telegram.",
        comment="Необязательный контакт в Telegram.",
    )
    avatar_key: Mapped[str | None] = mapped_column(
        String(length=255),
        nullable=True,
        doc="Относительный ключ файла фотографии в локальном хранилище.",
        comment="Относительный путь фотографии внутри каталога uploads.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        doc="Признак активной учётной записи.",
        comment="Заблокированный пользователь не может войти.",
    )

    owned_projects: Mapped[list[Project]] = relationship(
        "Project",
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    memberships: Mapped[list[ProjectMember]] = relationship(
        "ProjectMember",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    api_tokens: Mapped[list[ApiToken]] = relationship(
        "ApiToken",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username!r})>"
