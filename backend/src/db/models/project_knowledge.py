"""Восстанавливаемая карта поверх существующего каталога: версии и доказательства."""

from datetime import datetime
from uuid import UUID, uuid4

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
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ProjectKnowledgeState(Base):
    """Монотонное поколение исходных изменений, не зависящее от хэша A→B→A."""

    __tablename__ = "project_knowledge_states"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Проект.",
        comment="Область доступа карты.",
    )
    generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Поколение изменений.",
        comment="Меняется в исходной транзакции, один раз на проект и txid.",
    )
    last_transaction_id: Mapped[int | None] = mapped_column(
        BigInteger,
        doc="Квитанция транзакции.",
        comment="Дедупликация поколения для нескольких строк одной транзакции.",
    )
    built_generation: Mapped[int | None] = mapped_column(
        BigInteger,
        doc="Опубликованное поколение.",
        comment="Только успешно проверенный входной набор.",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="empty",
        server_default="empty",
        doc="Обработка.",
        comment="empty/pending/updating/ready/partial/failed.",
    )
    manifest_id: Mapped[UUID | None] = mapped_column(
        Uuid, doc="Текущий manifest.", comment="ID входного набора без копии первичных материалов."
    )
    coverage: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        doc="Охват.",
        comment="Объявленный охват и причины неполноты.",
    )
    gaps: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        doc="Пробелы.",
        comment="Без фиктивных отрицательных фактов о продукте.",
    )
    metrics: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        doc="Измерения.",
        comment="Фактические объёмы, переиспользование, длительность и usage map-stage.",
    )
    last_error: Mapped[str | None] = mapped_column(
        Text, doc="Ошибка.", comment="Безопасная диагностика, без source payload или credentials."
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Изменение состояния.",
        comment="Фактическая дата изменения карты.",
    )


class ProjectKnowledgeSource(Base):
    """Поколение источника меняется transactional trigger до работы worker."""

    __tablename__ = "project_knowledge_sources"
    __table_args__ = (
        Index(
            "ix_project_knowledge_sources_pending",
            "project_id",
            "generation",
            "processed_generation",
        ),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Проект.",
        comment="Источник не переносит права между проектами.",
    )
    source_id: Mapped[str] = mapped_column(
        String(256),
        primary_key=True,
        doc="ID источника.",
        comment="Канонический ID существующего каталога.",
    )
    generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Поколение.",
        comment="Отличает возврат к тому же тексту от прежней версии.",
    )
    last_transaction_id: Mapped[int | None] = mapped_column(
        BigInteger,
        doc="Транзакция поколения.",
        comment="Повтор одного события в транзакции не создаёт дубль.",
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
        doc="Удалён.",
        comment="Tombstone закрывает чтение до фоновой очистки.",
    )
    processed_generation: Mapped[int | None] = mapped_column(
        BigInteger,
        doc="Обработанное поколение.",
        comment="Не подменяет текущую версию при задержке worker.",
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        doc="Обработанная версия.",
        comment="Ссылка на metadata версии, проверяемая вместе с generation.",
    )
    projection_hash: Mapped[str | None] = mapped_column(
        String(64),
        doc="Отпечаток проекции.",
        comment="Переиспользование неизменного точного содержимого и зависимостей.",
    )
    processed_chars: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Checkpoint.",
        comment="Следующее Unicode-смещение текущей версии.",
    )
    total_chars: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Полный объём.",
        comment="Размер объявленного текстового представления.",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
        doc="Обработка источника.",
        comment="pending/partial/ready/skipped/failed.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Изменение.",
        comment="Время последнего изменения source head.",
    )


