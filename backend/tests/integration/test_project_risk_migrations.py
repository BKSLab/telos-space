"""Реальные Alembic upgrade/downgrade в отдельной базе тестового контейнера."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

# Freeze this legacy roundtrip before the independently guarded history journal.
PRE_HISTORY_REVISION = "c32a202611200001"


async def test_risk_migrations_preserve_existing_data_and_constraints(postgres_container):
    name = f"risk_migration_{uuid4().hex}"
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        # Имя генерируется только из фиксированного префикса и UUID.
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_async_engine(admin.url.set(database=name))
    await admin.dispose()
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/db/alembic"))

    def verify(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, "d5f83a17c204")
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, first_name, last_name, is_active) VALUES (1, 'migration_user', '!', 'Тест', 'Тестов', true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, owner_id, key, name, color, status, order_index) VALUES (1, 1, 'MIG', 'Исходный проект', '#334455', 'ACTIVE', 0)"
            )
        )
        connection.commit()
        command.upgrade(config, PRE_HISTORY_REVISION)
        connection.commit()
        schema = inspect(connection)
        assert {column["name"] for column in schema.get_columns("tasks")} >= {"checklist", "checklist_revision"}
        connection.execute(text("INSERT INTO project_stages (id, project_id, name, color, order_index, is_done_stage) VALUES (1, 1, 'Работа', '#334455', 0, false)"))
        connection.execute(text("INSERT INTO tasks (id, project_id, stage_id, number, title, priority, position) VALUES (1, 1, 1, 1, 'Исходная задача', 'MEDIUM', 0)"))
        assert connection.execute(text("SELECT checklist, checklist_revision FROM tasks WHERE id=1")).one() == (None, 0)
        connection.execute(text("UPDATE tasks SET checklist = CAST(:value AS jsonb), checklist_revision=1 WHERE id=1"), {"value": '{"title":"Приёмка","items":[]}'})
        assert connection.execute(text("SELECT 'CHECKLIST_CHANGED'::task_activity_event_type")).scalar_one() == "CHECKLIST_CHANGED"
        assert {index["name"] for index in schema.get_indexes("project_risks")} >= {
            "ix_project_risks_project_status",
            "ix_project_risks_project_level",
            "ix_project_risks_project_review",
        }
        assert len(schema.get_check_constraints("project_risks")) == 2
        foreign_keys = {
            fk["constrained_columns"][0]: fk for fk in schema.get_foreign_keys("project_risks")
        }
        assert foreign_keys["task_id"]["options"]["ondelete"] == "SET NULL"
        assert foreign_keys["owner_user_id"]["options"]["ondelete"] == "SET NULL"
        assert foreign_keys["project_id"]["options"]["ondelete"] == "CASCADE"
        connection.execute(
            text(
                "INSERT INTO project_risks (project_id, title, description, probability, impact, risk_level, response_strategy) VALUES (1, 'Риск', 'Описание', 'HIGH', 'MEDIUM', 'HIGH', 'MITIGATE')"
            )
        )
        assert connection.execute(text("SELECT status, source FROM project_risks")).one() == (
            "OPEN",
            "MANUAL",
        )
        assert (
            connection.execute(text("SELECT 'RISK'::knowledge_entity_type")).scalar_one() == "RISK"
        )
        connection.commit()
        command.downgrade(config, "d5f83a17c204")
        connection.commit()
        assert "project_risks" not in inspect(connection).get_table_names()
        assert "checklist" not in {column["name"] for column in inspect(connection).get_columns("tasks")}
        assert connection.execute(text("SELECT title FROM tasks WHERE id=1")).scalar_one() == "Исходная задача"
        assert (
            connection.execute(text("SELECT name FROM projects WHERE id=1")).scalar_one()
            == "Исходный проект"
        )
        assert (
            connection.execute(text("SELECT username FROM users WHERE id=1")).scalar_one()
            == "migration_user"
        )
        # Обратный переход не удаляет общую enum-метку и данные outbox.
        assert (
            connection.execute(text("SELECT 'RISK'::knowledge_entity_type")).scalar_one() == "RISK"
        )
        connection.commit()
        command.upgrade(config, PRE_HISTORY_REVISION)
        connection.commit()
        assert "project_risks" in inspect(connection).get_table_names()
        assert connection.execute(text("SELECT checklist, checklist_revision FROM tasks WHERE id=1")).one() == (None, 0)

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
