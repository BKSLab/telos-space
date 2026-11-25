"""Структурные чтения Ариса сохраняют scope и private guards общего сервиса."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.agent.action_catalog import PROJECT_ACTIONS
from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolAccessError
from src.schemas.agent_context import GitSearchInput, GitSnapshotsInput
from src.schemas.git_sources import GitConnectionProof, GitSnapshotPage
from src.services.agent_context_tools import AgentContextToolsService
from src.services.context_evidence import ContextEvidenceService
from src.services.git_sources import GitSourceService


async def test_structure_adapter_keeps_connection_guard_private_and_enforces_actor():
    principal = ContextEvidenceService.session_principal(1)
    proof = GitConnectionProof(
        source_id="git_connection:7", entity_id=7, connection_id=7,
        project_id=2, repository_id=101, ref="main", generation=3, credential_revision=2,
    )
    git = AsyncMock(spec=GitSourceService)
    git.list_snapshots.return_value = GitSnapshotPage(items=[], next_offset=None, sources=[proof])
    service = AgentContextToolsService(
        knowledge=None, git=git, web=None, selection=None, evidence=None,
    )
    context = AgentToolContext(project_id=2, user_id=1, principal=principal)
    result = await service.execute(
        "telos_list_git_snapshots", context, GitSnapshotsInput(connection_id=7, offset=20),
    )
    assert result == {"items": [], "next_offset": None}
    assert result.git_proofs == (proof,)
    assert result.external_sources == ()
    assert git.list_snapshots.await_args.kwargs == {
        "project_id": 2, "principal": principal, "connection_id": 7, "offset": 20, "limit": 20,
    }
    with pytest.raises(AgentToolAccessError):
        await service.execute(
            "telos_list_git_snapshots",
            AgentToolContext(project_id=2, user_id=9, principal=principal),
            GitSnapshotsInput(connection_id=7),
        )


def test_structural_tools_are_bounded_readonly_and_pinned_search_has_one_repository():
    actions = {item.name: item for item in PROJECT_ACTIONS}
    for name in (
        "telos_list_git_snapshots", "telos_list_git_symbols",
        "telos_get_git_graph", "telos_get_git_impact",
    ):
        action = actions[name]
        assert not action.mutating and not action.requires_confirmation
        assert {"knowledge:read", "projects:read"} <= action.required_scopes
    for connections in ([], [1, 2]):
        with pytest.raises(ValidationError):
            GitSearchInput(query="Auth.login", connection_ids=connections, sha="a" * 40)
    assert GitSearchInput(query="Auth.login", connection_ids=[1], sha="a" * 40).sha
