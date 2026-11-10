import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.boards import Board
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.users import User
from src.repositories.boards import BoardsRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.users import UsersRepository


@pytest_asyncio.fixture
async def user(db_session: AsyncSession) -> User:
    """Создаёт владельца, без которого проект существовать не может."""
    return await UsersRepository(db_session).save(
        data={
            "username": "owner",
            "password_hash": "hash",
            "last_name": "Владельцев",
            "first_name": "Виктор",
            "is_active": True,
        }
    )


@pytest_asyncio.fixture
async def project(db_session: AsyncSession, user: User) -> Project:
    """Создаёт проект, от которого зависят остальные сущности трекера."""
    return await ProjectsRepository(db_session).save(
        data={
            "owner_id": user.id,
            "key": "PROJ",
            "name": "Тестовый проект",
            "status": "PLANNING",
            "color": "#58a6ff",
            "order_index": 0,
        }
    )


@pytest_asyncio.fixture
async def stage(db_session: AsyncSession, project: Project) -> ProjectStage:
    """Создаёт рабочую стадию проекта."""
    return await ProjectStagesRepository(db_session).save(
        data={
            "project_id": project.id,
            "name": "В работе",
            "order_index": 1,
            "color": "#58a6ff",
            "is_done_stage": False,
        }
    )


@pytest_asyncio.fixture
async def board(db_session: AsyncSession, project: Project, user: User) -> Board:
    """Создаёт доску «Основная», без которой у стикера нет места."""
    return await BoardsRepository(db_session).insert(
        data={
            "project_id": project.id,
            "title": "Основная",
            "position": 0,
            "created_by_user_id": user.id,
        }
    )
