import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.models.projects import Project, ProjectStatus
from src.exceptions.projects import (
    ProjectIconImageNotFoundError,
    ProjectsRepositoryError,
    ProjectsServiceError,
)
from src.services.project_icons import ProjectIconsService
from src.storage.task_attachments import TaskAttachmentStorage

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def setup_service(tmp_path):
    stamp = datetime(2026, 10, 1, tzinfo=UTC)
    project = Project(
        id=7,
        owner_id=1,
        key="DEMO",
        name="Проверка иконки",
        status=ProjectStatus.ACTIVE,
        color="#58a6ff",
        order_index=0,
        context_revision=0,
        created_at=stamp,
        updated_at=stamp,
        due_date_has_been_set=False,
        icon_image_key=None,
        icon_image_content_type=None,
    )

    async def update(*, project, data):
        for key, value in data.items():
            setattr(project, key, value)
        return project

    repository = SimpleNamespace(
        get_by_id=AsyncMock(return_value=project), update=AsyncMock(side_effect=update)
    )
    unit_of_work = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    storage = TaskAttachmentStorage(tmp_path / "uploads")
    service = ProjectIconsService(
        projects_repository=repository, unit_of_work=unit_of_work, storage=storage
    )
    return service, project, repository, unit_of_work


async def test_local_project_icon_round_trip_replacement_and_removal(tmp_path):
    service, project, _, unit_of_work = setup_service(tmp_path)
    first = await service.upload(project_id=7, content=PNG)
    previous = service.storage.resolve(project.icon_image_key)
    assert first.icon_image_version and previous.read_bytes() == PNG
    image = await service.get(project_id=7)
    assert image.content == PNG and image.content_type == "image/png"
    replaced = await service.upload(project_id=7, content=PNG)
    current = service.storage.resolve(project.icon_image_key)
    assert replaced.icon_image_version != first.icon_image_version
    assert not previous.exists() and current.read_bytes() == PNG
    cleared = await service.remove(project_id=7)
    assert cleared.icon_image_version is None and not current.exists()
    with pytest.raises(ProjectIconImageNotFoundError):
        await service.get(project_id=7)
    assert unit_of_work.commit.await_count == 3


async def test_failed_project_icon_update_compensates_new_local_file(tmp_path):
    service, project, repository, unit_of_work = setup_service(tmp_path)
    await service.upload(project_id=7, content=PNG)
    previous = service.storage.resolve(project.icon_image_key)
    repository.update.side_effect = ProjectsRepositoryError("save failed")
    with pytest.raises(ProjectsServiceError):
        await service.upload(project_id=7, content=PNG)
    assert list(previous.parent.iterdir()) == [previous]
    assert previous.read_bytes() == PNG
    unit_of_work.rollback.assert_awaited_once()
