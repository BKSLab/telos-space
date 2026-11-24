"""Транзакционный журнал проектных действий, общий для HTTP, MCP и Ариса.

Здесь намеренно перечислены только доменные публичные поля. Данные auth,
переписка, Git, содержимое файлов и геометрия свободных досок не читаются.
"""

from sqlalchemy import event, inspect, text

from src.db import project_history_actor as _project_history_actor  # noqa: F401

FIELD_LABEL_FUNCTION = r"""
CREATE OR REPLACE FUNCTION project_history_field_label(field text) RETURNS text AS $$
SELECT CASE field
    WHEN 'title' THEN 'Название' WHEN 'name' THEN 'Название' WHEN 'key' THEN 'Ключ проекта'
    WHEN 'owner_id' THEN 'Владелец' WHEN 'owner_user_id' THEN 'Ответственный'
    WHEN 'status' THEN 'Состояние' WHEN 'start_date' THEN 'Дата начала' WHEN 'due_date' THEN 'Срок'
    WHEN 'baseline_start_date' THEN 'Базовая дата начала' WHEN 'baseline_due_date' THEN 'Базовый срок'
    WHEN 'description_md' THEN 'Описание' WHEN 'description' THEN 'Описание'
    WHEN 'description_sections' THEN 'Паспорт проекта' WHEN 'content_md' THEN 'Текст документа'
    WHEN 'problem' THEN 'Проблема' WHEN 'goal' THEN 'Цель' WHEN 'expected_result' THEN 'Ожидаемый результат'
    WHEN 'additional' THEN 'Дополнительные сведения' WHEN 'audience' THEN 'Аудитория'
    WHEN 'capabilities' THEN 'Возможности' WHEN 'boundaries' THEN 'Границы' WHEN 'constraints' THEN 'Ограничения'
    WHEN 'glossary' THEN 'Термины' WHEN 'decisions' THEN 'Решения' WHEN 'open_questions' THEN 'Открытые вопросы'
    WHEN 'priority' THEN 'Приоритет' WHEN 'assignee' THEN 'Исполнитель' WHEN 'role' THEN 'Роль'
    WHEN 'stage_id' THEN 'Стадия' WHEN 'wbs_node_id' THEN 'Раздел ИСР' WHEN 'parent_id' THEN 'Родительский раздел ИСР'
    WHEN 'position' THEN 'Порядок' WHEN 'order_index' THEN 'Порядок' WHEN 'wbs_position' THEN 'Порядок в ИСР'
    WHEN 'canvas_x' THEN 'Размещение на холсте ИСР' WHEN 'canvas_y' THEN 'Размещение на холсте ИСР'
    WHEN 'checklist' THEN 'Чек-лист' WHEN 'color' THEN 'Цвет' WHEN 'icon' THEN 'Значок'
    WHEN 'is_done_stage' THEN 'Завершающая стадия' WHEN 'probability' THEN 'Вероятность'
    WHEN 'impact' THEN 'Влияние' WHEN 'risk_level' THEN 'Уровень риска' WHEN 'response_strategy' THEN 'Стратегия реагирования'
    WHEN 'mitigation_plan' THEN 'План снижения риска' WHEN 'response_plan' THEN 'План реагирования'
    WHEN 'review_date' THEN 'Дата пересмотра' WHEN 'source' THEN 'Источник' WHEN 'task_id' THEN 'Связь с задачей'
    WHEN 'caption' THEN 'Примечание' WHEN 'slug' THEN 'Адрес документа' WHEN 'display_title' THEN 'Название'
    WHEN 'original_name' THEN 'Имя файла' WHEN 'content_hash' THEN 'Содержимое файла'
    WHEN 'storage_key' THEN 'Содержимое файла' WHEN 'size' THEN 'Размер файла' WHEN 'content_type' THEN 'Тип файла'
    WHEN 'deleted_at' THEN 'Удаление' WHEN 'archived_at' THEN 'Архив'
    WHEN 'document_id' THEN 'Связь с документом' WHEN 'project_file_id' THEN 'Связь с файлом проекта'
    WHEN 'attachment_id' THEN 'Связь с вложением' WHEN 'comment_id' THEN 'Связь с комментарием'
    WHEN 'predecessor_task_id' THEN 'Предшествующая задача' WHEN 'successor_task_id' THEN 'Последующая задача'
    WHEN 'dependency_type' THEN 'Тип зависимости' WHEN 'lag_days' THEN 'Задержка в днях'
    WHEN 'user_id' THEN 'Пользователь' WHEN 'project_member_id' THEN 'Участник проекта'
    WHEN 'body' THEN 'Текст стикера' WHEN 'body_md' THEN 'Текст' WHEN 'kind' THEN 'Вид фигуры'
    WHEN 'label' THEN 'Подпись фигуры' WHEN 'start_ref' THEN 'Начало связи' WHEN 'end_ref' THEN 'Конец связи'
    WHEN 'sticker_id' THEN 'Связь со стикером' ELSE 'Содержимое' END
$$ LANGUAGE sql IMMUTABLE;
"""

