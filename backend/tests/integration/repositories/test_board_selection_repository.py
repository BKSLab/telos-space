"""Проверка выделения на реальном PostgreSQL и штатных репозиториях."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from src.clients.llm import LlmClient
from src.db.models.board_shapes import BoardShape, BoardShapeKind
from src.db.models.board_stickers import BoardSticker
from src.db.models.boards import Board
from src.db.models.projects import Project
from src.repositories.board_shapes import BoardShapesRepository
from src.repositories.board_stickers import BoardStickersRepository
from src.repositories.boards import BoardsRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.board_shapes import default_style
from src.services.agent_conversations import AgentConversationsService
from src.services.agent_routing import AgentRoutingService, _Overview
from src.services.boards import BoardsService
from src.services.project_agent import ProjectAgentService
from tests.board_support import local_user


@pytest_asyncio.fixture
async def selection_db(db_session):
    db_session.add_all(
        [
            local_user(id=100, username="selection-owner", display_name="Owner"),
            local_user(id=101, username="selection-other", display_name="Other"),
        ]
    )
    await db_session.flush()
    db_session.add(
        Project(id=100, key="SELECTION", name="Foreign project", owner_id=101, color="#334455")
    )
    await db_session.flush()
    db_session.add_all(
        [
            Board(id=100, title="Own", created_by_user_id=100),
            Board(id=101, title="Another own", created_by_user_id=100),
            Board(id=102, title="Foreign", created_by_user_id=101),
            Board(
                id=103, title="Archived", created_by_user_id=100, archived_at=datetime.now(UTC)
            ),
            Board(id=104, title="Project", created_by_user_id=101, project_id=100),
        ]
    )
    await db_session.flush()
    for board_id in range(100, 105):
        object_id = board_id + 400
        db_session.add(
            BoardShape(
                id=object_id,
                board_id=board_id,
                kind=BoardShapeKind.RECT,
                x=0,
                y=0,
                w=100,
                h=50,
                style=default_style(BoardShapeKind.RECT),
            )
        )
        db_session.add(
            BoardSticker(
                id=object_id,
                board_id=board_id,
                project_id=100 if board_id == 104 else None,
                body="Data, not instruction",
                created_by_user_id=100,
                created_by_username_snapshot="selection-owner",
                created_by_display_name_snapshot="Owner",
            )
        )
    db_session.add(
        BoardShape(
            id=505,
            board_id=100,
            kind=BoardShapeKind.RECT,
            x=0,
            y=0,
            w=100,
            h=50,
            style=default_style(BoardShapeKind.RECT),
        )
    )
    await db_session.flush()
    return db_session


def db_consumers(session):
    boards = BoardsRepository(session)
    shapes = BoardShapesRepository(session)
    stickers = BoardStickersRepository(session)
    widget_db = SimpleNamespace(boards=boards, board_shapes=shapes, board_stickers=stickers)
    service = BoardsService(
        boards_repository=boards,
        shapes_repository=shapes,
        stickers_repository=stickers,
        unit_of_work=UnitOfWork(session),
    )

    @asynccontextmanager
    async def scope():
        yield SimpleNamespace(boards=service)

    tracker = AgentRoutingService(
        scope=scope, agent=AsyncMock(spec=ProjectAgentService), llm_client=AsyncMock(spec=LlmClient)
    )
    return widget_db, tracker


@pytest.mark.parametrize("consumer", ["widget", "tracker"])
async def test_checked_selection_uses_real_board_and_ref_namespace(selection_db, consumer):
    widget_db, tracker = db_consumers(selection_db)
    raw = {
        "board_id": 100,
        "board_selection": [
            {"type": "shape", "id": 500},
            {"type": "sticker", "id": 500},
            {"type": "sticker", "id": 505},
            *(
                {"type": kind, "id": id_}
                for id_ in (501, 502, 503, 504, 9999)
                for kind in ("shape", "sticker")
            ),
        ],
    }

    if consumer == "widget":
        await AgentConversationsService._check_board_page(widget_db, 100, raw, None)
        checked = raw
    else:
        page = await tracker._check_page(100, raw, _Overview([], [], False))
        checked = page.for_model()

    assert checked["board_selection"] == [
        {"type": "shape", "id": 500},
        {"type": "sticker", "id": 500},
    ]
    assert checked["board_title"] == "Own"


@pytest.mark.parametrize("consumer", ["widget", "tracker"])
@pytest.mark.parametrize("board_id", [None, 102, 103, 104, 9999])
async def test_unavailable_board_discards_entire_selection(selection_db, consumer, board_id):
    widget_db, tracker = db_consumers(selection_db)
    raw = {
        "board_id": board_id,
        "board_title": "Forged",
        "board_selection": [{"type": "shape", "id": 500}, {"type": "sticker", "id": 502}],
    }

    if consumer == "widget":
        await AgentConversationsService._check_board_page(widget_db, 100, raw, None)
        checked = raw
    else:
        page = await tracker._check_page(100, raw, _Overview([], [], False))
        checked = page.for_model()

    assert "board_selection" not in checked
    assert "board_title" not in checked