class ProjectKnowledgeSourceVersion(Base):
    """Metadata версии; полное исходное содержимое остаётся в первичном каталоге."""

    __tablename__ = "project_knowledge_source_versions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "source_id",
            "generation",
            "identity_hash",
            name="uq_project_knowledge_source_generation",
        ),
        UniqueConstraint("id", "project_id", name="uq_project_knowledge_version_project"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        doc="Версия.",
        comment="Непрозрачный ID наблюдённой версии.",
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Проект.",
        comment="Текущая граница доступа.",
    )
    source_id: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        doc="Источник.",
        comment="Каноническая ссылка существующего каталога.",
    )
    generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, doc="Поколение.", comment="Поколение источника при наблюдении."
    )
    identity_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Идентичность входов.",
        comment="Текст и поколения зависимостей различают изменение контекста без подмены прежнего snapshot.",
    )
    native_revision: Mapped[str | None] = mapped_column(
        String(128),
        doc="Ревизия оригинала.",
        comment="context_revision/version/revision, если первичный источник имеет её.",
    )
    source_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Хэш снимка.",
        comment="Наблюдённый отпечаток источника существующего каталога.",
    )
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Хэш текста.",
        comment="SHA-256 полного текстового представления UTF-8.",
    )
    representation: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Представление.",
        comment="markdown/extracted_text/catalog_text_v2; диапазоны заданы в этом тексте.",
    )
    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Название.",
        comment="Не выдаётся после изменения/удаления/потери доступа к версии.",
    )
    source_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Тип.",
        comment="Тип первичного источника без служебных источников и переписки.",
    )
    document_ref: Mapped[str | None] = mapped_column(
        String(256), doc="Документ.", comment="Стабильный document_ref без копии документа."
    )
    task_id: Mapped[int | None] = mapped_column(
        Integer, doc="Связанная задача.", comment="Серверная принадлежность, не расширяющая доступ."
    )
    dependencies: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        doc="Зависимости версии.",
        comment="Поколения источников, влияющих на применимость.",
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Наблюдение.",
        comment="Не вымышленная дата создания исторического материала.",
    )


class ProjectKnowledgeManifest(Base):
    """Проверенный набор версий и измерения одной фоновой попытки."""

    __tablename__ = "project_knowledge_manifests"

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        doc="Manifest.",
        comment="Идентичность попытки построения производной карты.",
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Проект.",
        comment="Проект входного набора.",
    )
    generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="Входное поколение.",
        comment="CAS не позволяет старому worker заменить новую карту.",
    )
    sources: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        doc="Версии входов.",
        comment="ID/поколения без копии исходных текстов.",
    )
    complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        doc="Охват завершён.",
        comment="Готовность именно заявленного текстового метода.",
    )
    metrics: Mapped[dict] = mapped_column(
        JSONB, nullable=False, doc="Измерения.", comment="Фактическая попытка и переиспользование."
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Фиксация.",
        comment="Дата проверенной публикации manifest.",
    )


class ProjectKnowledgeClaim(Base):
    """Стабильная идентичность утверждения, отдельно от его заменяемых версий."""

    __tablename__ = "project_knowledge_claims"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_id", "stable_key", name="uq_project_knowledge_claim_key"
        ),
        UniqueConstraint("id", "project_id", name="uq_project_knowledge_claim_project"),
        Index("ix_project_knowledge_claims_page", "project_id", "category", "state"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        doc="Утверждение.",
        comment="Детерминированный ID по проекту, источнику и предметному ключу.",
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        doc="Проект.",
        comment="Область доступа утверждения.",
    )
    source_id: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        doc="Основной источник.",
        comment="Не меняется при следующей версии того же утверждения.",
    )
    stable_key: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        doc="Предметный ключ.",
        comment="Поле паспорта или детерминированное место точного фрагмента.",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Текущая ревизия.",
        comment="Актуальная запись append-only истории.",
    )
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="Категория.", comment="Раздел карты, не изменение паспорта."
    )
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        doc="Состояние.",
        comment="Текст всё равно проверяется по текущим source generations при чтении.",
    )
    fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Отпечаток результата.",
        comment="Повтор не добавляет дубль той же версии.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Фиксация.",
        comment="Время последней содержательной версии.",
    )


