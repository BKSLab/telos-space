"""Версии источников и производные утверждения карты проекта.

Revision ID: c26a202611160001
Revises: f23a8c91d046

Schema-only. Не создаёт исторических фактов/backfill и не меняет первичные тексты.
DDL снимок зафиксирован здесь и не зависит от будущего ORM metadata.
"""

import sqlalchemy as sa
from alembic import op

from src.knowledge.project_events import install_project_knowledge_ddl

revision = "c26a202611160001"
down_revision = "f23a8c91d046"
branch_labels = None
depends_on = None

SCHEMA_SQL = (
    "CREATE TABLE project_knowledge_states (\n\tproject_id INTEGER NOT NULL, \n\tgeneration BIGINT DEFAULT 0 NOT NULL, \n\tlast_transaction_id BIGINT, \n\tbuilt_generation BIGINT, \n\tstatus VARCHAR(16) DEFAULT 'empty' NOT NULL, \n\tmanifest_id UUID, \n\tcoverage JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tgaps JSONB DEFAULT '[]'::jsonb NOT NULL, \n\tmetrics JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tlast_error TEXT, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (project_id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE\n)",
    'COMMENT ON COLUMN project_knowledge_states."project_id" IS \'Область доступа карты.\'',
    'COMMENT ON COLUMN project_knowledge_states."generation" IS \'Меняется в исходной транзакции, один раз на проект и txid.\'',
    'COMMENT ON COLUMN project_knowledge_states."last_transaction_id" IS \'Дедупликация поколения для нескольких строк одной транзакции.\'',
    'COMMENT ON COLUMN project_knowledge_states."built_generation" IS \'Только успешно проверенный входной набор.\'',
    'COMMENT ON COLUMN project_knowledge_states."status" IS \'empty/pending/updating/ready/partial/failed.\'',
    'COMMENT ON COLUMN project_knowledge_states."manifest_id" IS \'ID входного набора без копии первичных материалов.\'',
    'COMMENT ON COLUMN project_knowledge_states."coverage" IS \'Объявленный охват и причины неполноты.\'',
    'COMMENT ON COLUMN project_knowledge_states."gaps" IS \'Без фиктивных отрицательных фактов о продукте.\'',
    'COMMENT ON COLUMN project_knowledge_states."metrics" IS \'Фактические объёмы, переиспользование, длительность и usage map-stage.\'',
    'COMMENT ON COLUMN project_knowledge_states."last_error" IS \'Безопасная диагностика, без source payload или credentials.\'',
    'COMMENT ON COLUMN project_knowledge_states."updated_at" IS \'Фактическая дата изменения карты.\'',
    "CREATE TABLE project_knowledge_sources (\n\tproject_id INTEGER NOT NULL, \n\tsource_id VARCHAR(256) NOT NULL, \n\tgeneration BIGINT DEFAULT 0 NOT NULL, \n\tlast_transaction_id BIGINT, \n\tis_deleted BOOLEAN DEFAULT false NOT NULL, \n\tprocessed_generation BIGINT, \n\tcurrent_version_id UUID, \n\tprojection_hash VARCHAR(64), \n\tprocessed_chars INTEGER DEFAULT 0 NOT NULL, \n\ttotal_chars INTEGER DEFAULT 0 NOT NULL, \n\tstatus VARCHAR(16) DEFAULT 'pending' NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (project_id, source_id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE\n)",
    'CREATE INDEX ix_project_knowledge_sources_pending ON project_knowledge_sources (project_id, generation, processed_generation)',
    'COMMENT ON COLUMN project_knowledge_sources."project_id" IS \'Источник не переносит права между проектами.\'',
    'COMMENT ON COLUMN project_knowledge_sources."source_id" IS \'Канонический ID существующего каталога.\'',
    'COMMENT ON COLUMN project_knowledge_sources."generation" IS \'Отличает возврат к тому же тексту от прежней версии.\'',
    'COMMENT ON COLUMN project_knowledge_sources."last_transaction_id" IS \'Повтор одного события в транзакции не создаёт дубль.\'',
    'COMMENT ON COLUMN project_knowledge_sources."is_deleted" IS \'Tombstone закрывает чтение до фоновой очистки.\'',
    'COMMENT ON COLUMN project_knowledge_sources."processed_generation" IS \'Не подменяет текущую версию при задержке worker.\'',
    'COMMENT ON COLUMN project_knowledge_sources."current_version_id" IS \'Ссылка на metadata версии, проверяемая вместе с generation.\'',
    'COMMENT ON COLUMN project_knowledge_sources."projection_hash" IS \'Переиспользование неизменного точного содержимого и зависимостей.\'',
    'COMMENT ON COLUMN project_knowledge_sources."processed_chars" IS \'Следующее Unicode-смещение текущей версии.\'',
    'COMMENT ON COLUMN project_knowledge_sources."total_chars" IS \'Размер объявленного текстового представления.\'',
    'COMMENT ON COLUMN project_knowledge_sources."status" IS \'pending/partial/ready/skipped/failed.\'',
    'COMMENT ON COLUMN project_knowledge_sources."updated_at" IS \'Время последнего изменения source head.\'',
    'CREATE TABLE project_knowledge_source_versions (\n\tid UUID NOT NULL, \n\tproject_id INTEGER NOT NULL, \n\tsource_id VARCHAR(256) NOT NULL, \n\tgeneration BIGINT NOT NULL, \n\tidentity_hash VARCHAR(64) NOT NULL, \n\tnative_revision VARCHAR(128), \n\tsource_hash VARCHAR(64) NOT NULL, \n\tcontent_hash VARCHAR(64) NOT NULL, \n\trepresentation VARCHAR(64) NOT NULL, \n\ttitle TEXT NOT NULL, \n\tsource_type VARCHAR(64) NOT NULL, \n\tdocument_ref VARCHAR(256), \n\ttask_id INTEGER, \n\tdependencies JSONB NOT NULL, \n\tobserved_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_project_knowledge_source_generation UNIQUE (project_id, source_id, generation, identity_hash), \n\tCONSTRAINT uq_project_knowledge_version_project UNIQUE (id, project_id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE\n)',
    'CREATE INDEX ix_project_knowledge_source_versions_project_id ON project_knowledge_source_versions (project_id)',
    'COMMENT ON COLUMN project_knowledge_source_versions."id" IS \'Непрозрачный ID наблюдённой версии.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."project_id" IS \'Текущая граница доступа.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."source_id" IS \'Каноническая ссылка существующего каталога.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."generation" IS \'Поколение источника при наблюдении.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."identity_hash" IS \'Текст и поколения зависимостей различают изменение контекста без подмены прежнего snapshot.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."native_revision" IS \'context_revision/version/revision, если первичный источник имеет её.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."source_hash" IS \'Наблюдённый отпечаток источника существующего каталога.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."content_hash" IS \'SHA-256 полного текстового представления UTF-8.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."representation" IS \'markdown/extracted_text/catalog_text_v2; диапазоны заданы в этом тексте.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."title" IS \'Не выдаётся после изменения/удаления/потери доступа к версии.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."source_type" IS \'Тип первичного источника без служебных источников и переписки.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."document_ref" IS \'Стабильный document_ref без копии документа.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."task_id" IS \'Серверная принадлежность, не расширяющая доступ.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."dependencies" IS \'Поколения источников, влияющих на применимость.\'',
    'COMMENT ON COLUMN project_knowledge_source_versions."observed_at" IS \'Не вымышленная дата создания исторического материала.\'',
    'CREATE TABLE project_knowledge_manifests (\n\tid UUID NOT NULL, \n\tproject_id INTEGER NOT NULL, \n\tgeneration BIGINT NOT NULL, \n\tsources JSONB NOT NULL, \n\tcomplete BOOLEAN NOT NULL, \n\tmetrics JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE\n)',
    'CREATE INDEX ix_project_knowledge_manifests_project_id ON project_knowledge_manifests (project_id)',
    'COMMENT ON COLUMN project_knowledge_manifests."id" IS \'Идентичность попытки построения производной карты.\'',
    'COMMENT ON COLUMN project_knowledge_manifests."project_id" IS \'Проект входного набора.\'',
    'COMMENT ON COLUMN project_knowledge_manifests."generation" IS \'CAS не позволяет старому worker заменить новую карту.\'',
    'COMMENT ON COLUMN project_knowledge_manifests."sources" IS \'ID/поколения без копии исходных текстов.\'',
    'COMMENT ON COLUMN project_knowledge_manifests."complete" IS \'Готовность именно заявленного текстового метода.\'',
    'COMMENT ON COLUMN project_knowledge_manifests."metrics" IS \'Фактическая попытка и переиспользование.\'',
    'COMMENT ON COLUMN project_knowledge_manifests."created_at" IS \'Дата проверенной публикации manifest.\'',
    'CREATE TABLE project_knowledge_claims (\n\tid UUID NOT NULL, \n\tproject_id INTEGER NOT NULL, \n\tsource_id VARCHAR(256) NOT NULL, \n\tstable_key VARCHAR(256) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tcategory VARCHAR(64) NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_project_knowledge_claim_key UNIQUE (project_id, source_id, stable_key), \n\tCONSTRAINT uq_project_knowledge_claim_project UNIQUE (id, project_id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE\n)',
    'CREATE INDEX ix_project_knowledge_claims_page ON project_knowledge_claims (project_id, category, state)',
    'COMMENT ON COLUMN project_knowledge_claims."id" IS \'Детерминированный ID по проекту, источнику и предметному ключу.\'',
    'COMMENT ON COLUMN project_knowledge_claims."project_id" IS \'Область доступа утверждения.\'',
    'COMMENT ON COLUMN project_knowledge_claims."source_id" IS \'Не меняется при следующей версии того же утверждения.\'',
    'COMMENT ON COLUMN project_knowledge_claims."stable_key" IS \'Поле паспорта или детерминированное место точного фрагмента.\'',
    'COMMENT ON COLUMN project_knowledge_claims."revision" IS \'Актуальная запись append-only истории.\'',
    'COMMENT ON COLUMN project_knowledge_claims."category" IS \'Раздел карты, не изменение паспорта.\'',
    'COMMENT ON COLUMN project_knowledge_claims."state" IS \'Текст всё равно проверяется по текущим source generations при чтении.\'',
    'COMMENT ON COLUMN project_knowledge_claims."fingerprint" IS \'Повтор не добавляет дубль той же версии.\'',
    'COMMENT ON COLUMN project_knowledge_claims."updated_at" IS \'Время последней содержательной версии.\'',
    'CREATE TABLE project_knowledge_claim_revisions (\n\tclaim_id UUID NOT NULL, \n\trevision INTEGER NOT NULL, \n\tproject_id INTEGER NOT NULL, \n\ttext TEXT NOT NULL, \n\tkind VARCHAR(32) NOT NULL, \n\tacceptance VARCHAR(16) NOT NULL, \n\tverification VARCHAR(32) NOT NULL, \n\tprovenance JSONB NOT NULL, \n\tsubject VARCHAR(256), \n\tvalue_hash VARCHAR(64), \n\tconflicts_with JSONB NOT NULL, \n\treplaced_by INTEGER, \n\treason TEXT, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (claim_id, revision), \n\tFOREIGN KEY(claim_id, project_id) REFERENCES project_knowledge_claims (id, project_id) ON DELETE CASCADE, \n\tCONSTRAINT uq_project_knowledge_revision_project UNIQUE (claim_id, revision, project_id), \n\tFOREIGN KEY(claim_id) REFERENCES project_knowledge_claims (id) ON DELETE CASCADE\n)',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."claim_id" IS \'Стабильная идентичность утверждения.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."revision" IS \'Монотонная версия утверждения.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."project_id" IS \'Составной FK исключает смешение проектов.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."text" IS \'Выдаётся только при текущем доступном evidence.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."kind" IS \'Требование, факт источника, вывод, предположение или вопрос.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."acceptance" IS \'LLM/документ не становятся авторским требованием автоматически.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."verification" IS \'participant_report/status_only не означают поведение продукта или prod.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."provenance" IS \'Автор, метод, источник и предел подтверждения.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."subject" IS \'Для ограниченного сопоставления явных ключей, без вывода скрытой цели.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."value_hash" IS \'Разошедшиеся значения показываются как требующие проверки.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."conflicts_with" IS \'ID других текущих утверждений по явному предмету.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."replaced_by" IS \'Следующая ревизия; исторический текст сам по себе не доступен.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."reason" IS \'Устаревание/замена/неполнота.\'',
    'COMMENT ON COLUMN project_knowledge_claim_revisions."created_at" IS \'Фактическое время этой версии.\'',
    'CREATE TABLE project_knowledge_evidence (\n\tid UUID NOT NULL, \n\tclaim_id UUID NOT NULL, \n\tclaim_revision INTEGER NOT NULL, \n\tproject_id INTEGER NOT NULL, \n\tsource_version_id UUID NOT NULL, \n\tquote TEXT, \n\tstart INTEGER, \n\t"end" INTEGER, \n\tfield_path VARCHAR(256), \n\tfield_value JSONB, \n\tchecked_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(claim_id, claim_revision, project_id) REFERENCES project_knowledge_claim_revisions (claim_id, revision, project_id) ON DELETE CASCADE, \n\tFOREIGN KEY(source_version_id, project_id) REFERENCES project_knowledge_source_versions (id, project_id) ON DELETE CASCADE, \n\tCONSTRAINT ck_project_knowledge_evidence_range CHECK ((start IS NULL AND "end" IS NULL) OR (start >= 0 AND "end" > start)), \n\tFOREIGN KEY(source_version_id) REFERENCES project_knowledge_source_versions (id) ON DELETE CASCADE\n)',
    'CREATE INDEX ix_project_knowledge_evidence_source ON project_knowledge_evidence (source_version_id)',
    'COMMENT ON COLUMN project_knowledge_evidence."id" IS \'Проверенный фрагмент или структурированное поле.\'',
    'COMMENT ON COLUMN project_knowledge_evidence."claim_id" IS \'Ссылка вместе с его ревизией.\'',
    'COMMENT ON COLUMN project_knowledge_evidence."claim_revision" IS \'Evidence не переносится на новый текст без проверки.\'',
    'COMMENT ON COLUMN project_knowledge_evidence."project_id" IS \'Составные FK не позволяют связать утверждение с версией другого проекта.\'',
    'COMMENT ON COLUMN project_knowledge_evidence."source_version_id" IS \'Точный проверенный source snapshot.\'',
    'COMMENT ON COLUMN project_knowledge_evidence."quote" IS \'Дословный Unicode-фрагмент, null для структурированного поля.\'',
    'COMMENT ON COLUMN project_knowledge_evidence."start" IS \'Unicode-смещение в сохранённом представлении.\'',
    'COMMENT ON COLUMN project_knowledge_evidence."end" IS \'Не включительно, в том же представлении.\'',
    'COMMENT ON COLUMN project_knowledge_evidence."field_path" IS \'Не подменяется цитатой из названия стадии.\'',
    'COMMENT ON COLUMN project_knowledge_evidence."field_value" IS \'Проверенное допустимое значение публичного поля.\'',
    'COMMENT ON COLUMN project_knowledge_evidence."checked_at" IS \'Проверка выполнена сервером в опубликованной версии.\'',
)


