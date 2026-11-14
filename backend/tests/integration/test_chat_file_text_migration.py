"""Добавочная миграция не изменяет оригиналы, сообщения и их права/привязки."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models import Base


async def test_chat_file_cache_upgrade_roundtrip_preserves_originals(postgres_container):
    name = f"chat_file_migration_{uuid4().hex}"
    admin = create_async_engine(
        postgres_container.get_connection_url().replace("psycopg2", "asyncpg"),
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_async_engine(admin.url.set(database=name))
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/db/alembic"))

    def verify(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, "e92a74c610bf")
        connection.execute(
            text(
                "INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) "
                "VALUES(1,'synthetic-cache-owner','!','Тест','Кэш',true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects(id,owner_id,key,name,color,status,order_index) "
                "VALUES(1,1,'CACHE','Сохранить','#334455','ACTIVE',0)"
            )
        )
        conversation = connection.execute(
            text(
                "INSERT INTO agent_conversations(user_id,project_id,title) VALUES(1,1,'Личный') RETURNING id"
            )
        ).scalar_one()
        question = connection.execute(
            text(
                "INSERT INTO agent_messages(conversation_id,request_id,role,status,content) "
                "VALUES(:c,:r,'user','completed','Сохранить дословно 🙂') RETURNING id"
            ),
            {"c": conversation, "r": uuid4()},
        ).scalar_one()
        agent_file, shared_file = uuid4(), uuid4()
        connection.execute(
            text(
                "INSERT INTO agent_files(id,conversation_id,message_id,original_name,content_type,size,storage_key) "
                "VALUES(:id,:c,:m,'Личный.txt','text/plain',15,'synthetic/private.txt')"
            ),
            {"id": agent_file, "c": conversation, "m": question},
        )
        chat = connection.execute(
            text("SELECT id FROM project_chats WHERE project_id=1")
        ).scalar_one()
        message = connection.execute(
            text(
                "INSERT INTO chat_messages(chat_id,project_id,seq,author_user_id,client_message_id,content) "
                "VALUES(:chat,1,1,1,:client,'Сохранить общий файл') RETURNING id"
            ),
            {"chat": chat, "client": uuid4()},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO chat_attachments(id,chat_id,message_id,uploader_id,storage_key,original_name,content_type,size_bytes) "
                "VALUES(:id,:chat,:m,1,'synthetic/shared.txt','Общий.txt','text/plain',20)"
            ),
            {"id": shared_file, "chat": chat, "m": message},
        )
        tables = (
            "users",
            "projects",
            "project_members",
            "agent_conversations",
            "agent_messages",
            "agent_files",
            "chat_messages",
            "chat_attachments",
        )

        def snapshot():
            return {
                table: connection.execute(text(f"SELECT to_jsonb(t) FROM {table} t ORDER BY id"))
                .scalars()
                .all()
                for table in tables
            }

        before = snapshot()
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
        assert snapshot() == before
        assert connection.execute(text("SELECT count(*) FROM chat_file_texts")).scalar_one() == 0

        def cache_only(obj, name, kind, reflected, compare_to):
            table = name if kind == "table" else getattr(getattr(obj, "table", None), "name", None)
            return table == "chat_file_texts"

        context = MigrationContext.configure(
            connection,
            opts={
                "compare_server_default": True,
                "include_object": cache_only,
            },
        )
        assert compare_metadata(context, Base.metadata) == []
        connection.commit()
        # XOR, source uniqueness и FK cleanup проверяются настоящими PG constraints.
        for private, shared in ((None, None), (agent_file, shared_file)):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO chat_file_texts(agent_file_id,chat_attachment_id,status) VALUES(:p,:s,'pending')"
                    ),
                    {"p": private, "s": shared},
                )
        connection.execute(
            text(
                "INSERT INTO chat_file_texts(agent_file_id,status,content,total_chars) VALUES(:p,'ready','Кэш',3)"
            ),
            {"p": agent_file},
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text("INSERT INTO chat_file_texts(agent_file_id,status) VALUES(:p,'pending')"),
                {"p": agent_file},
            )
        connection.commit()
        command.downgrade(config, "e92a74c610bf")
        connection.commit()
        assert snapshot() == before
        command.upgrade(config, "head")
        connection.commit()
        assert snapshot() == before
        assert connection.execute(text("SELECT count(*) FROM chat_file_texts")).scalar_one() == 0

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
