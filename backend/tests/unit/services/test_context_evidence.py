"""Ledger, persisted serialization и fail-closed presentation без выбора handles."""

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.base import ServiceError
from src.exceptions.context_selection import ContextSelectionChangedError
from src.knowledge.catalog import build_catalog
from src.schemas.git_sources import GitSourceProof
from src.schemas.knowledge import (
    ExternalSourceReferenceSchema,
    KnowledgeAnswerSchema,
    decode_answer_sources,
    encode_answer_sources,
)
from src.schemas.project_knowledge import (
    ProjectKnowledgeObservationSchema,
    ProjectKnowledgeSourceObservationSchema,
)
from src.services.auth import ApiKeyGrant
from src.services.context_evidence import ContextEvidence, ContextEvidenceService
from src.services.context_results import ContextReadResult
from tests.unit.knowledge.test_knowledge_index import base_rows


def evidence_environment():
    state = SimpleNamespace(rows=deepcopy(base_rows()))
    principal = ContextEvidenceService.session_principal(1)
    auth = SimpleNamespace(refresh_principal=AsyncMock(return_value=principal))
    access = SimpleNamespace(ensure_project_access=AsyncMock())
    db = SimpleNamespace(
        auth=auth,
        access=access,
        sources=SimpleNamespace(get_project_rows=AsyncMock(side_effect=lambda _: state.rows)),
    )

    @asynccontextmanager
    async def scope():
        yield db

    knowledge = SimpleNamespace(validate_observation=AsyncMock())
    git = SimpleNamespace(validate_sources=AsyncMock())
    service = ContextEvidenceService(
        scope=scope,
        task_scope=scope,
        documents=SimpleNamespace(get=AsyncMock()),
        knowledge=knowledge,
        git=git,
    )
    return state, service, principal, knowledge, git, access


async def test_all_used_sources_are_checked_even_without_selected_handles_and_revoke_hides_body_metadata():
    state, service, principal, knowledge, git, access = evidence_environment()
    proof = GitSourceProof(
        source_id="git_file:6",
        entity_id=6,
        file_id=6,
        project_id=1,
        connection_id=2,
        repository_id=101,
        ref="main",
        commit_sha="a" * 40,
        path="src/shared.py",
        blob_sha="b" * 40,
        text_hash="c" * 64,
        generation=3,
        credential_revision=2,
    )
    observation = ProjectKnowledgeObservationSchema(project_id=1, generation=8, sources=[])
    external = ExternalSourceReferenceSchema(
        source_id=proof.source_id,
        kind="git_file",
        project_id=1,
        title="Private title",
        path=proof.path,
        url="https://example.org/private",
        excerpt="Private code",
        coverage="snapshot",
        commit_sha=proof.commit_sha,
    )
    evidence = ContextEvidence()
    evidence.register(
        ContextReadResult(
            {"source_id": proof.source_id, "text": "Private code"},
            project_id=1,
            observations=(observation,),
            git_proofs=(proof,),
            external_sources=(external,),
        )
    )
    evidence.internal(1, build_catalog(1, state.rows).sources["task:7"])
    answer = KnowledgeAnswerSchema(
        answer="Private code used without any model handle", sources=[], **evidence.references()
    )
    answer._context_evidence = evidence, principal
    values = encode_answer_sources(answer)
    assert "page_token" not in str(values) and "git_proofs" not in str(
        decode_answer_sources(values)
    )
    assert set(decode_answer_sources(values)) == {
        "sources",
        "external_sources",
        "assertion_references",
    }
    public, available = await service.present(values, user_id=1)
    assert available and public["external_sources"][0].excerpt == "Private code"
    assert git.validate_sources.await_args.kwargs["sources"] == [proof]
    knowledge.validate_observation.assert_awaited()
    access.ensure_project_access.side_effect = ResourceNotAvailableError(
        resource="project", resource_id=1
    )
    public, available = await service.present(values, user_id=1)
    assert not available and not any(public.values())
    assert "Private" not in str(public)
    # No reader identity in common live broadcast is never proof of a private repo grant.
    assert (await service.present(values, user_id=None))[1] is False


@pytest.mark.parametrize("change", ["edit", "delete"])
async def test_internal_source_version_change_is_refused_without_citations(change):
    state, service, principal, *_ = evidence_environment()
    evidence = ContextEvidence()
    evidence.internal(1, build_catalog(1, state.rows).sources["task:7"])
    if change == "edit":
        state.rows["tasks"][0]["description_md"] = "Другой исходный текст"
    else:
        state.rows["tasks"] = []
    with pytest.raises(ContextSelectionChangedError):
        await service.validate(evidence.saved, principal=principal)