def upgrade() -> None:
    """Добавляет только схему и version/outbox triggers."""
    for statement in SCHEMA_SQL:
        op.execute(sa.text(statement))
    install_project_knowledge_ddl(op.get_bind())


def downgrade() -> None:
    """Не теряет наблюдённые версии и историю; пустая схема обратима."""
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE projects IN ACCESS EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE project_knowledge_states, project_knowledge_sources, project_knowledge_source_versions, project_knowledge_manifests, project_knowledge_claims, project_knowledge_claim_revisions, project_knowledge_evidence IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM project_knowledge_source_versions) OR EXISTS (SELECT 1 FROM project_knowledge_claims)")).scalar_one():
        raise RuntimeError("Cannot downgrade: project knowledge versions or claims contain saved history.")
    triggers = bind.execute(sa.text("SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE t.tgname = 'project_knowledge_changed' AND n.nspname = current_schema() AND NOT t.tgisinternal")).scalars().all()
    for table in triggers:
        quoted = bind.dialect.identifier_preparer.quote(table)
        op.execute(sa.text(f"DROP TRIGGER project_knowledge_changed ON {quoted}"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS project_knowledge_source_changed()"))
    for table in ("project_knowledge_evidence", "project_knowledge_claim_revisions", "project_knowledge_claims", "project_knowledge_manifests", "project_knowledge_source_versions", "project_knowledge_sources", "project_knowledge_states"):
        op.drop_table(table)
