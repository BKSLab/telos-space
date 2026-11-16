"""Точные statements, неизвестная цель и bounded checkpoints без моделей."""

from src.knowledge.catalog import build_catalog
from src.knowledge.project_map import (
    MAX_BATCH_CHARS,
    MAX_FRAGMENT_CHARS,
    build_projections,
    parse_projection,
)
from src.schemas.projects import ProjectDescriptionSchema
from src.services.project_knowledge_updates import MapSnapshot, ProjectKnowledgeUpdatesService
from src.utils.project_description import compose_project_description


def rows(*, structured=True):
    sections = ProjectDescriptionSchema(
        goal="Помочь координатору", constraints="Приватность: локально"
    )
    return {
        "projects": [
            {
                "id": 1,
                "key": "MAP",
                "name": "Проект",
                "context_revision": 1,
                "description_sections": sections.model_dump() if structured else None,
                "description_md": compose_project_description(sections)
                if structured
                else "## Цель\n\nСтарый произвольный текст",
            }
        ],
        "documents": [
            {
                "id": 2,
                "project_id": 1,
                "slug": "api",
                "title": "API",
                "version": 1,
                "content_md": "## Возможности\nДоступ: локальный\n\nПроверка 🙂 выполнена",
            }
        ],
    }


def test_exact_evidence_never_infers_owner_goal_or_accepts_document_requirements():
    catalog = build_catalog(1, rows())
    projections = build_projections(catalog)
    passport = parse_projection(projections["project:1"], offset=0, max_chars=32_000)
    assert any(
        d.kind == "requirement" and d.category == "goal" and d.acceptance == "accepted"
        for d in passport.drafts
    )
    assert any(
        g["category"] == "audience" and g["code"] == "passport_field_unknown" for g in passport.gaps
    )
    document = parse_projection(projections["document:2"], offset=0, max_chars=32_000)
    assert any(d.subject == "доступ" for d in document.drafts)
    for draft in passport.drafts + document.drafts:
        for evidence in draft.evidence:
            if evidence.quote:
                assert (
                    projections[evidence.source_id].text[evidence.start : evidence.end]
                    == evidence.quote
                )
    assert all(d.acceptance == "unreviewed" and d.kind == "source_fact" for d in document.drafts)
    assert all(not d.provenance["independently_verified"] for d in document.drafts)
    legacy = build_projections(build_catalog(1, rows(structured=False)))["project:1"]
    result = parse_projection(legacy, offset=0, max_chars=32_000)
    assert all(d.category == "additional" and d.kind == "source_fact" for d in result.drafts)
    assert any(g["category"] == "goal" for g in result.gaps)


def test_batch_and_source_limits_checkpoint_and_unchanged_reuse():
    import time

    data = rows()
    for identifier in range(3, 9):
        data["documents"].append(
            {
                "id": identifier,
                "project_id": 1,
                "slug": str(identifier),
                "title": "Длинный документ",
                "version": 1,
                "content_md": ("Явный факт\n" * 5000),
            }
        )
    snapshot = MapSnapshot(1, 0, data, {}, [])
    plan = ProjectKnowledgeUpdatesService._plan(snapshot, started_at=time.monotonic())
    assert plan.metrics["chars_processed"] <= MAX_BATCH_CHARS
    assert all(p.end - p.offset <= MAX_FRAGMENT_CHARS for p in plan.parsed)
    assert plan.continuation
    partial = next(p for p in plan.parsed if p.source_id == "document:3")
    assert not partial.complete and 0 < partial.end <= MAX_FRAGMENT_CHARS
    assert len(partial.drafts) <= 256
    heads = {}
    for item in plan.parsed:
        version = plan.versions[item.source_id]
        heads[item.source_id] = {
            "generation": 0,
            "processed_generation": 0,
            "projection_hash": version["identity_hash"],
            "processed_chars": item.end,
            "status": "ready" if item.complete else "partial",
        }
    next_plan = ProjectKnowledgeUpdatesService._plan(
        MapSnapshot(1, 0, data, heads, plan.gaps), started_at=time.monotonic()
    )
    assert next_plan.metrics["sources_reused"] >= 2
    assert (
        next(item for item in next_plan.parsed if item.source_id == "document:3").offset
        == partial.end
    )
    assert plan.metrics["model_requests"] == plan.metrics["http_attempts"] == 0
    assert plan.metrics["cost_usd"] is None and plan.metrics["cost_note"] == "no_model_requests"


def test_done_requires_canonical_stage_and_report_is_only_participant_statement():
    data = rows()
    data["project_stages"] = [
        {"id": 3, "project_id": 1, "name": "Done but active", "is_done_stage": False},
        {"id": 4, "project_id": 1, "name": "Принято", "is_done_stage": True},
    ]
    data["tasks"] = [
        {"id": 5, "project_id": 1, "stage_id": 3, "number": 1, "title": "Задача", "status": None}
    ]
    data["task_reports"] = [
        {
            "id": 6,
            "task_id": 5,
            "revision": 1,
            "author_name": "Участник",
            "body_md": "Заявлена публикация в prod",
            "created_at": "2026-11-16T10:00:00+00:00",
        }
    ]
    projections = build_projections(build_catalog(1, data))
    assert not projections["task:5"].task_done
    assert not parse_projection(projections["task_report:6"], offset=0, max_chars=32_000).drafts
    data["tasks"][0]["stage_id"] = 4
    projections = build_projections(build_catalog(1, data))
    parsed = parse_projection(projections["task_report:6"], offset=0, max_chars=32_000)
    assert len(parsed.drafts) == 1
    assert parsed.drafts[0].verification == "participant_report"
    assert parsed.drafts[0].acceptance == "unreviewed"
    assert not parsed.drafts[0].provenance["independently_verified"]
    data["task_reports"] = []
    no_report = build_projections(build_catalog(1, data))["task:5"]
    parsed = parse_projection(no_report, offset=0, max_chars=32_000)
    assert parsed.drafts[0].verification == "status_only"
    assert parsed.gaps[0]["code"] == "task_report_missing"
