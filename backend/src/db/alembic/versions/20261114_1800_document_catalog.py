"""Единая библиотека без переноса оригиналов и с сохранением прежних текстов.

Revision ID: e92a74c610bf
Revises: d86e1a90c427

Прежние DOCUMENT-ссылки чата получают канонический ref того же Markdown.
Для прежних проектных источников ставится сверка индекса по сохранённому
тексту без новой очереди извлечения оригиналов. Бизнес-источники не сливаются.
Downgrade останавливается, если новая модель уже содержит невоспроизводимые
в прежней схеме личные тексты, метаданные, связи файлов или ссылки чата на оригиналы.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

from src.db.chat_ddl import install_chat_ddl
from src.db.document_catalog_ddl import document_catalog_statements
from src.db.file_processing_ddl import file_processing_statements
from src.db.knowledge_outbox import install_outbox
from src.knowledge.catalog import POLICIES
from src.utils.document_refs import decode_document_ref

revision = "e92a74c610bf"
down_revision = "d86e1a90c427"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Расширяет владельцев, связи и производные квитанции без удаления данных."""
    op.alter_column(
        "agent_messages",
        "key_scopes",
        comment="Права внешнего ключа или READ/WRITE-токена на момент вопроса; NULL — сессия интерфейса.",
        existing_comment="Права сервисного ключа на момент вопроса; NULL — вопрос из интерфейса.",
    )
    op.add_column(
        "agent_messages",
        sa.Column(
            "api_token_id",
            sa.Integer(),
            nullable=True,
            comment="Локальный READ/WRITE-токен вопроса для повторной проверки доступа worker-ом.",
        ),
    )
    op.create_foreign_key(
        "fk_agent_messages_api_token",
        "agent_messages",
        "api_tokens",
        ["api_token_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("""UPDATE agent_messages AS reply
        SET status='failed', error='Авторизация обновлена. Повторите вопрос.',
            progress=NULL, run_id=NULL, completed_at=now()
        FROM agent_conversations AS conversation
        WHERE reply.conversation_id=conversation.id
          AND conversation.channel='api' AND conversation.api_key_id IS NULL
          AND reply.role='assistant' AND reply.status IN ('queued','processing')
          AND EXISTS (SELECT 1 FROM agent_messages AS question
              WHERE question.conversation_id=reply.conversation_id
                AND question.request_id=reply.request_id AND question.role='user'
                AND (question.key_scopes IS NULL OR question.key_scopes='null'::jsonb)
                AND question.api_token_id IS NULL)""")
    op.add_column(
        "documents",
        sa.Column(
            "owner_task_id",
            sa.Integer(),
            nullable=True,
            comment="Задача-владелец; её текущий доступ определяет доступ к тексту.",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "authored_by_user_id",
            sa.Integer(),
            nullable=True,
            comment="Локальный автор; у прежних документов может отсутствовать.",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "caption", sa.Text(), nullable=True, comment="Необязательное примечание пользователя."
        ),
    )
    _document_search(with_caption=True)
    op.create_foreign_key(
        "fk_documents_owner_task",
        "documents",
        "tasks",
        ["owner_task_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_documents_author",
        "documents",
        "users",
        ["authored_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_documents_owner_task_id", "documents", ["owner_task_id"])
    op.alter_column("documents", "project_id", existing_type=sa.Integer(), nullable=True)
    op.create_check_constraint(
        "ck_documents_owner", "documents", "num_nonnulls(project_id, owner_task_id) = 1"
    )
    for table, comment in (
        ("documents", "Монотонная версия редактируемых полей документа."),
        ("project_files", "Монотонная версия метаданных файлового документа."),
        ("task_attachments", "Монотонная версия метаданных оригинала задачи."),
    ):
        op.add_column(
            table,
            sa.Column("version", sa.Integer(), server_default="1", nullable=False, comment=comment),
        )
    for table, comment in (
        ("project_files", "Редактируемое название, не меняющее имя оригинала."),
        ("task_attachments", "Редактируемое название без изменения оригинала."),
    ):
        op.add_column(
            table, sa.Column("display_title", sa.String(255), nullable=True, comment=comment)
        )
    op.add_column(
        "task_attachments",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="Версия пользовательских метаданных оригинала.",
        ),
    )
    op.alter_column("document_links", "document_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "document_links",
        sa.Column(
            "project_file_id",
            sa.Integer(),
            nullable=True,
            comment="Оригинал проекта, связанный с задачей без копирования.",
        ),
    )
    op.create_foreign_key(
        "fk_document_links_project_file",
        "document_links",
        "project_files",
        ["project_file_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_document_links_project_file_id", "document_links", ["project_file_id"])
    op.create_unique_constraint(
        "uq_document_links_file_task", "document_links", ["project_file_id", "task_id"]
    )
    op.create_check_constraint(
        "ck_document_links_source",
        "document_links",
        "num_nonnulls(document_id, project_file_id) = 1",
    )
    op.create_table(
        "task_comment_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("comment_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "project_file_id",
            sa.Integer(),
            sa.ForeignKey("project_files.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "document_link_id",
            sa.Integer(),
            sa.ForeignKey("document_links.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["comment_id", "task_id"],
            ["task_comments.id", "task_comments.task_id"],
            name="fk_comment_documents_comment_task",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "num_nonnulls(document_id, project_file_id) = 1", name="ck_comment_documents_source"
        ),
        sa.UniqueConstraint("comment_id", "document_id", name="uq_comment_documents_document"),
        sa.UniqueConstraint("comment_id", "project_file_id", name="uq_comment_documents_file"),
    )
    for column in ("comment_id", "task_id", "document_id", "project_file_id", "document_link_id"):
        op.create_index(f"ix_task_comment_documents_{column}", "task_comment_documents", [column])
    op.create_table(
        "document_submissions",
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            comment="Локальный пользователь, выполнивший создание.",
        ),
        sa.Column(
            "request_id",
            sa.Uuid(),
            primary_key=True,
            comment="Стабильный UUID одной пользовательской отправки.",
        ),
        sa.Column(
            "arguments_hash",
            sa.String(64),
            nullable=False,
            comment="SHA-256 аргументов для проверки безопасного повтора.",
        ),
        sa.Column(
            "document_ref",
            sa.String(128),
            nullable=False,
            comment="Ссылка-квитанция сохраняется и после удаления документа.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="Время фиксации результата запроса.",
        ),
    )
    op.create_table(
        "document_index_states",
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
            comment="Проектная коллекция производного индекса.",
        ),
        sa.Column(
            "source_id",
            sa.String(128),
            primary_key=True,
            comment="Восстанавливаемый источник каталога знаний.",
        ),
        sa.Column(
            "source_hash",
            sa.String(64),
            nullable=False,
            comment="Хэш полного проверенного снимка источника.",
        ),
        sa.Column(
            "source_version",
            sa.Integer(),
            nullable=False,
            comment="Версия редактируемых метаданных в снимке индексации.",
        ),
        sa.Column(
            "text_hash",
            sa.String(64),
            nullable=False,
            comment="SHA-256 полного текстового представления UTF-8.",
        ),
        sa.Column(
            "embedding_signature",
            sa.String(64),
            nullable=False,
            comment="Отпечаток модели и размерности векторов.",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            comment="ready, pending или failed для сохранённой версии.",
        ),
        sa.Column(
            "detail",
            sa.Text(),
            nullable=True,
            comment="Безопасное пояснение сбоя семантического этапа.",
        ),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="Время подтверждения результата worker-ом.",
        ),
    )
    op.add_column("chat_message_entities", sa.Column("document_ref", sa.String(128), nullable=True))
    op.alter_column(
        "chat_message_entities",
        "entity_id",
        existing_type=sa.Integer(),
        nullable=True,
        comment="ID объекта, кроме документа.",
        existing_comment="ID объекта.",
    )
    op.execute(
        "UPDATE chat_message_entities SET document_ref = 'd1_' || rtrim(translate(encode(convert_to('document:' || entity_id::text, 'UTF8'), 'base64'), '+/', '-_'), '='), entity_id = NULL WHERE entity_type = 'DOCUMENT'"
    )
    op.create_unique_constraint(
        "uq_chat_message_document_ref", "chat_message_entities", ["message_id", "document_ref"]
    )
    op.create_check_constraint(
        "ck_chat_message_entity_identity",
        "chat_message_entities",
        "(entity_type = 'DOCUMENT' AND document_ref IS NOT NULL AND entity_id IS NULL) OR (entity_type <> 'DOCUMENT' AND document_ref IS NULL AND entity_id IS NOT NULL)",
    )
    for statement in (*document_catalog_statements(), *file_processing_statements()):
        op.execute(sa.text(statement))
    install_outbox(op.get_bind())
    install_chat_ddl(op.get_bind())
    op.execute("""WITH source_projects AS (
        SELECT coalesce(d.project_id,t.project_id) AS project_id FROM documents d LEFT JOIN tasks t ON t.id=d.owner_task_id
        UNION SELECT project_id FROM project_files WHERE deleted_at IS NULL
        UNION SELECT t.project_id FROM task_attachments a JOIN tasks t ON t.id=a.task_id
    ) INSERT INTO knowledge_index_jobs(project_id,entity_type,entity_id,operation,status,attempts,transaction_id)
      SELECT project_id,'PROJECT',NULL,'REINDEX_PROJECT','PENDING',0,txid_current()
      FROM source_projects WHERE project_id IS NOT NULL
      ON CONFLICT(project_id,transaction_id) DO NOTHING""")
    _normalize_historical_results()


def downgrade() -> None:
    """Сохраняет данные и отказывается от необратимого представления новых объектов."""
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM documents WHERE owner_task_id IS NOT NULL OR authored_by_user_id IS NOT NULL OR caption IS NOT NULL) OR EXISTS(SELECT 1 FROM project_files WHERE display_title IS NOT NULL) OR EXISTS(SELECT 1 FROM task_attachments WHERE display_title IS NOT NULL) OR EXISTS(SELECT 1 FROM document_links WHERE project_file_id IS NOT NULL) OR EXISTS(SELECT 1 FROM task_comment_documents) OR EXISTS(SELECT 1 FROM agent_messages WHERE api_token_id IS NOT NULL)"
        )
    ).scalar_one():
        raise RuntimeError(
            "Откат остановлен: прежняя схема не сохраняет новые документы, связи или метаданные."
        )
    references = bind.execute(
        sa.text("SELECT id, document_ref FROM chat_message_entities WHERE entity_type = 'DOCUMENT'")
    ).all()
    decoded = []
    for identifier, ref in references:
        kind, source_id = decode_document_ref(ref)
        if kind != "document":
            raise RuntimeError("Откат остановлен: чат ссылается на оригиналы новой модели.")
        decoded.append({"id": identifier, "entity_id": source_id})
    op.drop_constraint("ck_chat_message_entity_identity", "chat_message_entities", type_="check")
    op.drop_constraint("uq_chat_message_document_ref", "chat_message_entities", type_="unique")
    for row in decoded:
        bind.execute(
            sa.text(
                "UPDATE chat_message_entities SET entity_id=:entity_id, document_ref=NULL WHERE id=:id"
            ),
            row,
        )
    op.alter_column(
        "chat_message_entities",
        "entity_id",
        existing_type=sa.Integer(),
        nullable=False,
        comment="ID объекта.",
        existing_comment="ID объекта, кроме документа.",
    )
    op.drop_column("chat_message_entities", "document_ref")
    op.drop_constraint("fk_agent_messages_api_token", "agent_messages", type_="foreignkey")
    op.drop_column("agent_messages", "api_token_id")
    op.alter_column(
        "agent_messages",
        "key_scopes",
        comment="Права сервисного ключа на момент вопроса; NULL — вопрос из интерфейса.",
        existing_comment="Права внешнего ключа или READ/WRITE-токена на момент вопроса; NULL — сессия интерфейса.",
    )
    for table in ("documents", "project_files", "task_attachments"):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS document_version ON {table}"))
    op.execute("DROP TRIGGER IF EXISTS document_task_context ON tasks")
    op.execute("DROP TRIGGER IF EXISTS document_link_context ON document_links")
    op.execute("DROP FUNCTION IF EXISTS document_task_context_changed()")
    op.execute("DROP FUNCTION IF EXISTS document_link_context_changed()")
    op.execute("DROP FUNCTION IF EXISTS document_version_changed()")
    op.drop_table("document_index_states")
    op.drop_table("document_submissions")
    op.drop_table("task_comment_documents")
    op.drop_constraint("ck_document_links_source", "document_links", type_="check")
    op.drop_constraint("uq_document_links_file_task", "document_links", type_="unique")
    op.drop_index("ix_document_links_project_file_id", table_name="document_links")
    op.drop_constraint("fk_document_links_project_file", "document_links", type_="foreignkey")
    op.execute("DROP TRIGGER IF EXISTS knowledge_changed ON document_links")
    op.drop_column("document_links", "project_file_id")
    op.alter_column("document_links", "document_id", existing_type=sa.Integer(), nullable=False)
    op.drop_constraint("ck_documents_owner", "documents", type_="check")
    op.drop_constraint("fk_documents_owner_task", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_author", "documents", type_="foreignkey")
    op.drop_index("ix_documents_owner_task_id", table_name="documents")
    fields = {
        "documents": {"owner_task_id", "authored_by_user_id", "caption", "version"},
        "project_files": {"display_title", "version"},
        "task_attachments": {"display_title", "version", "updated_at"},
        "document_links": {"project_file_id"},
    }
    legacy_rules = [
        (
            rule.table,
            "project" if rule.table == "documents" else rule.scope,
            tuple(field for field in rule.fields if field not in fields.get(rule.table, set())),
        )
        for rule in POLICIES
        if rule.invalidates_index and rule.table != "task_comment_documents"
    ]
    install_outbox(bind, rules=legacy_rules)
    _document_search(with_caption=False)
    for statement in file_processing_statements():
        legacy = (
            statement.replace("coalesce(NEW.display_title, NEW.original_name)", "NEW.original_name")
            .replace(
                "coalesce(original->>'display_title', original->>'original_name')",
                "original->>'original_name'",
            )
            .replace("original_name, display_title,", "original_name,")
        )
        op.execute(sa.text(legacy))
    for table, columns in fields.items():
        if table == "document_links":
            continue
        for column in columns:
            op.drop_column(table, column)
    op.alter_column("documents", "project_id", existing_type=sa.Integer(), nullable=False)
    op.execute("DROP TRIGGER IF EXISTS chat_entity ON project_files")
    op.execute("DROP TRIGGER IF EXISTS chat_entity ON task_attachments")
    op.execute("""CREATE OR REPLACE FUNCTION chat_entity_changed() RETURNS trigger AS $$
    DECLARE item jsonb;
    BEGIN
        IF TG_OP='DELETE' THEN item:=to_jsonb(OLD); ELSE item:=to_jsonb(NEW); END IF;
        PERFORM chat_emit((item->>'project_id')::integer,'entity.updated',jsonb_build_object('entity_type',TG_ARGV[0],'entity_id',(item->>'id')::integer));
        RETURN NULL;
    END; $$ LANGUAGE plpgsql""")


def _document_search(*, with_caption: bool) -> None:
    """Пересоздаёт только производный столбец и GIN, сохраняя все тексты."""
    expression = "setweight(to_tsvector('russian', coalesce(title, '')), 'A') || "
    if with_caption:
        expression += "setweight(to_tsvector('russian', coalesce(caption, '')), 'B') || "
    expression += "setweight(to_tsvector('russian', coalesce(content_md, '')), 'B')"
    op.drop_index("ix_documents_search_vector", table_name="documents")
    op.drop_column("documents", "search_vector")
    op.add_column(
        "documents",
        sa.Column(
            "search_vector",
            TSVECTOR(),
            sa.Computed(expression, persisted=True),
            nullable=False,
            comment="Взвешенный FTS-вектор заголовка и содержимого документа.",
        ),
    )
    op.create_index(
        "ix_documents_search_vector", "documents", ["search_vector"], postgresql_using="gin"
    )


def _normalize_historical_results() -> None:
    """Дополняет сохранённые карточки без изменения фактов, аргументов и квитанций."""
    op.execute("""CREATE OR REPLACE FUNCTION pg_temp.telos_document_result(value jsonb)
    RETURNS jsonb LANGUAGE plpgsql AS $$
    DECLARE node jsonb; kind text; identifier text; ref text; title text; document jsonb;
    BEGIN
        IF jsonb_typeof(value) <> 'object' THEN RETURN value; END IF;
        IF jsonb_typeof(value->'document')='object' AND (value->'document'->>'id') ~ '^[1-9][0-9]{0,9}$' THEN
            node:=value->'document'; kind:='document';
        ELSIF jsonb_typeof(value->'attachment')='object' AND (value->'attachment'->>'id') ~ '^[1-9][0-9]{0,9}$' THEN
            node:=value->'attachment'; kind:='attachment';
        ELSIF jsonb_typeof(value->'project_file')='object' AND (value->'project_file'->>'id') ~ '^[1-9][0-9]{0,9}$' THEN
            node:=value->'project_file'; kind:='project_file';
        ELSE RETURN value; END IF;
        identifier:=node->>'id';
        ref:='d1_' || rtrim(translate(encode(convert_to(kind || ':' || identifier,'UTF8'),'base64'),'+/','-_'),'=');
        title:=coalesce(nullif(node->>'title',''),nullif(node->>'original_name',''),nullif(node->>'slug',''),'Документ');
        document:=CASE WHEN jsonb_typeof(value->'document')='object' THEN value->'document' ELSE '{}'::jsonb END;
        RETURN value || jsonb_build_object('document',document || jsonb_build_object('ref',ref,'title',title), 'document_ref',ref,'source_id',kind || ':' || identifier);
    END; $$""")
    op.execute("""UPDATE agent_tool_runs SET result=pg_temp.telos_document_result(result) ||
        CASE WHEN jsonb_typeof(result->'preview')='object'
             THEN jsonb_build_object('preview',pg_temp.telos_document_result(result->'preview'))
             ELSE '{}'::jsonb END
        WHERE jsonb_typeof(result)='object' AND (result ? 'document' OR result ? 'attachment'
            OR result ? 'project_file' OR jsonb_typeof(result->'preview')='object')""")
    op.execute("DROP FUNCTION pg_temp.telos_document_result(jsonb)")
