"""Цитаты переписки и timezone вопроса без изменения исходных сообщений.

Revision ID: f12c7a309bd1
Revises: f01b96d24c8a
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f12c7a309bd1"
down_revision = "f01b96d24c8a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет производные цитаты и контекст времени; история сохраняется."""
    for table in ("agent_messages", "chat_agent_turns"):
        op.add_column(table, sa.Column("message_references", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb"), comment="Проверенные цитаты сообщений диалога." if table == "agent_messages" else "Проверенные цитаты сообщений чата."))
    op.add_column("chat_agent_turns", sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC", comment="Часовой пояс IANA инициатора."))
    op.create_index("ix_agent_messages_date", "agent_messages", ["conversation_id", "created_at", "id"])
    op.create_index("ix_agent_messages_history", "agent_messages", ["conversation_id", "role", "status", "id"])
    op.create_index("ix_chat_messages_author_date", "chat_messages", ["chat_id", "author_user_id", "created_at", "id"])


def downgrade() -> None:
    """Удаляет производные цитаты и timezone; исходные сообщения не затрагиваются."""
    op.drop_index("ix_chat_messages_author_date", table_name="chat_messages")
    op.drop_index("ix_agent_messages_history", table_name="agent_messages")
    op.drop_index("ix_agent_messages_date", table_name="agent_messages")
    op.drop_column("chat_agent_turns", "timezone")
    op.drop_column("chat_agent_turns", "message_references")
    op.drop_column("agent_messages", "message_references")
