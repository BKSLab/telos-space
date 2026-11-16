"""Версия единого паспорта и полные неизменяемые снимки.

Revision ID: f23a8c91d046
Revises: f23d8b410ce2

Schema-only: прежние поля, тексты, задачи и документы не переписываются.
Baseline версии 0 фиксирует сервис только при первом содержательном изменении.
Откат с историей запрещён, чтобы не потерять новые снимки и авторство.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f23a8c91d046"
down_revision = "f23d8b410ce2"
branch_labels = None
depends_on = None

PROJECT_FIELDS = (
    "id",
    "owner_id",
    "key",
    "name",
    "description_md",
    "status",
    "start_date",
    "due_date",
    "description_sections",
    "due_date_has_been_set",
)


def _project_outbox(*, with_revision: bool) -> None:
    """Обновляет только DDL проектного outbox, не ставит задания."""
    fields = (*PROJECT_FIELDS, "context_revision") if with_revision else PROJECT_FIELDS
    columns = ", ".join(f'"{name}"' for name in fields)
    op.execute('DROP TRIGGER IF EXISTS knowledge_changed ON "projects"')
    op.execute(
        f"CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF {columns} "
        "ON \"projects\" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed('self')"
    )


def upgrade() -> None:
    """Расширяет схему, сохраняя прежние проекты в версии 0 без backfill."""
    op.add_column(
        "projects",
        sa.Column(
            "context_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Серверная версия содержательных полей; прежние проекты имеют 0 до первого изменения.",
        ),
    )
    op.create_check_constraint(
        "ck_projects_context_revision_nonnegative", "projects", "context_revision >= 0"
    )
    op.create_table(
        "project_context_history",
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
            comment="История сохраняется до удаления самого проекта.",
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            primary_key=True,
            comment="Уникальная версия внутри проекта; 0 — прежний baseline.",
        ),
        sa.Column(
            "description_sections",
            postgresql.JSONB(),
            nullable=True,
            comment="Сохранённые поля паспорта без ограничения превью.",
        ),
        sa.Column(
            "description_md",
            sa.Text(),
            nullable=True,
            comment="Прежний или составленный текст сохраняется полностью и без повторного разбора.",
        ),
        sa.Column(
            "changed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            comment="Неизвестный исторический автор не выдумывается.",
        ),
        sa.Column(
            "changed_by_name",
            sa.Text(),
            nullable=True,
            comment="Определён сервером по текущей локальной identity, null для исходного baseline.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="Для baseline — фактическое время сохранения, не историческая дата написания текста.",
        ),
        sa.Column(
            "is_initial",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="Создание нового проекта или прежний baseline версии 0.",
        ),
        sa.Column(
            "changed_fields",
            postgresql.JSONB(),
            nullable=False,
            comment="Канонические имена полей паспорта либо description_md для свободного текста.",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_project_context_history_revision_nonnegative"),
    )
    _project_outbox(with_revision=True)


def downgrade() -> None:
    """Отказывает при новых снимках; пустую схему откатывает без потери raw текста."""
    # Runtime меняет проект, затем добавляет историю. Тот же порядок relation
    # locks закрывает окно EXISTS→DROP и не требует upgrade lock для ALTER.
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE projects IN ACCESS EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE project_context_history IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM project_context_history)")).scalar_one():
        raise RuntimeError("Cannot downgrade: project context history contains saved snapshots.")
    _project_outbox(with_revision=False)
    op.drop_table("project_context_history")
    op.drop_constraint("ck_projects_context_revision_nonnegative", "projects", type_="check")
    op.drop_column("projects", "context_revision")
