import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.ai import AiProviderError
from src.exceptions.auth import NotAuthenticatedError
from src.exceptions.clients import ClientError
from src.exceptions.tasks import (
    TaskChecklistGenerationError,
    TaskContextDocumentError,
    TaskNotFoundError,
)
from src.schemas.task_checklists import (
    ChecklistSuggestionDraftSchema,
    ChecklistSuggestionRequestSchema,
)
from src.schemas.tasks import TaskRephraseFile
from src.services.task_checklist_suggestions import (
    MAX_CONTEXT_CHARS,
    ChecklistSuggestionScope,
    TaskChecklistSuggestionService,
)
from src.storage.task_attachments import TaskAttachmentStorage
from tests.unit.services.test_task_checklists import checklist


def setup_service(tmp_path):
    db = ChecklistSuggestionScope(
        **{
            name: AsyncMock()
            for name in (
                "auth",
                "access",
                "projects",
                "tasks",
                "documents",
                "links",
                "attachments",
            )
        }
    )
    db.auth.resolve_principal.return_value = SimpleNamespace(user_id=7)
    db.projects.get_by_id.return_value = SimpleNamespace(id=1, key="PROJ", name="Проект")
    db.tasks.get_by_id.return_value = SimpleNamespace(id=12, project_id=1, checklist=checklist())
    db.documents.get_by_ids.return_value = []
    db.links.get_for_task.return_value = []
    db.attachments.get_for_task.return_value = []
    state = {"active": False}

    @asynccontextmanager
    async def scope():
        state["active"] = True
        try:
            yield db
        finally:
            state["active"] = False

    llm = AsyncMock()
    llm.get_structured_response.return_value = ChecklistSuggestionDraftSchema(
        items=["Согласовать условия", "Проверить результат", "Подписать акт"]
    )
    service = TaskChecklistSuggestionService(
        scope=scope,
        llm_client=llm,
        vision=AsyncMock(),
        storage=TaskAttachmentStorage(tmp_path),
        file_context_limit=5000,
        max_file_size=10000,
    )
    return service, db, llm, state


async def suggest(service, **data):
    return await service.suggest(
        project_id=1,
        data=ChecklistSuggestionRequestSchema(title="Запуск", **data),
        files=[],
        session_token="session",
        bearer_secret=None,
    )


async def test_draft_and_existing_files_documents_and_checklist_are_read_without_writes(
    tmp_path, monkeypatch
):
    service, db, llm, state = setup_service(tmp_path)
    (tmp_path / "stored.txt").write_text("Сохранённый файл: согласовать акт.", encoding="utf-8")
    db.attachments.get_for_task.return_value = [
        SimpleNamespace(original_name="Условия.txt", storage_key="stored.txt")
    ]
    db.links.get_for_task.return_value = [SimpleNamespace(document_id=3)]
    db.documents.get_by_ids.return_value = [
        SimpleNamespace(
            id=3, project_id=1, title="Требования", content_md="Проверить безопасность."
        )
    ]
    original_resolve = service.storage.resolve

    def resolve(key):
        assert not state["active"]
        return original_resolve(key)

    monkeypatch.setattr(service.storage, "resolve", resolve)

    async def answer(**kwargs):
        assert not state["active"]
        payload = json.loads(kwargs["content"])
        assert payload["task"]["title"] == "Запуск"
        assert payload["task"]["checklist"]["items"][0]["text"] == "Проверить результат"
        assert {item["content"] for item in payload["files"]} == {
            "Новый файл: подготовить резерв.",
            "Сохранённый файл: согласовать акт.",
        }
        assert payload["documents"][0]["content"] == "Проверить безопасность."
        return ChecklistSuggestionDraftSchema(
            items=["Согласовать условия", "Проверить результат", "Подписать акт"]
        )

    llm.get_structured_response.side_effect = answer
    result = await service.suggest(
        project_id=1,
        data=ChecklistSuggestionRequestSchema(
            title="Запуск", description_md="Описание из формы.", task_id=12
        ),
        files=[
            TaskRephraseFile(name="draft.txt", content="Новый файл: подготовить резерв.".encode())
        ],
        session_token="session",
        bearer_secret=None,
    )
    assert len(result.checklist.items) == 3
    assert len({item.id for item in result.checklist.items}) == 3
    assert not any(item.is_completed for item in result.checklist.items)
    assert not result.warnings
    db.tasks.update.assert_not_awaited()
    db.tasks.save.assert_not_awaited()
    db.documents.save.assert_not_awaited()


@pytest.mark.parametrize("denial", ["auth", "access"])
async def test_denied_requests_never_read_context_or_call_ai(tmp_path, denial):
    service, db, llm, _ = setup_service(tmp_path)
    if denial == "auth":
        db.auth.resolve_principal.side_effect = NotAuthenticatedError()
        expected = NotAuthenticatedError
    else:
        db.access.ensure_project_access.side_effect = ResourceNotAvailableError(
            resource="project", resource_id=1
        )
        expected = ResourceNotAvailableError
    with pytest.raises(expected):
        await suggest(service, task_id=12)
    db.projects.get_by_id.assert_not_awaited()
    llm.get_structured_response.assert_not_awaited()


async def test_foreign_task_and_document_are_not_used(tmp_path):
    service, db, llm, _ = setup_service(tmp_path)
    db.tasks.get_by_id.return_value.project_id = 2
    with pytest.raises(TaskNotFoundError):
        await suggest(service, task_id=12)
    db.documents.get_by_ids.return_value = [SimpleNamespace(id=9, project_id=2)]
    with pytest.raises(TaskContextDocumentError):
        await suggest(service, document_ids=[9])
    llm.get_structured_response.assert_not_awaited()


@pytest.mark.parametrize(
    "items", [[], ["Один"], ["Один"] * 3, ["Пункт" + str(i) for i in range(6)], [" ", "Два", "Три"]]
)
async def test_invalid_model_draft_is_rejected(tmp_path, items):
    service, _, llm, _ = setup_service(tmp_path)
    llm.get_structured_response.return_value = {"items": items}
    with pytest.raises(TaskChecklistGenerationError):
        await suggest(service)


async def test_provider_failure_and_unreadable_file_limits_are_explicit(tmp_path):
    service, db, llm, _ = setup_service(tmp_path)
    db.attachments.get_for_task.return_value = [
        SimpleNamespace(original_name="Missing.pdf", storage_key="missing.pdf")
    ]
    result = await suggest(service, task_id=12)
    assert "Missing.pdf" in result.warnings[0]
    assert "source_warnings" in json.loads(llm.get_structured_response.await_args.kwargs["content"])
    llm.get_structured_response.side_effect = ClientError("private provider details")
    with pytest.raises(AiProviderError):
        await suggest(service)


async def test_large_context_is_bounded_and_proposal_does_not_reuse_completion(tmp_path):
    service, db, llm, _ = setup_service(tmp_path)
    db.documents.get_by_ids.return_value = [
        SimpleNamespace(id=i, project_id=1, title=f"Документ {i}", content_md="Текст" * 10000)
        for i in range(1, 21)
    ]
    value = checklist()
    for item in value["items"]:
        item["is_completed"] = True
    result = await suggest(
        service,
        task_id=12,
        description_md="Контекст" * 6000,
        document_ids=list(range(1, 21)),
        checklist=value,
    )
    assert len(llm.get_structured_response.await_args.kwargs["content"]) <= MAX_CONTEXT_CHARS
    assert result.warnings and not any(item.is_completed for item in result.checklist.items)