EMIT_FUNCTION = r"""
CREATE OR REPLACE FUNCTION project_history_emit(
    pid integer, event_kind text, action text, heading text,
    details text DEFAULT NULL, tid integer DEFAULT NULL,
    before_value text DEFAULT NULL, after_value text DEFAULT NULL,
    source_kind text DEFAULT NULL, source_key integer DEFAULT NULL
) RETURNS void AS $$
DECLARE eid bigint;
BEGIN
    IF pid IS NULL OR NOT EXISTS (SELECT 1 FROM projects WHERE id = pid) THEN RETURN; END IF;
    eid := nextval(pg_get_serial_sequence('project_history_events', 'id'));
    INSERT INTO project_history_events
        (id, project_id, public_id, kind, event_type, title, detail, task_id,
         actor_user_id, actor_name, from_value, to_value, entity_type, entity_id,
         source_table, source_id, created_at, transaction_id)
    VALUES (eid, pid, 'project_event:' || eid, event_kind, action, heading, left(details, 500), tid,
        nullif(current_setting('telos.actor_user_id', true), '')::integer,
        nullif(current_setting('telos.actor_name', true), ''),
        left(before_value, 500), left(after_value, 500), source_kind, source_key,
        source_kind, source_key, statement_timestamp(), txid_current());
END;
$$ LANGUAGE plpgsql;
"""

LEGACY_FUNCTION = r"""
CREATE OR REPLACE FUNCTION project_history_snapshot_source(
    source text, item jsonb, pid integer, task_title text DEFAULT NULL, tid integer DEFAULT NULL
) RETURNS void AS $$
DECLARE key text; category text; heading text; details text; action text;
    author_id integer; author_name text; before_value text; after_value text;
    cid integer; rid integer; rev integer; docref text;
BEGIN
    IF pid IS NULL OR NOT EXISTS (SELECT 1 FROM projects WHERE id = pid) THEN RETURN; END IF;
    IF source = 'tasks' THEN
        key := 'task_created:' || (item->>'id'); category := 'task_activity';
        heading := 'Создана задача · ' || (item->>'title'); action := 'TASK_CREATED';
    ELSIF source = 'task_activity' THEN
        action := item->>'event_type';
        IF action IN ('COMMENT_ADDED', 'TASK_MOVED', 'LINKS_REMOVED') THEN RETURN; END IF;
        key := 'task_activity:' || (item->>'id'); category := 'task_activity';
        heading := CASE action
            WHEN 'STAGE_CHANGED' THEN 'Смена стадии' WHEN 'DUE_DATE_CHANGED' THEN 'Изменение срока'
            WHEN 'START_DATE_CHANGED' THEN 'Изменение даты начала' WHEN 'DATES_CHANGED' THEN 'Изменение дат'
            WHEN 'BASELINE_CHANGED' THEN 'Изменение базового плана' WHEN 'DESCRIPTION_CHANGED' THEN 'Изменение описания'
            WHEN 'PRIORITY_CHANGED' THEN 'Изменение приоритета' WHEN 'ASSIGNEE_CHANGED' THEN 'Изменение исполнителя'
            WHEN 'WBS_NODE_CHANGED' THEN 'Изменение размещения' WHEN 'CHECKLIST_CHANGED' THEN 'Изменение чек-листа'
            ELSE 'Изменение задачи' END || ' · ' || coalesce(task_title, 'Задача');
        before_value := item->>'from_value'; after_value := item->>'to_value';
        details := concat_ws(' · ', nullif(concat_ws(' → ', before_value, after_value), ''), item->>'reason');
        author_id := (item->>'actor_user_id')::integer; author_name := item->>'actor_name';
    ELSIF source IN ('task_comments', 'task_reports') THEN
        category := CASE WHEN source = 'task_comments' THEN 'task_comment' ELSE 'task_report' END;
        key := category || ':' || (item->>'id');
        heading := CASE WHEN source = 'task_comments' THEN 'Добавлен комментарий' ELSE 'Опубликован отчёт' END || ' · ' || coalesce(task_title, 'Задача');
        details := item->>'body_md'; author_id := (item->>'author_id')::integer;
        author_name := item->>'author_name';
        IF source = 'task_comments' THEN cid := (item->>'id')::integer; action := 'COMMENT_ADDED';
        ELSE rid := (item->>'id')::integer; rev := (item->>'revision')::integer;
            heading := heading || ' · v' || rev; action := 'REPORT_PUBLISHED'; END IF;
    ELSIF source = 'project_context_history' THEN
        IF (item->>'revision')::integer <= 0 THEN RETURN; END IF;
        category := 'project_context'; key := category || ':' || pid || ':' || (item->>'revision');
        heading := 'Изменение паспорта проекта';
        SELECT string_agg(project_history_field_label(value), ', ' ORDER BY ordinality) INTO details
            FROM jsonb_array_elements_text(coalesce(item->'changed_fields', '[]'::jsonb)) WITH ORDINALITY AS changes(value, ordinality);
        author_id := (item->>'changed_by_user_id')::integer; author_name := item->>'changed_by_name';
        action := 'PROJECT_CONTEXT_CHANGED';
    ELSIF source = 'project_deadline_changes' THEN
        category := 'project_deadline'; key := category || ':' || (item->>'id');
        heading := 'Изменение срока проекта'; before_value := item->>'previous_due_date'; after_value := item->>'new_due_date';
        details := concat_ws(' · ', coalesce(before_value, '—') || ' → ' || coalesce(after_value, '—'), item->>'comment');
        author_id := (item->>'changed_by_user_id')::integer; author_name := item->>'changed_by_name';
        action := 'PROJECT_DEADLINE_CHANGED';
    ELSIF source = 'project_risks' THEN
        category := 'risk_created'; key := category || ':' || (item->>'id');
        heading := 'Создан риск · ' || (item->>'title'); action := 'RISK_CREATED';
    ELSIF source IN ('documents', 'project_files') THEN
        IF source = 'documents' AND ((item->>'owner_task_id') IS NOT NULL OR (item->>'origin_attachment_id') IS NOT NULL) THEN RETURN; END IF;
        IF source = 'project_files' AND (item->>'deleted_at') IS NOT NULL THEN RETURN; END IF;
        category := 'document_created';
        key := CASE WHEN source = 'documents' THEN 'document:' ELSE 'project_file:' END || (item->>'id');
        heading := CASE WHEN source = 'documents' THEN 'Создан документ · ' ELSE 'Загружен файл · ' END || coalesce(item->>'title', item->>'display_title', item->>'original_name');
        author_id := coalesce((item->>'authored_by_user_id')::integer, (item->>'uploaded_by_user_id')::integer);
        SELECT nullif(concat_ws(' ', last_name, first_name, middle_name), '') INTO author_name FROM users WHERE id = author_id;
        action := CASE WHEN source = 'documents' THEN 'DOCUMENT_CREATED' ELSE 'PROJECT_FILE_UPLOADED' END;
        docref := CASE WHEN source = 'documents' THEN 'document:' ELSE 'project_file:' END || (item->>'id');
    ELSE RETURN;
    END IF;
    INSERT INTO project_history_events
        (project_id, public_id, kind, event_type, title, detail, actor_user_id, actor_name,
         task_id, entity_type, entity_id, source_table, source_id, document_ref, comment_id,
         report_id, report_revision, from_value, to_value, created_at, transaction_id)
    VALUES (pid, key, category, action, heading, left(details,500), author_id, left(author_name,350),
        tid, source, (item->>'id')::integer, source, (item->>'id')::integer, docref, cid,
        rid, rev, left(before_value,500), left(after_value,500), (item->>'created_at')::timestamptz, txid_current())
    ON CONFLICT (project_id, public_id) DO NOTHING;
END;
$$ LANGUAGE plpgsql;
"""

