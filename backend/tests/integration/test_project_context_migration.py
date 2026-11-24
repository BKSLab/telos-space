"""DDL контекста не переписывает данные; откат защищает сохранённую историю."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import load_only

from src.db.models import Base
from src.db.models.users import User
from src.repositories.projects import ProjectsRepository
from src.repositories.users import UsersRepository
from tests.integration.repositories.test_project_passport import project_service

PARENT = "f23d8b410ce2"
REVISION = "f23a8c91d046"


class LegacyAuthorRepository(UsersRepository):
    """Читает автора из PostgreSQL на схеме до добавления GitHub-профиля."""

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.db_session.scalar(
            select(User)
            .options(load_only(User.id, User.username, User.first_name, User.last_name, User.middle_name))
            .where(User.id == user_id)
        )


async def test_context_migration_is_schema_only_and_downgrade_preserves_saved_snapshots(
    postgres_container,
):
    database = f"project_context_migration_{uuid4().hex}"
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
    raw = (
        "## Цель\n\nСтарый Markdown не становится структурированной целью.\n"
        + "Исходный хвост🙂 " * 40
    )
    old_sections = {
        "problem": "Проблема",
        "goal": "Прежняя цель",
        "expected_result": "",
        "additional": "Договорённость",
    }

    def snapshots(connection):
        return (
            connection.execute(
                text(
                    "SELECT id,description_md,description_sections,updated_at FROM projects ORDER BY id"
                )
            ).all(),
            connection.execute(text("SELECT id,description_md FROM tasks ORDER BY id")).all(),
            connection.execute(text("SELECT id,content_md FROM documents ORDER BY id")).all(),
            connection.execute(
                text(
                    "SELECT id,project_id,entity_type,entity_id,operation,status FROM knowledge_index_jobs ORDER BY id"
                )
            ).all(),
        )

    def verify(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, PARENT)
        connection.execute(
            text(
                "INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) VALUES (1,'context-migration-owner','!','Иван','Тестов',true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects(id,owner_id,key,name,description_md,description_sections,color,status,order_index) VALUES (1,1,'OLDCTX','Старое описание',:raw,NULL,'#334455','ACTIVE',0),(2,1,'FIELDSCTX','Прежние поля','Неизменяемый текст',CAST(:sections AS jsonb),'#334455','ACTIVE',1)"
            ),
            {"raw": raw, "sections": json.dumps(old_sections, ensure_ascii=False)},
        )
        connection.execute(
            text(
                "INSERT INTO project_stages(id,project_id,name,order_index,color,is_done_stage) VALUES (1,1,'Работа',0,'#334455',false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tasks(id,project_id,stage_id,number,title,description_md,priority,position) VALUES (1,1,1,1,'Прежняя задача','Полное описание задачи','MEDIUM',1000)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents(id,project_id,slug,title,content_md) VALUES (1,1,'original','Документ','Неизменяемый текст документа')"
            )
        )
        connection.commit()
        before = snapshots(connection)
        connection.commit()
        command.upgrade(config, REVISION)
        connection.commit()
        assert snapshots(connection) == before
        assert connection.execute(
            text("SELECT context_revision FROM projects ORDER BY id")
        ).scalars().all() == [0, 0]
        assert (
            connection.execute(text("SELECT count(*) FROM project_context_history")).scalar_one()
            == 0
        )
        assert connection.execute(
            text("SELECT description_md,description_sections FROM projects ORDER BY id")
        ).all() == [(raw, None), ("Неизменяемый текст", old_sections)]

        def include_object(obj, name, kind, reflected, compare_to):
            table = name if kind == "table" else getattr(getattr(obj, "table", None), "name", None)
            if kind == "table":
                return name in {"projects", "project_context_history"}
            return table == "project_context_history" or (
                table == "projects" and kind == "column" and name == "context_revision"
            )

        context = MigrationContext.configure(
            connection, opts={"include_object": include_object, "compare_server_default": True}
        )
        assert compare_metadata(context, Base.metadata) == []
        connection.commit()
        # Пустая новая история позволяет безопасный откат и повторный upgrade.
        command.downgrade(config, PARENT)
        connection.commit()
        assert "project_context_history" not in inspect(connection).get_table_names()
        assert "context_revision" not in {
            column["name"] for column in inspect(connection).get_columns("projects")
        }
        assert snapshots(connection) == before
        connection.commit()
        command.upgrade(config, REVISION)
        connection.commit()
        assert snapshots(connection) == before
        connection.execute(
            text(
                "INSERT INTO project_context_history(project_id,revision,description_md,description_sections,changed_by_user_id,changed_by_name,created_at,is_initial,changed_fields) VALUES (1,0,:raw,NULL,NULL,NULL,CURRENT_TIMESTAMP,true,'[]'::jsonb)"
            ),
            {"raw": raw},
        )
        connection.commit()
        with pytest.raises(RuntimeError, match="saved snapshots"):
            command.downgrade(config, PARENT)
        connection.rollback()
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
        assert (
            connection.execute(
                text(
                    "SELECT description_md FROM project_context_history WHERE project_id=1 AND revision=0"
                )
            ).scalar_one()
            == raw
        )
        assert snapshots(connection) == before

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()


async def test_context_downgrade_waits_for_project_writer_and_preserves_committed_snapshots(
    postgres_container,
):
    """Начатая правка завершается до проверки guard, сохраняя оба снимка и head."""
    database = f"project_context_race_{uuid4().hex}"
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
    ready = asyncio.Event()
    migration_pid = {}
    pending = None
    original = "Первоначальный полный текст🙂"

    def prepare(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, REVISION)
        connection.execute(
            text(
                "INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) "
                "VALUES (1,'context-race-owner','!','Иван','Тестов',true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects(id,owner_id,key,name,description_md,color,status,order_index) "
                "VALUES (1,1,'CTXGUARD','Защита истории',:raw,'#334455','ACTIVE',0)"
            ),
            {"raw": original},
        )
        connection.commit()

    def downgrade(connection):
        config.attributes["connection"] = connection
        command.downgrade(config, PARENT)
        connection.commit()

    async def start_downgrade():
        async with engine.connect() as connection:
            migration_pid["value"] = await connection.scalar(text("SELECT pg_backend_pid()"))
            await connection.commit()
            ready.set()
            try:
                await connection.run_sync(downgrade)
            finally:
                await connection.rollback()

    try:
        async with engine.connect() as connection:
            await connection.run_sync(prepare)
        async with AsyncSession(engine, expire_on_commit=False) as writer:
            await ProjectsRepository(writer).get_by_id(1, for_update=True)
            pending = asyncio.create_task(start_downgrade())
            async with asyncio.timeout(10):
                await ready.wait()
                while not await writer.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM pg_locks "
                        "WHERE pid=:pid AND relation='projects'::regclass "
                        "AND mode='AccessExclusiveLock' AND NOT granted)"
                    ),
                    {"pid": migration_pid["value"]},
                ):
                    if pending.done():
                        await pending
                        pytest.fail(
                            "Downgrade finished before the project writer released its lock"
                        )
                    await asyncio.sleep(0)

            service = project_service(writer)
            service.users_repository = LegacyAuthorRepository(writer)
            changed = await asyncio.wait_for(
                service.update_project(
                    1,
                    {"description_md": "Новая авторская версия", "expected_context_revision": 0},
                    1,
                ),
                timeout=10,
            )
            assert changed.context_revision == 1
            with pytest.raises(RuntimeError, match="saved snapshots"):
                await asyncio.wait_for(pending, timeout=10)
            assert await writer.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
            assert (
                await writer.execute(
                    text("SELECT context_revision,description_md FROM projects WHERE id=1")
                )
            ).one() == (1, "Новая авторская версия")
            snapshots = (
                await writer.execute(
                    text(
                        "SELECT revision,description_md,changed_by_user_id,is_initial "
                        "FROM project_context_history WHERE project_id=1 ORDER BY revision"
                    )
                )
            ).all()
            assert snapshots == [
                (0, original, None, True),
                (1, "Новая авторская версия", 1, False),
            ]
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()
