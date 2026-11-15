"""Документы задачи читаются отдельным каталогом во всех проектных режимах, без RAG/OCR."""

# ruff: noqa: F811 -- pytest внедряет импортированные фикстуры по имени параметра.

from dataclasses import replace
from uuid import uuid4

import pytest

from src.agent.aris import ArisMode
from src.clients.vision import DisabledVisionCapability
from src.dependencies.file_processing import build_file_processing_worker
from src.exceptions.agent_tools import AgentToolAccessError
from src.services.file_processing import FileProcessingConfig
from src.storage.task_attachments import TaskAttachmentStorage
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_agent_project_actions import (
    actions_env as actions_env,
)
from tests.integration.repositories.test_agent_project_actions import (
    invoke,
    result,
    task,
)


@pytest.mark.parametrize("mode", [ArisMode.PROJECT_CHAT, ArisMode.WIDGET_PROJECT])
async def test_task_catalog_lists_and_reads_owned_and_linked_documents(actions_env, mode):
    env = actions_env
    target = await task(env, "Задача с документами")
    empty_task = await task(env, "Без документов")
    expected = {}
    owned_text = "Собственные условия задачи\n" * 55 + "\nOWNED_MD_END"
    owned = result(await invoke(env, "telos_create_document", {
        "title": "План задачи", "owner_task_id": target["id"], "content_md": owned_text,
    }))["document"]
    expected[owned["ref"]] = owned_text
    linked_text = "Связанный проектный документ\n" * 55 + "\nLINKED_MD_END"
    linked = result(await invoke(env, "telos_create_document", {
        "title": "Условия проекта", "content_md": linked_text,
    }))["document"]
    await invoke(env, "telos_link_document", {"document_ref": linked["ref"], "task_id": target["id"]})
    expected[linked["ref"]] = linked_text
    attachment_text = "Сохранённый оригинал задачи\n" * 55 + "\nTASK_FILE_END"
    attached = result(await invoke(env, "telos_create_text_attachment", {
        "task_id": target["id"], "file_name": "task.md", "content": attachment_text,
    }))["document"]
    expected[attached["ref"]] = attachment_text
    project_file_text = "Загруженные требования проекта\n" * 55 + "\nPROJECT_FILE_END"
    async with env.actions.scope() as db:
        project_file = await db.document_catalog.upload(
            user_id=1, project_id=1, request_id=uuid4(), file_name="project.md",
            content_type="text/markdown", content=project_file_text.encode(),
        )
        await db.unit_of_work.commit()
    await invoke(env, "telos_link_document", {"document_ref": project_file.ref, "task_id": target["id"]})
    expected[project_file.ref] = project_file_text

    # Настоящий parser и durable jobs; vision отключён, ни LLM, ни Qdrant не нужны.
    worker = build_file_processing_worker(
        session_factory=env.factory, storage=TaskAttachmentStorage(env.files),
        vision=DisabledVisionCapability(), config=FileProcessingConfig(),
    )
    assert await worker.process_next()
    assert await worker.process_next()
    assert not await worker.process_next()

    context = replace(env.context, mode=mode)
    card = await invoke(env, "telos_get_task", {"task_id": target["id"]}, context=context)
    assert card["documents"]["loaded"] is False
    assert card["documents"]["arguments"] == {"task_id": target["id"], "scope": "all"}
    found = []
    offset = 0
    while offset is not None:
        page = await invoke(env, "telos_list_documents", {
            "task_id": target["id"], "scope": "all", "limit": 2, "offset": offset,
        }, context=context)
        assert page["total"] == 4
        found.extend(page["items"])
        offset = page["next_offset"]
    assert len(found) == len({item["ref"] for item in found}) == 4
    assert {item["ref"] for item in found} == set(expected)
    for item in found:
        text = []
        offset = 0
        while offset is not None:
            page = await invoke(env, "telos_get_document", {
                "document_ref": item["ref"], "offset": offset, "max_chars": 500,
            }, context=context)
            text.append(page["text"])
            offset = page["next_offset"]
        assert "".join(text) == expected[item["ref"]]
    assert (await invoke(env, "telos_list_documents", {"task_id": empty_task["id"]}, context=context))["total"] == 0

    other_project = replace(context, project_id=2)
    with pytest.raises(AgentToolAccessError):
        await invoke(env, "telos_list_documents", {"task_id": target["id"]}, context=other_project)
    with pytest.raises(AgentToolAccessError):
        await invoke(env, "telos_get_document", {"document_ref": owned["ref"]}, context=other_project)
