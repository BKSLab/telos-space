"""Транзакционные версии задачи и сигнал после commit для всех писателей."""

from sqlalchemy import event, text

CHILD_TABLES = (
    "task_comments",
    "task_activity",
    "task_participants",
    "task_attachments",
    "task_reports",
    "document_links",
)

REVISION_SQL = """
CREATE OR REPLACE FUNCTION task_attention_revision() RETURNS trigger AS $$
DECLARE old_closed boolean; new_closed boolean;
BEGIN
    old_closed := CASE WHEN OLD.project_id IS NULL THEN OLD.status::text IN ('DONE','CANCELLED')
        ELSE COALESCE((SELECT is_done_stage FROM project_stages WHERE id=OLD.stage_id), false) END;
    new_closed := CASE WHEN NEW.project_id IS NULL THEN NEW.status::text IN ('DONE','CANCELLED')
        ELSE COALESCE((SELECT is_done_stage FROM project_stages WHERE id=NEW.stage_id), false) END;
    IF (to_jsonb(NEW) - ARRAY['updated_at','search_vector','position','revision','attention_revision','attention_deadline_revision'])
        IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['updated_at','search_vector','position','revision','attention_revision','attention_deadline_revision'])
       OR NEW.attention_revision IS DISTINCT FROM OLD.attention_revision THEN
        NEW.attention_revision := OLD.attention_revision + 1;
    ELSE
        NEW.attention_revision := OLD.attention_revision;
    END IF;
    IF NEW.due_date IS DISTINCT FROM OLD.due_date OR new_closed IS DISTINCT FROM old_closed
       OR NEW.attention_deadline_revision IS DISTINCT FROM OLD.attention_deadline_revision THEN
        NEW.attention_deadline_revision := OLD.attention_deadline_revision + 1;
    ELSE
        NEW.attention_deadline_revision := OLD.attention_deadline_revision;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

CHILD_SQL = """
CREATE OR REPLACE FUNCTION task_attention_child() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND to_jsonb(NEW) IS NOT DISTINCT FROM to_jsonb(OLD) THEN
        RETURN NEW;
    END IF;
    IF TG_OP <> 'INSERT' THEN
        UPDATE tasks SET attention_revision=attention_revision+1 WHERE id=OLD.task_id;
    END IF;
    IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE' AND NEW.task_id IS DISTINCT FROM OLD.task_id) THEN
        UPDATE tasks SET attention_revision=attention_revision+1 WHERE id=NEW.task_id;
    END IF;
    PERFORM pg_notify('task_attention', '');
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

NOTIFY_SQL = """
CREATE OR REPLACE FUNCTION task_attention_notify() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('task_attention', '');
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

STAGE_SQL = """
CREATE OR REPLACE FUNCTION task_attention_stage() RETURNS trigger AS $$
BEGIN
    IF NEW.is_done_stage IS DISTINCT FROM OLD.is_done_stage THEN
        UPDATE tasks SET attention_revision=attention_revision+1,
            attention_deadline_revision=attention_deadline_revision+1 WHERE stage_id=NEW.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


DEPENDENCY_SQL = """
CREATE OR REPLACE FUNCTION task_attention_dependency() RETURNS trigger AS $attention$
DECLARE changed_ids integer[];
BEGIN
    IF TG_OP = 'UPDATE' AND to_jsonb(NEW) IS NOT DISTINCT FROM to_jsonb(OLD) THEN RETURN NEW; END IF;
    changed_ids := ARRAY[]::integer[];
    IF TG_OP <> 'INSERT' THEN
        changed_ids := changed_ids || ARRAY[OLD.predecessor_task_id, OLD.successor_task_id];
    END IF;
    IF TG_OP <> 'DELETE' THEN
        changed_ids := changed_ids || ARRAY[NEW.predecessor_task_id, NEW.successor_task_id];
    END IF;
    PERFORM id FROM tasks WHERE id = ANY(changed_ids) ORDER BY id FOR UPDATE;
    UPDATE tasks SET attention_revision=attention_revision+1 WHERE id=ANY(changed_ids);
    RETURN NULL;
END;
$attention$ LANGUAGE plpgsql
"""


def install_attention(connection) -> None:
    """Устанавливает только DDL; старые задачи получают версию через DEFAULT колонки."""
    for sql in (REVISION_SQL, CHILD_SQL, NOTIFY_SQL, STAGE_SQL, DEPENDENCY_SQL):
        connection.execute(text(sql))
    connection.execute(text("DROP TRIGGER IF EXISTS task_attention_revision ON tasks"))
    connection.execute(
        text(
            "CREATE TRIGGER task_attention_revision BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION task_attention_revision()"
        )
    )
    for table in CHILD_TABLES:
        connection.execute(text(f"DROP TRIGGER IF EXISTS task_attention_changed ON {table}"))
        connection.execute(
            text(
                f"CREATE TRIGGER task_attention_changed AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION task_attention_child()"
            )
        )
    for table in ("tasks", "task_views", "project_members", "users"):
        connection.execute(text(f"DROP TRIGGER IF EXISTS task_attention_notify ON {table}"))
        connection.execute(
            text(
                f"CREATE TRIGGER task_attention_notify AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION task_attention_notify()"
            )
        )
    connection.execute(
        text("DROP TRIGGER IF EXISTS task_attention_dependency ON task_dependencies")
    )
    connection.execute(
        text(
            "CREATE TRIGGER task_attention_dependency AFTER INSERT OR UPDATE OR DELETE ON task_dependencies FOR EACH ROW EXECUTE FUNCTION task_attention_dependency()"
        )
    )
    connection.execute(text("DROP TRIGGER IF EXISTS task_attention_stage ON project_stages"))
    connection.execute(
        text(
            "CREATE TRIGGER task_attention_stage AFTER UPDATE ON project_stages FOR EACH ROW EXECUTE FUNCTION task_attention_stage()"
        )
    )


def register_attention_ddl(metadata) -> None:
    """Делает create_all и Alembic эквивалентными для интеграционных тестов."""

    @event.listens_for(metadata, "after_create")
    def after_create(_metadata, connection, **_kwargs):
        if connection.dialect.name == "postgresql":
            install_attention(connection)
