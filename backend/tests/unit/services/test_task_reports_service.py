from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.access import ResourceNotAvailableError, ResourceWriteRequiredError
from src.exceptions.api_tokens import InsufficientTokenScopeError
from src.exceptions.task_reports import (
    TaskReportConflictError,
    TaskReportSourceError,
)
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.task_reports import TaskReportsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.access import AccessGrant, AccessService
from src.services.auth import Principal
from src.services.task_reports import TaskReportsService


def setup_service():
    reports = AsyncMock(spec=TaskReportsRepository)
    tasks = AsyncMock(spec=TasksRepository)
    comments = AsyncMock(spec=TaskCommentsRepository)
    access = AsyncMock(spec=AccessService)
    uow = AsyncMock(spec=UnitOfWork)
    access.ensure_task_access.return_value = AccessGrant(2, 5, False)
    access.require_write.side_effect = AccessService.require_write
    tasks.get_for_update.return_value = SimpleNamespace(id=5)
    reports.latest.return_value = None

    async def append(data):
        return SimpleNamespace(**data, created_at=datetime.now(UTC))

    reports.append.side_effect = append
    return (
        TaskReportsService(reports, tasks, comments, access, uow),
        reports,
        tasks,
        comments,
        access,
        uow,
    )


def actor():
    return Principal(1, "k.test", "Тестов", "Кирилл", None, ApiTokenScope.WRITE, False)


@pytest.mark.asyncio
async def test_member_can_read_history_and_publish_without_admin_role():
    service, reports, tasks, comments, access, uow = setup_service()
    reports.history.return_value = []
    user = actor()
    assert (await service.get(5, user)).revision == 0
    assert await service.history(5, user) == []
    saved = await service.publish(5, user, 0, "Результат")
    assert saved.body_md == "Результат"
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_viewer_reads_report_but_cannot_publish_or_copy_comment():
    service, reports, tasks, comments, access, uow = setup_service()
    access.ensure_task_access.return_value = AccessGrant(2, 5, False, can_write=False)
    reports.history.return_value = []
    user = actor()
    assert (await service.get(5, user)).revision == 0
    assert await service.history(5, user) == []
    with pytest.raises(ResourceWriteRequiredError):
        await service.publish(5, user, 0, "text")
    with pytest.raises(ResourceWriteRequiredError):
        await service.publish(5, user, 0, comment_id=7, expected_comment_body_md="text")
    reports.append.assert_not_awaited()
    tasks.get_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_inaccessible_task_does_not_leak_report_or_history():
    service, reports, tasks, comments, access, uow = setup_service()
    access.ensure_task_access.side_effect = ResourceNotAvailableError(
        resource="Задача", resource_id=5
    )
    for operation in (
        service.get(5, actor()),
        service.history(5, actor()),
        service.history(5, actor(), report_id=7),
        service.history(5, actor(), revision=1),
        service.publish(5, actor(), 0, "text"),
    ):
        with pytest.raises(ResourceNotAvailableError):
            await operation
    reports.latest.assert_not_awaited()
    reports.history.assert_not_awaited()
    reports.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_publication_keeps_full_text_and_author():
    service, reports, tasks, comments, access, uow = setup_service()
    body = "Результат проверки\n" * 100
    saved = await service.publish(5, actor(), 0, body)
    assert saved.body_md == body and saved.revision == 1 and saved.author_name == "Тестов Кирилл"
    uow.commit.assert_awaited_once()
    tasks.get_for_update.assert_awaited_once_with(task_id=5)


@pytest.mark.asyncio
async def test_stale_report_does_not_overwrite():
    service, reports, tasks, comments, access, uow = setup_service()
    reports.latest.return_value = SimpleNamespace(revision=2)
    with pytest.raises(TaskReportConflictError):
        await service.publish(5, actor(), 1, "new")
    reports.append.assert_not_awaited()
    uow.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_foreign_comment_cannot_be_used_as_source():
    service, reports, tasks, comments, access, uow = setup_service()
    comments.get_for_update.return_value = SimpleNamespace(id=7, task_id=99, body_md="secret")
    with pytest.raises(TaskReportSourceError):
        await service.publish(5, actor(), 0, comment_id=7, expected_comment_body_md="secret")
    reports.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_comment_requires_refresh():
    service, reports, tasks, comments, access, uow = setup_service()
    comments.get_for_update.return_value = SimpleNamespace(id=7, task_id=5, body_md="edited")
    with pytest.raises(TaskReportConflictError):
        await service.publish(5, actor(), 0, comment_id=7, expected_comment_body_md="old")
    reports.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_comment_copy_keeps_origin_and_next_publication_has_new_revision():
    service, reports, tasks, comments, access, uow = setup_service()
    comments.get_for_update.return_value = SimpleNamespace(
        id=7, task_id=5, body_md="done", author_name="Автор"
    )
    saved = await service.publish(5, actor(), 0, comment_id=7, expected_comment_body_md="done")
    assert (
        saved.body_md == "done"
        and saved.source_comment_original_id == 7
        and saved.source_author_name == "Автор"
    )
    reports.latest.return_value = SimpleNamespace(revision=1)
    manual = await service.publish(5, actor(), 1, "уточнение")
    assert manual.revision == 2 and manual.source_comment_id is None


@pytest.mark.asyncio
async def test_read_token_reads_report_and_history_but_cannot_publish():

    service, reports, tasks, comments, access, uow = setup_service()
    token_actor = replace(actor(), via_api_token=True, scope=ApiTokenScope.READ)
    reports.history.return_value = []
    assert (await service.get(5, token_actor)).revision == 0
    assert await service.history(5, token_actor) == []
    with pytest.raises(InsufficientTokenScopeError):
        await service.publish(5, token_actor, 0, "new")
    reports.append.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_token_publishes_using_task_write_permission():

    service, reports, tasks, comments, access, uow = setup_service()
    token_actor = replace(actor(), via_api_token=True)
    result = await service.publish(5, token_actor, 0, "Результат")
    assert result.revision == 1
    access.require_write.assert_called_once()
    uow.commit.assert_awaited_once()
