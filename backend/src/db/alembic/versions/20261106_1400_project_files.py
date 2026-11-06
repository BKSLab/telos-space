"""Самостоятельные файлы проекта и производные результаты обработки.

Revision ID: e31a7c9d204f
Revises: c07e4f82a1b6
"""

import sqlalchemy as sa
from alembic import op

revision = "e31a7c9d204f"
down_revision = "c07e4f82a1b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_files",
        sa.Column("id", sa.Integer(), primary_key=True, comment="Идентификатор файла проекта."),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            comment="Проект-владелец файла.",
        ),
        sa.Column(
            "uploaded_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            comment="Локальный пользователь, загрузивший файл.",
        ),
        sa.Column(
            "original_name",
            sa.String(512),
            nullable=False,
            comment="Имя файла без компонентов пути.",
        ),
        sa.Column(
            "content_type",
            sa.String(255),
            nullable=False,
            comment="MIME-тип, определённый сервером.",
        ),
        sa.Column("size", sa.BigInteger(), nullable=False, comment="Размер файла в байтах."),
        sa.Column(
            "storage_key",
            sa.String(255),
            nullable=False,
            unique=True,
            comment="Внутренний относительный ключ в uploads.",
        ),
        sa.Column(
            "content_hash",
            sa.String(64),
            nullable=False,
            comment="SHA-256 неизменяемого оригинала.",
        ),
        sa.Column(
            "retry_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Время явного запроса повторной обработки.",
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Скрывает источник до фоновой очистки оригинала.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="Дата и время создания записи.",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="Дата и время последнего обновления записи.",
        ),
        sa.CheckConstraint("size > 0", name="ck_project_files_size_positive"),
    )
    op.create_index("ix_project_files_project_id", "project_files", ["project_id"])
    op.create_table(
        "knowledge_project_file_texts",
        sa.Column(
            "project_file_id",
            sa.Integer(),
            sa.ForeignKey("project_files.id", ondelete="CASCADE"),
            primary_key=True,
            comment="Идентификатор исходного файла проекта.",
        ),
        sa.Column("text", sa.Text(), nullable=False, comment="Полный извлечённый текст оригинала."),
        sa.Column("status", sa.String(32), nullable=False, comment="Состояние извлечения текста."),
        sa.Column(
            "detail",
            sa.Text(),
            nullable=True,
            comment="Безопасное пояснение результата извлечения.",
        ),
        sa.Column(
            "original_chars",
            sa.Integer(),
            nullable=False,
            comment="Число символов полного извлечённого текста.",
        ),
        sa.Column(
            "content_hash",
            sa.String(64),
            nullable=False,
            comment="SHA-256 оригинала обработанной версии.",
        ),
        sa.Column(
            "index_status",
            sa.String(32),
            nullable=False,
            comment="Состояние векторного индекса файла.",
        ),
        sa.Column(
            "index_detail",
            sa.Text(),
            nullable=True,
            comment="Безопасное пояснение состояния индекса.",
        ),
        sa.Column(
            "indexed_hash",
            sa.String(64),
            nullable=True,
            comment="SHA-256 успешно проиндексированного оригинала.",
        ),
        sa.Column(
            "embedding_signature",
            sa.String(64),
            nullable=True,
            comment="Отпечаток модели и размерности успешного индекса.",
        ),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Время успешной индексации версии.",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="Время последнего изменения результата обработки.",
        ),
    )
    op.add_column(
        "knowledge_source_summaries",
        sa.Column(
            "project_file_id",
            sa.Integer(),
            nullable=True,
            comment="Исходный самостоятельный файл проекта.",
        ),
    )
    op.create_foreign_key(
        "fk_knowledge_summary_project_file",
        "knowledge_source_summaries",
        "project_files",
        ["project_file_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("ck_knowledge_summary_owner", "knowledge_source_summaries", type_="check")
    op.create_check_constraint(
        "ck_knowledge_summary_owner",
        "knowledge_source_summaries",
        "num_nonnulls(document_id, attachment_id, project_file_id) = 1",
    )
    # Снимок политики этой версии: последующие изменения каталога не меняют миграцию.
    op.execute("""CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF
        id, project_id, uploaded_by_user_id, original_name, storage_key, content_type,
        size, content_hash, retry_requested_at, deleted_at
        ON project_files FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed('project')""")


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM project_files)")).scalar():
        raise RuntimeError(
            "Откат остановлен: в проекте есть файлы. Сначала сохраните и удалите их через приложение."
        )
    op.drop_constraint("ck_knowledge_summary_owner", "knowledge_source_summaries", type_="check")
    op.drop_constraint(
        "fk_knowledge_summary_project_file", "knowledge_source_summaries", type_="foreignkey"
    )
    op.drop_column("knowledge_source_summaries", "project_file_id")
    op.create_check_constraint(
        "ck_knowledge_summary_owner",
        "knowledge_source_summaries",
        "(document_id IS NOT NULL) <> (attachment_id IS NOT NULL)",
    )
    op.drop_table("knowledge_project_file_texts")
    op.drop_index("ix_project_files_project_id", table_name="project_files")
    op.drop_table("project_files")
