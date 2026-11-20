"""Hourly Git reconciliation and atomic stage wakeups."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c31a202611200001"
down_revision = "c30a202611200001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "git_connections",
        sa.Column(
            "sync_stage_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
            comment="Opt-in: ID стадий своего проекта.",
        ),
    )
    op.add_column(
        "git_connections",
        sa.Column(
            "sync_requested_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Поколение запроса сверки.",
        ),
    )
    op.add_column(
        "git_connections",
        sa.Column(
            "sync_completed_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Завершённый запрос.",
        ),
    )
    op.add_column(
        "git_connections",
        sa.Column(
            "lease_sync_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Запрос на момент захвата lease.",
        ),
    )
    op.add_column(
        "git_connections",
        sa.Column(
            "last_sync_reason",
            sa.String(24),
            comment="Причина запуска: poll/manual/webhook/task_stage/configuration.",
        ),
    )
    op.add_column(
        "git_connections",
        sa.Column(
            "last_sync_requested_at", sa.DateTime(timezone=True), comment="UTC сигнала сверки."
        ),
    )
    op.add_column(
        "git_connections",
        sa.Column(
            "last_webhook_received_at",
            sa.DateTime(timezone=True),
            comment="Последняя подписанная новая доставка своего repository.",
        ),
    )
    op.add_column(
        "git_connections",
        sa.Column(
            "last_webhook_event",
            sa.String(40),
            comment="Тип доставки; получение не означает обработку.",
        ),
    )
    op.add_column(
        "git_connections",
        sa.Column(
            "last_webhook_processed_at",
            sa.DateTime(timezone=True),
            comment="Успешная сверка последней релевантной доставки.",
        ),
    )
    op.add_column(
        "git_connections",
        sa.Column(
            "last_webhook_request_revision",
            sa.Integer(),
            comment="Запрос доставки: старый lease не подтверждает новое событие.",
        ),
    )
    op.execute("""
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
""")
    op.execute(
        "CREATE TRIGGER git_stage_sync AFTER UPDATE OF stage_id ON tasks FOR EACH ROW EXECUTE FUNCTION git_task_stage_changed()"
    )


def downgrade() -> None:
    if not op.get_context().as_sql:
        count = op.get_bind().scalar(
            sa.text(
                "SELECT count(*) FROM git_connections WHERE sync_stage_ids <> '[]'::jsonb OR sync_requested_revision > sync_completed_revision"
            )
        )
        if count:
            raise RuntimeError("Disable stage rules and finish queued Git sync before downgrade.")
    op.execute("DROP TRIGGER IF EXISTS git_stage_sync ON tasks")
    op.execute("DROP FUNCTION IF EXISTS git_task_stage_changed()")
    op.drop_column("git_connections", "last_webhook_request_revision")
    op.drop_column("git_connections", "last_webhook_processed_at")
    op.drop_column("git_connections", "last_webhook_event")
    op.drop_column("git_connections", "last_webhook_received_at")
    op.drop_column("git_connections", "last_sync_requested_at")
    op.drop_column("git_connections", "last_sync_reason")
    op.drop_column("git_connections", "lease_sync_revision")
    op.drop_column("git_connections", "sync_completed_revision")
    op.drop_column("git_connections", "sync_requested_revision")
    op.drop_column("git_connections", "sync_stage_ids")
