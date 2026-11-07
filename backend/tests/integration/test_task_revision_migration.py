"""Новая DDL-миграция сохраняет задачи и совпадает с ORM-контрактом."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models import Base
from src.db.task_attention_ddl import REVISION_SQL


async def test_task_revision_migration_preserves_existing_task_and_positive_default(postgres_container):
    database = f"task_revision_migration_{uuid4().hex}"
    admin = create_async_engine(
        postgres_container.get_connection_url().replace("psycopg2", "asyncpg"),
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database}"'))
    engine = create_async_engine(admin.url.set(database=database))
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/db/alembic"))

    def verify(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, "e31a7c9d204f")
        # Старые миграции импортируют текущий helper; здесь нужна настоящая прежняя функция.
        legacy_revision_sql = REVISION_SQL.replace(",'revision'", "")
        connection.execute(text(legacy_revision_sql))
        connection.execute(text("INSERT INTO users (id,username,password_hash,first_name,last_name,is_active) VALUES (1,'revision-owner','!','Иван','Тестов',true)"))
        connection.execute(text("INSERT INTO projects (id,owner_id,key,name,color,status,order_index) VALUES (1,1,'REV','Проект','#334455','ACTIVE',0)"))
        connection.execute(text("INSERT INTO project_stages (id,project_id,name,order_index,color,is_done_stage) VALUES (1,1,'Работа',0,'#334455',false)"))
        connection.execute(text("INSERT INTO tasks (id,project_id,stage_id,number,title,description_md,priority,position) VALUES (1,1,1,1,'Старая задача','Прежнее описание','MEDIUM',1000)"))
        connection.commit()
        with connection.begin_nested() as transaction:
            connection.execute(text("ALTER TABLE tasks ADD COLUMN revision integer NOT NULL DEFAULT 1"))
            attention_before = connection.execute(text("SELECT attention_revision FROM tasks WHERE id=1")).scalar_one()
            connection.execute(text("UPDATE tasks SET position=2000,revision=revision+1 WHERE id=1"))
            assert connection.execute(text("SELECT attention_revision FROM tasks WHERE id=1")).scalar_one() == attention_before + 1
            transaction.rollback()
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
        assert connection.execute(text("SELECT title,description_md,revision,checklist_revision FROM tasks WHERE id=1")).one() == ("Старая задача", "Прежнее описание", 1, 0)

        def include_object(obj, name, kind, reflected, compare_to):
            if kind == "table":
                return name == "tasks"
            if kind == "column":
                return name == "revision"
            return False

        context = MigrationContext.configure(connection, opts={"include_object": include_object, "compare_server_default": True, "compare_column_comments": True})
        assert compare_metadata(context, Base.metadata) == []
        constraints = {item["name"] for item in inspect(connection).get_check_constraints("tasks")}
        assert "ck_tasks_revision_positive" in constraints
        attention_before = connection.execute(text("SELECT attention_revision FROM tasks WHERE id=1")).scalar_one()
        connection.execute(text("UPDATE tasks SET position=2000,revision=revision+1 WHERE id=1"))
        assert connection.execute(text("SELECT revision,attention_revision FROM tasks WHERE id=1")).one() == (2, attention_before)
        connection.execute(text("UPDATE tasks SET title='Изменённый заголовок',revision=revision+1 WHERE id=1"))
        assert connection.execute(text("SELECT attention_revision FROM tasks WHERE id=1")).scalar_one() == attention_before + 1
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(text("UPDATE tasks SET revision=0 WHERE id=1"))
        command.downgrade(config, "e31a7c9d204f")
        connection.commit()
        assert "revision" not in {column["name"] for column in inspect(connection).get_columns("tasks")}
        assert connection.execute(text("SELECT description_md FROM tasks WHERE id=1")).scalar_one() == "Прежнее описание"
        restored_function = connection.execute(text("SELECT pg_get_functiondef('task_attention_revision()'::regprocedure)")).scalar_one()
        assert "'revision'" not in restored_function
        attention_before = connection.execute(text("SELECT attention_revision FROM tasks WHERE id=1")).scalar_one()
        connection.execute(text("UPDATE tasks SET position=3000 WHERE id=1"))
        assert connection.execute(text("SELECT attention_revision FROM tasks WHERE id=1")).scalar_one() == attention_before
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
        assert connection.execute(text("SELECT revision FROM tasks WHERE id=1")).scalar_one() == 1
        connection.execute(text("UPDATE tasks SET position=4000,revision=revision+1 WHERE id=1"))
        assert connection.execute(text("SELECT revision,attention_revision FROM tasks WHERE id=1")).one() == (2, attention_before)

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()
