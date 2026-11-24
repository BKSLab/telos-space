"""Старая цепочка Alembic: данные сохраняются, DDL совпадает с create_all."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models import Base
from src.db.project_history_backfill import backfill_history

PARENT = "c32a202611200001"
REVISION = "c33a202611240001"


async def test_history_migration_preserves_legacy_data_and_matches_create_all(
    postgres_container,
    engine,
):
    database = f"project_history_migration_{uuid4().hex}"
    admin = create_async_engine(
        postgres_container.get_connection_url().replace("psycopg2", "asyncpg"),
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database}"'))
    migrated = create_async_engine(admin.url.set(database=database))
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/db/alembic"))

    def ddl(connection):
        return (
            connection.execute(
                text(
                    "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid=t.tgrelid "
                    "WHERE t.tgname LIKE 'project_history_%' ORDER BY c.relname,t.tgname"
                )
            ).all(),
            connection.execute(
                text(
                    "SELECT proname,pg_get_functiondef(oid) FROM pg_proc "
                    "WHERE proname LIKE 'project_history_%' ORDER BY proname"
                )
            ).all(),
        )

    async with engine.connect() as connection:
        create_all_ddl = await connection.run_sync(ddl)

    def source_rows(connection):
        return tuple(
            connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
            for table in (
                "users",
                "projects",
                "project_stages",
                "tasks",
                "task_comments",
                "task_reports",
                "documents",
            )
        )

    def verify(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, PARENT)
        connection.execute(
            text(
                "INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) VALUES (1,'history-migration','!','Иван','Историков',true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects(id,owner_id,key,name,color,status,order_index) VALUES (1,1,'HISTORY','Прежний проект','#123456','ACTIVE',0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO project_stages(id,project_id,name,color,order_index,is_done_stage) VALUES (1,1,'Работа','#123456',0,false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tasks(id,project_id,stage_id,number,title,description_md,priority,position) VALUES (1,1,1,1,'Прежнее название','Полный старый текст','MEDIUM',0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO task_comments(id,task_id,body_md,author_name) VALUES (1,1,'Прежний комментарий','Снимок автора')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO task_reports(id,task_id,revision,body_md,author_name) VALUES (1,1,1,'Прежний отчёт','Снимок автора')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents(id,project_id,slug,title,content_md) VALUES (1,1,'old','Прежний документ','Полный текст документа')"
            )
        )
        connection.commit()
        before = source_rows(connection)
        connection.commit()
        command.upgrade(config, REVISION)
        connection.commit()
        assert source_rows(connection) == before
        assert connection.scalar(text("SELECT count(*) FROM project_history_events")) == 0
        assert ddl(connection) == create_all_ddl

        def include_object(obj, name, kind, reflected, compare_to):
            table = name if kind == "table" else getattr(getattr(obj, "table", None), "name", None)
            return table == "project_history_events"

        context = MigrationContext.configure(
            connection, opts={"include_object": include_object, "compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        connection.commit()
        command.downgrade(config, PARENT)
        connection.commit()
        assert "project_history_events" not in inspect(connection).get_table_names()
        assert source_rows(connection) == before
        connection.commit()
        command.upgrade(config, REVISION)
        connection.commit()
        # The first mutation freezes legacy facts before their source changes.
        connection.execute(text("UPDATE tasks SET title='Новое название' WHERE id=1"))
        connection.commit()
        rows = connection.execute(
            text(
                "SELECT public_id,title,detail,actor_user_id,actor_name FROM project_history_events ORDER BY id"
            )
        ).all()
        by_id = {row.public_id: row for row in rows}
        assert by_id["task_created:1"].title == "Создана задача · Прежнее название"
        assert by_id["task_created:1"].actor_user_id is None
        assert by_id["task_created:1"].actor_name is None
        assert by_id["task_comment:1"].detail == "Прежний комментарий"
        assert by_id["task_report:1"].detail == "Прежний отчёт"
        connection.execute(text("DELETE FROM tasks WHERE id=1"))
        connection.execute(text("DELETE FROM documents WHERE id=1"))
        connection.commit()
        assert connection.scalar(text("SELECT count(*) FROM tasks")) == 0
        assert connection.scalar(text("SELECT count(*) FROM task_comments")) == 0
        assert connection.scalar(text("SELECT count(*) FROM task_reports")) == 0
        saved = connection.execute(
            text(
                "SELECT public_id,title,detail,actor_user_id,actor_name FROM project_history_events ORDER BY id"
            )
        ).all()
        assert set(rows) <= set(saved)
        assert (
            connection.scalar(
                text("SELECT title FROM project_history_events WHERE public_id='document:1'")
            )
            == "Создан документ · Прежний документ"
        )
        connection.commit()
        with pytest.raises(RuntimeError, match="durable project history"):
            command.downgrade(config, PARENT)
        connection.rollback()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
        assert (
            connection.execute(
                text(
                    "SELECT public_id,title,detail,actor_user_id,actor_name FROM project_history_events ORDER BY id"
                )
            ).all()
            == saved
        )

    try:
        async with migrated.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await migrated.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()


async def test_explicit_legacy_backfill_is_idempotent_scoped_and_does_not_infer_authors(
    postgres_container,
):
    database = f"project_history_backfill_{uuid4().hex}"
    admin = create_async_engine(
        postgres_container.get_connection_url().replace("psycopg2", "asyncpg"),
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database}"'))
    migrated = create_async_engine(admin.url.set(database=database))
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/db/alembic"))

    def bootstrap(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, PARENT)
        statements = [
            "INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) VALUES (1,'backfill-owner','!','Виктор','Владельцев',true)",
            "INSERT INTO projects(id,owner_id,key,name,color,status,order_index) VALUES (1,1,'BACKFILL','Прежний проект','#123456','ACTIVE',0)",
            "INSERT INTO project_stages(id,project_id,name,color,order_index,is_done_stage) VALUES (1,1,'Работа','#123456',0,false)",
            "INSERT INTO project_members(id,project_id,user_id,role) VALUES (1,1,1,'OWNER')",
            "INSERT INTO tasks(id,project_id,stage_id,number,title,priority,position,status) VALUES (1,1,1,1,'Прежняя задача','MEDIUM',0,NULL),(2,1,1,2,'Ранее перенесённая','MEDIUM',0,NULL),(3,NULL,NULL,1,'Личная задача','MEDIUM',0,'NEW')",
            "INSERT INTO task_participants(id,task_id,project_member_id,role) VALUES (1,1,1,'REPORTER')",
            "INSERT INTO task_activity(id,task_id,event_type,from_value,to_value) VALUES (1,1,'STAGE_CHANGED','План','Работа'),(2,2,'TASK_MOVED','Личная задача','Проект')",
            "INSERT INTO task_comments(id,task_id,body_md,author_name) VALUES (1,1,'Старый публичный комментарий','Автор комментария'),(2,2,'Чужой прежний текст','Чужой автор'),(3,3,'Личный прежний текст','Личный автор')",
            "INSERT INTO task_reports(id,task_id,revision,body_md,author_name) VALUES (1,1,1,'Старый отчёт','Автор отчёта')",
            "INSERT INTO documents(id,project_id,owner_task_id,slug,title,content_md) VALUES (1,1,NULL,'public','Публичный документ','Прежний текст'),(2,NULL,3,'private','Личный документ','Секретный текст')",
            "INSERT INTO project_files(id,project_id,original_name,content_type,size,storage_key,content_hash,deleted_at) VALUES (1,1,'public.txt','text/plain',1,'old-public','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',NULL),(2,1,'removed.txt','text/plain',1,'old-removed','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',CURRENT_TIMESTAMP)",
            "INSERT INTO project_risks(id,project_id,title,description,probability,impact,risk_level,response_strategy,owner_user_id) VALUES (1,1,'Прежний риск','Прежнее описание','LOW','LOW','LOW','ACCEPT',1)",
            "INSERT INTO project_context_history(project_id,revision,description_md,is_initial,changed_fields) VALUES (1,0,'Первый снимок',true,'[]'),(1,1,'Прежний паспорт',false,'[\"goal\"]')",
            "INSERT INTO project_deadline_changes(id,project_id,previous_due_date,new_due_date,comment,changed_by_name) VALUES (1,1,NULL,'2026-12-01','Прежняя договорённость','Автор срока')",
        ]
        for statement in statements:
            connection.execute(text(statement))
        connection.commit()
        command.upgrade(config, REVISION)
        connection.commit()

    try:
        async with migrated.connect() as connection:
            await connection.run_sync(bootstrap)
            before = (
                await connection.execute(text("SELECT id,title FROM tasks ORDER BY id"))
            ).all()
            assert await connection.scalar(text("SELECT count(*) FROM project_history_events")) == 0
            first = await backfill_history(connection)
            assert first == {"before": 0, "after": 9, "created": 9}
            saved = (
                await connection.execute(
                    text(
                        "SELECT public_id,title,detail,actor_user_id,actor_name FROM project_history_events ORDER BY id"
                    )
                )
            ).all()
            expected_ids = {
                "task_created:1",
                "task_activity:1",
                "task_comment:1",
                "task_report:1",
                "document:1",
                "project_file:1",
                "risk_created:1",
                "project_context:1:1",
                "project_deadline:1",
            }
            assert {item.public_id for item in saved} == expected_ids
            by_id = {item.public_id: item for item in saved}
            for public_id in (
                "task_created:1",
                "task_activity:1",
                "risk_created:1",
                "project_context:1:1",
            ):
                assert (
                    by_id[public_id].actor_user_id is None and by_id[public_id].actor_name is None
                )
            assert by_id["task_comment:1"].detail == "Старый публичный комментарий"
            assert by_id["task_report:1"].detail == "Старый отчёт"
            assert by_id["task_comment:1"].actor_name == "Автор комментария"
            assert by_id["project_deadline:1"].actor_name == "Автор срока"
            assert (
                "Личный" not in str(saved)
                and "Чужой" not in str(saved)
                and "Секретный" not in str(saved)
            )
            second = await backfill_history(connection)
            assert second == {"before": 9, "after": 9, "created": 0}
            assert (
                await connection.execute(
                    text(
                        "SELECT public_id,title,detail,actor_user_id,actor_name FROM project_history_events ORDER BY id"
                    )
                )
            ).all() == saved
            assert (
                await connection.execute(text("SELECT id,title FROM tasks ORDER BY id"))
            ).all() == before
            await connection.commit()
    finally:
        await migrated.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()
