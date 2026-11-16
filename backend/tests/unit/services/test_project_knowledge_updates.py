"""Границы DB/CPU, общий deadline и server-side scopes карты."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.exceptions.project_knowledge import ProjectKnowledgeAccessError
from src.repositories.project_knowledge import ProjectKnowledgeRepository
from src.services.auth import ApiKeyGrant
from src.services.project_knowledge import ProjectKnowledgeService
from src.services.project_knowledge_updates import ProjectKnowledgeUpdatesService
from tests.unit.knowledge.test_project_map import rows


async def test_cpu_map_work_has_no_open_db_scope():
    active = False
    knowledge = AsyncMock(spec=ProjectKnowledgeRepository)
    knowledge.ensure_state.return_value = {"generation": 0, "gaps": []}
    knowledge.get_state.return_value = {"generation": 0}
    knowledge.get_sources.return_value = {}
    knowledge.get_claim_heads.return_value = {}
    knowledge.publish.return_value = True
    sources = SimpleNamespace(get_project_rows=AsyncMock(return_value=rows()))
    unit_of_work = SimpleNamespace(commit=AsyncMock())

    @asynccontextmanager
    async def scope():
        nonlocal active
        assert not active
        active = True
        try:
            yield SimpleNamespace(knowledge=knowledge, sources=sources, unit_of_work=unit_of_work)
        finally:
            active = False

    service = ProjectKnowledgeUpdatesService(scope=scope)
    original = service._plan

    def plan(snapshot, *, started_at):
        assert not active
        return original(snapshot, started_at=started_at)

    with patch.object(service, "_plan", side_effect=plan):
        await service.process_project(1)
    assert not active and unit_of_work.commit.await_count == 2
    assert knowledge.append_evidence.await_args.args[0]


async def test_deadline_cancels_blocked_snapshot_without_wallclock_assertions(monkeypatch):
    entered = asyncio.Event()
    blocker = asyncio.Event()
    cancelled = asyncio.Event()

    async def snapshot(_project_id):
        entered.set()
        try:
            await blocker.wait()
        finally:
            cancelled.set()

    @asynccontextmanager
    async def unused_scope():
        raise AssertionError("No DB state before snapshot completion")
        yield

    monkeypatch.setattr("src.services.project_knowledge_updates.MAP_DEADLINE_SECONDS", 0.01)
    service = ProjectKnowledgeUpdatesService(scope=unused_scope)
    with patch.object(service, "_snapshot", side_effect=snapshot):
        task = asyncio.create_task(service.process_project(1))
        await asyncio.wait_for(entered.wait(), timeout=1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, timeout=1)
    assert cancelled.is_set() and not blocker.is_set()


async def test_read_requires_both_server_scopes_even_when_agent_ask_is_granted(current_principal):
    knowledge = AsyncMock(spec=ProjectKnowledgeRepository)
    for scopes in ({"agent:ask", "knowledge:read"}, {"agent:ask", "projects:read"}):
        restricted = replace(
            current_principal,
            via_api_token=True,
            api_key=ApiKeyGrant(
                key_id=uuid4(),
                label="test",
                key_prefix="test",
                scopes=frozenset(scopes),
                owner_user_id=current_principal.user_id,
            ),
        )

        @asynccontextmanager
        async def scope(restricted=restricted):
            yield SimpleNamespace(
                knowledge=knowledge,
                auth=SimpleNamespace(refresh_principal=AsyncMock(return_value=restricted)),
                access=AsyncMock(),
            )

        service = ProjectKnowledgeService(scope=scope)
        with pytest.raises(ProjectKnowledgeAccessError) as error:
            await service.get_status(project_id=1, principal=restricted)
        assert error.value.status_code == 403
    knowledge.get_state.assert_not_awaited()
