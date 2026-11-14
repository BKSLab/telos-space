"""Версии документов меняются для всех путей записи и переносов задач."""

from sqlalchemy import event, text

VERSION_FUNCTION = """
CREATE OR REPLACE FUNCTION document_version_changed() RETURNS trigger AS $$
BEGIN
    NEW.version := OLD.version + 1;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

OWNER_FUNCTION = """
CREATE OR REPLACE FUNCTION document_task_context_changed() RETURNS trigger AS $$
BEGIN
    IF OLD.project_id IS DISTINCT FROM NEW.project_id
        OR OLD.number IS DISTINCT FROM NEW.number OR OLD.title IS DISTINCT FROM NEW.title THEN
        UPDATE documents SET version = version + 1 WHERE owner_task_id = NEW.id;
        UPDATE task_attachments SET version = version + 1 WHERE task_id = NEW.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

LINK_FUNCTION = """
CREATE OR REPLACE FUNCTION document_link_context_changed() RETURNS trigger AS $$
DECLARE item jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN item := to_jsonb(OLD); ELSE item := to_jsonb(NEW); END IF;
    IF (item->>'document_id') IS NOT NULL THEN
        UPDATE documents SET version = version + 1 WHERE id = (item->>'document_id')::integer;
    ELSE
        UPDATE project_files SET version = version + 1 WHERE id = (item->>'project_file_id')::integer;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def document_catalog_statements() -> list[str]:
    """Возвращает одинаковый DDL для metadata.create_all и Alembic."""
    return [
        VERSION_FUNCTION,
        OWNER_FUNCTION,
        LINK_FUNCTION,
        *(
            f"CREATE OR REPLACE TRIGGER document_version BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION document_version_changed()"
            for table in ("documents", "project_files", "task_attachments")
        ),
        "CREATE OR REPLACE TRIGGER document_task_context AFTER UPDATE OF project_id, number, title ON tasks FOR EACH ROW EXECUTE FUNCTION document_task_context_changed()",
        "CREATE OR REPLACE TRIGGER document_link_context AFTER INSERT OR DELETE ON document_links FOR EACH ROW EXECUTE FUNCTION document_link_context_changed()",
    ]


def register_document_catalog_ddl(metadata) -> None:
    """Регистрирует версии также в изолированной PostgreSQL-схеме тестов."""

    @event.listens_for(metadata, "after_create")
    def after_create(target, connection, **kwargs):
        if connection.dialect.name == "postgresql":
            for statement in document_catalog_statements():
                connection.execute(text(statement))
