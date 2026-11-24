"""Миграция чата не создаёт backfill и полностью соответствует ORM."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models import Base

# Includes the final chat columns compared below. Later project-history
# migrations have their own roundtrip and data-preserving downgrade checks.
CHAT_SCHEMA_REVISION = "f12c7a309bd1"


async def test_chat_upgrade_downgrade_and_transactional_lifecycle(postgres_container):
    database = f"chat_migration_{uuid4().hex}"
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
        command.upgrade(config, "ed781b034ac9")
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, first_name, last_name, is_active) VALUES (1, 'owner', '!', 'Иван', 'Тестов', true)"
            )
        )
        insert_project = text(
            "INSERT INTO projects (id, owner_id, key, name, color, status, order_index) VALUES (:id, 1, :key, 'Тестовый проект', '#334455', 'ACTIVE', 0)"
        )
        connection.execute(insert_project, {"id": 1, "key": "OLD"})
        connection.commit()
        command.upgrade(config, CHAT_SCHEMA_REVISION)
        connection.commit()
        names = {
            name
            for name in Base.metadata.tables
            if name.startswith("chat_") or name == "project_chats"
        }

        def include_object(obj, name, kind, reflected, compare_to):
            return (
                name if kind == "table" else getattr(getattr(obj, "table", None), "name", None)
            ) in names

        context = MigrationContext.configure(
            connection, opts={"include_object": include_object, "compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        assert connection.scalar(text("SELECT count(*) FROM project_chats")) == 0
        connection.execute(insert_project, {"id": 2, "key": "NEW"})
        connection.execute(
            text("INSERT INTO project_members (project_id, user_id, role) VALUES (2, 1, 'OWNER')")
        )
        assert connection.scalar(text("SELECT count(*) FROM project_chats WHERE project_id=2")) == 1
        assert connection.scalar(text("SELECT count(*) FROM chat_read_states")) == 1
        assert (
            connection.scalar(
                text("SELECT count(*) FROM chat_events WHERE event_type='member.changed'")
            )
            == 1
        )
        connection.commit()
        connection.execute(insert_project, {"id": 3, "key": "ROLLBACK"})
        connection.rollback()
        assert connection.scalar(text("SELECT count(*) FROM project_chats WHERE project_id=3")) == 0
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.triggers WHERE trigger_name='knowledge_changed' AND event_object_table LIKE 'chat_%'"
                )
            )
            == 0
        )
        connection.commit()
        command.downgrade(config, "ed781b034ac9")
        connection.commit()
        assert not names & set(inspect(connection).get_table_names())
        assert connection.scalar(text("SELECT count(*) FROM projects")) == 2
        connection.commit()
        command.upgrade(config, CHAT_SCHEMA_REVISION)
        connection.commit()
        assert connection.scalar(text("SELECT count(*) FROM project_chats")) == 0

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        await admin.dispose()
