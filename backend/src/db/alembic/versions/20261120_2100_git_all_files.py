"""Index every tracked file; remove path filtering configuration."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c32a202611200001"
down_revision = "c31a202611200001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("git_connections", "exclude_patterns")
    op.drop_column("git_connections", "include_paths")
    # Preserve historical snapshots. Only revoke old publication and queue the new policy.
    op.execute("""
        UPDATE git_connections SET
            generation = generation + 1, revision = revision + 1,
            scope_hash = repeat('0', 64), indexed_sha = NULL, indexed_at = NULL,
            coverage = '{}', lease_id = NULL, lease_until = NULL,
            state = CASE WHEN enabled THEN 'pending' ELSE 'disabled' END,
            sync_requested_revision = sync_requested_revision + 1,
            last_sync_reason = 'configuration', last_sync_requested_at = now(),
            next_poll_at = CASE WHEN last_error_code = 'rate_limited' AND next_poll_at > now()
                THEN next_poll_at ELSE now() END
    """)


def downgrade() -> None:
    # Removed rules are not reconstructed: the restored configuration still covers all paths.
    op.add_column(
        "git_connections",
        sa.Column(
            "include_paths",
            postgresql.JSONB(),
            nullable=False,
            server_default='[""]',
            comment="Пустая строка в списке означает корень Git.",
        ),
    )
    op.add_column(
        "git_connections",
        sa.Column(
            "exclude_patterns",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
            comment="Обязательная политика безопасности применяется сверх них.",
        ),
    )
    op.alter_column("git_connections", "include_paths", server_default=None)
