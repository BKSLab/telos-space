import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.clients.vision import DisabledVisionCapability
from src.exceptions.document_catalog import CatalogDocumentNotFoundError
from src.exceptions.tasks import TaskContextDocumentError, TaskDescriptionRewriteError
from src.repositories.projects import ProjectsRepository
from src.repositories.tasks import TasksRepository
from src.schemas.tasks import TaskRephraseFile, TaskRephraseRequestSchema, TaskRephraseResultSchema
from src.services.db_scope import TaskDescriptionScope
from src.services.document_catalog import DocumentCatalogService
from src.services.task_descriptions import TaskDescriptionService
from src.utils.document_refs import encode_document_ref


def build_service(*, file_context_limit: int = 5000):
    projects = AsyncMock(spec=ProjectsRepository)
    projects.get_by_id.return_value = SimpleNamespace(
        id=1,
        key="TELOS",
        name="TELOS SPACE",
        description_md="Контекст проекта",
    )
    tasks = AsyncMock(spec=TasksRepository)
    tasks.get_by_project.return_value = [
        SimpleNamespace(
            id=3,
            number=7,
            title="Согласовать макет",
            description_md="Проверить состояния формы.",
            updated_at=datetime.now(UTC),
        )
    ]
    documents = AsyncMock(spec=DocumentCatalogService)
    documents.read_context.return_value = "Документ: Требования\nДанные документа"
    llm = AsyncMock()
    llm.get_structured_response.return_value = TaskRephraseResultSchema(
        description_md="Понятное описание задачи."
    )
    db = TaskDescriptionScope(projects=projects, tasks=tasks)

    @asynccontextmanager
    async def scope():
        yield db

    service = TaskDescriptionService(
        scope=scope,
        llm_client=llm,
        vision=DisabledVisionCapability(),
        document_catalog=documents,
        file_context_limit=file_context_limit,
        max_file_size=10 * 1024 * 1024,
    )
    return service, projects, tasks, documents, llm


@pytest.mark.asyncio
async def test_rephrase_uses_project_tasks_documents_and_limited_file_text() -> None:
    service, _, tasks, _, llm = build_service(file_context_limit=5)
    tasks.get_by_project.return_value[0].checklist = {
        "title": "Приёмка",
        "items": [{"text": "Согласовать макет", "is_completed": True}],
    }

    result = await service.rephrase(
        project_id=1,
        user_id=7,
        data=TaskRephraseRequestSchema(
            title="Новая задача",
            description_md="сделать форму понятнее",
            document_refs=[encode_document_ref("document", 5)],
            checklist={"title": "Новые проверки", "items": [{"text": "Проверить фокус"}]},
        ),
        files=[TaskRephraseFile(name="brief.txt", content=b"abcdefghij")],
    )

    assert result.description_md == "Понятное описание задачи."
    prompt = json.loads(llm.get_structured_response.await_args.kwargs["content"])
    assert prompt["project"]["name"] == "TELOS SPACE"
    assert prompt["related_task_wording"][0]["key"] == "TELOS-7"
    assert "Документ: Требования" in prompt["selected_documents"]
    assert prompt["new_files"][0]["content"] == "abcde"
    assert prompt["task"]["checklist"]["items"] == [
        {"text": "Проверить фокус", "is_completed": False}
    ]
    assert prompt["related_task_wording"][0]["checklist"]["completed_items"] == 1


@pytest.mark.asyncio
async def test_rephrase_rejects_foreign_document_and_overgrown_result() -> None:
    """Документ чужого проекта и чрезмерно раздутый ответ модели отклоняются."""

    service, _, _, documents, llm = build_service()
    documents.read_context.side_effect = CatalogDocumentNotFoundError()

    with pytest.raises(TaskContextDocumentError):
        await service.rephrase(
            project_id=1,
            user_id=7,
            data=TaskRephraseRequestSchema(
                description_md="Черновик", document_refs=[encode_document_ref("document", 5)]
            ),
            files=[],
        )

    llm.get_structured_response.assert_not_awaited()

    service, _, _, _, llm = build_service()
    llm.get_structured_response.return_value = TaskRephraseResultSchema(description_md="x" * 1000)

    with pytest.raises(TaskDescriptionRewriteError):
        await service.rephrase(
            project_id=1,
            user_id=7,
            data=TaskRephraseRequestSchema(description_md="Короткий черновик"),
            files=[],
        )
