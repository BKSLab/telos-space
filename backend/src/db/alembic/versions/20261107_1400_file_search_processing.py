"""Независимая обработка оригиналов и хранимый оконный FTS.

Revision ID: b64da02f537c
Revises: a53c9e1f426b

Schema-only: прежние тексты заполняет отдельная идемпотентная CLI-команда.
Downgrade удаляет только производные очередь/окна, сохраняя оригиналы и кеши.
"""

from alembic import op

revision = "b64da02f537c"
down_revision = "a53c9e1f426b"
branch_labels = None
depends_on = None

SCHEMA_DDL = [
    """CREATE TABLE file_processing_jobs (
	id SERIAL NOT NULL,
	attachment_id INTEGER,
	project_file_id INTEGER,
	status VARCHAR(16) DEFAULT 'pending' NOT NULL,
	requested_version INTEGER DEFAULT '1' NOT NULL,
	claimed_version INTEGER,
	lease_token VARCHAR(36),
	lease_until TIMESTAMP WITH TIME ZONE,
	attempts INTEGER DEFAULT '0' NOT NULL,
	available_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	detail TEXT,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_file_processing_owner CHECK (num_nonnulls(attachment_id, project_file_id) = 1),
	CONSTRAINT ck_file_processing_status CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
	FOREIGN KEY(attachment_id) REFERENCES task_attachments (id) ON DELETE CASCADE,
	FOREIGN KEY(project_file_id) REFERENCES project_files (id) ON DELETE CASCADE
)""",
    "CREATE INDEX ix_file_processing_lease ON file_processing_jobs (lease_until) WHERE status = 'processing'",
    "CREATE INDEX ix_file_processing_pending ON file_processing_jobs (available_at, id) WHERE status = 'pending'",
    "CREATE UNIQUE INDEX uq_file_processing_attachment ON file_processing_jobs (attachment_id) WHERE attachment_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_file_processing_project_file ON file_processing_jobs (project_file_id) WHERE project_file_id IS NOT NULL",
    "COMMENT ON COLUMN file_processing_jobs.id IS 'Идентификатор обработки оригинала.'",
    "COMMENT ON COLUMN file_processing_jobs.attachment_id IS 'Ровно один исходный оригинал: вложение задачи.'",
    "COMMENT ON COLUMN file_processing_jobs.project_file_id IS 'Ровно один исходный оригинал: файл проекта.'",
    "COMMENT ON COLUMN file_processing_jobs.status IS 'pending, processing, ready или failed.'",
    "COMMENT ON COLUMN file_processing_jobs.requested_version IS 'Монотонная версия запроса; обновление во время обработки не теряется.'",
    "COMMENT ON COLUMN file_processing_jobs.claimed_version IS 'Версия запроса в начале текущего lease.'",
    "COMMENT ON COLUMN file_processing_jobs.lease_token IS 'Случайная квитанция владельца обработки.'",
    "COMMENT ON COLUMN file_processing_jobs.lease_until IS 'После этого времени обработку можно безопасно перехватить.'",
    "COMMENT ON COLUMN file_processing_jobs.attempts IS 'Число попыток текущего запроса.'",
    "COMMENT ON COLUMN file_processing_jobs.available_at IS 'Время следующей попытки обработки.'",
    "COMMENT ON COLUMN file_processing_jobs.detail IS 'Безопасное пояснение ошибки обработки.'",
    "COMMENT ON COLUMN file_processing_jobs.updated_at IS 'Время последнего изменения очереди.'",
    """CREATE TABLE file_search_chunks (
	id SERIAL NOT NULL,
	attachment_id INTEGER,
	project_file_id INTEGER,
	chunk_index INTEGER NOT NULL,
	start_offset INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	title VARCHAR(512) NOT NULL,
	caption TEXT DEFAULT '' NOT NULL,
	body TEXT DEFAULT '' NOT NULL,
	search_vector TSVECTOR GENERATED ALWAYS AS (setweight(to_tsvector('russian', coalesce(title, '')), 'A') || setweight(to_tsvector('russian', coalesce(caption, '')), 'B') || setweight(to_tsvector('russian', coalesce(body, '')), 'C')) STORED NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_file_search_owner CHECK (num_nonnulls(attachment_id, project_file_id) = 1),
	CONSTRAINT ck_file_search_offsets CHECK (chunk_index >= 0 AND start_offset >= 0 AND end_offset >= start_offset),
	CONSTRAINT ck_file_search_bounded CHECK (char_length(body) <= 12000 AND char_length(caption) <= 4000),
	FOREIGN KEY(attachment_id) REFERENCES task_attachments (id) ON DELETE CASCADE,
	FOREIGN KEY(project_file_id) REFERENCES project_files (id) ON DELETE CASCADE
)""",
    "CREATE INDEX ix_file_search_vector ON file_search_chunks USING gin (search_vector)",
    "CREATE UNIQUE INDEX uq_file_search_attachment_chunk ON file_search_chunks (attachment_id, chunk_index) WHERE attachment_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_file_search_project_chunk ON file_search_chunks (project_file_id, chunk_index) WHERE project_file_id IS NOT NULL",
    "COMMENT ON COLUMN file_search_chunks.id IS 'Идентификатор поискового окна.'",
    "COMMENT ON COLUMN file_search_chunks.attachment_id IS 'Исходное вложение задачи.'",
    "COMMENT ON COLUMN file_search_chunks.project_file_id IS 'Исходный самостоятельный файл проекта.'",
    "COMMENT ON COLUMN file_search_chunks.chunk_index IS 'Порядковый номер окна оригинала, начиная с нуля.'",
    "COMMENT ON COLUMN file_search_chunks.start_offset IS 'Начальное смещение в полном тексте, начиная с нуля.'",
    "COMMENT ON COLUMN file_search_chunks.end_offset IS 'Исключающая правая граница окна в полном тексте.'",
    "COMMENT ON COLUMN file_search_chunks.title IS 'Название оригинала для немедленного поиска.'",
    "COMMENT ON COLUMN file_search_chunks.caption IS 'Ограниченная пользовательская подпись для поиска.'",
    "COMMENT ON COLUMN file_search_chunks.body IS 'Ограниченное окно полного извлечённого текста.'",
    "COMMENT ON COLUMN file_search_chunks.search_vector IS 'Взвешенный хранимый вектор: название, подпись, содержимое окна.'",
]

