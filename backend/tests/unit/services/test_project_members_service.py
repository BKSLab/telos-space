from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.models.project_members import ProjectRole
from src.db.models.task_participants import TaskParticipantRole
from src.exceptions.projects import (
    ProjectMemberAlreadyExistsError,
    ProjectMemberNotFoundError,
    ProjectMemberUserNotFoundError,
    ProjectOwnerRemovalError,
)
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_risks import ProjectRiskRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.services.knowledge_events import KnowledgeEvents
from src.services.project_members import ProjectMembersService
from src.services.users import UsersService


def user(user_id: int, username: str = "member", *, active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        username=username,
        last_name="Участников",
        first_name=f"Пользователь {user_id}",
        middle_name=None,
        avatar_key=None,
        is_active=active,
    )


def member(
    user_id: int,
    role: ProjectRole = ProjectRole.MEMBER,
    membership_id: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=membership_id or user_id + 10,
        project_id=1,
        user_id=user_id,
        role=role,
        user=user(user_id, username=f"user{user_id}"),
        created_at=datetime.now(UTC),
    )


def build_service(
    *,
    members: AsyncMock | None = None,
    users: AsyncMock | None = None,
    participants: AsyncMock | None = None,
    tasks: AsyncMock | None = None,
    unit_of_work: AsyncMock | None = None,
    users_service: AsyncMock | None = None,
) -> ProjectMembersService:
    return ProjectMembersService(
        members_repository=members or AsyncMock(spec=ProjectMembersRepository),
        users_repository=users or AsyncMock(spec=UsersRepository),
        participants_repository=participants or AsyncMock(spec=TaskParticipantsRepository),
        tasks_repository=tasks or AsyncMock(spec=TasksRepository),
        unit_of_work=unit_of_work or AsyncMock(spec=UnitOfWork),
        users_service=users_service or AsyncMock(spec=UsersService),
        risks_repository=AsyncMock(spec=ProjectRiskRepository),
        knowledge_events=AsyncMock(spec=KnowledgeEvents),
    )


@pytest.mark.asyncio
async def test_member_list_places_owner_first_and_exposes_safe_identity() -> None:
    members = AsyncMock(spec=ProjectMembersRepository)
    members.get_for_project.return_value = [member(2), member(1, ProjectRole.OWNER)]

    result = await build_service(members=members).get_member_list(project_id=1)

    assert [item.role for item in result] == [ProjectRole.OWNER, ProjectRole.MEMBER]
    assert result[0].user.username == "user1"
    assert not hasattr(result[0].user, "email")


@pytest.mark.asyncio
async def test_add_member_validates_the_candidate() -> None:
    """Добавление участника: точный логин, неизвестный или отключённый пользователь, повторное добавление."""

    users = AsyncMock(spec=UsersRepository)
    added_user = user(2, username="known.login")
    users.get_by_username.return_value = added_user
    members = AsyncMock(spec=ProjectMembersRepository)
    members.get.return_value = None
    members.save.return_value = member(2, membership_id=21)
    unit_of_work = AsyncMock(spec=UnitOfWork)

    result = await build_service(
        members=members,
        users=users,
        unit_of_work=unit_of_work,
    ).add_member(project_id=1, username="known.login")

    users.get_by_username.assert_awaited_once_with(username="known.login")
    assert members.save.await_args.kwargs["data"]["role"] is ProjectRole.MEMBER
    assert result.user.username == "known.login"
    unit_of_work.commit.assert_awaited_once()

    users = AsyncMock(spec=UsersRepository)
    users.get_by_username.return_value = None

    with pytest.raises(ProjectMemberUserNotFoundError) as error:
        await build_service(users=users).add_member(project_id=1, username="unknown")

    assert error.value.detail == "Пользователь с таким логином не найден."

    users = AsyncMock(spec=UsersRepository)
    users.get_by_username.return_value = user(2)
    members = AsyncMock(spec=ProjectMembersRepository)
    members.get.return_value = member(2)

    with pytest.raises(ProjectMemberAlreadyExistsError):
        await build_service(members=members, users=users).add_member(
            project_id=1,
            username="member",
        )

    members.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_member_clears_executor_labels_and_assignments() -> None:
    members = AsyncMock(spec=ProjectMembersRepository)
    removed = member(2, membership_id=12)
    members.get.return_value = removed
    participants = AsyncMock(spec=TaskParticipantsRepository)
    participants.get_for_project_member.return_value = [
        SimpleNamespace(task_id=31, role=TaskParticipantRole.EXECUTOR),
        SimpleNamespace(task_id=31, role=TaskParticipantRole.REPORTER),
        SimpleNamespace(task_id=32, role=TaskParticipantRole.OBSERVER),
        SimpleNamespace(task_id=33, role=TaskParticipantRole.REPORTER),
    ]
    tasks = AsyncMock(spec=TasksRepository)
    unit_of_work = AsyncMock(spec=UnitOfWork)

    await build_service(
        members=members,
        participants=participants,
        tasks=tasks,
        unit_of_work=unit_of_work,
    ).remove_member(project_id=1, user_id=2)

    participants.get_for_project_member.assert_awaited_once_with(
        project_member_id=12,
    )
    tasks.clear_assignees.assert_awaited_once_with(task_ids=[31])
    tasks.bump_revisions.assert_awaited_once_with(task_ids={32, 33})
    members.delete.assert_awaited_once_with(member=removed)
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_cannot_be_removed() -> None:
    members = AsyncMock(spec=ProjectMembersRepository)
    members.get.return_value = member(1, ProjectRole.OWNER)

    with pytest.raises(ProjectOwnerRemovalError):
        await build_service(members=members).remove_member(project_id=1, user_id=1)

    members.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_avatar_is_available_only_while_membership_lasts() -> None:
    """Аватар доступен участнику проекта и перестаёт запрашиваться после исключения."""

    members = AsyncMock(spec=ProjectMembersRepository)
    members.get.return_value = member(2)
    users_service = AsyncMock(spec=UsersService)
    users_service.get_avatar.return_value = (b"image", "image/webp")

    result = await build_service(
        members=members,
        users_service=users_service,
    ).get_member_avatar(project_id=1, user_id=2)

    assert result == (b"image", "image/webp")
    users_service.get_avatar.assert_awaited_once_with(user_id=2)

    members = AsyncMock(spec=ProjectMembersRepository)
    members.get.return_value = None
    users_service = AsyncMock(spec=UsersService)

    with pytest.raises(ProjectMemberNotFoundError) as error:
        await build_service(
            members=members,
            users_service=users_service,
        ).get_member_avatar(project_id=1, user_id=2)

    assert getattr(error.value, "status_code", None) == 404
    users_service.get_avatar.assert_not_awaited()
