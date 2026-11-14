"""Обновление существующей схемы сохраняет тексты, оригиналы и карточки чата."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.db.models import Base
from src.utils.document_refs import encode_document_ref


def legacy_queue_cases(connection):
    """Готовит старые API, cookie и завершённые ответы с разной авторизацией."""
    key_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO api_keys(id,label,key_hash,key_prefix,owner_user_id,kind,scopes,is_active) VALUES(:id,'Synthetic migration key','!','f00dcafe',1,'personal','[]'::json,true)"
        ),
        {"id": key_id},
    )
    rows = []
    for name, channel, key, scopes, status, stop in (
        ("unknown-sql-null", "api", None, "NULL", "queued", True),
        ("unknown-json-null", "api", None, "'null'::jsonb", "processing", True),
        ("cookie", "web", None, "NULL", "queued", False),
        ("finished", "api", None, "NULL", "completed", False),
        ("scoped-marker", "api", None, "'[]'::jsonb", "queued", False),
        ("known-key", "api", key_id, "NULL", "queued", False),
    ):
        conversation = connection.execute(
            text(
                "INSERT INTO agent_conversations(project_id,user_id,title,channel,api_key_id) VALUES(1,1,:title,:channel,:key) RETURNING id"
            ),
            {"title": name, "channel": channel, "key": key},
        ).scalar_one()
        request = uuid4()
        question_text = f"Сохранить вопрос {name}"
        question = connection.execute(
            text(
                f"INSERT INTO agent_messages(conversation_id,request_id,role,status,content,key_scopes) VALUES(:conversation,:request,'user','completed',:content,{scopes}) RETURNING id"
            ),
            {"conversation": conversation, "request": request, "content": question_text},
        ).scalar_one()
        answer = connection.execute(
            text(
                "INSERT INTO agent_messages(conversation_id,request_id,role,status,content,progress,run_id) VALUES(:conversation,:request,'assistant',:status,:content,'До обновления',:run) RETURNING id"
            ),
            {
                "conversation": conversation,
                "request": request,
                "status": status,
                "content": "Исторический ответ" if status == "completed" else "",
                "run": uuid4(),
            },
        ).scalar_one()
        rows.append((question, question_text, answer, status, stop))
    return rows


def legacy_action_cases(connection):
    """Исторические результаты не требуют наличия прежнего оригинала на диске."""
    rows = []
    for status, payload in (
        (
            "completed",
            {
                "document": {
                    "id": 1,
                    "slug": "original",
                    "title": "Исходный",
                    "content_md": "Прежний текст",
                }
            },
        ),
        (
            "pending",
            {
                "preview": {
                    "attachment": {"id": 9, "task_id": 100, "original_name": "Прежний.txt"}
                },
                "confirmation": "preserve",
            },
        ),
        ("completed", {"task": {"id": 123, "title": "Не менять"}}),
    ):
        identifier, request = uuid4(), uuid4()
        arguments = {"document_id": 1, "audit": "unchanged"}
        connection.execute(
            text(
                "INSERT INTO agent_tool_runs(id,project_id,user_id,request_id,tool_name,title,arguments,status,result) VALUES(:id,1,1,:request,'telos_create_document','Историческое действие',CAST(:arguments AS jsonb),:status,CAST(:result AS jsonb))"
            ),
            {
                "id": identifier,
                "request": request,
                "arguments": json.dumps(arguments),
                "status": status,
                "result": json.dumps(payload, ensure_ascii=False),
            },
        )
        rows.append((identifier, request, status, payload, arguments))
    return rows


async def test_catalog_upgrade_roundtrip_and_data_preserving_downgrade_guard(postgres_container):
    database = f"document_catalog_{uuid4().hex}"
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
        command.upgrade(config, "d86e1a90c427")
        connection.execute(
            text(
                "INSERT INTO users(id,username,password_hash,first_name,last_name,is_active) VALUES(1,'catalog-owner','!','Тест','Каталога',true)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO projects(id,owner_id,key,name,color,status,order_index) VALUES(1,1,'CAT','Каталог','#123456','ACTIVE',0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents(id,project_id,slug,title,content_md) VALUES(1,1,'original','Исходный','Прежний текст 😀')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO project_files(id,project_id,original_name,content_type,size,storage_key,content_hash) VALUES(1,1,'source.txt','text/plain',4,'projects/1/source.txt',:hash)"
            ),
            {"hash": "a" * 64},
        )
        chat_id = connection.execute(
            text("SELECT id FROM project_chats WHERE project_id=1")
        ).scalar_one()
        message_id = connection.execute(
            text(
                "INSERT INTO chat_messages(chat_id,project_id,seq,author_user_id,content,client_message_id) VALUES(:chat,1,1,1,'Ссылка на прежний документ',:request) RETURNING id"
            ),
            {"chat": chat_id, "request": uuid4()},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO chat_message_entities(message_id,entity_type,entity_id,position) VALUES(:message,'DOCUMENT',1,0)"
            ),
            {"message": message_id},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_project_file_texts(project_file_id,text,status,original_chars,content_hash,index_status,indexed_hash,embedding_signature) VALUES(1,'Готовый текст','ready',12,:hash,'ready',:hash,:signature)"
            ),
            {"hash": "a" * 64, "signature": "b" * 64},
        )
        connection.execute(text("UPDATE file_processing_jobs SET status='ready'"))
        connection.execute(text("UPDATE knowledge_index_jobs SET status='SUCCEEDED'"))
        extraction_jobs = connection.execute(
            text("SELECT count(*) FROM file_processing_jobs")
        ).scalar_one()
        queue_cases = legacy_queue_cases(connection)
        action_cases = legacy_action_cases(connection)
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
        assert (
            connection.execute(text("SELECT content_md FROM documents WHERE id=1")).scalar_one()
            == "Прежний текст 😀"
        )
        assert (
            connection.execute(
                text("SELECT storage_key FROM project_files WHERE id=1")
            ).scalar_one()
            == "projects/1/source.txt"
        )
        link = connection.execute(
            text("SELECT entity_id,document_ref FROM chat_message_entities WHERE message_id=:id"),
            {"id": message_id},
        ).one()
        assert tuple(link) == (None, encode_document_ref("document", 1))
        for identifier, request, status, payload, arguments in action_cases:
            stored = (
                connection.execute(
                    text(
                        "SELECT request_id,status,result,arguments FROM agent_tool_runs WHERE id=:id"
                    ),
                    {"id": identifier},
                )
                .mappings()
                .one()
            )
            assert (
                stored["request_id"] == request
                and stored["status"] == status
                and stored["arguments"] == arguments
            )
            result = stored["result"]
            if "document" in payload:
                assert result["document"]["ref"] == encode_document_ref("document", 1)
                assert result["source_id"] == "document:1"
                assert result["document"]["content_md"] == payload["document"]["content_md"]
                assert result["document"]["slug"] == "original"
            elif "preview" in payload:
                preview = result["preview"]
                assert preview["document"]["ref"] == encode_document_ref("attachment", 9)
                assert preview["document"]["title"] == "Прежний.txt"
                assert preview["source_id"] == "attachment:9"
                assert preview["attachment"] == payload["preview"]["attachment"]
                assert result["confirmation"] == "preserve"
            else:
                assert result == payload
        for question, question_text, answer, old_status, stop in queue_cases:
            assert (
                connection.execute(
                    text("SELECT content FROM agent_messages WHERE id=:id"), {"id": question}
                ).scalar_one()
                == question_text
            )
            result = (
                connection.execute(
                    text(
                        "SELECT status,error,progress,run_id,completed_at,content FROM agent_messages WHERE id=:id"
                    ),
                    {"id": answer},
                )
                .mappings()
                .one()
            )
            assert result["status"] == ("failed" if stop else old_status)
            if stop:
                assert result["error"] == "Авторизация обновлена. Повторите вопрос."
                assert (
                    result["progress"] is None
                    and result["run_id"] is None
                    and result["completed_at"] is not None
                )
            elif old_status == "completed":
                assert result["content"] == "Исторический ответ"
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM knowledge_index_jobs WHERE project_id=1 AND status='PENDING'"
                )
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(text("SELECT count(*) FROM file_processing_jobs")).scalar_one()
            == extraction_jobs
        )
        assert connection.execute(
            text(
                "SELECT text,index_status FROM knowledge_project_file_texts WHERE project_file_id=1"
            )
        ).one() == ("Готовый текст", "ready")
        included = {
            "documents",
            "document_links",
            "project_files",
            "task_attachments",
            "document_submissions",
            "document_index_states",
            "chat_message_entities",
            "task_comment_documents",
            "agent_messages",
        }

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
        connection.execute(text("UPDATE documents SET caption='миграциокварцевый' WHERE id=1"))
        assert connection.execute(
            text(
                "SELECT search_vector @@ plainto_tsquery('russian','миграциокварцевый') FROM documents WHERE id=1"
            )
        ).scalar_one()
        connection.execute(text("UPDATE documents SET caption=NULL WHERE id=1"))
        connection.commit()
        command.downgrade(config, "d86e1a90c427")
        connection.commit()
        assert (
            connection.execute(
                text("SELECT entity_id FROM chat_message_entities WHERE message_id=:id"),
                {"id": message_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(text("SELECT content_md FROM documents WHERE id=1")).scalar_one()
            == "Прежний текст 😀"
        )
        command.upgrade(config, "head")
        connection.commit()
        connection.execute(
            text("UPDATE project_files SET display_title='Сохранить название' WHERE id=1")
        )
        connection.commit()
        with pytest.raises(RuntimeError, match="Откат остановлен"):
            command.downgrade(config, "d86e1a90c427")
        connection.rollback()
        assert (
            connection.execute(
                text("SELECT display_title FROM project_files WHERE id=1")
            ).scalar_one()
            == "Сохранить название"
        )
        connection.execute(text("UPDATE project_files SET display_title=NULL WHERE id=1"))
        connection.commit()
        connection.execute(
            text(
                "INSERT INTO tasks(id,project_id,number,title,status,priority,position) VALUES(100,NULL,100,'Личная','NEW','MEDIUM',0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents(id,owner_task_id,slug,title,content_md) VALUES(2,100,'personal','Личный','Сохранить данные')"
            )
        )
        connection.commit()
        with pytest.raises(RuntimeError, match="Откат остановлен"):
            command.downgrade(config, "d86e1a90c427")
        connection.rollback()
        assert (
            connection.execute(
                text("SELECT content_md FROM documents WHERE owner_task_id=100")
            ).scalar_one()
            == "Сохранить данные"
        )

    try:
        async with engine.connect() as connection:
            await connection.run_sync(verify)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin.dispose()
