"""Atomic stage signals into the existing durable Git connection queue."""

from sqlalchemy import event, text

STAGE_SYNC_FUNCTION = """
CREATE OR REPLACE FUNCTION git_task_stage_changed() RETURNS trigger AS $$
DECLARE cid integer;
BEGIN
    IF NEW.project_id IS NULL OR NEW.project_id IS DISTINCT FROM OLD.project_id
        OR NEW.stage_id IS NOT DISTINCT FROM OLD.stage_id THEN
        RETURN NULL;
    END IF;
    FOR cid IN
        SELECT c.id FROM git_connections c
        WHERE c.project_id = NEW.project_id AND c.enabled
          AND c.sync_stage_ids @> jsonb_build_array(NEW.stage_id)
          AND (
              NOT EXISTS (
                  SELECT 1 FROM git_task_change_links l
                  JOIN git_changes g ON g.id = l.change_id
                  JOIN git_connections linked ON linked.id = g.connection_id
                  WHERE l.task_id = NEW.id AND linked.project_id = NEW.project_id
              ) OR EXISTS (
                  SELECT 1 FROM git_task_change_links l
                  JOIN git_changes g ON g.id = l.change_id
                  WHERE l.task_id = NEW.id AND g.connection_id = c.id
              )
          )
        ORDER BY c.id FOR UPDATE
    LOOP
        UPDATE git_connections SET
            sync_requested_revision = sync_requested_revision + 1,
            last_sync_reason = 'task_stage', last_sync_requested_at = now(),
            next_poll_at = CASE WHEN last_error_code = 'rate_limited'
                AND next_poll_at > now() THEN next_poll_at ELSE now() END,
            updated_at = now()
        WHERE id = cid;
        UPDATE git_changes SET next_refresh_at = now(), refresh_state = '{}',
            revision = revision + 1, updated_at = now()
        WHERE connection_id = cid;
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def stage_sync_statements() -> list[str]:
    """Single queue and transaction for UI/API/MCP writes and bulk operations."""
    return [
        STAGE_SYNC_FUNCTION,
        "DROP TRIGGER IF EXISTS git_stage_sync ON tasks",
        "CREATE TRIGGER git_stage_sync AFTER UPDATE OF stage_id ON tasks "
        "FOR EACH ROW EXECUTE FUNCTION git_task_stage_changed()",
    ]


def register_git_sync_ddl(metadata):
    @event.listens_for(metadata, "after_create")
    def after_create(target, connection, **kwargs):
        if connection.dialect.name == "postgresql":
            for statement in stage_sync_statements():
                connection.execute(text(statement))