PROCESSING_DDL = [
    """
CREATE OR REPLACE FUNCTION file_original_changed() RETURNS trigger AS $$
DECLARE source_column text; is_original_change boolean; is_retry boolean;
BEGIN
    source_column := CASE WHEN TG_TABLE_NAME = 'task_attachments'
        THEN 'attachment_id' ELSE 'project_file_id' END;
    is_original_change := TG_OP = 'INSERT';
    is_retry := false;
    IF TG_OP = 'UPDATE' THEN
        is_original_change := (to_jsonb(NEW)->'storage_key') IS DISTINCT FROM (to_jsonb(OLD)->'storage_key')
            OR (to_jsonb(NEW)->'size') IS DISTINCT FROM (to_jsonb(OLD)->'size')
            OR (to_jsonb(NEW)->'content_type') IS DISTINCT FROM (to_jsonb(OLD)->'content_type')
            OR (to_jsonb(NEW)->'content_hash') IS DISTINCT FROM (to_jsonb(OLD)->'content_hash');
        is_retry := (to_jsonb(NEW)->'retry_requested_at') IS DISTINCT FROM (to_jsonb(OLD)->'retry_requested_at')
            OR (to_jsonb(NEW)->'deleted_at') IS DISTINCT FROM (to_jsonb(OLD)->'deleted_at');
    END IF;
    IF is_original_change THEN
        IF source_column = 'attachment_id' THEN
            DELETE FROM knowledge_attachment_texts WHERE attachment_id = NEW.id;
        ELSE
            DELETE FROM knowledge_project_file_texts WHERE project_file_id = NEW.id;
        END IF;
        EXECUTE format('DELETE FROM file_search_chunks WHERE %I = $1', source_column) USING NEW.id;
    END IF;
    IF (to_jsonb(NEW)->>'deleted_at') IS NOT NULL THEN
        EXECUTE format('DELETE FROM file_search_chunks WHERE %I = $1', source_column) USING NEW.id;
    ELSE
        EXECUTE format('UPDATE file_search_chunks SET title = $2, caption = $3 WHERE %I = $1', source_column)
            USING NEW.id, NEW.original_name, left(coalesce(NEW.caption, ''), 4000);
        EXECUTE format('INSERT INTO file_search_chunks (%I, chunk_index, start_offset, end_offset, title, caption, body)
            VALUES ($1, 0, 0, 0, $2, $3, '''') ON CONFLICT (%I, chunk_index)
            WHERE %I IS NOT NULL DO NOTHING', source_column, source_column, source_column)
            USING NEW.id, NEW.original_name, left(coalesce(NEW.caption, ''), 4000);
    END IF;
    IF is_original_change OR is_retry THEN
        EXECUTE format('INSERT INTO file_processing_jobs (%I) VALUES ($1)
            ON CONFLICT (%I) WHERE %I IS NOT NULL DO UPDATE SET
            requested_version = file_processing_jobs.requested_version + 1,
            status = CASE WHEN file_processing_jobs.status = ''processing'' THEN ''processing'' ELSE ''pending'' END,
            attempts = CASE WHEN file_processing_jobs.status = ''processing'' THEN file_processing_jobs.attempts ELSE 0 END,
            available_at = now(), detail = NULL, updated_at = now()', source_column, source_column, source_column)
            USING NEW.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
""",
    "DROP TRIGGER IF EXISTS file_original_changed ON task_attachments",
    "CREATE TRIGGER file_original_changed AFTER INSERT OR UPDATE OF original_name, caption, storage_key, content_type, size ON task_attachments FOR EACH ROW EXECUTE FUNCTION file_original_changed()",
    "DROP TRIGGER IF EXISTS file_original_changed ON project_files",
    "CREATE TRIGGER file_original_changed AFTER INSERT OR UPDATE OF original_name, caption, storage_key, content_type, size, content_hash, retry_requested_at, deleted_at ON project_files FOR EACH ROW EXECUTE FUNCTION file_original_changed()",
    """
CREATE OR REPLACE FUNCTION file_text_search_changed() RETURNS trigger AS $$
DECLARE source_column text; source_table text; source_id integer; original jsonb;
BEGIN
    IF TG_TABLE_NAME = 'knowledge_attachment_texts' THEN
        source_column := 'attachment_id'; source_table := 'task_attachments';
        source_id := NEW.attachment_id;
    ELSE
        source_column := 'project_file_id'; source_table := 'project_files';
        source_id := NEW.project_file_id;
    END IF;
    EXECUTE format('SELECT to_jsonb(s) FROM %I s WHERE id = $1', source_table)
        INTO original USING source_id;
    EXECUTE format('DELETE FROM file_search_chunks WHERE %I = $1', source_column) USING source_id;
    IF original IS NULL OR (original->>'deleted_at') IS NOT NULL THEN RETURN NULL; END IF;
    EXECUTE format('INSERT INTO file_search_chunks
        (%I, chunk_index, start_offset, end_offset, title, caption, body)
        SELECT $1, start_offset / 11744, start_offset,
            least(start_offset + 12000, length($4::text)), $2, $3,
            substr($4::text, start_offset + 1, 12000)
        FROM generate_series(0, greatest(length($4::text) - 1, 0), 11744) AS offsets(start_offset)', source_column)
        USING source_id, original->>'original_name', left(coalesce(original->>'caption', ''), 4000), coalesce(NEW.text, '');
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
""",
    "DROP TRIGGER IF EXISTS file_text_search_changed ON knowledge_attachment_texts",
    "CREATE TRIGGER file_text_search_changed AFTER INSERT OR UPDATE OF text, status, content_hash ON knowledge_attachment_texts FOR EACH ROW EXECUTE FUNCTION file_text_search_changed()",
    "DROP TRIGGER IF EXISTS file_text_search_changed ON knowledge_project_file_texts",
    "CREATE TRIGGER file_text_search_changed AFTER INSERT OR UPDATE OF text, status, content_hash ON knowledge_project_file_texts FOR EACH ROW EXECUTE FUNCTION file_text_search_changed()",
]

