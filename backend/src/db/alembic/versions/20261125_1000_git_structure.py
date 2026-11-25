"""Версионированная структура Git. Существующие снимки и связи сохраняются."""

import sqlalchemy as sa
from alembic import op

revision = "c34a202611250001"
down_revision = "c33a202611240001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE git_analyses (
	id SERIAL NOT NULL,
	snapshot_id INTEGER NOT NULL,
	version VARCHAR(100) NOT NULL,
	state VARCHAR(16) DEFAULT 'extracting' NOT NULL,
	cursor INTEGER DEFAULT '0' NOT NULL,
	coverage JSONB DEFAULT '{}' NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_git_analysis_version UNIQUE (snapshot_id, version),
	CONSTRAINT uq_git_analysis_snapshot UNIQUE (id, snapshot_id),
	CONSTRAINT ck_git_analysis_state CHECK (state IN ('extracting','resolving','published')),
	FOREIGN KEY(snapshot_id) REFERENCES git_snapshots (id) ON DELETE CASCADE
)
""")
    op.execute("""
CREATE TABLE git_symbols (
	id SERIAL NOT NULL,
	analysis_id INTEGER NOT NULL,
	snapshot_id INTEGER NOT NULL,
	path TEXT NOT NULL,
	key_hash VARCHAR(64) NOT NULL,
	parent_id INTEGER,
	name TEXT NOT NULL,
	qualified_name TEXT NOT NULL,
	search_name VARCHAR(256) NOT NULL,
	kind VARCHAR(50) NOT NULL,
	signature TEXT,
	line_start INTEGER NOT NULL,
	line_end INTEGER NOT NULL,
	"offset" INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(analysis_id, snapshot_id) REFERENCES git_analyses (id, snapshot_id) ON DELETE CASCADE,
	FOREIGN KEY(snapshot_id, path) REFERENCES git_files (snapshot_id, path) ON DELETE CASCADE,
	FOREIGN KEY(analysis_id, parent_id) REFERENCES git_symbols (analysis_id, id),
	CONSTRAINT uq_git_symbol_analysis UNIQUE (analysis_id, id),
	CONSTRAINT uq_git_symbol_key UNIQUE (analysis_id, path, key_hash),
	CONSTRAINT ck_git_symbol_range CHECK (line_start >= 1 AND line_end >= line_start AND "offset" >= 0 AND end_offset >= "offset")
)
""")
    op.execute("""
CREATE INDEX ix_git_symbol_qualified_name ON git_symbols USING hash (qualified_name)
""")
    op.execute("""
CREATE INDEX ix_git_symbol_normalized ON git_symbols (analysis_id, search_name)
""")
    op.execute("""
CREATE TABLE git_relations (
	id SERIAL NOT NULL,
	analysis_id INTEGER NOT NULL,
	source_id INTEGER NOT NULL,
	target_id INTEGER,
	kind VARCHAR(50) NOT NULL,
	confidence VARCHAR(16) NOT NULL,
	label TEXT NOT NULL,
	line_start INTEGER NOT NULL,
	line_end INTEGER NOT NULL,
	candidates JSONB DEFAULT '[]' NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(analysis_id, source_id) REFERENCES git_symbols (analysis_id, id) ON DELETE CASCADE,
	FOREIGN KEY(analysis_id, target_id) REFERENCES git_symbols (analysis_id, id) ON DELETE CASCADE,
	CONSTRAINT ck_git_relation_confidence CHECK (confidence IN ('declared','resolved','candidate','unresolved')),
	CONSTRAINT ck_git_relation_target CHECK (target_id IS NOT NULL OR confidence IN ('unresolved','candidate','declared')),
	CONSTRAINT ck_git_relation_range CHECK (line_start >= 1 AND line_end >= line_start)
)
""")
    op.execute("""
CREATE INDEX ix_git_relation_source ON git_relations (analysis_id, source_id, id)
""")
    op.execute("""