class ProjectKnowledgeClaimRevision(Base):
    """История текста/происхождения; старый пересказ не выдаётся по старому evidence."""

    __tablename__ = "project_knowledge_claim_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["claim_id", "project_id"],
            ["project_knowledge_claims.id", "project_knowledge_claims.project_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "claim_id", "revision", "project_id", name="uq_project_knowledge_revision_project"
        ),
    )

    claim_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_knowledge_claims.id", ondelete="CASCADE"),
        primary_key=True,
        doc="Утверждение.",
        comment="Стабильная идентичность утверждения.",
    )
    revision: Mapped[int] = mapped_column(
        Integer, primary_key=True, doc="Ревизия.", comment="Монотонная версия утверждения."
    )
    project_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Проект версии.",
        comment="Составной FK исключает смешение проектов.",
    )
    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Текст.",
        comment="Выдаётся только при текущем доступном evidence.",
    )
    kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc="Вид.",
        comment="Требование, факт источника, вывод, предположение или вопрос.",
    )
    acceptance: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        doc="Принятие.",
        comment="LLM/документ не становятся авторским требованием автоматически.",
    )
    verification: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc="Проверка.",
        comment="participant_report/status_only не означают поведение продукта или prod.",
    )
    provenance: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        doc="Происхождение.",
        comment="Автор, метод, источник и предел подтверждения.",
    )
    subject: Mapped[str | None] = mapped_column(
        String(256),
        doc="Явный предмет.",
        comment="Для ограниченного сопоставления явных ключей, без вывода скрытой цели.",
    )
    value_hash: Mapped[str | None] = mapped_column(
        String(64),
        doc="Явное значение.",
        comment="Разошедшиеся значения показываются как требующие проверки.",
    )
    conflicts_with: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        doc="Расхождения.",
        comment="ID других текущих утверждений по явному предмету.",
    )
    replaced_by: Mapped[int | None] = mapped_column(
        Integer,
        doc="Заменено.",
        comment="Следующая ревизия; исторический текст сам по себе не доступен.",
    )
    reason: Mapped[str | None] = mapped_column(
        Text, doc="Причина.", comment="Устаревание/замена/неполнота."
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Фиксация.",
        comment="Фактическое время этой версии.",
    )


class ProjectKnowledgeEvidence(Base):
    """Одна версия источника и проверенный диапазон/поле для версии утверждения."""

    __tablename__ = "project_knowledge_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["claim_id", "claim_revision", "project_id"],
            [
                "project_knowledge_claim_revisions.claim_id",
                "project_knowledge_claim_revisions.revision",
                "project_knowledge_claim_revisions.project_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_version_id", "project_id"],
            [
                "project_knowledge_source_versions.id",
                "project_knowledge_source_versions.project_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            '(start IS NULL AND "end" IS NULL) OR (start >= 0 AND "end" > start)',
            name="ck_project_knowledge_evidence_range",
        ),
        Index("ix_project_knowledge_evidence_source", "source_version_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        doc="Evidence.",
        comment="Проверенный фрагмент или структурированное поле.",
    )
    claim_id: Mapped[UUID] = mapped_column(
        Uuid, nullable=False, doc="Утверждение.", comment="Ссылка вместе с его ревизией."
    )
    claim_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Ревизия утверждения.",
        comment="Evidence не переносится на новый текст без проверки.",
    )
    project_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Проект доказательства.",
        comment="Составные FK не позволяют связать утверждение с версией другого проекта.",
    )
    source_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_knowledge_source_versions.id", ondelete="CASCADE"),
        nullable=False,
        doc="Версия источника.",
        comment="Точный проверенный source snapshot.",
    )
    quote: Mapped[str | None] = mapped_column(
        Text, doc="Цитата.", comment="Дословный Unicode-фрагмент, null для структурированного поля."
    )
    start: Mapped[int | None] = mapped_column(
        Integer, doc="Начало.", comment="Unicode-смещение в сохранённом представлении."
    )
    end: Mapped[int | None] = mapped_column(
        Integer, doc="Конец.", comment="Не включительно, в том же представлении."
    )
    field_path: Mapped[str | None] = mapped_column(
        String(256), doc="Структурное поле.", comment="Не подменяется цитатой из названия стадии."
    )
    field_value: Mapped[dict | list | str | int | bool | None] = mapped_column(
        JSONB, doc="Значение.", comment="Проверенное допустимое значение публичного поля."
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Проверено.",
        comment="Проверка выполнена сервером в опубликованной версии.",
    )
