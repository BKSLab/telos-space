"""Согласовать описания колонок с именем Арис в ORM.

Revision ID: c07e4f82a1b6
Revises: b62dfe210a84
Create Date: 2026-11-04
"""

from alembic import op

revision = "c07e4f82a1b6"
down_revision = "b62dfe210a84"
branch_labels = None
depends_on = None

_COMMENTS = (
    (
        "agent_messages",
        "progress",
        "Что Тим делает сейчас, пока готовит ответ; NULL — статуса нет или ответ завершён.",
        "Что Арис делает сейчас, пока готовит ответ; NULL — статуса нет или ответ завершён.",
    ),
    (
        "chat_agent_turns",
        "progress",
        "Что Тим делает сейчас, пока готовит ответ; NULL — статуса нет или ответ завершён.",
        "Что Арис делает сейчас, пока готовит ответ; NULL — статуса нет или ответ завершён.",
    ),
    (
        "chat_agent_turns",
        "request_message_id",
        "Сообщение участника, обращённое к Тиму.",
        "Сообщение участника, обращённое к Арису.",
    ),
    (
        "chat_agent_turns",
        "reply_message_id",
        "Сообщение Тима с ответом.",
        "Сообщение Ариса с ответом.",
    ),
    (
        "chat_agent_turns",
        "requester_id",
        "Кто позвал Тима; его права действуют в ответе.",
        "Кто позвал Ариса; его права действуют в ответе.",
    ),
    (
        "chat_messages",
        "author_kind",
        "Автор: user — участник, agent — Тим.",
        "Автор: user — участник, agent — Арис.",
    ),
)


def upgrade() -> None:
    for table, column, before, after in _COMMENTS:
        op.alter_column(table, column, comment=after, existing_comment=before)


def downgrade() -> None:
    for table, column, before, after in reversed(_COMMENTS):
        op.alter_column(table, column, comment=before, existing_comment=after)
