from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from main import app
from src.dependencies.services import get_project_members_service
from src.exceptions.projects import ProjectMemberNotFoundError
from src.exceptions.users import AvatarNotFoundError
from src.services.project_members import ProjectMembersService


@pytest.mark.asyncio
async def test_member_avatar_answers_only_for_current_member_with_avatar(api_client: AsyncClient) -> None:
    """Аватар участника отдаётся с приватным кешем; исключённый участник и участник без аватара дают 404."""

    service = AsyncMock(spec=ProjectMembersService)
    service.get_member_avatar.return_value = (b"webp", "image/webp")
    app.dependency_overrides[get_project_members_service] = lambda: service

    response = await api_client.get("/api/v1/projects/1/members/2/avatar")

    assert response.status_code == 200
    assert response.content == b"webp"
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"].startswith("private")

    service = AsyncMock(spec=ProjectMembersService)
    service.get_member_avatar.side_effect = ProjectMemberNotFoundError(user_id=2)
    app.dependency_overrides[get_project_members_service] = lambda: service

    response = await api_client.get("/api/v1/projects/1/members/2/avatar")

    assert response.status_code == 404

    service = AsyncMock(spec=ProjectMembersService)
    service.get_member_avatar.side_effect = AvatarNotFoundError(user_id=2)
    app.dependency_overrides[get_project_members_service] = lambda: service

    response = await api_client.get("/api/v1/projects/1/members/2/avatar")

    assert response.status_code == 404
