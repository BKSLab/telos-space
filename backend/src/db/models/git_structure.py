"""Производные структуры Git, версионированные независимо от исходного текста."""

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class GitAnalysis(Base):
    """Checkpoint публикации одной версии анализатора для одного снимка."""

    __tablename__ = "git_analyses"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "version", name="uq_git_analysis_version"),
        UniqueConstraint("id", "snapshot_id", name="uq_git_analysis_snapshot"),
        CheckConstraint(
            "state IN ('extracting','resolving','published')", name="ck_git_analysis_state"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, comment="Поколение структуры.")
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("git_snapshots.id", ondelete="CASCADE"), comment="Исходный снимок."
    )
    version: Mapped[str] = mapped_column(
        String(100), comment="Версия детерминированного анализатора."
    )
    state: Mapped[str] = mapped_column(
        String(16), server_default="extracting", comment="Текущая фаза."
    )
    cursor: Mapped[int] = mapped_column(
        Integer, server_default="0", comment="Последний обработанный ID в текущей фазе."
    )
    coverage: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", comment="Счётчики подготовки структуры."
    )


class GitSymbol(Base):
    """Объявление с координатами оригинального исходника."""

    __tablename__ = "git_symbols"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "snapshot_id"],
            ["git_analyses.id", "git_analyses.snapshot_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "path"], ["git_files.snapshot_id", "git_files.path"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["analysis_id", "parent_id"], ["git_symbols.analysis_id", "git_symbols.id"]
        ),
        UniqueConstraint("analysis_id", "id", name="uq_git_symbol_analysis"),
        UniqueConstraint("analysis_id", "path", "key_hash", name="uq_git_symbol_key"),
        Index("ix_git_symbol_normalized", "analysis_id", "search_name"),
        Index("ix_git_symbol_qualified_name", "qualified_name", postgresql_using="hash"),
        CheckConstraint(
            'line_start >= 1 AND line_end >= line_start AND "offset" >= 0 AND end_offset >= "offset"',
            name="ck_git_symbol_range",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, comment="ID объявления.")
    analysis_id: Mapped[int] = mapped_column(comment="Поколение анализатора.")
    snapshot_id: Mapped[int] = mapped_column(comment="Снимок исходника.")
    path: Mapped[str] = mapped_column(Text, comment="Путь в снимке.")
    key_hash: Mapped[str] = mapped_column(
        String(64), comment="SHA-256 локального ключа объявления."
    )
    parent_id: Mapped[int | None] = mapped_column(comment="Внешнее объявление в этом поколении.")
    name: Mapped[str] = mapped_column(Text, comment="Короткое имя.")
    qualified_name: Mapped[str] = mapped_column(Text, comment="Имя в контексте файла.")
    search_name: Mapped[str] = mapped_column(String(256), comment="Нормализованное имя для поиска.")
    kind: Mapped[str] = mapped_column(String(50), comment="Вид объявления.")
    signature: Mapped[str | None] = mapped_column(Text, comment="Сигнатура из исходника.")
    line_start: Mapped[int] = mapped_column(comment="Начальная строка включительно.")
    line_end: Mapped[int] = mapped_column(comment="Последняя строка включительно.")
    offset: Mapped[int] = mapped_column(comment="Смещение в Unicode символах.")
    end_offset: Mapped[int] = mapped_column(comment="Конец диапазона, исключая границу.")


class GitRelation(Base):
    """Связь и основание в том же снимке и поколении."""

    __tablename__ = "git_relations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "source_id"],
            ["git_symbols.analysis_id", "git_symbols.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["analysis_id", "target_id"],
            ["git_symbols.analysis_id", "git_symbols.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "confidence IN ('declared','resolved','candidate','unresolved')",
            name="ck_git_relation_confidence",
        ),
        CheckConstraint(
            "target_id IS NOT NULL OR confidence IN ('unresolved','candidate','declared')",
            name="ck_git_relation_target",
        ),
        CheckConstraint("line_start >= 1 AND line_end >= line_start", name="ck_git_relation_range"),
        Index("ix_git_relation_source", "analysis_id", "source_id", "id"),
        Index("ix_git_relation_target", "analysis_id", "target_id", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, comment="ID связи.")
    analysis_id: Mapped[int] = mapped_column(comment="Поколение концов связи.")
    source_id: Mapped[int] = mapped_column(comment="Потребитель или родитель.")
    target_id: Mapped[int | None] = mapped_column(comment="Подтверждённая цель или кандидат.")
    kind: Mapped[str] = mapped_column(String(50), comment="Тип связи.")
    confidence: Mapped[str] = mapped_column(String(16), comment="Уровень доказательства.")
    label: Mapped[str] = mapped_column(Text, comment="Имя из исходника.")
    line_start: Mapped[int] = mapped_column(comment="Строка основания.")
    line_end: Mapped[int] = mapped_column(comment="Последняя строка основания.")
    imported_name: Mapped[str | None] = mapped_column(
        Text, comment="Исходное имя импортированного объявления."
    )
    candidates: Mapped[list] = mapped_column(
        JSONB, server_default="[]", comment="Точные относительные пути возможных целей."
    )