OUTBOX_UPGRADE = [
    """CREATE OR REPLACE FUNCTION knowledge_project_changed() RETURNS trigger AS $$
DECLARE item jsonb; pid integer; items jsonb[];
BEGIN
    IF TG_TABLE_NAME = 'task_attachments' AND TG_OP = 'UPDATE' THEN
        IF OLD.storage_key IS DISTINCT FROM NEW.storage_key
            OR OLD.size IS DISTINCT FROM NEW.size
            OR OLD.content_type IS DISTINCT FROM NEW.content_type THEN
            DELETE FROM knowledge_attachment_texts WHERE attachment_id = NEW.id;
        END IF;
    END IF;
    IF TG_OP = 'INSERT' THEN items := ARRAY[to_jsonb(NEW)];
    ELSIF TG_OP = 'DELETE' THEN items := ARRAY[to_jsonb(OLD)];
    ELSE items := ARRAY[to_jsonb(OLD), to_jsonb(NEW)]; END IF;
    FOREACH item IN ARRAY items LOOP
        FOR pid IN
            SELECT (item->>'id')::integer WHERE TG_ARGV[0] = 'self'
            UNION SELECT (item->>'project_id')::integer WHERE TG_ARGV[0] = 'project'
            UNION SELECT project_id FROM tasks WHERE TG_ARGV[0] = 'task' AND id = (item->>'task_id')::integer
            UNION SELECT project_id FROM project_members WHERE TG_ARGV[0] = 'user' AND user_id = (item->>'id')::integer
        LOOP
            IF pid IS NOT NULL THEN
                INSERT INTO knowledge_index_jobs
                    (project_id, entity_type, entity_id, operation, status, attempts, transaction_id)
                VALUES (pid, 'PROJECT', NULL, 'REINDEX_PROJECT', 'PENDING', 0, txid_current())
                ON CONFLICT (project_id, transaction_id) DO NOTHING;
            END IF;
        END LOOP;
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;""",
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_files"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "uploaded_by_user_id", "original_name", "caption", "storage_key", "content_type", "size", "content_hash", "retry_requested_at", "deleted_at" ON "project_files" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_reports"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "task_id", "revision", "body_md", "author_name", "source_comment_id", "source_comment_original_id", "source_author_name" ON "task_reports" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "projects"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "owner_id", "key", "name", "description_md", "status", "start_date", "due_date", "description_sections", "due_date_has_been_set" ON "projects" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'self\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "tasks"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "stage_id", "status", "wbs_node_id", "number", "title", "description_md", "checklist", "checklist_revision", "priority", "role", "assignee", "start_date", "due_date", "baseline_start_date", "baseline_due_date", "completed_at", "position", "wbs_position" ON "tasks" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "documents"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "slug", "title", "content_md", "origin_attachment_id" ON "documents" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_comments"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "task_id", "author_name", "body_md" ON "task_comments" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_attachments"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "task_id", "uploaded_by_user_id", "original_name", "caption", "storage_key", "content_type", "size" ON "task_attachments" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_milestones"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "title", "description_md", "due_date", "status", "wbs_node_id" ON "project_milestones" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_risks"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "task_id", "title", "description", "mitigation_plan", "response_plan", "probability", "impact", "risk_level", "status", "response_strategy", "owner_user_id", "review_date", "source" ON "project_risks" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "wbs_nodes"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "parent_id", "title", "position" ON "wbs_nodes" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_stages"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "name", "order_index", "color", "is_done_stage" ON "project_stages" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_stickers"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "board_id", "body", "color", "created_by_user_id", "created_by_username_snapshot", "created_by_display_name_snapshot", "revision" ON "project_stickers" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_members"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "user_id", "role" ON "project_members" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_activity"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "task_id", "event_type", "from_value", "to_value", "actor_user_id", "actor_name", "via_api_key_label" ON "task_activity" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_deadline_changes"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "previous_due_date", "new_due_date", "changed_by_user_id", "changed_by_name", "comment" ON "project_deadline_changes" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "analytics_reports"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "created_by_user_id", "created_by_display_name_snapshot", "llm_model", "payload", "context_summary" ON "analytics_reports" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "document_links"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "document_id", "task_id" ON "document_links" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_comment_attachments"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "comment_id", "attachment_id", "task_id" ON "task_comment_attachments" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_sticker_task_links"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "sticker_id", "task_id" ON "project_sticker_task_links" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_dependencies"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "predecessor_task_id", "successor_task_id", "dependency_type", "lag_days" ON "task_dependencies" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_participants"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "task_id", "project_member_id", "user_id", "role" ON "task_participants" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "users"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "username", "first_name", "last_name", "middle_name", "is_active" ON "users" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'user\')',
]

