"""Отбор использует исходные материалы и не объявляет частичный поиск полным."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.exceptions.context_selection import (
    ContextSelectionArgumentsError,
    ContextSelectionChangedError,
)
from src.schemas.context_selection import ContextSelectionInput
from src.schemas.git_sources import GitChangeProof, GitEvidence, GitTaskLink, GitTaskLinks
from src.schemas.project_knowledge import (
    ProjectKnowledgeMapSchema,
    ProjectKnowledgeObservationSchema,
)
from src.services.context_selection import ContextSelectionService


def selection(*, generation=4, text=""):
    observation = ProjectKnowledgeObservationSchema(project_id=1, generation=generation, sources=[])
    knowledge = SimpleNamespace(
        capture_observation=AsyncMock(return_value=observation),
        read_map=AsyncMock(
            return_value=ProjectKnowledgeMapSchema(
                project_id=1,
                generation=generation,
                built_generation=3,
                status="pending",
                observation=observation,
                total=0,
                offset=0,
                limit=5,
                has_more=False,
            )
        ),
    )
    query = SimpleNamespace(
        get_project_overview=AsyncMock(
            return_value=SimpleNamespace(
                summary=SimpleNamespace(project_key="P", name="Проект"),
                description_sections={"goal": text},
                description="",
                context={
                    "revision": 1,
                    "fields": {"goal": text[:160]},
                    "unknown_fields": [],
                    "truncated_fields": [],
                },
            )
        )
    )
    search = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                {
                    "source_id": "document:9",
                    "text": "Кандидат",
                    "matched_locations": [{"offset": 9000}],
                    "next_offset": 9100,
                }
            ]
        )
    )
    return (
        ContextSelectionService(
            knowledge=knowledge,
            query=query,
            search=search,
            git=SimpleNamespace(get_task_links=AsyncMock()),
        ),
        knowledge,
        query,
        search,
    )


async def test_empty_passport_does_not_invent_goal_or_block_clear_request_and_reads_targeted_tail():
    service, knowledge, query, search = selection()
    request = ContextSelectionInput(
        question="Сохранять введённый текст",
        provided={"desired_change": "Сохранять введённый текст"},
        aspects=["goal", "desired_change"],
    )
    value = await service.select(
        project_id=1,
        principal=object(),
        request=request,
        request_text="Сохранять введённый текст после повторного открытия",
    )
    assert value["provided"][0]["provenance"] == "current_request"
    assert not any(
        item.get("aspect") == "desired_change" and item["status"] == "missing"
        for item in value["gaps"]
    )
    assert {item["status"] for item in value["gaps"]} >= {
        "missing",
        "processing",
        "stale",
        "partial_search",
    }
    assert value["sufficiency"] == "question_specific" and value["writes_performed"] is False
    assert value["search_scope"]["git_searched"] is False
    assert value["search_scope"]["git_next_read"]["tool"] == "telos_list_git_connections"
    assert value["next_reads"] == [
        {"tool": "telos_read_source", "arguments": {"source_id": "document:9", "offset": 9000}}
    ]
    assert knowledge.capture_observation.await_args.kwargs["source_ids"] == [
        "document:9",
        "project:1",
    ]
    assert query.get_project_overview.await_args.kwargs == {"project_id": 1}
    assert search.search.await_args.kwargs["limit"] == 5
    assert len(value.observations) == 2 and "observation" not in value["map"]


async def test_model_quote_is_not_human_fact_and_source_change_during_selection_is_not_passed():
    service, knowledge, query, search = selection(text="Исходная цель")
    with pytest.raises(ContextSelectionArgumentsError):
        await service.select(
            project_id=1,
            principal=object(),
            request=ContextSelectionInput(
                question="Прочитать паспорт", provided={"goal": "Выдуманная цель"}
            ),
            request_text="Прочитать паспорт",
        )
    query.get_project_overview.assert_not_awaited()
    search.search.assert_not_awaited()
    knowledge.capture_observation.side_effect = [
        ProjectKnowledgeObservationSchema(project_id=1, generation=4, sources=[]),
        ProjectKnowledgeObservationSchema(project_id=1, generation=5, sources=[]),
    ]
    with pytest.raises(ContextSelectionChangedError):
        await service.select(
            project_id=1,
            principal=object(),
            request=ContextSelectionInput(question="Прочитать паспорт"),
        )


async def test_task_relevant_completion_keeps_two_prs_exact_ci_sha_and_production_rollback_separate():
    service, _, _, search = selection()
    search.search.return_value = [
        {"source_id": "task:7", "text": "Участник сообщил о завершении", "task_id": 7}
    ]
    now = datetime.now(UTC)
    failed_ci = GitEvidence(
        kind="check",
        provider_type="check_run",
        provider_id="ci-5",
        connection_id=2,
        repository_id=101,
        commit_sha="c" * 40,
        status="completed",
        conclusion="failure",
        source_url="https://example.org/checks/5",
        observed_at=now,
    )
    rollback = GitEvidence(
        kind="deployment",
        provider_type="deployment",
        provider_id="deploy-4",
        connection_id=2,
        repository_id=101,
        commit_sha="a" * 40,
        status="success",
        environment="production",
        is_current=True,
        source_url="https://example.org/deployments/4",
        observed_at=now,
    )
    staged = rollback.model_copy(
        update={"provider_id": "deploy-6", "commit_sha": "d" * 40, "environment": "staging"}
    )
    links = GitTaskLinks(
        project_id=1,
        task_id=7,
        items=[
            GitTaskLink(
                id=3,
                task_id=7,
                change_id=12,
                connection_id=2,
                repository_id=101,
                kind="pull_request",
                title="Первый PR",
                source_url="https://example.org/pr/12",
                commit_sha="b" * 40,
                merge_sha="c" * 40,
                pull_number=12,
                status="merged",
                revision=2,
                checked_at=now,
                evidence=[failed_ci, rollback],
                evidence_partial=False,
            ),
            GitTaskLink(
                id=4,
                task_id=7,
                change_id=13,
                connection_id=2,
                repository_id=101,
                kind="pull_request",
                title="Второй PR",
                source_url="https://example.org/pr/13",
                commit_sha="d" * 40,
                pull_number=13,
                status="open",
                revision=1,
                checked_at=now,
                evidence=[staged],
                evidence_partial=False,
            ),
        ],
        sources=[
            GitChangeProof(
                project_id=1,
                task_id=7,
                link_id=3,
                connection_id=2,
                change_id=12,
                repository_id=101,
                generation=4,
                credential_revision=2,
                revision=2,
                commit_sha="c" * 40,
                evidence_hash="proof-late-ci-rollback",
            ),
            GitChangeProof(
                project_id=1,
                task_id=7,
                link_id=4,
                connection_id=2,
                change_id=13,
                repository_id=101,
                generation=4,
                credential_revision=2,
                revision=1,
                commit_sha="d" * 40,
                evidence_hash="proof-staging",
            ),
        ],
    )
    service.git.get_task_links.return_value = links
    result = await service.select(
        project_id=1,
        principal=object(),
        request=ContextSelectionInput(question="Можно ли считать результат задачи опубликованным?"),
    )
    first, second = result["completion_evidence"][0]["items"]
    assert first["status"] == "merged" and first["evidence"][0]["conclusion"] == "failure"
    assert (
        first["merge_sha"] != first["evidence"][1]["commit_sha"]
        and first["evidence"][1]["environment"] == "production"
    )
    assert second["status"] == "open" and second["evidence"][0]["environment"] == "staging"
    assert (
        result.git_proofs == tuple(links.sources)
        and result.external_sources[0].kind == "git_change"
    )
    assert (
        result.external_sources[0].commit_sha == "c" * 40
        and result.external_sources[1].commit_sha == "d" * 40
    )
    assert "production" in result["completion_rule"] and "source_ids" not in result
    assert service.git.get_task_links.await_args.kwargs["task_id"] == 7
