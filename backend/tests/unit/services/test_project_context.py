"""Единый паспорт: версии, происхождение, bounded context и полное дочитывание."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent.tools import AgentToolContext
from src.exceptions.projects import (
    ProjectContextConflictError,
    ProjectOwnerRequiredError,
    ProjectsRepositoryError,
    ProjectsServiceError,
    ProjectValidationError,
)
from src.knowledge.catalog import build_catalog
from src.knowledge.context import read_catalog, source_brief
from src.mcp_server.presenters import project_detail
from src.schemas.knowledge import KnowledgeReadRequest
from src.schemas.project_tool_inputs import EmptyInput, ProjectChangesInput
from src.schemas.projects import ProjectDescriptionSchema, ProjectSchema, ProjectUpdateSchema
from src.services import agent_project_actions
from src.services.project_agent import ProjectAgentService, _SourceRegistry
from src.services.project_query import ProjectOverviewDto, ProjectSummaryDto
from src.utils.project_context import PROJECT_CONTEXT_FIELD_NAMES, compact_project_context
from src.utils.project_description import compose_project_description
from tests.unit.services.test_projects_service import OWNER_ID, build_service, make_project


def context_service(*, revision=0, sections=None, description_md=None):
    service = build_service()
    project = make_project()
    project.context_revision = revision
    project.description_sections = deepcopy(sections)
    project.description_md = description_md
    service.projects_repository.get_by_id.return_value = project

    async def update(*, project, data):
        for name, value in data.items():
            setattr(project, name, value)
        return project

    service.projects_repository.update.side_effect = update
    return service, project


def test_single_passport_preserves_long_text_and_exposes_only_bounded_known_fields() -> None:
    long_goal = "Цель 👋 " * 500
    sections = ProjectDescriptionSchema(
        goal=long_goal, audience="Команда", open_questions="Неизвестно"
    )
    composed = compose_project_description(sections)
    assert long_goal.strip() in composed
    assert "## Аудитория\n\nКоманда" in composed
    assert "## Открытые вопросы\n\nНеизвестно" in composed
    project = make_project()
    project.description_sections = sections.model_dump()
    project.description_md = composed
    project.context_revision = 3
    dto = ProjectSchema.model_validate(project)
    assert dto.description_sections.goal == long_goal.strip()
    assert dto.context.revision == 3 and dto.context_revision == 3
    assert dto.context.fields["goal"] == long_goal.strip()[:160]
    assert dto.context.truncated_fields == ["goal"]
    assert "problem" in dto.context.unknown_fields and "goal" not in dto.context.unknown_fields
    assert set(dto.context.fields) == {"goal", "audience", "open_questions"}

    legacy = compact_project_context(
        sections=None, description_md="## Цель\n\nНе разбирать как поле", revision=0
    )
    assert legacy["fields"] == {"additional": "## Цель\n\nНе разбирать как поле"}
    assert "goal" in legacy["unknown_fields"]
    assert len(legacy["unknown_fields"]) == len(PROJECT_CONTEXT_FIELD_NAMES) - 1


async def test_create_records_revision_one_with_server_author_and_complete_snapshot() -> None:
    service = build_service()
    project = make_project()

    async def save(*, data):
        for name, value in data.items():
            setattr(project, name, value)
        return project

    service.projects_repository.save.side_effect = save
    service.projects_repository.get_max_order_index.return_value = 0
    text = "Большая цель " * 1000
    result = await service.create_project(
        {"key": "PROJ", "name": "Проект", "description_sections": {"goal": text}}, OWNER_ID
    )
    assert result.context_revision == 1
    saved = service.projects_repository.append_context_history.await_args.args[0]
    assert saved["revision"] == 1 and saved["is_initial"] is True
    assert saved["changed_by_user_id"] == OWNER_ID and saved["changed_by_name"] == "Тестов Иван"
    assert saved["description_sections"]["goal"] == text.strip()
    assert text.strip() in saved["description_md"]
    assert saved["changed_fields"] == ["goal"]
    service.unit_of_work.commit.assert_awaited_once()


async def test_legacy_first_change_keeps_unknown_baseline_and_adds_authoritative_revision() -> None:
    raw = "Неразмеченное описание 👋 " * 1000
    service, project = context_service(description_md=raw)
    result = await service.update_project(
        project.id,
        {
            "description_sections": {"goal": "Цель", "additional": raw},
            "expected_context_revision": 0,
        },
        OWNER_ID,
    )
    assert result.context_revision == 1
    baseline, changed = [
        call.args[0] for call in service.projects_repository.append_context_history.await_args_list
    ]
    assert baseline["revision"] == 0 and baseline["description_md"] == raw
    assert baseline["description_sections"] is None and baseline["is_initial"] is True
    assert baseline["changed_by_user_id"] is None and baseline["changed_by_name"] is None
    assert "created_at" not in baseline
    assert changed["revision"] == 1 and changed["is_initial"] is False
    assert changed["changed_by_user_id"] == OWNER_ID and changed["changed_by_name"] == "Тестов Иван"
    assert changed["description_sections"]["additional"] == raw.strip()
    snapshot = deepcopy(changed["description_sections"])
    project.description_sections["goal"] = "Следующая редакция"
    assert changed["description_sections"] == snapshot
    service.projects_repository.get_by_id.assert_awaited_once_with(
        project_id=project.id, for_update=True
    )


async def test_noop_and_metadata_do_not_create_fake_revisions_or_rewrite_legacy() -> None:
    old_four = {"problem": "", "goal": "Цель", "expected_result": "", "additional": ""}
    md = compose_project_description(ProjectDescriptionSchema(**old_four))
    service, project = context_service(revision=4, sections=old_four, description_md=md)
    result = await service.update_project(
        project.id,
        {
            "description_sections": ProjectDescriptionSchema(**old_four).model_dump(),
            "expected_context_revision": 4,
        },
        OWNER_ID,
    )
    assert result.context_revision == 4 and project.description_sections == old_four
    service.projects_repository.append_context_history.assert_not_awaited()
    service.knowledge_events.upsert.assert_not_awaited()
    assert service.projects_repository.update.await_args.kwargs["data"] == {}

    legacy, project = context_service(description_md="Старый текст")
    result = await legacy.update_project(project.id, {"name": "Другое имя"}, OWNER_ID)
    assert result.name == "Другое имя" and result.context_revision == 0
    assert result.description_md == "Старый текст" and result.description_sections is None
    legacy.projects_repository.append_context_history.assert_not_awaited()
    await legacy.update_project(project.id, {"description_md": "Старый текст"}, OWNER_ID)
    legacy.projects_repository.append_context_history.assert_not_awaited()


async def test_partial_sections_preserve_unsubmitted_bytes_and_only_explicit_empty_clears() -> None:
    original = {
        "goal": "  \n" + "Цель 👋 " * 1000 + "\n  ",
        "expected_result": "  " + "Проверяемый результат " * 1000 + "  ",
        "constraints": "\n" + "Ограничение " * 1000 + "\n",
    }
    original_md = "\n\n".join(f"## {name}\n\n{value}" for name, value in original.items())
    for request_type in (ProjectUpdateSchema, ProjectChangesInput):
        service, project = context_service(
            revision=3, sections=original, description_md=original_md
        )
        patch = request_type(
            description_sections={"audience": "Команда"}, expected_context_revision=3
        )
        data = patch.model_dump(exclude_unset=True)
        assert data["description_sections"] == {"audience": "Команда"}
        await service.update_project(project.id, data, OWNER_ID)
        assert project.context_revision == 4
        for name, value in original.items():
            assert project.description_sections[name].encode() == value.encode()
            assert value in project.description_md
        snapshot = service.projects_repository.append_context_history.await_args.args[0]
        assert snapshot["changed_fields"] == ["audience"]
        assert snapshot["description_sections"]["goal"].encode() == original["goal"].encode()

        clear = request_type(description_sections={"goal": ""}, expected_context_revision=4)
        await service.update_project(project.id, clear.model_dump(exclude_unset=True), OWNER_ID)
        assert project.context_revision == 5 and project.description_sections["goal"] == ""
        assert project.description_sections["audience"] == "Команда"
        assert project.description_sections["expected_result"] == original["expected_result"]
        assert project.description_sections["constraints"] == original["constraints"]
        assert service.projects_repository.append_context_history.await_args.args[0][
            "changed_fields"
        ] == ["goal"]


async def test_legacy_partial_sections_keep_raw_additional_without_inventing_goal() -> None:
    raw = "\n  ## Цель\nНе выводить поле из заголовка. " + "Юникод 👋 " * 1000 + "  \n"
    for request_type in (ProjectUpdateSchema, ProjectChangesInput):
        service, project = context_service(description_md=raw)
        empty = request_type(description_sections={}, expected_context_revision=0)
        await service.update_project(project.id, empty.model_dump(exclude_unset=True), OWNER_ID)
        assert project.context_revision == 0 and project.description_sections is None
        assert project.description_md == raw
        service.projects_repository.append_context_history.assert_not_awaited()

        partial = request_type(
            description_sections={"audience": "Команда"}, expected_context_revision=0
        )
        result = await service.update_project(
            project.id, partial.model_dump(exclude_unset=True), OWNER_ID
        )
        assert project.context_revision == 1
        assert project.description_sections["additional"].encode() == raw.encode()
        assert (
            project.description_sections["goal"] == "" and "goal" in result.context.unknown_fields
        )
        assert raw in project.description_md
        snapshots = service.projects_repository.append_context_history.await_args_list
        assert snapshots[0].args[0]["description_md"] == raw
        assert snapshots[1].args[0]["changed_fields"] == ["audience"]

        clear = request_type(description_sections={"additional": ""}, expected_context_revision=1)
        await service.update_project(project.id, clear.model_dump(exclude_unset=True), OWNER_ID)
        assert project.description_sections["additional"] == ""
        assert project.description_sections["audience"] == "Команда"
        assert service.projects_repository.append_context_history.await_args.args[0][
            "changed_fields"
        ] == ["additional"]


async def test_raw_markdown_edit_keeps_its_explicit_history_field() -> None:
    service, project = context_service(description_md="Прежний текст")
    await service.update_project(project.id, {"description_md": "Новый текст"}, OWNER_ID)
    assert project.description_sections is None
    assert service.projects_repository.append_context_history.await_args.args[0][
        "changed_fields"
    ] == ["description_md"]


async def test_lock_owner_revision_and_history_errors_prevent_partial_publication() -> None:
    service, project = context_service(revision=3, description_md="Текст")
    with pytest.raises(ProjectContextConflictError):
        await service.update_project(
            project.id,
            {
                "description_md": "Изменение",
                "expected_context_revision": 2,
                "due_date_comment": "Причина",
            },
            OWNER_ID,
        )
    service.projects_repository.update.assert_not_awaited()
    service.projects_repository.append_context_history.assert_not_awaited()
    service.deadline_changes_repository.save.assert_not_awaited()
    service.unit_of_work.commit.assert_not_awaited()
    service.unit_of_work.rollback.assert_awaited_once()

    service, project = context_service(revision=3)
    project.owner_id = 2
    with pytest.raises(ProjectOwnerRequiredError):
        await service.update_project(project.id, {"description_md": "Изменение"}, OWNER_ID)
    service.projects_repository.update.assert_not_awaited()
    service.unit_of_work.rollback.assert_awaited_once()

    service, project = context_service(revision=3)
    service.projects_repository.append_context_history.side_effect = ProjectsRepositoryError(
        "history unavailable"
    )
    with pytest.raises(ProjectsServiceError):
        await service.update_project(project.id, {"description_md": "Изменение"}, OWNER_ID)
    service.unit_of_work.rollback.assert_awaited_once()
    service.unit_of_work.commit.assert_not_awaited()
    service.knowledge_events.upsert.assert_not_awaited()


async def test_history_page_contains_only_metadata_and_validates_pagination() -> None:
    service, project = context_service(revision=2)
    service.projects_repository.count_context_history.return_value = 3
    service.projects_repository.get_context_history.return_value = [
        {
            "revision": 2,
            "changed_by_user_id": OWNER_ID,
            "changed_by_name": "Автор",
            "created_at": datetime.now(UTC),
            "is_initial": False,
            "changed_fields": ["audience"],
            "description_md": "Большой сохранённый текст",
            "description_sections": {"audience": "Команда"},
        }
    ]
    page = await service.get_context_history(project.id, limit=1)
    assert page.total == 3 and page.has_more and page.next_offset == 1
    dumped = page.model_dump(mode="json")
    assert (
        "description_md" not in dumped["items"][0]
        and "description_sections" not in dumped["items"][0]
    )
    for limit, offset in ((0, 0), (51, 0), (1, -1)):
        with pytest.raises(ProjectValidationError):
            await service.get_context_history(project.id, limit=limit, offset=offset)


async def test_model_tools_and_prompt_use_compact_passport_while_source_reader_keeps_full_text() -> (
    None
):
    long = "Содержательный полный текст " * 1000
    sections = ProjectDescriptionSchema(goal=long, audience="Команда")
    project = make_project()
    project.context_revision = 7
    project.description_sections = sections.model_dump()
    project.description_md = compose_project_description(sections)
    dto = ProjectSchema.model_validate(project)
    db = SimpleNamespace(projects=AsyncMock(), access=AsyncMock())
    db.projects.get_project.return_value = dto
    db.projects.update_project.return_value = dto
    db.projects.get_project_stats.return_value = SimpleNamespace(model_dump=lambda **kwargs: {})
    db.projects.get_deadline_history.return_value = []
    ctx = AgentToolContext(project_id=project.id, user_id=OWNER_ID)
    for result in (
        await agent_project_actions.get_project(db, ctx, EmptyInput()),
        await agent_project_actions.update_project(
            db,
            ctx,
            ProjectChangesInput(
                description_sections={"audience": "Команда"}, expected_context_revision=7
            ),
        ),
    ):
        assert (
            "description_md" not in result["project"]
            and "description_sections" not in result["project"]
        )
        assert result["project"]["context"]["fields"]["goal"] == long[:160]
        assert result["project"]["context_revision"] == 7
        assert result["project"]["context_read"]["arguments"]["source_id"] == "project:7"
        assert len(json.dumps(result, ensure_ascii=False)) < 5000
    prompt = ProjectAgentService._build_postgres_context(
        SimpleNamespace(), project=project, stages=[], registry=_SourceRegistry()
    )
    assert "description" not in prompt["project"]
    assert prompt["project"]["context"]["fields"]["goal"] == long[:160]

    rows = {
        "projects": [
            {
                "id": project.id,
                "key": "PROJ",
                "name": project.name,
                "description_md": project.description_md,
                "description_sections": project.description_sections,
                "context_revision": 7,
            }
        ]
    }
    catalog = build_catalog(project.id, rows)
    source = catalog.sources[f"project:{project.id}"]
    brief = source_brief(source)
    assert brief["context"]["fields"]["goal"] == long[:160]
    assert brief["context_revision"] == 7
    full_parts = []
    offset = 0
    while True:
        read = read_catalog(
            catalog,
            KnowledgeReadRequest(
                name="telos_read_source", source_id=source.source_id, offset=offset, max_chars=8000
            ),
        )
        full_parts.append(read["text"])
        if read["next_offset"] is None:
            break
        offset = read["next_offset"]
    assert "".join(full_parts) == source.text and long.strip() in source.text

    overview = ProjectOverviewDto(
        summary=ProjectSummaryDto("PROJ", project.name, "ACTIVE", None, None),
        description=project.description_md,
        total_tasks=0,
        stages=[],
        context_revision=7,
        description_sections=project.description_sections,
    )
    compact = project_detail(overview)
    assert compact["context_revision"] == 7 and compact["context"]["fields"]["goal"] == long[:160]
    assert compact["description_meta"]["truncated"]
    assert project_detail(overview, full_text=True)["description"] == project.description_md
