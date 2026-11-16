"""Версионные Git-источники, их очередь и доказательства; credentials только encrypted."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class GitConnection(Base, TimestampMixin):
    """Одна независимая project→repository/ref привязка и текущая generation."""

    __tablename__ = "git_connections"
    __table_args__ = (
        CheckConstraint(
            "revision > 0 AND generation > 0 AND credential_revision >= 0",
            name="ck_git_connection_revisions",
        ),
        CheckConstraint(
            "state IN ('pending','syncing','ready','partial','error','disabled','access_lost')",
            name="ck_git_connection_state",
        ),
        Index("ix_git_connections_due", "enabled", "next_poll_at"),
        Index("ix_git_connections_project", "project_id", "id"),
    )
    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="Локальный ID подключения.",
        comment="ID подключения, ограниченный проектным ACL.",
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        doc="Проект.",
        comment="Владелец корпуса Git в TELOS.",
    )
    repository_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="Стабильный ID GitHub repository.",
        comment="Не заменяется owner/name при проверке identity.",
    )
    owner: Mapped[str] = mapped_column(
        String(39),
        nullable=False,
        doc="Login GitHub.",
        comment="Канонический владелец upstream repository.",
    )
    repo: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Имя GitHub repository.",
        comment="Каноническое имя без URL.",
    )
    name: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        doc="Имя подключения.",
        comment="Человекочитаемое назначение источника.",
    )
    purpose: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        server_default="",
        doc="Назначение.",
        comment="Явное описание владельца, не вывод из кода.",
    )
    ref: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        doc="Выбранный ref.",
        comment="heads/branch, tags/tag или полный SHA.",
    )
    include_paths: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        doc="Разрешённые корни.",
        comment="Пустая строка в списке означает корень Git.",
    )
    exclude_patterns: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
        doc="Дополнительные исключения.",
        comment="Обязательная политика безопасности применяется сверх них.",
    )
    scope_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="SHA-256 политики области.",
        comment="Разделяет snapshots одного SHA с разными разрешёнными путями.",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        doc="Источник включён.",
        comment="Отключение немедленно закрывает выдачу, сохраняя историю.",
    )
    is_private: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        doc="Upstream privacy.",
        comment="Проверенная приватность, не расширение локальных прав.",
    )
    share_private_with_project: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        doc="Согласие на общий закрытый корпус.",
        comment="Явное разрешение текущего владельца проекта.",
    )
    credential_ciphertext: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Encrypted credential.",
        comment="Fernet ciphertext; не выдаётся в DTO/логи.",
    )
    webhook_secret_ciphertext: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Encrypted webhook secret.",
        comment="Отдельный секрет HMAC входящих событий.",
    )
    credential_owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="Локальный владелец credential.",
        comment="После потери владения привязка требует повторного предоставления.",
    )
    credential_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc="Версия credential binding.",
        comment="Ротация/отзыв инвалидирует прочитанные доказательства.",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        doc="Версия настройки.",
        comment="Optimistic concurrency управления подключением.",
    )
    generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        doc="Монотонное поколение.",
        comment="Различает A→B→A и запоздавшие публикации.",
    )
    observation_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc="CAS порядка внешних проверок.",
        comment="Даже unchanged ref закрывает запоздавший ответ более раннего чтения.",
    )
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        doc="Наблюдаемое состояние.",
        comment="Готовность и доступность только данного подключения.",
    )
    observed_sha: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        doc="Последний наблюдаемый SHA.",
        comment="Authoritative selected ref, независимо от публикации индекса.",
    )
    indexed_sha: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        doc="Последний опубликованный SHA.",
        comment="Завершённый manifest разрешённой области.",
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Время наблюдения ref.",
        comment="UTC последней смены наблюдаемой версии.",
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Время публикации manifest.",
        comment="UTC успешного CAS завершения поколения.",
    )
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Время проверки доступа/ref.",
        comment="Не является доказательством текущего доступа без новой проверки.",
    )
    next_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Следующая сверка.",
        comment="Ограниченная очередь polling/reconciliation.",
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        doc="Безопасная категория ошибки.",
        comment="Никакого upstream body или текста исключения.",
    )
    coverage: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        doc="Охват версии.",
        comment="Счётчики и причины неполноты, без содержимого файлов.",
    )
    lease_id: Mapped[UUID | None] = mapped_column(
        nullable=True,
        doc="Квитанция текущего worker.",
        comment="CAS исключает публикацию потерявшего lease worker.",
    )
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Срок lease.",
        comment="Восстановление обработки после crash без секретов в job.",
    )
    work_phase: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="index",
        server_default="index",
        doc="index/evidence.",
        comment="Чередует ограниченные пачки без голодания CI/deploy.",
    )


class GitSnapshot(Base, TimestampMixin):
    """Неизменяемая версия разрешённой области конкретного repository SHA."""

    __tablename__ = "git_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "commit_sha", "scope_hash", name="uq_git_snapshot_identity"
        ),
        UniqueConstraint("id", "connection_id", name="uq_git_snapshot_connection"),
    )
    id: Mapped[int] = mapped_column(
        primary_key=True, doc="ID snapshot.", comment="Локальная неизменяемая версия Git."
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("git_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Подключение.",
        comment="Snapshot не переносится между проектами.",
    )
    commit_sha: Mapped[str] = mapped_column(
        String(40), nullable=False, doc="Полный SHA commit.", comment="Immutable upstream identity."
    )
    tree_sha: Mapped[str] = mapped_column(
        String(40), nullable=False, doc="SHA корневого дерева.", comment="Дерево того же commit."
    )
    scope_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Хэш выбранной области.",
        comment="Изменение фильтров создаёт отдельный manifest.",
    )
    manifest: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
        doc="Ограниченный manifest дерева.",
        comment="Только Git metadata; секретный контент отсутствует.",
    )
    tree_loaded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        doc="Дерево прочитано.",
        comment="Отличает пустой репозиторий от ещё не полученного manifest.",
    )
    tree_truncated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        doc="Неполное дерево.",
        comment="Не разрешает удалять прежние неувиденные файлы.",
    )
    complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        doc="Разрешённая область завершена.",
        comment="Публикация требует полного дерева и отсутствия ошибок чтения.",
    )
    coverage: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        doc="Охват snapshot.",
        comment="Фактические indexed/excluded/failed/truncated.",
    )


class GitFile(Base, TimestampMixin):
    """Файл конкретного snapshot; содержимое после записи не меняется."""

    __tablename__ = "git_files"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "path", name="uq_git_file_snapshot_path"),
        Index("ix_git_file_blob", "connection_id", "blob_sha"),
        ForeignKeyConstraint(
            ["snapshot_id", "connection_id"],
            ["git_snapshots.id", "git_snapshots.connection_id"],
            ondelete="CASCADE",
            name="fk_git_file_snapshot_connection",
        ),
    )
    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="ID файла.",
        comment="entity_id и git_file:<id> для безопасной ссылки.",
    )
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        doc="Snapshot.",
        comment="Версия commit и области той же connection.",
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("git_connections.id", ondelete="CASCADE"),
        nullable=False,
        doc="Подключение.",
        comment="Для reuse проверенного blob внутри одной привязки.",
    )
    path: Mapped[str] = mapped_column(
        String(4096),
        nullable=False,
        doc="Точный POSIX путь.",
        comment="Без серверного файлового пути и Unicode нормализации.",
    )
    blob_sha: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        doc="SHA blob.",
        comment="Клиент проверяет SHA по фактическим байтам.",
    )
    size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Число исходных байтов.",
        comment="Лимит до декодирования/индексации.",
    )
    state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        doc="ready/excluded/error.",
        comment="Исключение не является успешно прочитанным текстом.",
    )
    reason: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        doc="Безопасная причина исключения.",
        comment="Категория без содержимого или секрета.",
    )
    text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Проверенный UTF-8 текст.",
        comment="Только разрешённый, отфильтрованный статический исходник.",
    )
    text_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="SHA-256 полного текста.",
        comment="Повторная проверка evidence и фрагментов.",
    )
    analysis: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        doc="Статическая структура файла.",
        comment="Символы/imports с координатами, без исполнения кода.",
    )


class GitFragment(Base):
    """Bounded фрагмент текста с PostgreSQL FTS и точными координатами."""

    __tablename__ = "git_fragments"
    __table_args__ = (
        UniqueConstraint("file_id", "ordinal", name="uq_git_fragment_ordinal"),
        Index("ix_git_fragment_fts", "search_vector", postgresql_using="gin"),
    )
    id: Mapped[int] = mapped_column(
        primary_key=True, doc="ID фрагмента.", comment="Внутренний поисковый фрагмент."
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("git_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Файл версии.",
        comment="Immutable owner фрагмента.",
    )
    ordinal: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Номер фрагмента.",
        comment="Стабильная последовательность в файле.",
    )
    offset: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="Unicode offset.", comment="Начало в полном тексте."
    )
    line_start: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="Начальная строка.", comment="Нумерация строк от единицы."
    )
    line_end: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Конечная строка.",
        comment="Диапазон точного текста фрагмента.",
    )
    text: Mapped[str] = mapped_column(
        Text, nullable=False, doc="Текст фрагмента.", comment="Не более 8000 Unicode code points."
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        nullable=False,
        doc="FTS simple.",
        comment="Вектор строится SQL при вставке, без внешнего LLM.",
    )


class GitSyncJob(Base, TimestampMixin):
    """Durable checkpoint поколения, без credential и snapshot прав в payload."""

    __tablename__ = "git_sync_jobs"
    __table_args__ = (
        UniqueConstraint("connection_id", "generation", name="uq_git_job_generation"),
        ForeignKeyConstraint(
            ["snapshot_id", "connection_id"],
            ["git_snapshots.id", "git_snapshots.connection_id"],
            ondelete="CASCADE",
            name="fk_git_job_snapshot_connection",
        ),
    )
    id: Mapped[int] = mapped_column(
        primary_key=True, doc="ID задания.", comment="Durable работа над одним поколением."
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("git_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Подключение.",
        comment="Не проектный общий SHA.",
    )
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Целевой snapshot.",
        comment="Immutable commit и scope hash той же connection.",
    )
    generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Ожидаемое поколение.",
        comment="Старое задание не публикуется в новое поколение.",
    )
    credential_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="Версия credential binding.", comment="Не сам credential."
    )
    cursor: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc="Checkpoint manifest.",
        comment="Продолжение после crash/лимита без повторной загрузки готовых файлов.",
    )
    bytes_processed: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
        doc="Фактический объём.",
        comment="Предел объёма snapshot.",
    )
    state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
        doc="pending/complete/partial/superseded/error.",
        comment="Не считает незавершённую работу готовой.",
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        doc="Безопасная категория ошибки.",
        comment="Без upstream тела и credentials.",
    )


class GitDelivery(Base):
    """Подтверждённая HMAC квитанция, не исходный webhook payload."""

    __tablename__ = "git_deliveries"
    __table_args__ = (UniqueConstraint("connection_id", "delivery_id", name="uq_git_delivery"),)
    id: Mapped[int] = mapped_column(
        primary_key=True, doc="ID квитанции.", comment="Внутренний ID события."
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("git_connections.id", ondelete="CASCADE"),
        nullable=False,
        doc="Подключение.",
        comment="Repository binding проверен сервисом.",
    )
    delivery_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        doc="Upstream delivery ID.",
        comment="Дедупликация повторной доставки.",
    )
    payload_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="SHA-256 payload.",
        comment="Не допускает другой payload с той же квитанцией.",
    )
    event_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        doc="Тип события.",
        comment="Push/PR/check/deployment ставит сверку, не меняет observed SHA напрямую.",
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Время приёма.",
        comment="Не определяет порядок Git версий.",
    )


class GitChange(Base, TimestampMixin):
    """Постоянная identity PR или commit независимо от удаления ветки."""

    __tablename__ = "git_changes"
    __table_args__ = (
        UniqueConstraint("connection_id", "kind", "provider_id", name="uq_git_change_identity"),
    )
    id: Mapped[int] = mapped_column(
        primary_key=True,
        doc="ID изменения.",
        comment="Один PR/commit может относиться к нескольким задачам.",
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("git_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Подключение.",
        comment="Repository identity всегда сохраняется.",
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, doc="commit/pull_request.", comment="Тип постоянного изменения."
    )
    provider_id: Mapped[str] = mapped_column(
        String(80), nullable=False, doc="SHA или номер PR.", comment="Ветка не является identity."
    )
    title: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        doc="Безопасная подпись.",
        comment="Проверенная, ограниченная и отфильтрованная от секретов подпись.",
    )
    head_sha: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        doc="Точный SHA изменения.",
        comment="CI и deploy другого SHA не подтверждают эту версию.",
    )
    merge_sha: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
        doc="Подтверждённый merge SHA.",
        comment="Не равнозначен выкладке.",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        doc="committed/open/closed/merged.",
        comment="Предложение и реализация различаются.",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        doc="Версия наблюдаемого состояния.",
        comment="Защищает от устаревшего ответа Ариса.",
    )
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Время authoritative проверки.",
        comment="Не заменяет текущую проверку доступа.",
    )
    evidence: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
        doc="Последние проверенные CI/deploy факты.",
        comment="Каждый содержит repository/SHA/environment/provider identity.",
    )
    evidence_partial: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        doc="Неполнота доказательств.",
        comment="Недоступность CI не означает успешных проверок.",
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        doc="Безопасная ошибка доказательств.",
        comment="Отдельна от состояния читаемости кода.",
    )
    refresh_state: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        doc="Checkpoint доказательств.",
        comment="Только проверенные DTO stages, не credentials и не upstream body.",
    )
    next_refresh_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Следующая проверка фактов.",
        comment="Поздние merge/check/deploy читаются независимо от стадии задачи.",
    )


class GitTaskChangeLink(Base, TimestampMixin):
    """N:N задача↔изменение; удаление ветки не удаляет связь."""

    __tablename__ = "git_task_change_links"
    __table_args__ = (UniqueConstraint("task_id", "change_id", name="uq_git_task_change_link"),)
    id: Mapped[int] = mapped_column(
        primary_key=True, doc="ID связи.", comment="Стабильная ручная привязка."
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Задача текущего проекта.",
        comment="Сервис блокирует задачу и проверяет project identity.",
    )
    change_id: Mapped[int] = mapped_column(
        ForeignKey("git_changes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Изменение.",
        comment="Одна задача может иметь несколько изменений в нескольких repo.",
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="Серверный автор связи.",
        comment="Не принимается из model/UI arguments.",
    )
