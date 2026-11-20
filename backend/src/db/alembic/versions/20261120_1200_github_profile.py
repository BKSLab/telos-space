"""Move GitHub credentials to user profiles."""

import sqlalchemy as sa
from alembic import op

revision = "c30a202611200001"
down_revision = "c29a202611160001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("github_token_ciphertext", sa.Text()))
    op.add_column("users", sa.Column("github_account_id", sa.BigInteger()))
    op.add_column("users", sa.Column("github_login", sa.String(255)))
    op.execute("""
        UPDATE git_connections SET credential_ciphertext = NULL, credential_owner_id = NULL,
            credential_revision = credential_revision + 1, lease_id = NULL, lease_until = NULL,
            state = CASE WHEN enabled THEN 'access_lost' ELSE 'disabled' END,
            last_error_code = CASE WHEN enabled THEN 'github_profile_required' END
    """)


def downgrade() -> None:
    if not op.get_context().as_sql:
        count = op.get_bind().scalar(
            sa.text("SELECT count(*) FROM users WHERE github_token_ciphertext IS NOT NULL")
        )
        if count:
            raise RuntimeError("GitHub profiles must be disconnected before downgrade.")
    op.drop_column("users", "github_login")
    op.drop_column("users", "github_account_id")
    op.drop_column("users", "github_token_ciphertext")
