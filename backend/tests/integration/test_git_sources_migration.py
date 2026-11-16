"""Полный replay Alembic Git поверх карты, schema parity и guard сохранённых данных."""

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

PARENT = "c26a202611160001"
REVISION = "c29a202611160001"


async def test_git_migration_replays_preserves_primary_and_refuses_destructive_downgrade(
    postgres_container,
):
    database = "git_migration_" + uuid4().hex
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
        command.upgrade(config, PARENT)
        connection.execute(
            text(
                "INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) VALUES (1,'git-migration','!','Git','Migration',true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects(id,owner_id,key,name,description_md,color,status,order_index) VALUES (1,1,'GITOLD','Existing','Preserve bytes','#334455','ACTIVE',0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents(id,project_id,slug,title,content_md) VALUES (1,1,'git-doc','Existing','Original content')"
            )
        )
        connection.commit()
        primary = tuple(
            connection.execute(text(statement)).all()
            for statement in (
                "SELECT id,description_md,description_sections,context_revision,updated_at FROM projects",
                "SELECT id,content_md FROM documents",
            )
        )
        connection.commit()
        command.upgrade(config, REVISION)
        connection.commit()
        tables = {name for name in inspect(connection).get_table_names() if name.startswith("git_")}
        assert len(tables) == 8
        assert all(
            connection.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one() == 0
            for name in tables
        )
        assert (
            tuple(
                connection.execute(text(statement)).all()
                for statement in (
                    "SELECT id,description_md,description_sections,context_revision,updated_at FROM projects",
                    "SELECT id,content_md FROM documents",
                )
            )
            == primary
        )

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
        connection.commit()
        command.upgrade(config, REVISION)
        connection.commit()
        connection.execute(
            text(
                "INSERT INTO git_connections(project_id,repository_id,owner,repo,name,ref,include_paths,scope_hash,is_private) VALUES (1,101,'example','app','App','heads/main','[\"\"]'::jsonb,:hash,false)"
            ),
            {"hash": "a" * 64},
        )
        connection.commit()
        with pytest.raises(RuntimeError):
            command.downgrade(config, PARENT)
        connection.rollback()
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
        assert connection.execute(text("SELECT count(*) FROM git_connections")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT content_md FROM documents WHERE id=1")).scalar_one()
            == "Original content"
        )

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        await admin.dispose()