TASK_SNAPSHOT_FUNCTION = r"""
CREATE OR REPLACE FUNCTION project_history_snapshot_task(item jsonb) RETURNS void AS $$
DECLARE tid integer := (item->>'id')::integer; pid integer := (item->>'project_id')::integer; row_data jsonb;
BEGIN
    -- A legacy transfer marker proves that the old project scope is unknown.
    IF pid IS NULL OR EXISTS (SELECT 1 FROM task_activity WHERE task_id = tid AND event_type::text = 'TASK_MOVED')
        OR EXISTS (SELECT 1 FROM project_history_events WHERE task_id = tid
            AND event_type IN ('TASK_TRANSFERRED_IN', 'TASK_TRANSFERRED_OUT')) THEN RETURN; END IF;
    IF NOT EXISTS (SELECT 1 FROM project_history_events WHERE project_id = pid AND public_id = 'task_created:' || tid) THEN
        PERFORM project_history_snapshot_source('tasks', item, pid, item->>'title', tid);
    END IF;
    FOR row_data IN SELECT to_jsonb(a) FROM task_activity a WHERE a.task_id = tid
        AND a.event_type::text NOT IN ('COMMENT_ADDED', 'TASK_MOVED', 'LINKS_REMOVED')
        AND NOT EXISTS (SELECT 1 FROM project_history_events h WHERE h.project_id = pid AND h.public_id = 'task_activity:' || a.id)
    LOOP
        PERFORM project_history_snapshot_source('task_activity', row_data, pid, item->>'title', tid);
    END LOOP;
    FOR row_data IN SELECT to_jsonb(c) FROM task_comments c WHERE c.task_id = tid
        AND NOT EXISTS (SELECT 1 FROM project_history_events h WHERE h.project_id = pid AND h.public_id = 'task_comment:' || c.id)
    LOOP
        PERFORM project_history_snapshot_source('task_comments', row_data, pid, item->>'title', tid);
    END LOOP;
    FOR row_data IN SELECT to_jsonb(r) FROM task_reports r WHERE r.task_id = tid
        AND NOT EXISTS (SELECT 1 FROM project_history_events h WHERE h.project_id = pid AND h.public_id = 'task_report:' || r.id)
    LOOP
        PERFORM project_history_snapshot_source('task_reports', row_data, pid, item->>'title', tid);
    END LOOP;
END;
$$ LANGUAGE plpgsql;
"""

