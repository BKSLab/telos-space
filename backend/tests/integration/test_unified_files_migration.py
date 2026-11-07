"""Аддитивные миграции файлов сохраняют прежние оригиналы и данные."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models import Base


async def test_file_migrations_preserve_originals_and_match_metadata(postgres_container):
    database = f"files_migration_{uuid4().hex}"
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
        command.upgrade(config, "f42b8d0e315a")
        connection.execute(text(
            "INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) "
            "VALUES(1,'files-owner','!','Иван','Тестов',true)"
        ))
        connection.execute(text(
            "INSERT INTO projects(id,owner_id,key,name,color,status,order_index) "
            "VALUES(1,1,'FILE','Проект','#334455','ACTIVE',0)"
        ))
        connection.execute(text(
            "INSERT INTO project_stages(id,project_id,name,order_index,color,is_done_stage) "
            "VALUES(1,1,'Работа',0,'#334455',false)"
        ))
        connection.execute(text(
            "INSERT INTO tasks(id,project_id,stage_id,number,title,priority,position) "
            "VALUES(1,1,1,1,'Старая задача','MEDIUM',1000)"
        ))
        connection.execute(text(
            "INSERT INTO task_attachments(id,task_id,original_name,storage_key,content_type,size) "
            "VALUES(1,1,'Старый.txt','tasks/1/original.txt','text/plain',12)"
        ))
        connection.execute(text(
            "INSERT INTO task_comments(id,task_id,author_id,author_name,body_md) "
            "VALUES(1,1,1,'Иван','Прежний комментарий')"
        ))
        connection.execute(text(
            "INSERT INTO knowledge_attachment_texts(attachment_id,text,status,original_chars,content_hash) "
            "VALUES(1,'Сохранённый оригинал','ready',20,'existing-hash')"
        ))
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
        assert connection.execute(text(
            "SELECT original_name,storage_key,caption,uploaded_by_user_id "
            "FROM task_attachments WHERE id=1"
        )).one() == ("Старый.txt", "tasks/1/original.txt", None, None)
        assert connection.execute(text(
            "SELECT text,status FROM knowledge_attachment_texts WHERE attachment_id=1"
        )).one() == ("Сохранённый оригинал", "ready")
        # DDL не прогоняет старые файлы через vision и не запускает массовый backfill.
        assert connection.execute(text("SELECT count(*) FROM file_processing_jobs")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM file_search_chunks")).scalar_one() == 0
        allowed = {
            "project_files", "task_attachments", "task_comments", "task_comment_attachments",
            "file_comment_submissions", "file_processing_jobs", "file_search_chunks",
        }

        def include_object(obj, name, kind, reflected, compare_to):
            return name in allowed if kind == "table" else obj.table.name in allowed

        context = MigrationContext.configure(connection, opts={
            "include_object": include_object, "compare_server_default": True,
        })
        assert compare_metadata(context, Base.metadata) == []
        connection.execute(text("UPDATE task_attachments SET caption='Подпись' WHERE id=1"))
        assert connection.execute(text(
            "SELECT text FROM knowledge_attachment_texts WHERE attachment_id=1"
        )).scalar_one() == "Сохранённый оригинал"
        assert connection.execute(text(
            "SELECT count(*) FROM file_search_chunks WHERE attachment_id=1"
        )).scalar_one() == 1
        connection.commit()
        command.downgrade(config, "f42b8d0e315a")
        connection.commit()
        assert "caption" not in {column["name"] for column in inspect(connection).get_columns("task_attachments")}
        assert connection.execute(text(
            "SELECT storage_key FROM task_attachments WHERE id=1"
        )).scalar_one() == "tasks/1/original.txt"
        assert connection.execute(text(
            "SELECT body_md FROM task_comments WHERE id=1"
        )).scalar_one() == "Прежний комментарий"
        command.upgrade(config, "head")
        connection.commit()

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()
