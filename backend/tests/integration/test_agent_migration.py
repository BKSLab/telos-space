"""Миграция диалогов совместима с ORM и сохраняет существующие проекты."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models import AgentConversation, AgentMessage, Base


async def test_agent_migration_upgrade_and_downgrade_preserve_project(postgres_container):
    database = f"agent_migration_{uuid4().hex}"
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database}"'))
    engine = create_async_engine(admin.url.set(database=database))
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/db/alembic"))

    def verify(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, "f4a72c908e16")
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, first_name, last_name, is_active) VALUES (1, 'owner', '!', 'Иван', 'Тестов', true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, owner_id, key, name, color, status, order_index) VALUES (1, 1, 'OLD', 'Существующий проект', '#334455', 'ACTIVE', 0)"
            )
        )
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()

        def include_object(obj, name, kind, reflected, compare_to):
            table_name = (
                name if kind == "table" else getattr(getattr(obj, "table", None), "name", None)
            )
            return table_name in {
                "agent_conversations",
                "agent_messages",
                "agent_tool_runs",
                "agent_files",
                "api_keys",
                "api_tokens",
            }

        context = MigrationContext.configure(
            connection, opts={"include_object": include_object, "compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        conversation_id = connection.execute(
            AgentConversation.__table__.insert()
            .values(project_id=1, user_id=1, title="Первый вопрос")
            .returning(AgentConversation.id)
        ).scalar_one()
        request_id = uuid4()
        connection.execute(
            AgentMessage.__table__.insert().values(
                [
                    dict(
                        conversation_id=conversation_id,
                        request_id=request_id,
                        role="user",
                        content="Что известно по проекту?",
                        status="completed",
                    ),
                    dict(
                        conversation_id=conversation_id,
                        request_id=request_id,
                        role="assistant",
                        content="",
                        status="queued",
                    ),
                ]
            )
        )
        assert connection.execute(
            text("SELECT summary, summary_through_id FROM agent_conversations")
        ).one() == ("", 0)
        assert (
            connection.execute(
                text("SELECT sources FROM agent_messages WHERE role='assistant'")
            ).scalar_one()
            == []
        )
        triggers = connection.execute(
            text(
                "SELECT event_object_table FROM information_schema.triggers WHERE trigger_name='knowledge_changed' AND event_object_table LIKE 'agent_%'"
            )
        ).all()
        assert triggers == []
        connection.commit()

        command.downgrade(config, "f4a72c908e16")
        connection.commit()
        assert not {
            "agent_conversations",
            "agent_messages",
            "agent_tool_runs",
            "agent_files",
        } & set(inspect(connection).get_table_names())
        assert (
            connection.execute(text("SELECT name FROM projects WHERE id=1")).scalar_one()
            == "Существующий проект"
        )
        connection.commit()
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
