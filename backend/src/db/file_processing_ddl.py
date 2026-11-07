"""DDL очереди и немедленного поиска метаданных для всех путей записи."""

from sqlalchemy import event, text

FILE_PROCESSING_FUNCTION = """
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
"""

FILE_TEXT_SEARCH_FUNCTION = """
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
"""


def file_processing_statements() -> list[str]:
    """Возвращает DDL, общий для metadata.create_all и текущей схемы."""
    return [
        FILE_PROCESSING_FUNCTION,
        "DROP TRIGGER IF EXISTS file_original_changed ON task_attachments",
        "CREATE TRIGGER file_original_changed AFTER INSERT OR UPDATE OF original_name, caption, storage_key, content_type, size ON task_attachments FOR EACH ROW EXECUTE FUNCTION file_original_changed()",
        "DROP TRIGGER IF EXISTS file_original_changed ON project_files",
        "CREATE TRIGGER file_original_changed AFTER INSERT OR UPDATE OF original_name, caption, storage_key, content_type, size, content_hash, retry_requested_at, deleted_at ON project_files FOR EACH ROW EXECUTE FUNCTION file_original_changed()",
        FILE_TEXT_SEARCH_FUNCTION,
        "DROP TRIGGER IF EXISTS file_text_search_changed ON knowledge_attachment_texts",
        "CREATE TRIGGER file_text_search_changed AFTER INSERT OR UPDATE OF text, status, content_hash ON knowledge_attachment_texts FOR EACH ROW EXECUTE FUNCTION file_text_search_changed()",
        "DROP TRIGGER IF EXISTS file_text_search_changed ON knowledge_project_file_texts",
        "CREATE TRIGGER file_text_search_changed AFTER INSERT OR UPDATE OF text, status, content_hash ON knowledge_project_file_texts FOR EACH ROW EXECUTE FUNCTION file_text_search_changed()",
    ]


def register_file_processing_ddl(metadata) -> None:
    """Устанавливает одинаковые триггеры в PostgreSQL тестовой metadata."""

    @event.listens_for(metadata, "after_create")
    def after_create(target, connection, **kwargs):
        if connection.dialect.name == "postgresql":
            for statement in file_processing_statements():
                connection.execute(text(statement))