CAPTURE_FUNCTION = r"""
CREATE OR REPLACE FUNCTION project_history_capture() RETURNS trigger AS $$
DECLARE item jsonb; previous jsonb; pid integer; old_pid integer; tid integer; task_title text;
    category text := TG_ARGV[0]; noun text := TG_ARGV[1]; prefix text := TG_ARGV[2];
    field text; watched text[]; changed text[] := ARRAY[]::text[]; action text; heading text; details text;
    before_value text; after_value text; new_actor jsonb; source_public_id text;
    action_label text; field_labels text[] := ARRAY[]::text[]; gender text;
    pending_activities jsonb; consumed_activity text; consumed_activities text[] := ARRAY[]::text[];
    canonical_id bigint; replaced_count integer; latest_generated timestamptz; matched_activity boolean;
BEGIN
    IF TG_OP <> 'INSERT' THEN previous := to_jsonb(OLD); END IF;
    IF TG_OP = 'DELETE' THEN item := previous; ELSE item := to_jsonb(NEW); END IF;
    tid := coalesce((item->>'task_id')::integer, (item->>'owner_task_id')::integer);
    IF TG_TABLE_NAME = 'tasks' THEN tid := (item->>'id')::integer; END IF;
    pid := (item->>'project_id')::integer;
    IF TG_TABLE_NAME = 'projects' THEN pid := (item->>'id')::integer; END IF;
    IF tid IS NOT NULL AND TG_TABLE_NAME <> 'tasks' THEN
        SELECT project_id, title INTO pid, task_title FROM tasks WHERE id = tid;
        -- Risks and dependencies retain their own project even while detaching a moved task.
        pid := coalesce((item->>'project_id')::integer, pid);
    END IF;
    IF TG_TABLE_NAME = 'tasks' THEN task_title := item->>'title'; END IF;
    IF TG_TABLE_NAME = 'board_shapes' OR TG_TABLE_NAME = 'project_stickers' THEN
        SELECT coalesce((item->>'project_id')::integer, b.project_id, h.project_id) INTO pid
            FROM (SELECT 1) anchor LEFT JOIN project_boards b ON b.id = (item->>'board_id')::integer
            LEFT JOIN project_history_events h ON h.entity_type = 'project_boards' AND h.entity_id = (item->>'board_id')::integer
                AND h.transaction_id = txid_current() AND h.event_type = 'BOARD_DELETED' LIMIT 1;
    ELSIF TG_TABLE_NAME = 'project_sticker_task_links' THEN
        SELECT coalesce(b.project_id, h.project_id) INTO pid FROM (SELECT 1) anchor
            LEFT JOIN project_stickers s ON s.id = (item->>'sticker_id')::integer
            LEFT JOIN project_boards b ON b.id = s.board_id
            LEFT JOIN project_history_events h ON h.entity_type = 'project_stickers' AND h.entity_id = (item->>'sticker_id')::integer
                AND h.transaction_id = txid_current() AND h.event_type = 'STICKER_DELETED' LIMIT 1;
    ELSIF TG_TABLE_NAME IN ('document_links', 'task_comment_documents') THEN
        SELECT coalesce(d.project_id, f.project_id) INTO pid
            FROM (SELECT 1) anchor LEFT JOIN documents d ON d.id = (item->>'document_id')::integer
            LEFT JOIN project_files f ON f.id = (item->>'project_file_id')::integer;
    END IF;

    IF TG_WHEN = 'BEFORE' THEN
        IF TG_TABLE_NAME = 'project_boards' AND TG_OP = 'DELETE' THEN
            PERFORM project_history_emit(pid, category, 'BOARD_DELETED', 'Удалена доска · ' || (item->>'title'), NULL, NULL, NULL, NULL, TG_TABLE_NAME, (item->>'id')::integer);
        ELSIF TG_TABLE_NAME = 'project_stickers' AND TG_OP = 'DELETE' THEN
            PERFORM project_history_emit(pid, category, 'STICKER_DELETED', 'Удалён стикер · #' || (item->>'id'), NULL, NULL, NULL, NULL, TG_TABLE_NAME, (item->>'id')::integer);
        END IF;
        IF TG_TABLE_NAME = 'tasks' THEN
            -- Only these mutations invalidate the legacy title/project scope.
            -- Ordinary field and bookkeeping updates never rescan task history.
            IF TG_OP = 'DELETE' OR previous->'title' IS DISTINCT FROM item->'title'
                OR previous->'project_id' IS DISTINCT FROM item->'project_id' THEN
                PERFORM project_history_snapshot_task(previous);
            END IF;
        ELSIF TG_TABLE_NAME IN ('task_comments', 'task_reports', 'task_activity') THEN
            source_public_id := CASE TG_TABLE_NAME WHEN 'task_comments' THEN 'task_comment:'
                WHEN 'task_reports' THEN 'task_report:' ELSE 'task_activity:' END || (previous->>'id');
            -- A transferred task cannot prove the project of an old child row.
            -- Both service markers and durable raw-SQL transfer facts survive
            -- the transfer transaction. New target children are already frozen
            -- by their own INSERT trigger and need no legacy reconstruction.
            IF NOT EXISTS (SELECT 1 FROM project_history_events WHERE project_id = pid AND public_id = source_public_id)
                AND NOT EXISTS (SELECT 1 FROM task_activity WHERE task_id = tid AND event_type::text = 'TASK_MOVED')
                AND NOT EXISTS (SELECT 1 FROM project_history_events WHERE task_id = tid
                    AND event_type IN ('TASK_TRANSFERRED_IN', 'TASK_TRANSFERRED_OUT')) THEN
                PERFORM project_history_snapshot_source(TG_TABLE_NAME, previous, pid, task_title, tid);
            END IF;
        ELSE
            PERFORM project_history_snapshot_source(TG_TABLE_NAME, previous, pid, task_title, tid);
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME = 'tasks' AND TG_OP = 'UPDATE' AND previous->'project_id' IS DISTINCT FROM item->'project_id' THEN
        old_pid := (previous->>'project_id')::integer;
        PERFORM project_history_emit(old_pid, 'task_activity', 'TASK_TRANSFERRED_OUT', 'Задача перенесена из проекта · ' || (previous->>'title'), NULL, tid, NULL, NULL, 'tasks', tid);
        PERFORM project_history_emit(pid, 'task_activity', 'TASK_TRANSFERRED_IN', 'Задача перенесена в проект', NULL, tid, NULL, NULL, 'tasks', tid);
        RETURN NULL;
    END IF;
    IF pid IS NULL OR NOT EXISTS (SELECT 1 FROM projects WHERE id = pid) THEN RETURN NULL; END IF;
    IF TG_TABLE_NAME IN ('project_boards', 'project_stickers') AND TG_OP = 'DELETE' THEN RETURN NULL; END IF;
    IF TG_TABLE_NAME = 'task_participants' AND EXISTS (
        SELECT 1 FROM project_history_events WHERE task_id = tid AND transaction_id = txid_current()
        AND event_type IN ('TASK_TRANSFERRED_IN', 'TASK_TRANSFERRED_OUT')) THEN RETURN NULL; END IF;

    IF TG_TABLE_NAME = 'task_activity' THEN
        IF TG_OP <> 'INSERT' OR item->>'event_type' IN ('COMMENT_ADDED', 'TASK_MOVED', 'LINKS_REMOVED') THEN RETURN NULL; END IF;
        IF EXISTS (SELECT 1 FROM project_history_events WHERE task_id = tid AND transaction_id = txid_current() AND event_type IN ('TASK_TRANSFERRED_IN', 'TASK_TRANSFERRED_OUT')) THEN RETURN NULL; END IF;
        action := item->>'event_type';
        -- Only the latest matching statement belongs to this action. Earlier raw
        -- changes in the same transaction remain separate historical facts.
        SELECT max(created_at) INTO latest_generated FROM project_history_events
            WHERE project_id = pid AND task_id = tid AND transaction_id = txid_current()
            AND public_id LIKE 'project_event:%' AND (event_type = action
                OR (action = 'DATES_CHANGED' AND event_type IN ('START_DATE_CHANGED', 'DUE_DATE_CHANGED')));
        DELETE FROM project_history_events WHERE project_id = pid AND task_id = tid AND transaction_id = txid_current()
            AND created_at = latest_generated
            AND public_id LIKE 'project_event:%' AND (event_type = action
                OR (action = 'DATES_CHANGED' AND event_type IN ('START_DATE_CHANGED', 'DUE_DATE_CHANGED')));
        GET DIAGNOSTICS replaced_count = ROW_COUNT;
        PERFORM project_history_snapshot_source(TG_TABLE_NAME, item, pid, task_title, tid);
        IF nullif(current_setting('telos.actor_user_id',true),'') IS NOT NULL THEN
            UPDATE project_history_events SET actor_user_id = nullif(current_setting('telos.actor_user_id',true),'')::integer,
                actor_name = nullif(current_setting('telos.actor_name',true),'')
                WHERE project_id = pid AND public_id = 'task_activity:' || (item->>'id') AND transaction_id = txid_current();
        END IF;
        IF replaced_count = 0 THEN
            -- Some shared services write rich activity before updating the task.
            -- Pending IDs are transaction-local and only actual inserts enter
            -- them; freezing legacy sources cannot suppress a later mutation.
            SELECT id INTO canonical_id FROM project_history_events
                WHERE project_id = pid AND public_id = 'task_activity:' || (item->>'id');
            pending_activities := coalesce(nullif(current_setting('telos.history_pending_activities', true), ''), '{}')::jsonb;
            PERFORM set_config('telos.history_pending_activities',
                (pending_activities || jsonb_build_object(canonical_id::text, true))::text, true);
        END IF;
        RETURN NULL;
    END IF;

    watched := string_to_array(TG_ARGV[3], ',');
    IF TG_OP = 'UPDATE' THEN
        FOREACH field IN ARRAY watched LOOP
            IF previous->field IS DISTINCT FROM item->field THEN changed := array_append(changed, field); END IF;
        END LOOP;
        IF cardinality(changed) = 0 THEN RETURN NULL; END IF;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF TG_TABLE_NAME IN ('tasks', 'task_comments', 'task_reports', 'project_context_history', 'project_deadline_changes', 'project_risks', 'documents', 'project_files') THEN
            -- Authenticated actions carry the actor from this DML, never a role-holder inferred as author.
            new_actor := item;
            IF TG_TABLE_NAME = 'tasks' THEN
                PERFORM project_history_snapshot_source(TG_TABLE_NAME, item, pid, task_title, tid);
                UPDATE project_history_events SET actor_user_id = nullif(current_setting('telos.actor_user_id',true),'')::integer,
                    actor_name = nullif(current_setting('telos.actor_name',true),'') WHERE project_id = pid AND public_id = 'task_created:' || tid AND transaction_id = txid_current();
                RETURN NULL;
            END IF;
            IF TG_TABLE_NAME IN ('task_comments', 'task_reports', 'project_context_history', 'project_deadline_changes', 'project_risks', 'project_files') OR (TG_TABLE_NAME = 'documents' AND item->>'owner_task_id' IS NULL AND item->>'origin_attachment_id' IS NULL) THEN
                IF TG_TABLE_NAME IN ('project_context_history', 'project_deadline_changes') THEN
                    DELETE FROM project_history_events WHERE project_id = pid AND transaction_id = txid_current()
                        AND public_id LIKE 'project_event:%' AND event_type = CASE TG_TABLE_NAME
                            WHEN 'project_context_history' THEN 'PROJECT_CONTEXT_CHANGED' ELSE 'PROJECT_DEADLINE_CHANGED' END;
                END IF;
                PERFORM project_history_snapshot_source(TG_TABLE_NAME, new_actor, pid, task_title, tid);
                IF nullif(current_setting('telos.actor_user_id',true),'') IS NOT NULL THEN
                    source_public_id := CASE TG_TABLE_NAME
                        WHEN 'project_risks' THEN 'risk_created:' || (item->>'id')
                        WHEN 'task_comments' THEN 'task_comment:' || (item->>'id')
                        WHEN 'task_reports' THEN 'task_report:' || (item->>'id')
                        WHEN 'project_context_history' THEN 'project_context:' || pid || ':' || (item->>'revision')
                        WHEN 'project_deadline_changes' THEN 'project_deadline:' || (item->>'id')
                        WHEN 'project_files' THEN 'project_file:' || (item->>'id')
                        WHEN 'documents' THEN 'document:' || (item->>'id') END;
                    UPDATE project_history_events SET actor_user_id = nullif(current_setting('telos.actor_user_id',true),'')::integer,
                        actor_name = nullif(current_setting('telos.actor_name',true),'')
                        WHERE project_id = pid AND public_id = source_public_id AND transaction_id = txid_current();
                END IF;
                RETURN NULL;
            END IF;
        END IF;
    END IF;

    heading := noun || ' · ' || coalesce(item->>'title', item->>'name', item->>'display_title', item->>'original_name', task_title, '#' || coalesce(item->>'id', item->>'comment_id', ''));
    IF TG_TABLE_NAME = 'projects' AND TG_OP = 'UPDATE' THEN
        IF 'due_date' = ANY(changed) THEN
            PERFORM project_history_emit(pid, 'project_deadline', 'PROJECT_DEADLINE_CHANGED', 'Изменение срока проекта', NULL, NULL, previous->>'due_date', item->>'due_date', 'projects', pid);
            changed := array_remove(changed, 'due_date');
        END IF;
        IF 'description_md' = ANY(changed) OR 'description_sections' = ANY(changed) THEN
            PERFORM project_history_emit(pid, 'project_context', 'PROJECT_CONTEXT_CHANGED', 'Изменение паспорта проекта', NULL, NULL, NULL, NULL, 'projects', pid);
            changed := array_remove(array_remove(changed, 'description_md'), 'description_sections');
        END IF;
        IF cardinality(changed) = 0 THEN RETURN NULL; END IF;
    END IF;
    IF TG_TABLE_NAME = 'tasks' AND TG_OP = 'UPDATE' THEN
        pending_activities := coalesce(nullif(current_setting('telos.history_pending_activities', true), ''), '{}')::jsonb;
        FOREACH field IN ARRAY changed LOOP
            IF field IN ('canvas_x', 'canvas_y') THEN
                IF field = 'canvas_y' OR ((previous->>'canvas_x') IS NULL) = ((item->>'canvas_x') IS NULL) THEN CONTINUE; END IF;
            END IF;
            action := CASE field WHEN 'title' THEN 'TITLE_CHANGED' WHEN 'stage_id' THEN 'STAGE_CHANGED'
                WHEN 'due_date' THEN 'DUE_DATE_CHANGED' WHEN 'start_date' THEN 'START_DATE_CHANGED'
                WHEN 'baseline_start_date' THEN 'BASELINE_CHANGED' WHEN 'baseline_due_date' THEN 'BASELINE_CHANGED'
                WHEN 'description_md' THEN 'DESCRIPTION_CHANGED' WHEN 'priority' THEN 'PRIORITY_CHANGED'
                WHEN 'assignee' THEN 'ASSIGNEE_CHANGED' WHEN 'wbs_node_id' THEN 'WBS_NODE_CHANGED'
                WHEN 'wbs_position' THEN 'WBS_ORDER_CHANGED' WHEN 'position' THEN 'TASK_ORDER_CHANGED'
                WHEN 'canvas_x' THEN 'TASK_PLACEMENT_CHANGED'
                WHEN 'checklist' THEN 'CHECKLIST_CHANGED' ELSE 'TASK_' || upper(field) || '_CHANGED' END;
            matched_activity := false;
            FOR consumed_activity IN
                SELECT id::text FROM project_history_events WHERE project_id = pid AND task_id = tid
                    AND transaction_id = txid_current() AND public_id LIKE 'task_activity:%'
                    AND pending_activities ? id::text
                    AND (event_type = action OR (event_type = 'DATES_CHANGED' AND action IN ('START_DATE_CHANGED', 'DUE_DATE_CHANGED')))
            LOOP
                consumed_activities := array_append(consumed_activities, consumed_activity);
                matched_activity := true;
            END LOOP;
            IF matched_activity THEN CONTINUE; END IF;
            before_value := previous->>field; after_value := item->>field;
            IF field = 'checklist' THEN before_value := NULL; after_value := NULL; END IF;
            IF field = 'stage_id' THEN
                SELECT name INTO before_value FROM project_stages WHERE id = (previous->>'stage_id')::integer AND project_id = pid;
                SELECT name INTO after_value FROM project_stages WHERE id = (item->>'stage_id')::integer AND project_id = pid;
            ELSIF field = 'wbs_node_id' THEN
                SELECT title INTO before_value FROM wbs_nodes WHERE id = (previous->>'wbs_node_id')::integer AND project_id = pid;
                SELECT title INTO after_value FROM wbs_nodes WHERE id = (item->>'wbs_node_id')::integer AND project_id = pid;
            ELSIF field = 'priority' THEN
                before_value := CASE before_value WHEN 'LOW' THEN 'Низкий' WHEN 'MEDIUM' THEN 'Средний' WHEN 'HIGH' THEN 'Высокий' WHEN 'URGENT' THEN 'Срочный' ELSE before_value END;
                after_value := CASE after_value WHEN 'LOW' THEN 'Низкий' WHEN 'MEDIUM' THEN 'Средний' WHEN 'HIGH' THEN 'Высокий' WHEN 'URGENT' THEN 'Срочный' ELSE after_value END;
            ELSIF field IN ('canvas_x', 'position', 'wbs_position') THEN
                before_value := NULL; after_value := NULL;
            END IF;
            IF field = 'baseline_due_date' AND 'baseline_start_date' = ANY(changed) THEN CONTINUE; END IF;
            action_label := CASE action WHEN 'TITLE_CHANGED' THEN 'Переименована задача'
                WHEN 'STAGE_CHANGED' THEN 'Смена стадии' WHEN 'DUE_DATE_CHANGED' THEN 'Изменение срока'
                WHEN 'START_DATE_CHANGED' THEN 'Изменение даты начала' WHEN 'BASELINE_CHANGED' THEN 'Изменение базового плана'
                WHEN 'DESCRIPTION_CHANGED' THEN 'Изменение описания' WHEN 'PRIORITY_CHANGED' THEN 'Изменение приоритета'
                WHEN 'ASSIGNEE_CHANGED' THEN 'Изменение исполнителя' WHEN 'WBS_NODE_CHANGED' THEN 'Изменение размещения в ИСР'
                WHEN 'WBS_ORDER_CHANGED' THEN 'Изменение порядка в ИСР' WHEN 'TASK_ORDER_CHANGED' THEN 'Изменение порядка задач'
                WHEN 'TASK_PLACEMENT_CHANGED' THEN 'Изменение размещения на холсте ИСР'
                WHEN 'CHECKLIST_CHANGED' THEN 'Изменение чек-листа' ELSE 'Изменение задачи' END;
            details := project_history_field_label(field);
            IF field = 'description_md' THEN details := after_value; END IF;
            PERFORM project_history_emit(pid, category, action, action_label || ' · ' || task_title, details, tid, before_value, after_value, TG_TABLE_NAME, tid);
        END LOOP;
        IF cardinality(consumed_activities) > 0 THEN
            PERFORM set_config('telos.history_pending_activities', (pending_activities - consumed_activities)::text, true);
        END IF;
        RETURN NULL;
    END IF;

    action := prefix || CASE TG_OP WHEN 'INSERT' THEN '_CREATED' WHEN 'DELETE' THEN '_DELETED' ELSE '_UPDATED' END;
    IF TG_TABLE_NAME = 'task_comments' THEN
        action := CASE TG_OP WHEN 'DELETE' THEN 'COMMENT_DELETED' ELSE 'COMMENT_EDITED' END;
        IF TG_OP = 'UPDATE' THEN details := item->>'body_md'; END IF;
    ELSIF TG_TABLE_NAME = 'project_boards' AND TG_OP = 'UPDATE' AND 'archived_at' = ANY(changed) THEN
        action := CASE WHEN item->>'archived_at' IS NULL THEN 'BOARD_RESTORED' ELSE 'BOARD_ARCHIVED' END;
    ELSIF TG_TABLE_NAME = 'project_files' AND TG_OP = 'UPDATE' AND previous->>'deleted_at' IS NULL AND item->>'deleted_at' IS NOT NULL THEN
        action := 'PROJECT_FILE_DELETED';
    ELSIF TG_TABLE_NAME = 'project_context_history' OR TG_TABLE_NAME = 'project_deadline_changes' THEN
        RETURN NULL;
    END IF;
    IF details IS NULL AND TG_OP = 'UPDATE' THEN
        FOREACH field IN ARRAY changed LOOP field_labels := array_append(field_labels, project_history_field_label(field)); END LOOP;
        details := array_to_string(field_labels, ', ');
    END IF;
    IF TG_TABLE_NAME = 'task_participants' THEN
        details := concat_ws(' · ', CASE item->>'role' WHEN 'REPORTER' THEN 'Постановщик' WHEN 'EXECUTOR' THEN 'Исполнитель' WHEN 'OBSERVER' THEN 'Наблюдатель' ELSE 'Участник' END,
            'Пользователь #' || coalesce(item->>'user_id', item->>'project_member_id'));
    ELSIF TG_TABLE_NAME = 'project_members' THEN
        details := concat_ws(' · ', CASE item->>'role' WHEN 'OWNER' THEN 'Владелец' ELSE 'Участник' END, 'Пользователь #' || (item->>'user_id'));
    ELSIF TG_TABLE_NAME = 'project_stickers' AND TG_OP <> 'DELETE' THEN
        details := item->>'body';
    ELSIF TG_TABLE_NAME = 'board_shapes' AND TG_OP <> 'DELETE' THEN
        details := item->>'label';
    END IF;
    gender := CASE WHEN TG_TABLE_NAME IN ('tasks', 'project_stages', 'document_links', 'task_dependencies', 'project_milestones', 'project_sticker_task_links', 'board_shapes', 'project_boards') THEN 'f'
        WHEN TG_TABLE_NAME IN ('task_attachments', 'task_comment_attachments') THEN 'n' ELSE 'm' END;
    action_label := CASE TG_OP WHEN 'INSERT' THEN CASE gender WHEN 'f' THEN 'Создана' WHEN 'n' THEN 'Создано' ELSE 'Создан' END
        WHEN 'DELETE' THEN CASE gender WHEN 'f' THEN 'Удалена' WHEN 'n' THEN 'Удалено' ELSE 'Удалён' END
        ELSE CASE gender WHEN 'f' THEN 'Изменена' WHEN 'n' THEN 'Изменено' ELSE 'Изменён' END END;
    IF TG_TABLE_NAME IN ('project_members', 'task_participants') AND TG_OP = 'INSERT' THEN action_label := 'Добавлен'; END IF;
    IF action = 'BOARD_ARCHIVED' THEN action_label := 'Архивирована'; END IF;
    IF action = 'BOARD_RESTORED' THEN action_label := 'Восстановлена'; END IF;
    IF action = 'PROJECT_FILE_DELETED' THEN action_label := 'Удалён'; END IF;
    heading := action_label || ' ' || lower(left(noun,1)) || substr(noun,2) || ' · ' || coalesce(item->>'title', item->>'name', item->>'display_title', item->>'original_name', task_title, '#' || coalesce(item->>'id', item->>'comment_id', ''));
    PERFORM project_history_emit(pid, category, action, heading, details, tid, NULL, NULL, TG_TABLE_NAME, (item->>'id')::integer);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

# Scope and semantic columns are explicit; timestamps, processing bookkeeping,
# revisions and board geometry never create history noise. File storage changes
# indicate replacement, but storage values are never included in events.
HISTORY_RULES = (
    (
        "projects",
        "project_activity",
        "Проект",
        "PROJECT",
        "name,key,owner_id,status,start_date,due_date,order_index,color,icon,description_md,description_sections",
    ),
    (
        "tasks",
        "task_activity",
        "Задача",
        "TASK",
        "title,stage_id,start_date,due_date,baseline_start_date,baseline_due_date,description_md,priority,assignee,role,wbs_node_id,wbs_position,position,checklist,canvas_x,canvas_y",
    ),
    (
        "task_activity",
        "task_activity",
        "Изменение задачи",
        "TASK",
        "event_type,from_value,to_value,reason",
    ),
    ("task_comments", "task_comment", "Комментарий", "COMMENT", "body_md"),
    ("task_reports", "task_report", "Отчёт", "REPORT", "body_md,revision"),
    (
        "task_participants",
        "task_activity",
        "Участник задачи",
        "TASK_PARTICIPANT",
        "role,user_id,project_member_id",
    ),
    ("project_members", "project_member", "Участник проекта", "PROJECT_MEMBER", "role,user_id"),
    ("project_stages", "project_stage", "Стадия", "STAGE", "name,order_index,color,is_done_stage"),
    (
        "project_risks",
        "risk_activity",
        "Риск",
        "RISK",
        "title,description,probability,impact,risk_level,status,response_strategy,mitigation_plan,response_plan,owner_user_id,review_date,source,task_id",
    ),
    (
        "project_context_history",
        "project_context",
        "Паспорт проекта",
        "PROJECT_CONTEXT",
        "revision,changed_fields",
    ),
    (
        "project_deadline_changes",
        "project_deadline",
        "Срок проекта",
        "PROJECT_DEADLINE",
        "previous_due_date,new_due_date,comment",
    ),
    ("documents", "document_activity", "Документ", "DOCUMENT", "title,content_md,caption,slug"),
    (
        "project_files",
        "document_activity",
        "Файл проекта",
        "PROJECT_FILE",
        "display_title,original_name,caption,content_hash,deleted_at",
    ),
    (
        "task_attachments",
        "document_activity",
        "Вложение задачи",
        "TASK_ATTACHMENT",
        "display_title,original_name,caption,storage_key,size,content_type",
    ),
    (
        "document_links",
        "document_activity",
        "Связь документа с задачей",
        "DOCUMENT_LINK",
        "document_id,project_file_id,task_id",
    ),
    (
        "task_comment_documents",
        "document_activity",
        "Документ комментария",
        "COMMENT_DOCUMENT",
        "document_id,project_file_id,task_id,comment_id",
    ),
    (
        "task_comment_attachments",
        "document_activity",
        "Вложение комментария",
        "COMMENT_ATTACHMENT",
        "attachment_id,comment_id,task_id",
    ),
    (
        "project_milestones",
        "project_plan",
        "Веха",
        "MILESTONE",
        "title,due_date,status,wbs_node_id,description_md",
    ),
    (
        "task_dependencies",
        "project_plan",
        "Зависимость задач",
        "TASK_DEPENDENCY",
        "predecessor_task_id,successor_task_id,dependency_type,lag_days",
    ),
    ("wbs_nodes", "project_plan", "Раздел ИСР", "WBS_NODE", "title,parent_id,position"),
    ("project_boards", "project_board", "Доска", "BOARD", "title,archived_at"),
    ("project_stickers", "project_board", "Стикер", "STICKER", "body"),
    (
        "board_shapes",
        "project_board",
        "Фигура доски",
        "BOARD_SHAPE",
        "kind,label,start_ref,end_ref",
    ),
    (
        "project_sticker_task_links",
        "project_board",
        "Связь стикера с задачей",
        "STICKER_TASK_LINK",
        "sticker_id,task_id",
    ),
)


def history_statements(rules=HISTORY_RULES) -> list[str]:
    """Возвращает SQL журнала; миграция хранит замороженную копию."""
    statements = [
        FIELD_LABEL_FUNCTION,
        EMIT_FUNCTION,
        LEGACY_FUNCTION,
        TASK_SNAPSHOT_FUNCTION,
        CAPTURE_FUNCTION,
    ]
    for table, kind, noun, prefix, fields in rules:
        arguments = ", ".join("'" + value + "'" for value in (kind, noun, prefix, fields))
        for phase in ("before", "after"):
            statements.append(f'DROP TRIGGER IF EXISTS project_history_{phase} ON "{table}"')
            operations = "UPDATE OR DELETE" if phase == "before" else "INSERT OR UPDATE OR DELETE"
            statements.append(
                f'CREATE TRIGGER project_history_{phase} {phase.upper()} {operations} ON "{table}" FOR EACH ROW EXECUTE FUNCTION project_history_capture({arguments})'
            )
    return statements


def register_project_history_ddl(metadata):
    """Устанавливает журнал также при create_all на изолированном PostgreSQL."""

    @event.listens_for(metadata, "after_create")
    def after_create(target, connection, **kwargs):
        if connection.dialect.name != "postgresql":
            return
        tables = set(inspect(connection).get_table_names())
        if "project_history_events" not in tables:
            return
        for statement in history_statements(
            tuple(rule for rule in HISTORY_RULES if rule[0] in tables)
        ):
            connection.execute(text(statement))
