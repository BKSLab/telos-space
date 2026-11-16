"""Transactional source generations рядом с существующим knowledge outbox.

Триггер хранит только идентичности/поколения. Старый пересказ закрывается сразу
после commit источника, а не после успешного embeddings/worker. Тот же outbox
и очередь используются для восстановления и объединения технических событий.
"""

from sqlalchemy import event, inspect, text

PROJECT_KNOWLEDGE_EVENT_FUNCTION = r"""
CREATE OR REPLACE FUNCTION project_knowledge_source_changed() RETURNS trigger AS $$
DECLARE
    items jsonb[]; item jsonb; prior jsonb; current_item jsonb;
    refs text[] := ARRAY[]::text[]; direct_refs text[] := ARRAY[]::text[];
    deleted_refs text[] := ARRAY[]::text[]; project_ids integer[] := ARRAY[]::integer[];
    pid integer; identifier integer; ref text; kind text := TG_ARGV[0];
    scope_name text := TG_ARGV[1]; mode text := TG_ARGV[3]; fields text[];
    event_txid bigint := txid_current();
BEGIN
    IF TG_OP = 'UPDATE' THEN
        prior := to_jsonb(OLD); current_item := to_jsonb(NEW);
        fields := string_to_array(TG_ARGV[2], ',');
        IF NOT EXISTS (SELECT 1 FROM unnest(fields) AS changed(field_name) WHERE prior->changed.field_name IS DISTINCT FROM current_item->changed.field_name) THEN
            RETURN NULL;
        END IF;
    END IF;
    IF TG_OP = 'INSERT' THEN items := ARRAY[to_jsonb(NEW)];
    ELSIF TG_OP = 'DELETE' THEN items := ARRAY[to_jsonb(OLD)];
    ELSE items := ARRAY[to_jsonb(OLD), to_jsonb(NEW)]; END IF;
    FOREACH item IN ARRAY items LOOP
        identifier := CASE WHEN mode = 'attachment_cache' THEN (item->>'attachment_id')::integer
            WHEN mode = 'project_file_cache' THEN (item->>'project_file_id')::integer
            ELSE (item->>'id')::integer END;
        IF kind <> '' AND identifier IS NOT NULL THEN
            ref := kind || ':' || identifier;
            refs := array_append(refs, ref);
            IF mode = 'primary' THEN
                direct_refs := array_append(direct_refs, ref);
                IF TG_OP = 'DELETE' THEN deleted_refs := array_append(deleted_refs, ref); END IF;
            END IF;
        END IF;
        IF item->>'task_id' IS NOT NULL THEN refs := array_append(refs, 'task:' || (item->>'task_id')); END IF;
        IF item->>'owner_task_id' IS NOT NULL THEN refs := array_append(refs, 'task:' || (item->>'owner_task_id')); END IF;
        IF item->>'predecessor_task_id' IS NOT NULL THEN refs := array_append(refs, 'task:' || (item->>'predecessor_task_id')); END IF;
        IF item->>'successor_task_id' IS NOT NULL THEN refs := array_append(refs, 'task:' || (item->>'successor_task_id')); END IF;
        IF item->>'document_id' IS NOT NULL THEN refs := array_append(refs, 'document:' || (item->>'document_id')); END IF;
        IF item->>'project_file_id' IS NOT NULL THEN refs := array_append(refs, 'project_file:' || (item->>'project_file_id')); END IF;
        IF item->>'attachment_id' IS NOT NULL THEN refs := array_append(refs, 'attachment:' || (item->>'attachment_id')); END IF;
        IF item->>'comment_id' IS NOT NULL THEN refs := array_append(refs, 'comment:' || (item->>'comment_id')); END IF;
        IF item->>'sticker_id' IS NOT NULL THEN refs := array_append(refs, 'sticker:' || (item->>'sticker_id')); END IF;
        IF item->>'project_member_id' IS NOT NULL THEN refs := array_append(refs, 'member:' || (item->>'project_member_id')); END IF;
        FOR pid IN
            SELECT (item->>'id')::integer WHERE scope_name = 'self'
            UNION SELECT (item->>'project_id')::integer WHERE scope_name IN ('project', 'document')
            UNION SELECT project_id FROM tasks WHERE scope_name = 'task' AND id = (item->>'task_id')::integer
            UNION SELECT project_id FROM tasks WHERE scope_name = 'document' AND id = (item->>'owner_task_id')::integer
            UNION SELECT project_id FROM project_members WHERE scope_name = 'user' AND user_id = (item->>'id')::integer
            UNION SELECT t.project_id FROM task_attachments a JOIN tasks t ON t.id = a.task_id
                WHERE mode = 'attachment_cache' AND a.id = (item->>'attachment_id')::integer
            UNION SELECT project_id FROM project_files WHERE mode = 'project_file_cache' AND id = (item->>'project_file_id')::integer
        LOOP
            IF pid IS NOT NULL THEN project_ids := array_append(project_ids, pid); END IF;
        END LOOP;
        IF scope_name = 'user' THEN
            FOR identifier IN SELECT id FROM project_members WHERE user_id = (item->>'id')::integer LOOP
                refs := array_append(refs, 'member:' || identifier);
            END LOOP;
        END IF;
    END LOOP;
    IF cardinality(project_ids) = 0 THEN
        SELECT coalesce(array_agg(DISTINCT s.project_id), ARRAY[]::integer[]) INTO project_ids
            FROM project_knowledge_sources s WHERE s.source_id = ANY(refs);
    END IF;
    -- Один порядок блокировок для переносов между проектами и разных source rows.
    FOR pid IN SELECT DISTINCT value FROM unnest(project_ids) AS p(value) ORDER BY value LOOP
        PERFORM 1 FROM projects WHERE id = pid FOR KEY SHARE;
        IF NOT FOUND THEN CONTINUE; END IF;
        INSERT INTO project_knowledge_states (project_id, generation, last_transaction_id, status, updated_at)
            VALUES (pid, 1, event_txid, 'pending', clock_timestamp())
            ON CONFLICT (project_id) DO UPDATE SET
                generation = CASE WHEN project_knowledge_states.last_transaction_id = event_txid
                    THEN project_knowledge_states.generation ELSE project_knowledge_states.generation + 1 END,
                last_transaction_id = event_txid, status = 'pending', updated_at = clock_timestamp();
        FOR ref IN SELECT DISTINCT value FROM unnest(refs) AS r(value) ORDER BY value LOOP
            INSERT INTO project_knowledge_sources
                (project_id, source_id, generation, last_transaction_id, is_deleted, status, updated_at)
                VALUES (pid, ref, 1, event_txid, ref = ANY(deleted_refs), 'pending', clock_timestamp())
                ON CONFLICT (project_id, source_id) DO UPDATE SET
                    generation = CASE WHEN project_knowledge_sources.last_transaction_id = event_txid
                        THEN project_knowledge_sources.generation ELSE project_knowledge_sources.generation + 1 END,
                    last_transaction_id = event_txid,
                    is_deleted = CASE WHEN ref = ANY(direct_refs) THEN ref = ANY(deleted_refs)
                        ELSE project_knowledge_sources.is_deleted END,
                    status = 'pending', updated_at = clock_timestamp();
        END LOOP;
        INSERT INTO knowledge_index_jobs
            (project_id, entity_type, entity_id, operation, status, attempts, transaction_id)
            VALUES (pid, 'PROJECT', NULL, 'REINDEX_PROJECT', 'PENDING', 0, event_txid)
            ON CONFLICT (project_id, transaction_id) DO NOTHING;
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def install_project_knowledge_ddl(connection) -> None:
    """Ставит source-generation triggers только для фактической текущей схемы.

    Args:
        connection: Явное DDL-соединение Alembic/create_all.
    """
    from src.knowledge.catalog import POLICIES

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if not {"project_knowledge_states", "project_knowledge_sources"}.issubset(tables):
        return
    connection.execute(text(PROJECT_KNOWLEDGE_EVENT_FUNCTION))
    rules = [
        (rule.table, rule.kind.value if rule.kind else "", rule.scope, rule.fields, "primary")
        for rule in POLICIES
        if rule.invalidates_index
    ]
    rules.extend(
        [
            (
                "knowledge_attachment_texts",
                "attachment",
                "attachment",
                ("attachment_id", "text", "status", "content_hash"),
                "attachment_cache",
            ),
            (
                "knowledge_project_file_texts",
                "project_file",
                "project_file",
                ("project_file_id", "text", "status", "content_hash"),
                "project_file_cache",
            ),
        ]
    )
    for table, kind, scope, declared, mode in rules:
        if table not in tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        watched = [
            name
            for name in declared
            if name in columns and name not in {"created_at", "updated_at"}
        ]
        if not watched:
            continue
        names = ", ".join(f'"{name}"' for name in watched)
        connection.execute(text(f'DROP TRIGGER IF EXISTS project_knowledge_changed ON "{table}"'))
        connection.execute(
            text(
                f"CREATE TRIGGER project_knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF {names} "
                f'ON "{table}" FOR EACH ROW EXECUTE FUNCTION project_knowledge_source_changed('
                f"'{kind}', '{scope}', '{','.join(watched)}', '{mode}')"
            )
        )


def register_project_knowledge_ddl(metadata) -> None:
    """create_all использует тот же DDL, что миграция, без ручного backfill."""

    @event.listens_for(metadata, "after_create")
    def after_create(target, connection, **kwargs):
        if connection.dialect.name == "postgresql":
            install_project_knowledge_ddl(connection)
