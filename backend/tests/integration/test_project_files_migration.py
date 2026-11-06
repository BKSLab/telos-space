"""DDL новой функции совместим с прежней схемой и сохраняет документы."""

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


async def test_project_files_migration_preserves_data_and_matches_metadata(postgres_container):
    database = f"project_files_migration_{uuid4().hex}"
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
        command.upgrade(config, "c07e4f82a1b6")
        connection.execute(
            text(
                "INSERT INTO users (id,username,password_hash,first_name,last_name,is_active) VALUES (1,'files-owner','!','Иван','Тестов',true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects (id,owner_id,key,name,color,status,order_index) VALUES (1,1,'FILES','Проект','#334455','ACTIVE',0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents (project_id,slug,title,content_md) VALUES (1,'original','Документ','Неизменяемый прежний текст')"
            )
        )
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
        included = {"project_files", "knowledge_project_file_texts", "knowledge_source_summaries"}

        def include_object(obj, name, kind, reflected, compare_to):
            table = name if kind == "table" else getattr(getattr(obj, "table", None), "name", None)
            return table in included

        context = MigrationContext.configure(
            connection,
            opts={
                "include_object": include_object,
                "compare_server_default": True,
                "compare_column_comments": True,
            },
        )
        assert compare_metadata(context, Base.metadata) == []
        assert (
            connection.execute(text("SELECT content_md FROM documents")).scalar_one()
            == "Неизменяемый прежний текст"
        )
        connection.execute(
            text(
                "INSERT INTO project_files (project_id,original_name,content_type,size,storage_key,content_hash) VALUES (1,'test.txt','text/plain',4,'projects/1/test.txt',:hash)"
            ),
            {"hash": "a" * 64},
        )
        connection.commit()
        with pytest.raises(RuntimeError, match="Откат остановлен"):
            command.downgrade(config, "c07e4f82a1b6")
        connection.rollback()
        connection.execute(text("DELETE FROM project_files"))
        connection.commit()
        command.downgrade(config, "c07e4f82a1b6")
        connection.commit()
        assert "project_files" not in inspect(connection).get_table_names()
        command.upgrade(config, "head")
        connection.commit()
        assert (
            connection.execute(text("SELECT content_md FROM documents")).scalar_one()
            == "Неизменяемый прежний текст"
        )

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()
