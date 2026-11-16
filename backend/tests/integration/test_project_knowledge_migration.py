"""Полный replay цепочки, неизменность primary и guarded downgrade карты."""

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

PARENT = "f23a8c91d046"
REVISION = "c26a202611160001"


async def test_project_knowledge_migration_is_schema_only_replayable_and_preserves_saved_versions(
    postgres_container,
):
    database = "map_migration_" + uuid4().hex
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

    def primary_rows(connection):
        return tuple(
            connection.execute(text(statement)).all()
            for statement in (
                "SELECT id,description_md,description_sections,context_revision,updated_at FROM projects ORDER BY id",
                "SELECT id,content_md FROM documents ORDER BY id",
                "SELECT id,project_id,entity_type,entity_id,operation,status FROM knowledge_index_jobs ORDER BY id",
            )
        )

    def verify(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, PARENT)
        connection.execute(
            text(
                "INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) VALUES (1,'map-migration','!','Тест','Карты',true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects(id,owner_id,key,name,description_md,color,status,order_index) VALUES (1,1,'MAPOLD','Старый проект','## Цель\nНе парсить как цель','#334455','ACTIVE',0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents(id,project_id,slug,title,content_md) VALUES (1,1,'map-doc','Документ','Прежнее содержимое')"
            )
        )
        connection.commit()
        before = primary_rows(connection)
        connection.commit()
        command.upgrade(config, REVISION)
        connection.commit()
        assert primary_rows(connection) == before
        tables = {
            name
            for name in inspect(connection).get_table_names()
            if name.startswith("project_knowledge_")
        }
        assert len(tables) == 7
        for table in tables:
            assert connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one() == 0

        def include_object(obj, name, kind, reflected, compare_to):
            table = name if kind == "table" else getattr(getattr(obj, "table", None), "name", None)
            return table in tables

        context = MigrationContext.configure(
            connection, opts={"include_object": include_object, "compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        connection.commit()
        command.downgrade(config, PARENT)
        connection.commit()
        assert not tables.intersection(inspect(connection).get_table_names())
        assert primary_rows(connection) == before
        connection.commit()
        command.upgrade(config, REVISION)
        connection.commit()
        # Trigger действует и после replay, source mutation и outbox атомарны.
        connection.execute(text("UPDATE documents SET content_md='Изменено' WHERE id=1"))
        connection.commit()
        assert (
            connection.execute(
                text(
                    "SELECT generation FROM project_knowledge_sources WHERE project_id=1 AND source_id='document:1'"
                )
            ).scalar_one()
            == 1
        )
        connection.execute(
            text(
                "INSERT INTO project_knowledge_source_versions(id,project_id,source_id,generation,identity_hash,source_hash,content_hash,representation,title,source_type,dependencies) VALUES (:id,1,'document:1',1,:hash,:hash,:hash,'markdown','Документ','document','{}'::jsonb)"
            ),
            {"id": uuid4(), "hash": "a" * 64},
        )
        connection.commit()
        with pytest.raises(RuntimeError, match="saved history"):
            command.downgrade(config, PARENT)
        connection.rollback()
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM project_knowledge_source_versions")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(text("SELECT content_md FROM documents WHERE id=1")).scalar_one()
            == "Изменено"
        )

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        await admin.dispose()
