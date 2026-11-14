"""Добавочные цитаты/индексы/timezone сохраняют старую переписку и откат схемы."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


async def test_history_migration_preserves_original_messages_and_supports_roundtrip(postgres_container):
    name = f"chat_history_migration_{uuid4().hex}"
    admin = create_async_engine(postgres_container.get_connection_url().replace("psycopg2", "asyncpg"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_async_engine(admin.url.set(database=name))
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/db/alembic"))

    def verify(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, "f01b96d24c8a")
        connection.execute(text("INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) VALUES(1,'synthetic-history-owner','!','Тест','История',true)"))
        connection.execute(text("INSERT INTO projects(id,owner_id,key,name,color,status,order_index) VALUES(1,1,'HISTORY','Сохранить','#334455','ACTIVE',0)"))
        conversation = connection.execute(text("INSERT INTO agent_conversations(user_id,project_id,title) VALUES(1,1,'Личный') RETURNING id")).scalar_one()
        connection.execute(text("INSERT INTO agent_messages(conversation_id,request_id,role,status,content) VALUES(:c,:r,'user','completed','Оригинал личного 🙂')"), {"c": conversation, "r": uuid4()})
        chat = connection.execute(text("SELECT id FROM project_chats WHERE project_id=1")).scalar_one()
        question = connection.execute(text("INSERT INTO chat_messages(chat_id,project_id,seq,author_user_id,client_message_id,content) VALUES(:chat,1,1,1,:client,'Исходный вопрос') RETURNING id"), {"chat": chat, "client": uuid4()}).scalar_one()
        reply = connection.execute(text("INSERT INTO chat_messages(chat_id,project_id,seq,author_kind,client_message_id,content,reply_to_message_id) VALUES(:chat,1,2,'agent',:client,'Исходный ответ 🙂',:question) RETURNING id"), {"chat": chat, "client": uuid4(), "question": question}).scalar_one()
        connection.execute(text("INSERT INTO chat_agent_turns(chat_id,project_id,request_message_id,reply_message_id,requester_id,status) VALUES(:chat,1,:q,:r,1,'completed')"), {"chat": chat, "q": question, "r": reply})
        tables = ("users", "projects", "project_members", "agent_conversations", "agent_messages", "chat_messages", "chat_agent_turns")

        def snapshot():
            return {table: connection.execute(text(f"SELECT to_jsonb(t)-ARRAY['message_references','timezone'] FROM {table} t ORDER BY id")).scalars().all() for table in tables}

        before = snapshot()
        connection.commit()
        command.upgrade(config, "f12c7a309bd1")
        connection.commit()
        assert snapshot() == before
        assert connection.execute(text("SELECT message_references FROM agent_messages")).scalar_one() == []
        assert connection.execute(text("SELECT timezone,message_references FROM chat_agent_turns")).one() == ("UTC", [])
        catalog = inspect(connection)
        for table, names in {
            "agent_messages": {"ix_agent_messages_date", "ix_agent_messages_history"},
            "chat_messages": {"ix_chat_messages_author_date"},
        }.items():
            assert names.issubset({index["name"] for index in catalog.get_indexes(table)})
        connection.commit()
        command.downgrade(config, "f01b96d24c8a")
        connection.commit()
        assert snapshot() == before
        assert "message_references" not in {col["name"] for col in inspect(connection).get_columns("agent_messages")}
        command.upgrade(config, "f12c7a309bd1")
        connection.commit()
        assert snapshot() == before

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