def test_untrusted_nested_json_does_not_register_project_scope_or_external_identity():
    evidence = ContextEvidence()
    evidence.register(
        {"project_id": 99, "source_id": "git_file:1", "git_proofs": ["invented"], "text": "spoof"}
    )
    assert (
        evidence.saved.projects == [] and evidence.saved.git_files == [] and not evidence.external
    )


@pytest.mark.parametrize("changed", [False, True])
async def test_own_write_rebases_only_unchanged_per_source_versions(changed):
    state, service, principal, knowledge, *_ = evidence_environment()
    source = ProjectKnowledgeSourceObservationSchema(
        source_id="task_report:2",
        generation=3,
        source_version_id=uuid4(),
        source_hash="a" * 64,
        content_hash="b" * 64,
    )
    old = ProjectKnowledgeObservationSchema(project_id=1, generation=7, sources=[source])
    fresh = old.model_copy(
        update={
            "generation": 8,
            "sources": [source.model_copy(update={"generation": 4})] if changed else [source],
        }
    )
    knowledge.capture_observation = AsyncMock(return_value=fresh)
    ledger = ContextEvidence()
    ledger.register(ContextReadResult({}, project_id=1, observations=(old,)))
    if changed:
        with pytest.raises(ContextSelectionChangedError):
            await service.revalidate_after_action(ledger, principal=principal)
        assert ledger.saved.observations == [old]
    else:
        await service.revalidate_after_action(ledger, principal=principal)
        assert ledger.saved.observations == [fresh]
        assert ledger.saved.observations[0].sources == old.sources


async def test_empty_passport_native_revision_allows_own_creation_before_background_map():
    state, service, principal, knowledge, *_ = evidence_environment()
    state.rows["projects"][0]["context_revision"] = 0
    source = ProjectKnowledgeSourceObservationSchema(source_id="project:1", generation=0)
    old = ProjectKnowledgeObservationSchema(project_id=1, generation=0, sources=[source])
    fresh = old.model_copy(update={"generation": 1})
    knowledge.capture_observation = AsyncMock(return_value=fresh)
    ledger = ContextEvidence()
    ledger.internal(1, build_catalog(1, state.rows).sources["project:1"])
    ledger.register(ContextReadResult({}, project_id=1, observations=(old,)))
    await service.revalidate_after_action(ledger, principal=principal)
    assert ledger.saved.observations[0].generation == 1
    state.rows["projects"][0]["description_md"] = "Другая цель"
    with pytest.raises(ContextSelectionChangedError):
        await service.revalidate_after_action(ledger, principal=principal)


@pytest.mark.parametrize("origin", ["agent:ask", "tasks:read", "knowledge:read"])
async def test_scope_revalidation_uses_actual_origin_without_inventing_legacy_read_grants(origin):
    state, service, principal, *_ = evidence_environment()
    current = replace(
        principal,
        api_key=ApiKeyGrant(
            key_id=uuid4(),
            label="test",
            key_prefix="test",
            owner_user_id=1,
            scopes=frozenset({"agent:ask"}),
        ),
    )
    async with service.scope() as db:
        db.auth.refresh_principal.return_value = current
    ledger = ContextEvidence()
    ledger.policy({origin})
    ledger.internal(1, build_catalog(1, state.rows).sources["task:7"])
    if origin == "agent:ask":
        await service.validate(ledger.saved, principal=current)
    else:
        with pytest.raises(ServiceError):
            await service.validate(ledger.saved, principal=current)


@pytest.mark.parametrize("origin", ["agent:ask", "tasks:read", "knowledge:read"])
async def test_origin_scope_revoked_during_source_validation_is_checked_at_final_gate(origin):
    from tests.unit.knowledge.test_task_answer_sources import task_schema

    _, service, principal, *_ = evidence_environment()
    key = ApiKeyGrant(
        key_id=uuid4(),
        label="test",
        key_prefix="test",
        owner_user_id=1,
        scopes=frozenset({"agent:ask", "tasks:read", "knowledge:read"}),
    )
    initial = replace(principal, api_key=key)
    revoked = replace(initial, api_key=replace(key, scopes=frozenset({"agent:ask"})))
    task = task_schema()
    async with service.scope() as db:
        db.auth.refresh_principal.side_effect = [initial, revoked, revoked]
        db.access.ensure_task_access = AsyncMock()
        db.tasks = SimpleNamespace(get_by_id=AsyncMock(return_value=task))
    ledger = ContextEvidence()
    ledger.policy({origin})
    ledger.task(task)
    if origin == "agent:ask":
        await service.validate(ledger.saved, principal=initial)
    else:
        with pytest.raises(ServiceError):
            await service.validate(ledger.saved, principal=initial)