CREATE INDEX ix_git_relation_target ON git_relations (analysis_id, target_id, id)
""")
    op.execute("COMMENT ON COLUMN git_analyses.id IS 'Поколение структуры.'")
    op.execute("COMMENT ON COLUMN git_analyses.snapshot_id IS 'Исходный снимок.'")
    op.execute("COMMENT ON COLUMN git_analyses.version IS 'Версия детерминированного анализатора.'")
    op.execute("COMMENT ON COLUMN git_analyses.state IS 'Текущая фаза.'")
    op.execute("COMMENT ON COLUMN git_analyses.cursor IS 'Последний обработанный ID в текущей фазе.'")
    op.execute("COMMENT ON COLUMN git_analyses.coverage IS 'Счётчики подготовки структуры.'")
    op.execute("COMMENT ON COLUMN git_symbols.id IS 'ID объявления.'")
    op.execute("COMMENT ON COLUMN git_symbols.analysis_id IS 'Поколение анализатора.'")
    op.execute("COMMENT ON COLUMN git_symbols.snapshot_id IS 'Снимок исходника.'")
    op.execute("COMMENT ON COLUMN git_symbols.path IS 'Путь в снимке.'")
    op.execute("COMMENT ON COLUMN git_symbols.key_hash IS 'SHA-256 локального ключа объявления.'")
    op.execute("COMMENT ON COLUMN git_symbols.parent_id IS 'Внешнее объявление в этом поколении.'")
    op.execute("COMMENT ON COLUMN git_symbols.name IS 'Короткое имя.'")
    op.execute("COMMENT ON COLUMN git_symbols.qualified_name IS 'Имя в контексте файла.'")
    op.execute("COMMENT ON COLUMN git_symbols.search_name IS 'Нормализованное имя для поиска.'")
    op.execute("COMMENT ON COLUMN git_symbols.kind IS 'Вид объявления.'")
    op.execute("COMMENT ON COLUMN git_symbols.signature IS 'Сигнатура из исходника.'")
    op.execute("COMMENT ON COLUMN git_symbols.line_start IS 'Начальная строка включительно.'")
    op.execute("COMMENT ON COLUMN git_symbols.line_end IS 'Последняя строка включительно.'")
    op.execute('COMMENT ON COLUMN git_symbols."offset" IS \'Смещение в Unicode символах.\'')
    op.execute("COMMENT ON COLUMN git_symbols.end_offset IS 'Конец диапазона, исключая границу.'")
    op.execute("COMMENT ON COLUMN git_relations.id IS 'ID связи.'")
    op.execute("COMMENT ON COLUMN git_relations.analysis_id IS 'Поколение концов связи.'")
    op.execute("COMMENT ON COLUMN git_relations.source_id IS 'Потребитель или родитель.'")
    op.execute("COMMENT ON COLUMN git_relations.target_id IS 'Подтверждённая цель или кандидат.'")
    op.execute("COMMENT ON COLUMN git_relations.kind IS 'Тип связи.'")
    op.execute("COMMENT ON COLUMN git_relations.confidence IS 'Уровень доказательства.'")
    op.execute("COMMENT ON COLUMN git_relations.label IS 'Имя из исходника.'")
    op.execute("COMMENT ON COLUMN git_relations.line_start IS 'Строка основания.'")
    op.execute("COMMENT ON COLUMN git_relations.line_end IS 'Последняя строка основания.'")
    op.execute("COMMENT ON COLUMN git_relations.candidates IS 'Точные относительные пути возможных целей.'")


    op.add_column("git_relations", sa.Column("imported_name", sa.Text(), comment="Исходное имя импортированного объявления."))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM git_analyses)")):
        raise RuntimeError("Существующие поколения структуры требуют восстановления из резервной копии.")
    op.drop_table("git_relations")
    op.drop_table("git_symbols")
    op.drop_table("git_analyses")
