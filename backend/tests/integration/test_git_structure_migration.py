"""c34 сохраняет прежние Git-данные и изолирует поколения реальными FK."""

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


async def test_populated_upgrade_generation_constraints_rollback_and_safe_downgrade(postgres_container):
    database = "git_structure_" + uuid4().hex
    admin = create_async_engine(postgres_container.get_connection_url().replace("psycopg2", "asyncpg"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as db:
        await db.execute(text(f'CREATE DATABASE "{database}"'))
    engine = create_async_engine(admin.url.set(database=database))
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/db/alembic"))

    def verify(db):
        config.attributes["connection"] = db
        command.upgrade(config, "c33a202611240001")
        db.execute(text("INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) VALUES (1,'structure-migration','!','Test','User',true)"))
        db.execute(text("INSERT INTO projects(id,owner_id,key,name,description_md,color,status,order_index) VALUES (1,1,'MIG','Existing','Preserve','#334455','ACTIVE',0)"))
        for identifier in (1, 2):
            db.execute(text("INSERT INTO git_connections(id,project_id,repository_id,owner,repo,name,ref,scope_hash,is_private) VALUES (:id,1,101,'example','app','App','heads/main',repeat('a',64),false)"), {"id": identifier})
            db.execute(text("INSERT INTO git_snapshots(id,connection_id,commit_sha,tree_sha,scope_hash,complete) VALUES (:id,:id,repeat('b',40),repeat('c',40),repeat('a',64),true)"), {"id": identifier})
            db.execute(text("INSERT INTO git_files(id,snapshot_id,connection_id,path,blob_sha,size,state,text,text_hash) VALUES (:id,:id,:id,'src/auth.py',repeat('d',40),5,'ready','code',repeat('e',64))"), {"id": identifier})
        db.commit()
        before = [db.execute(text(f"SELECT row_to_json(t) FROM {table} t ORDER BY id")).all() for table in ("users", "projects", "git_connections", "git_snapshots", "git_files")]
        db.commit()
        command.upgrade(config, "c34a202611250001")
        db.commit()
        after = [db.execute(text(f"SELECT row_to_json(t) FROM {table} t ORDER BY id")).all() for table in ("users", "projects", "git_connections", "git_snapshots", "git_files")]
        assert before == after
        context = MigrationContext.configure(db, opts={"include_object": lambda obj, name, kind, reflected, compare_to: (name if kind == "table" else getattr(getattr(obj, "table", None), "name", None)) in {"git_analyses", "git_symbols", "git_relations"}, "compare_server_default": True})
        assert compare_metadata(context, Base.metadata) == []
        db.execute(text("INSERT INTO git_analyses(id,snapshot_id,version) VALUES (1,1,'v1'),(2,1,'v2'),(3,2,'v1')"))
        symbol = "INSERT INTO git_symbols(id,analysis_id,snapshot_id,path,key_hash,name,qualified_name,search_name,kind,line_start,line_end,\"offset\",end_offset) VALUES (:id,:analysis,:snapshot,'src/auth.py',repeat('f',64),'login','Auth.login','authlogin','method',1,1,0,4)"
        for identifier, analysis, snapshot in ((1, 1, 1), (2, 2, 1), (3, 3, 2)):
            db.execute(text(symbol), {"id": identifier, "analysis": analysis, "snapshot": snapshot})
        db.execute(text("SELECT setval(pg_get_serial_sequence('git_analyses','id'),3)"))
        db.commit()
        invalid = [
            "INSERT INTO git_analyses(snapshot_id,version) VALUES (1,'v1')",
            "INSERT INTO git_symbols(id,analysis_id,snapshot_id,path,key_hash,name,qualified_name,search_name,kind,line_start,line_end,\"offset\",end_offset) VALUES (4,1,1,'src/auth.py',repeat('f',64),'login','Auth.login','authlogin','method',1,1,0,4)",
            "UPDATE git_symbols SET parent_id=2 WHERE id=1",
            "UPDATE git_symbols SET snapshot_id=2 WHERE id=1",
            "INSERT INTO git_relations(analysis_id,source_id,target_id,kind,confidence,label,line_start,line_end) VALUES (1,1,3,'imports','resolved','Auth',1,1)",
            "INSERT INTO git_relations(analysis_id,source_id,target_id,kind,confidence,label,line_start,line_end) VALUES (1,1,NULL,'imports','resolved','Auth',1,1)",
        ]
        for statement in invalid:
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(text(statement))
        assert db.execute(text("SELECT count(*) FROM git_symbols")).scalar_one() == 3
        db.commit()
        with pytest.raises(RuntimeError):
            command.downgrade(config, "c33a202611240001")
        db.rollback()
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "c34a202611250001"
        db.execute(text("DELETE FROM git_analyses"))
        db.commit()
        command.downgrade(config, "c33a202611240001")
        db.commit()
        assert db.execute(text("SELECT count(*) FROM git_files")).scalar_one() == 2
        command.upgrade(config, "c34a202611250001")
        db.commit()

    try:
        async with engine.connect() as db:
            await db.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as db:
            await db.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        await admin.dispose()