OUTBOX_DOWNGRADE = [
    """
CREATE OR REPLACE FUNCTION knowledge_project_changed() RETURNS trigger AS $$
DECLARE item jsonb; pid integer; items jsonb[];
BEGIN
    IF TG_TABLE_NAME = 'task_attachments' AND TG_OP = 'UPDATE' THEN
        DELETE FROM knowledge_attachment_texts WHERE attachment_id = NEW.id;
    END IF;
    IF TG_OP = 'INSERT' THEN items := ARRAY[to_jsonb(NEW)];
    ELSIF TG_OP = 'DELETE' THEN items := ARRAY[to_jsonb(OLD)];
    ELSE items := ARRAY[to_jsonb(OLD), to_jsonb(NEW)]; END IF;
    FOREACH item IN ARRAY items LOOP
        FOR pid IN
            SELECT (item->>'id')::integer WHERE TG_ARGV[0] = 'self'
            UNION SELECT (item->>'project_id')::integer WHERE TG_ARGV[0] = 'project'
            UNION SELECT project_id FROM tasks WHERE TG_ARGV[0] = 'task' AND id = (item->>'task_id')::integer
            UNION SELECT project_id FROM project_members WHERE TG_ARGV[0] = 'user' AND user_id = (item->>'id')::integer
        LOOP
            IF pid IS NOT NULL THEN
                INSERT INTO knowledge_index_jobs
                    (project_id, entity_type, entity_id, operation, status, attempts, transaction_id)
                VALUES (pid, 'PROJECT', NULL, 'REINDEX_PROJECT', 'PENDING', 0, txid_current())
                ON CONFLICT (project_id, transaction_id) DO NOTHING;
            END IF;
        END LOOP;
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
""",
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_files"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "uploaded_by_user_id", "original_name", "storage_key", "content_type", "size", "content_hash", "retry_requested_at", "deleted_at" ON "project_files" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_reports"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "task_id", "revision", "body_md", "author_name", "source_comment_id", "source_comment_original_id", "source_author_name" ON "task_reports" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "projects"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "owner_id", "key", "name", "description_md", "status", "start_date", "due_date", "description_sections", "due_date_has_been_set" ON "projects" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'self\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "tasks"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "stage_id", "status", "wbs_node_id", "number", "title", "description_md", "checklist", "checklist_revision", "priority", "role", "assignee", "start_date", "due_date", "baseline_start_date", "baseline_due_date", "completed_at", "position", "wbs_position" ON "tasks" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "documents"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "slug", "title", "content_md", "origin_attachment_id" ON "documents" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_comments"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "task_id", "author_name", "body_md" ON "task_comments" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_attachments"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "task_id", "original_name", "storage_key", "content_type", "size" ON "task_attachments" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_milestones"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "title", "description_md", "due_date", "status", "wbs_node_id" ON "project_milestones" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_risks"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "task_id", "title", "description", "mitigation_plan", "response_plan", "probability", "impact", "risk_level", "status", "response_strategy", "owner_user_id", "review_date", "source" ON "project_risks" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "wbs_nodes"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "parent_id", "title", "position" ON "wbs_nodes" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_stages"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "name", "order_index", "color", "is_done_stage" ON "project_stages" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_stickers"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "board_id", "body", "color", "created_by_user_id", "created_by_username_snapshot", "created_by_display_name_snapshot", "revision" ON "project_stickers" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_members"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "user_id", "role" ON "project_members" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_activity"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "task_id", "event_type", "from_value", "to_value", "actor_user_id", "actor_name", "via_api_key_label" ON "task_activity" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_deadline_changes"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "previous_due_date", "new_due_date", "changed_by_user_id", "changed_by_name", "comment" ON "project_deadline_changes" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "analytics_reports"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "created_by_user_id", "created_by_display_name_snapshot", "llm_model", "payload", "context_summary" ON "analytics_reports" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "document_links"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "document_id", "task_id" ON "document_links" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "project_sticker_task_links"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "sticker_id", "task_id" ON "project_sticker_task_links" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_dependencies"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "project_id", "predecessor_task_id", "successor_task_id", "dependency_type", "lag_days" ON "task_dependencies" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'project\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "task_participants"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "task_id", "project_member_id", "user_id", "role" ON "task_participants" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'task\')',
    'DROP TRIGGER IF EXISTS knowledge_changed ON "users"',
    'CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF "id", "username", "first_name", "last_name", "middle_name", "is_active" ON "users" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed(\'user\')',
]


def upgrade() -> None:
    """Создаёт производные структуры и frozen-снимки триггеров без backfill."""
    for statement in (*SCHEMA_DDL, *PROCESSING_DDL, *OUTBOX_UPGRADE):
        op.execute(statement)


def downgrade() -> None:
    """Удаляет производные данные и возвращает прежний набор outbox-триггеров."""
    op.execute("DROP TRIGGER IF EXISTS file_original_changed ON task_attachments")
    op.execute("DROP TRIGGER IF EXISTS file_original_changed ON project_files")
    op.execute("DROP TRIGGER IF EXISTS file_text_search_changed ON knowledge_attachment_texts")
    op.execute("DROP TRIGGER IF EXISTS file_text_search_changed ON knowledge_project_file_texts")
    op.execute("DROP FUNCTION IF EXISTS file_original_changed()")
    op.execute("DROP FUNCTION IF EXISTS file_text_search_changed()")
    op.execute("DROP TRIGGER IF EXISTS knowledge_changed ON task_comment_attachments")
    for statement in OUTBOX_DOWNGRADE:
        op.execute(statement)
    op.drop_table("file_search_chunks")
    op.drop_table("file_processing_jobs")
