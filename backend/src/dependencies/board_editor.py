"""DI редактора; вложенное создание проекта делает flush вместо общего commit."""

from typing import Annotated

from fastapi import Depends

from src.dependencies.db_session import DbSessionDep
from src.dependencies.repositories import (
    BoardsRepositoryDep,
    ProjectDeadlineChangesRepositoryDep,
    ProjectMembersRepositoryDep,
    ProjectsRepositoryDep,
    ProjectStagesRepositoryDep,
    TasksRepositoryDep,
    UnitOfWorkDep,
    UsersRepositoryDep,
)
from src.dependencies.services import AccessServiceDep, KnowledgeEventsDep, get_projects_service
from src.dependencies.storage import TaskAttachmentStorageDep
from src.repositories.board_editor import BoardEditorRepository, BoardPromotionUnitOfWork
from src.services.board_editor import BoardEditorService
from src.services.projects import ProjectsService


def get_promotion_projects_service(
    session: DbSessionDep,
    projects_repository: ProjectsRepositoryDep,
    members_repository: ProjectMembersRepositoryDep,
    stages_repository: ProjectStagesRepositoryDep,
    tasks_repository: TasksRepositoryDep,
    storage: TaskAttachmentStorageDep,
    knowledge_events: KnowledgeEventsDep,
    users_repository: UsersRepositoryDep,
    deadline_changes_repository: ProjectDeadlineChangesRepositoryDep,
    boards_repository: BoardsRepositoryDep,
) -> ProjectsService:
    return get_projects_service(
        projects_repository=projects_repository,
        members_repository=members_repository,
        stages_repository=stages_repository,
        tasks_repository=tasks_repository,
        storage=storage,
        knowledge_events=knowledge_events,
        unit_of_work=BoardPromotionUnitOfWork(session),
        users_repository=users_repository,
        deadline_changes_repository=deadline_changes_repository,
        boards_repository=boards_repository,
    )


PromotionProjectsDep = Annotated[ProjectsService, Depends(get_promotion_projects_service)]


def get_board_editor_service(
    session: DbSessionDep,
    access: AccessServiceDep,
    unit_of_work: UnitOfWorkDep,
    projects: PromotionProjectsDep,
) -> BoardEditorService:
    return BoardEditorService(
        repository=BoardEditorRepository(session),
        access=access,
        unit_of_work=unit_of_work,
        projects=projects,
    )


BoardEditorServiceDep = Annotated[BoardEditorService, Depends(get_board_editor_service)]
