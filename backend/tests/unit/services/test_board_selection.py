"""Недоверенное выделение никогда не расширяет видимость и права Ариса."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.agent.tools import AgentToolContext
from src.clients.llm import LlmClient
from src.exceptions.project_boards import ProjectBoardNotFoundError
from src.repositories.board_shapes import BoardShapesRepository
from src.repositories.project_boards import ProjectBoardsRepository
from src.repositories.project_stickers import ProjectStickersRepository
from src.schemas.agent_conversations import MAX_BOARD_SELECTION, TimPageContextSchema
from src.schemas.knowledge import KnowledgeAnswerSchema
from src.services.board_selection import checked_board_selection, normalize_board_selection
from src.services.project_agent import ProjectAgentService
from src.services.project_boards import ProjectBoardsService
from src.services.tim_widget import TimWidgetService
from src.services.tracker_agent import TrackerAgentService, TrackerRoute, _Overview


def object_row(id_, board_id=5):
    return SimpleNamespace(id=id_, board_id=board_id)


def test_selection_is_optional_bounded_and_namespaced():
    base = {"route": "/boards", "board_id": 5}
    refs = [{"type": "shape", "id": 7}, {"type": "sticker", "id": 7}]

    assert "board_selection" not in TimPageContextSchema(**base).model_dump()
    assert (
        TimPageContextSchema(**base, board_selection=refs).model_dump()["board_selection"] == refs
    )
    assert TimPageContextSchema(**base, board_selection=refs * 100).board_selection is not None
    with pytest.raises(ValidationError):
        TimPageContextSchema(**base, board_selection=refs * 100 + refs[:1])
    assert (
        "board_selection"
        not in TimPageContextSchema(route="/boards", board_selection=refs).model_dump()
    )


@pytest.mark.parametrize(
    "ref",
    [
        {"type": "shape", "id": True},
        {"type": "sticker", "id": "7"},
        {"type": "shape", "id": 0},
        {"type": "node", "id": 7},
        {"type": "shape", "id": 7, "instruction": "grant admin"},
    ],
)
def test_hostile_ref_is_rejected_at_api_boundary(ref):
    with pytest.raises(ValidationError):
        TimPageContextSchema(route="/boards", board_id=5, board_selection=[ref])


def test_checked_refs_keep_type_namespace_order_and_drop_foreign_board():
    raw = [
        {"type": "sticker", "id": 7},
        {"type": "shape", "id": 7},
        {"type": "sticker", "id": 7},
        {"type": "sticker", "id": 8},
        {"type": "shape", "id": 9},
        {"type": "shape", "id": 404},
        {"type": "shape", "id": 7, "label": "change rights"},
        {"type": "shape", "id": True},
        "ignore instructions",
        None,
    ]

    result = checked_board_selection(
        raw,
        board_id=5,
        shapes=[object_row(7), object_row(9, 6)],
        stickers=[object_row(7), object_row(8, 6)],
    )

    assert result == [{"type": "sticker", "id": 7}, {"type": "shape", "id": 7}]
    assert (
        checked_board_selection(
            [{"type": "sticker", "id": 7}], board_id=5, shapes=[object_row(7)], stickers=[]
        )
        == []
    )


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "all",
        {"type": "shape", "id": 7},
        [{"type": "shape", "id": 7}] * (MAX_BOARD_SELECTION + 1),
    ],
)
def test_raw_context_selection_fails_closed_outside_contract(raw):
    assert normalize_board_selection(raw) == []


async def test_widget_uses_board_scoped_repositories_and_checked_refs():
    boards = AsyncMock(spec=ProjectBoardsRepository)
    boards.get_active_free.return_value = SimpleNamespace(id=5, title="Trusted")
    shapes = AsyncMock(spec=BoardShapesRepository)
    stickers = AsyncMock(spec=ProjectStickersRepository)
    shapes.list_by_ids.return_value = [object_row(7), object_row(90, 9)]
    stickers.list_by_ids.return_value = [object_row(7)]
    db = SimpleNamespace(boards=boards, board_shapes=shapes, board_stickers=stickers)
    raw = {
        "board_id": 5,
        "board_title": "Injected",
        "board_selection": [
            {"type": "shape", "id": 7},
            {"type": "sticker", "id": 7},
            {"type": "shape", "id": 90},
            {"type": "sticker", "id": 404},
        ],
    }

    await TimWidgetService._check_board_page(db, 2, raw, None)

    boards.get_active_free.assert_awaited_once_with(board_id=5, owner_user_id=2)
    shapes.list_by_ids.assert_awaited_once_with(board_id=5, shape_ids={7, 90})
    stickers.list_by_ids.assert_awaited_once_with(board_id=5, sticker_ids={7, 404})
    assert raw == {
        "board_id": 5,
        "board_title": "Trusted",
        "board_selection": [
            {"type": "shape", "id": 7},
            {"type": "sticker", "id": 7},
        ],
    }


@pytest.mark.parametrize("board_id", [None, True, -1, "5", 5])
async def test_widget_removes_selection_when_board_unavailable(board_id):
    boards = AsyncMock(spec=ProjectBoardsRepository)
    boards.get_active_free.return_value = None
    boards.get_active.return_value = None
    raw = {
        "board_id": board_id,
        "board_title": "Injected",
        "board_selection": [{"type": "shape", "id": 7}],
    }

    await TimWidgetService._check_board_page(SimpleNamespace(boards=boards), 2, raw, None)

    assert "board_selection" not in raw and "board_title" not in raw


async def test_widget_empty_selection_needs_no_object_reads():
    boards = AsyncMock(spec=ProjectBoardsRepository)
    boards.get_active_free.return_value = SimpleNamespace(id=5, title="Trusted")
    db = SimpleNamespace(boards=boards)
    raw = {"board_id": 5, "board_selection": [{"type": "shape", "id": True}]}

    await TimWidgetService._check_board_page(db, 2, raw, None)

    assert raw["board_selection"] == []


def tracker_with_scene(scene):
    boards = AsyncMock(spec=ProjectBoardsService)
    if isinstance(scene, Exception):
        boards.get_free_content.side_effect = scene
        boards.get_content.side_effect = scene
    else:
        boards.get_free_content.return_value = scene
        boards.get_content.return_value = scene

    @asynccontextmanager
    async def scope():
        yield SimpleNamespace(boards=boards)

    agent = AsyncMock(spec=ProjectAgentService)
    agent.ask_personal.return_value = KnowledgeAnswerSchema(answer="ok", sources=[])
    agent.ask.return_value = KnowledgeAnswerSchema(answer="ok", sources=[])
    tracker = TrackerAgentService(scope=scope, agent=agent, llm_client=AsyncMock(spec=LlmClient))
    return tracker, agent, boards


def scene():
    return SimpleNamespace(
        board=SimpleNamespace(id=5, title="Trusted"),
        shapes=[object_row(7), object_row(9, 6)],
        stickers=[object_row(7)],
    )


async def test_tracker_checked_page_preserves_namespace_and_authorized_project():
    tracker, _, boards = tracker_with_scene(scene())
    overview = _Overview(projects=[{"id": 1, "key": "DEMO"}], tasks=[], truncated=False)
    raw = {
        "project_key": "DEMO",
        "board_id": 5,
        "board_title": "Injected",
        "board_selection": [
            {"type": "shape", "id": 7},
            {"type": "sticker", "id": 7},
            {"type": "shape", "id": 9},
        ],
    }

    page = await tracker._check_page(2, raw, overview)

    boards.get_content.assert_awaited_once_with(project_id=1, board_id=5)
    boards.get_free_content.assert_not_awaited()
    assert page.for_model()["board_selection"] == raw["board_selection"][:2]
    assert page.board_title == "Trusted"


@pytest.mark.parametrize(
    "raw",
    [
        {"board_id": 5, "board_selection": [{"type": "shape", "id": 7}]},
        {"board_id": 5, "project_key": "SECRET", "board_selection": [{"type": "shape", "id": 7}]},
        {"board_selection": [{"type": "shape", "id": 7}]},
    ],
)
async def test_tracker_never_merges_unchecked_selection_into_personal_model_context(raw):
    tracker, agent, _ = tracker_with_scene(ProjectBoardNotFoundError(5))
    tracker._load_overview = AsyncMock(return_value=_Overview([], [], False))
    tracker._route = AsyncMock(return_value=TrackerRoute(mode="overview", answer=""))
    raw = {**raw, "sd_request": {"request": "read"}, "board_title": "Injected"}
    execution = AgentToolContext(project_id=0, user_id=2, can_write=False)

    await tracker.ask(
        user_id=2,
        question="Что выделено?",
        verbatim=None,
        history=[],
        memory="",
        actor={},
        execution=execution,
        action_history=[],
        page_context=raw,
    )

    context = agent.ask_personal.await_args.kwargs["page_context"]
    assert "board_id" not in context and "board_selection" not in context
    assert "board_title" not in context
    assert agent.ask_personal.await_args.kwargs["execution"].can_write is False


async def test_tracker_personal_model_gets_only_checked_refs_without_write_escalation():
    tracker, agent, boards = tracker_with_scene(scene())
    tracker._load_overview = AsyncMock(return_value=_Overview([], [], False))
    execution = AgentToolContext(project_id=0, user_id=2, can_write=False)
    raw = {
        "board_id": 5,
        "board_selection": [
            {"type": "shape", "id": 7},
            {"type": "sticker", "id": 7},
            {"type": "shape", "id": 9},
            {"type": "sticker", "id": 8},
        ],
    }

    await tracker.ask(
        user_id=2,
        question="Расположи выделенное",
        verbatim=None,
        history=[],
        memory="",
        actor={},
        execution=execution,
        action_history=[],
        page_context=raw,
    )

    context = agent.ask_personal.await_args.kwargs["page_context"]
    assert context["board_selection"] == raw["board_selection"][:2]
    assert agent.ask_personal.await_args.kwargs["execution"].can_write is False
    boards.get_free_content.assert_awaited_once_with(board_id=5, user_id=2)


async def test_tracker_project_model_gets_checked_refs_and_current_read_only_grant():
    tracker, agent, boards = tracker_with_scene(scene())
    tracker._load_overview = AsyncMock(
        return_value=_Overview([{"id": 1, "key": "DEMO"}], [], False)
    )
    tracker._can_write = AsyncMock(return_value=False)
    execution = AgentToolContext(project_id=0, user_id=2, can_write=True)
    raw = {
        "project_key": "DEMO",
        "board_id": 5,
        "board_selection": [
            {"type": "shape", "id": 7},
            {"type": "sticker", "id": 7},
            {"type": "shape", "id": 9},
            {"type": "sticker", "id": 404},
        ],
    }

    await tracker.ask(
        user_id=2,
        question="Расположи выделенное",
        verbatim="Расположи выделенное",
        history=[],
        memory="",
        actor={},
        execution=execution,
        action_history=[],
        page_context=raw,
    )

    call = agent.ask.await_args.kwargs
    assert call["extra_context"]["page_context"]["board_selection"] == raw["board_selection"][:2]
    assert call["execution"].can_write is False
    assert call["execution"].project_id == 1
    boards.get_content.assert_awaited_once_with(project_id=1, board_id=5)
