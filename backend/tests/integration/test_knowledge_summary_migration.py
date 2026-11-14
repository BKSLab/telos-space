"""Обновление схемы сохраняет тексты и ставит обработку старых документов в очередь."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models import Base


async def test_summary_migration_preserves_documents_and_schedules_background_work(
    postgres_container,
):
    database = f"summary_migration_{uuid4().hex}"
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
        command.upgrade(config, "f29c7618a403")
        connection.execute(
            text(
                "INSERT INTO users (id,username,password_hash,first_name,last_name,is_active) VALUES (1,'summary-owner','!','Иван','Тестов',true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects (id,owner_id,key,name,color,status,order_index) VALUES (1,1,'SUM','Проект','#334455','ACTIVE',0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents (project_id,slug,title,content_md) VALUES (1,'original','Документ','Полный исходный текст')"
            )
        )
        connection.execute(text("DELETE FROM knowledge_index_jobs"))
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()

        def include_object(obj, name, kind, reflected, compare_to):
            table = name if kind == "table" else getattr(getattr(obj, "table", None), "name", None)
            return table == "knowledge_source_summaries"

        context = MigrationContext.configure(
            connection, opts={"include_object": include_object, "compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        # Следующие ревизии также ставят сверку: worker объединяет задания проекта.
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM knowledge_index_jobs WHERE project_id=1 AND status='PENDING'"
                )
            ).scalar_one()
            >= 1
        )
        assert (
            connection.execute(text("SELECT content_md FROM documents")).scalar_one()
            == "Полный исходный текст"
        )
        connection.commit()
        command.downgrade(config, "f29c7618a403")
        connection.commit()
        assert "knowledge_source_summaries" not in inspect(connection).get_table_names()
        assert (
            connection.execute(text("SELECT content_md FROM documents")).scalar_one()
            == "Полный исходный текст"
        )
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM knowledge_index_jobs WHERE project_id=1 AND status='PENDING'"
                )
            ).scalar_one()
            >= 1
        )

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()
