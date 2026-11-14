"""DDL событий проекта: действует для HTTP, MCP и составных транзакций.

Как и knowledge outbox, триггеры записывают только факты в PostgreSQL.
Сеть здесь не используется. Для существующих проектов чаты не создаются.
"""

from sqlalchemy import event, text

CHAT_FUNCTIONS = (
    """
CREATE OR REPLACE FUNCTION chat_entities_invalidated() RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME = 'projects' THEN
        PERFORM chat_emit(NEW.id, 'entities.refresh', '{}'::jsonb);
    ELSE
        PERFORM chat_emit(NEW.project_id, 'entities.refresh', '{}'::jsonb);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
""",
    """
CREATE OR REPLACE FUNCTION chat_emit(pid integer, kind text, data jsonb) RETURNS void AS $$
DECLARE cid integer; next_seq bigint;
BEGIN
    UPDATE project_chats SET last_event_seq = last_event_seq + 1, updated_at = now()
        WHERE project_id = pid RETURNING id, last_event_seq INTO cid, next_seq;
    IF cid IS NOT NULL THEN
        INSERT INTO chat_events(chat_id, seq, event_type, payload)
            VALUES (cid, next_seq, kind, data);
    END IF;
END;
$$ LANGUAGE plpgsql;
""",
    """
CREATE OR REPLACE FUNCTION chat_project_created() RETURNS trigger AS $$
BEGIN
    INSERT INTO project_chats(project_id) VALUES (NEW.id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
""",
    """
CREATE OR REPLACE FUNCTION chat_membership_changed() RETURNS trigger AS $$
DECLARE cid integer; boundary bigint;
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM chat_read_states WHERE user_id = OLD.user_id AND chat_id IN
            (SELECT id FROM project_chats WHERE project_id = OLD.project_id);
        PERFORM chat_emit(OLD.project_id, 'member.removed', jsonb_build_object('user_id', OLD.user_id));
    ELSE
        IF TG_OP = 'INSERT' THEN
            SELECT id INTO cid FROM project_chats WHERE project_id = NEW.project_id FOR UPDATE;
            IF cid IS NOT NULL THEN
                SELECT coalesce(max(seq), 0) INTO boundary FROM chat_messages WHERE chat_id = cid;
                INSERT INTO chat_read_states(chat_id, user_id, last_read_seq)
                    VALUES (cid, NEW.user_id, boundary)
                    ON CONFLICT (chat_id, user_id) DO UPDATE SET last_read_seq = excluded.last_read_seq;
            END IF;
        END IF;
        PERFORM chat_emit(NEW.project_id, 'member.changed', jsonb_build_object('user_id', NEW.user_id));
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
""",
    """
CREATE OR REPLACE FUNCTION chat_entity_changed() RETURNS trigger AS $$
DECLARE item jsonb; pid integer; source_kind text; document_reference text;
BEGIN
    IF TG_OP = 'DELETE' THEN item := to_jsonb(OLD); ELSE item := to_jsonb(NEW); END IF;
    pid := (item->>'project_id')::integer;
    IF TG_ARGV[0] = 'DOCUMENT' THEN
        source_kind := CASE TG_TABLE_NAME WHEN 'documents' THEN 'document'
            WHEN 'project_files' THEN 'project_file' ELSE 'attachment' END;
        IF TG_TABLE_NAME = 'task_attachments' THEN
            SELECT project_id INTO pid FROM tasks WHERE id = (item->>'task_id')::integer;
        ELSIF TG_TABLE_NAME = 'documents' AND pid IS NULL THEN
            SELECT project_id INTO pid FROM tasks WHERE id = (item->>'owner_task_id')::integer;
        END IF;
        document_reference := 'd1_' || rtrim(translate(encode(convert_to(
            source_kind || ':' || (item->>'id'), 'UTF8'), 'base64'), '+/', '-_'), '=');
        PERFORM chat_emit(pid, 'entity.updated', jsonb_build_object(
            'entity_type', 'DOCUMENT', 'document_ref', document_reference));
    ELSE
        PERFORM chat_emit(pid, 'entity.updated',
            jsonb_build_object('entity_type', TG_ARGV[0], 'entity_id', (item->>'id')::integer));
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
""",
    """
CREATE OR REPLACE FUNCTION chat_user_changed() RETURNS trigger AS $$
DECLARE pid integer;
BEGIN
    FOR pid IN SELECT project_id FROM project_members WHERE user_id = NEW.id ORDER BY project_id LOOP
        PERFORM chat_emit(pid, CASE WHEN NEW.is_active THEN 'member.changed' ELSE 'member.removed' END,
            jsonb_build_object('user_id', NEW.id));
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
""",
)

CHAT_TRIGGERS = (
    "CREATE OR REPLACE TRIGGER chat_project_key AFTER UPDATE OF key ON projects FOR EACH ROW EXECUTE FUNCTION chat_entities_invalidated()",
    "CREATE OR REPLACE TRIGGER chat_stage AFTER UPDATE OF name ON project_stages FOR EACH ROW EXECUTE FUNCTION chat_entities_invalidated()",
    "CREATE OR REPLACE TRIGGER chat_new_project AFTER INSERT ON projects FOR EACH ROW EXECUTE FUNCTION chat_project_created()",
    "CREATE OR REPLACE TRIGGER chat_membership AFTER INSERT OR UPDATE OR DELETE ON project_members FOR EACH ROW EXECUTE FUNCTION chat_membership_changed()",
    "CREATE OR REPLACE TRIGGER chat_user AFTER UPDATE OF is_active, first_name, last_name, username ON users FOR EACH ROW EXECUTE FUNCTION chat_user_changed()",
    *(
        f"CREATE OR REPLACE TRIGGER chat_entity AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION chat_entity_changed('{kind}')"
        for table, kind in (
            ("tasks", "TASK"),
            ("documents", "DOCUMENT"),
            ("project_files", "DOCUMENT"),
            ("task_attachments", "DOCUMENT"),
            ("project_risks", "RISK"),
            ("project_milestones", "MILESTONE"),
            ("wbs_nodes", "WBS_NODE"),
        )
    ),
)


def install_chat_ddl(connection) -> None:
    """Устанавливает lifecycle и атомарную фиксацию изменений сущностей."""
    for statement in (*CHAT_FUNCTIONS, *CHAT_TRIGGERS):
        connection.execute(text(statement))


def register_chat_ddl(metadata) -> None:
    """create_all тестов получает те же ограничения и события, что миграция."""

    @event.listens_for(metadata, "after_create")
    def after_create(target, connection, **kwargs):
        if connection.dialect.name == "postgresql":
            install_chat_ddl(connection)
