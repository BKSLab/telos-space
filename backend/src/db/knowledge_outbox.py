"""Транзакционный outbox для всех зарегистрированных проектных данных.

Срабатывает также для bulk SQL, каскадных удалений и составных сценариев.
Одна транзакция создаёт не более одного задания на каждый затронутый проект.
"""

from sqlalchemy import event, text

OUTBOX_FUNCTION = """
CREATE OR REPLACE FUNCTION knowledge_project_changed() RETURNS trigger AS $$
DECLARE item jsonb; pid integer; items jsonb[];
BEGIN
    IF TG_TABLE_NAME = 'task_attachments' AND TG_OP = 'UPDATE' THEN
        IF OLD.storage_key IS DISTINCT FROM NEW.storage_key
            OR OLD.size IS DISTINCT FROM NEW.size
            OR OLD.content_type IS DISTINCT FROM NEW.content_type THEN
            DELETE FROM knowledge_attachment_texts WHERE attachment_id = NEW.id;
        END IF;
    END IF;
    IF TG_OP = 'INSERT' THEN items := ARRAY[to_jsonb(NEW)];
    ELSIF TG_OP = 'DELETE' THEN items := ARRAY[to_jsonb(OLD)];
    ELSE items := ARRAY[to_jsonb(OLD), to_jsonb(NEW)]; END IF;
    FOREACH item IN ARRAY items LOOP
        FOR pid IN
            SELECT (item->>'id')::integer WHERE TG_ARGV[0] = 'self'
            UNION SELECT (item->>'project_id')::integer WHERE TG_ARGV[0] = 'project'
            UNION SELECT project_id FROM tasks WHERE TG_ARGV[0] = 'task' AND id = (item->>'task_id')::integer
            UNION SELECT project_id FROM project_members WHERE TG_ARGV[0] = 'user' AND user_id = (item->>'id')::integer
        LOOP
            IF pid IS NOT NULL THEN
                INSERT INTO knowledge_index_jobs
                    (project_id, entity_type, entity_id, operation, status, attempts, transaction_id)
                VALUES (pid, 'PROJECT', NULL, 'REINDEX_PROJECT', 'PENDING', 0, txid_current())
                ON CONFLICT (project_id, transaction_id) DO NOTHING;
            END IF;
        END LOOP;
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def outbox_statements(rules=None) -> list[str]:
    """Возвращает DDL функции и триггеров outbox для текущего реестра знаний.

    Тот же список SQL вписывается в базовую миграцию: так миграция и
    ``create_all`` в тестах создают одинаковые триггеры, а тест сверяет их.
    """
    if rules is None:
        from src.knowledge.catalog import POLICIES

        rules = [
            (rule.table, rule.scope, rule.fields) for rule in POLICIES if rule.invalidates_index
        ]
    statements = [OUTBOX_FUNCTION.strip()]
    for table, scope, fields in rules:
        watched = [field for field in fields if field not in {"created_at", "updated_at"}]
        columns = ", ".join(f'"{name}"' for name in watched)
        statements.append(f'DROP TRIGGER IF EXISTS knowledge_changed ON "{table}"')
        statements.append(
            f"CREATE TRIGGER knowledge_changed AFTER INSERT OR DELETE OR UPDATE OF {columns} "
            f"ON \"{table}\" FOR EACH ROW EXECUTE FUNCTION knowledge_project_changed('{scope}')"
        )
    return statements


def install_outbox(connection, rules=None):
    """Устанавливает единую функцию и триггеры выбранной версии реестра."""
    for statement in outbox_statements(rules):
        connection.execute(text(statement))


def register_outbox_ddl(metadata):
    """create_all в изолированных тестах создаёт тот же механизм, что Alembic."""

    @event.listens_for(metadata, "after_create")
    def after_create(target, connection, **kwargs):
        if connection.dialect.name == "postgresql":
            install_outbox(connection)
