"""Новая DDL применяется на чистую/прежнюю БД и защищает историю при откате."""

from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models import Base

# Freeze these legacy checks before the independently guarded history journal.
PRE_HISTORY_REVISION = "c32a202611200001"


@pytest_asyncio.fixture
async def date_change_migration(postgres_container):
    """Изолирует Alembic в отдельной синтетической базе, удаляемой после проверки."""
    database = f"date_change_migration_{uuid4().hex}"
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
    try:
        yield engine, config
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()


async def test_date_reason_migration_upgrades_clean_database(date_change_migration):
    engine, config = date_change_migration

    def verify(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, PRE_HISTORY_REVISION)
        connection.commit()
        columns = {
            column["name"]: column for column in inspect(connection).get_columns("task_activity")
        }
        assert columns["reason"]["nullable"]
        assert connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid "
                "WHERE t.typname='task_activity_event_type' AND e.enumlabel='DATES_CHANGED')"
            )
        ).scalar_one()

        def include_object(obj, name, kind, reflected, compare_to):
            if kind == "table":
                return name == "task_activity"
            return kind == "column" and name == "reason"

        context = MigrationContext.configure(
            connection,
            opts={
                "include_object": include_object,
                "compare_column_comments": True,
            },
        )
        assert compare_metadata(context, Base.metadata) == []

    async with engine.connect() as connection:
        await connection.run_sync(verify)


async def test_date_reason_migration_preserves_legacy_rows_and_guards_new_history(
    date_change_migration,
):
    engine, config = date_change_migration

    def verify(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, "f12c7a309bd1")
        connection.execute(
            text(
                "INSERT INTO users (id,username,password_hash,first_name,last_name,is_active) VALUES (1,'dates-owner','!','Иван','Тестов',true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects (id,owner_id,key,name,color,status,order_index) VALUES (1,1,'DATES','Сроки','#334455','ACTIVE',0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO project_stages (id,project_id,name,order_index,color,is_done_stage) VALUES (1,1,'Работа',0,'#334455',false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tasks (id,project_id,stage_id,number,title,priority,position,start_date,due_date) VALUES (1,1,1,1,'Прежняя задача','MEDIUM',1000,'2026-11-16','2026-11-20')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO task_activity (id,task_id,event_type,from_value,to_value,actor_user_id,actor_name) VALUES (1,1,'DUE_DATE_CHANGED','2026-11-19','2026-11-20',1,'Иван Тестов')"
            )
        )
        connection.commit()
        command.upgrade(config, PRE_HISTORY_REVISION)
        connection.commit()
        assert connection.execute(
            text(
                "SELECT event_type::text,from_value,to_value,actor_user_id,actor_name,reason FROM task_activity WHERE id=1"
            )
        ).one() == ("DUE_DATE_CHANGED", "2026-11-19", "2026-11-20", 1, "Иван Тестов", None)
        assert connection.execute(
            text("SELECT start_date,due_date FROM tasks WHERE id=1")
        ).one() == (date(2026, 11, 16), date(2026, 11, 20))
        # Первое назначение без причины тоже несовместимо с enum прежнего приложения.
        connection.execute(
            text(
                "INSERT INTO task_activity (id,task_id,event_type,from_value,to_value,reason) VALUES (2,1,'DATES_CHANGED','начало: —; завершение: —','начало: 2026-11-16; завершение: 2026-11-20',NULL)"
            )
        )
        connection.commit()
        with pytest.raises(DBAPIError, match="task date change history must be preserved"):
            command.downgrade(config, "f12c7a309bd1")
        connection.rollback()
        assert connection.execute(text("SELECT count(*) FROM task_activity")).scalar_one() == 2
        assert "reason" in {
            column["name"] for column in inspect(connection).get_columns("task_activity")
        }
        connection.execute(text("DELETE FROM task_activity WHERE id=2"))
        connection.commit()
        command.downgrade(config, "f12c7a309bd1")
        connection.commit()
        assert "reason" not in {
            column["name"] for column in inspect(connection).get_columns("task_activity")
        }
        assert (
            connection.execute(text("SELECT to_value FROM task_activity WHERE id=1")).scalar_one()
            == "2026-11-20"
        )
        command.upgrade(config, PRE_HISTORY_REVISION)
        connection.commit()
        assert (
            connection.execute(text("SELECT reason FROM task_activity WHERE id=1")).scalar_one()
            is None
        )

    async with engine.connect() as connection:
        await connection.run_sync(verify)
