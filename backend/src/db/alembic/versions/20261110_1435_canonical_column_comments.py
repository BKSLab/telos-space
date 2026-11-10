"""Актуальные названия Ариса и публичных идентификаторов ключей.

Revision ID: d86e1a90c427
Revises: b64da02f537c

Меняются только COMMENT ON COLUMN; таблицы, типы и данные сохраняются.
"""

from alembic import op

revision = "d86e1a90c427"
down_revision = "b64da02f537c"
branch_labels = None
depends_on = None

_COMMENTS = (
    (
        "agent_tool_runs", "message_id",
        "Ответ Тима в виджете; null для чата проекта и MCP.",
        "Ответ Ариса в виджете; null для чата проекта и MCP.",
    ),
    (
        "agent_tool_runs", "chat_turn_id",
        "Ответ Тима в чате проекта; null для виджета и MCP.",
        "Ответ Ариса в чате проекта; null для виджета и MCP.",
    ),
    (
        "api_keys", "key_prefix",
        "Первые символы ключа для узнавания и поиска.",
        "Публичный идентификатор для узнавания и поиска ключа, без секрета.",
    ),
    (
        "api_tokens", "prefix",
        "Префикс секрета для узнавания токена человеком.",
        "Публичная часть для узнавания токена; маркер формата не включён.",
    ),
)


def upgrade() -> None:
    """Приводит четыре комментария в соответствие текущему ORM."""
    for table, column, before, after in _COMMENTS:
        op.alter_column(table, column, comment=after, existing_comment=before)


def downgrade() -> None:
    """Восстанавливает комментарии предыдущей ревизии без изменения данных."""
    for table, column, before, after in reversed(_COMMENTS):
        op.alter_column(table, column, comment=before, existing_comment=after)
