import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.clients.llm import LlmClient
from src.db.models.analytics_reports import AnalyticsReport
from src.db.models.project_members import ProjectRole
from src.db.models.project_milestones import ProjectMilestoneStatus
from src.db.models.projects import ProjectStatus
from src.db.models.task_activity import TaskActivityEventType
from src.db.models.task_dependencies import TaskDependencyType
from src.db.models.task_participants import TaskParticipantRole
from src.db.models.tasks import TaskPriority
from src.exceptions.analytics import AnalyticsEmptyScopeError, AnalyticsServiceError
from src.exceptions.projects import ProjectNotFoundError, ProjectsRepositoryError
from src.exceptions.task_attachments import TaskAttachmentsRepositoryError
from src.prompts.analytics import (
    ANALYTICS_PORTFOLIO_SYSTEM_PROMPT,
    ANALYTICS_PROJECT_SYSTEM_PROMPT,
)
from src.repositories.project_risks import ProjectRiskRepository
from src.schemas.analytics import (
    AnalyticsDraftSchema,
    AnalyticsFindingDraftSchema,
    AnalyticsFindingKind,
    AnalyticsHealth,
    AnalyticsHorizon,
    AnalyticsRecommendationDraftSchema,
    AnalyticsScope,
    AnalyticsSeverity,
)
from src.services.analytics import MAX_CONTEXT_CHARS, AnalyticsService
from src.services.db_scope import AnalyticsDbScope
from tests.unit.services.test_project_risks_service import risk

TODAY = date.today()
USER_ID = 7
PROJECT_ID = 1


ACTOR_NAME = "Тестов Тест"


def actor() -> dict:
    """Возвращает автора анализа значениями, а не ORM-моделью."""
    return {"actor_id": USER_ID, "actor_name": ACTOR_NAME}


def project() -> SimpleNamespace:
    """Возвращает дублёр проекта области анализа."""
    return SimpleNamespace(
        id=PROJECT_ID,
        key="PROJ",
        name="Проект",
        description_md="Описание проекта.",
        status=ProjectStatus.ACTIVE,
        color="#58a6ff",
        icon=None,
        start_date=None,
        due_date=None,
    )


def stage(stage_id: int, name: str, is_done: bool) -> SimpleNamespace:
    """Возвращает дублёр стадии канбана."""
    return SimpleNamespace(
        id=stage_id,
        project_id=PROJECT_ID,
        name=name,
        order_index=stage_id,
        color="#58a6ff",
        is_done_stage=is_done,
    )


def task(
    task_id: int,
    stage_id: int,
    due_date: date | None = None,
    assignee: str | None = "Исполнитель",
    updated_days_ago: int = 0,
    wbs_node_id: int | None = None,
) -> SimpleNamespace:
    """Возвращает дублёр задачи проекта."""
    return SimpleNamespace(
        id=task_id,
        project_id=PROJECT_ID,
        number=task_id,
        title=f"Задача {task_id}",
        description_md=None,
        stage_id=stage_id,
        wbs_node_id=wbs_node_id,
        priority=TaskPriority.HIGH,
        role=None,
        assignee=assignee,
        start_date=None,
        due_date=due_date,
        baseline_start_date=None,
        baseline_due_date=None,
        completed_at=None,
        updated_at=datetime.now(UTC) - timedelta(days=updated_days_ago),
    )


def draft(task_keys: list[str]) -> AnalyticsDraftSchema:
    """Возвращает ответ модели со ссылками на указанные задачи."""
    return AnalyticsDraftSchema(
        headline="Работы идут, но сроки по интеграции сорваны.",
        health=AnalyticsHealth.RISK,
        health_note="Просрочки собрались в одном блоке.",
        findings=[
            AnalyticsFindingDraftSchema(
                kind=AnalyticsFindingKind.OVERDUE,
                severity=AnalyticsSeverity.HIGH,
                title="Интеграция просрочена",
                detail="Срок прошёл, в комментариях ждут доступы.",
                project_key="PROJ",
                task_keys=task_keys,
            )
        ],
        progress=[],
        recommendations=[
            AnalyticsRecommendationDraftSchema(
                horizon=AnalyticsHorizon.TODAY,
                title="Назначить владельца блока",
                detail="Иначе срок не за кем закрепить.",
                project_key="ДРУГОЙ",
                task_keys=[],
            )
        ],
    )


def build_service(
    *,
    projects: list | None = None,
    allowed_ids: set[int] | None = None,
    stages: list | None = None,
    tasks: list | None = None,
    dependencies: list | None = None,
    milestones: list | None = None,
    llm_response: AnalyticsDraftSchema | None = None,
    saved_report: AnalyticsReport | None = None,
) -> tuple[AnalyticsService, AsyncMock, AnalyticsDbScope]:
    """Собирает сервис аналитики с подменёнными репозиториями и моделью."""
    projects = [project()] if projects is None else projects
    allowed_ids = {item.id for item in projects} if allowed_ids is None else allowed_ids

    projects_repository = AsyncMock()
    projects_repository.get_all.return_value = projects
    projects_repository.get_by_id.return_value = projects[0] if projects else None

    members_repository = AsyncMock()
    members_repository.get_project_ids_for_user.return_value = allowed_ids
    members_repository.get_for_project.return_value = []

    stages_repository = AsyncMock()
    stages_repository.get_by_project.return_value = stages or []

    tasks_repository = AsyncMock()
    tasks_repository.get_by_project.return_value = tasks or []

    comments_repository = AsyncMock()
    comments_repository.get_for_tasks.return_value = []

    activity_repository = AsyncMock()
    activity_repository.get_recent_by_project.return_value = []
    activity_repository.get_count_by_project.return_value = 0

    dependencies_repository = AsyncMock()
    dependencies_repository.get_by_project.return_value = dependencies or []

    wbs_nodes_repository = AsyncMock()
    wbs_nodes_repository.get_by_project.return_value = []

    milestones_repository = AsyncMock()
    milestones_repository.get_by_project.return_value = milestones or []

    stickers_repository = AsyncMock()
    stickers_repository.list_by_project_id.return_value = []

    documents_repository = AsyncMock()
    documents_repository.get_by_project.return_value = []

    document_links_repository = AsyncMock()
    document_links_repository.get_for_documents.return_value = []

    report = saved_report or AnalyticsReport(
        id=1,
        project_id=PROJECT_ID,
        created_by_user_id=USER_ID,
        created_by_display_name_snapshot="Тестов Тест",
        llm_model="test-model",
        duration_ms=10,
        payload={},
        context_summary={},
        created_at=datetime.now(UTC),
    )
    reports_repository = AsyncMock()
    reports_repository.save.side_effect = lambda data: _stored(report, data)

    llm_client = AsyncMock(spec=LlmClient)
    llm_client.get_structured_response.return_value = llm_response or draft([])
    # Имя модели сервис берёт у клиента, а не у глобальных настроек.
    llm_client.model = "test-model"

    db = AnalyticsDbScope(
        risks=AsyncMock(
            spec=ProjectRiskRepository,
            get_by_project=AsyncMock(return_value=[]),
            get_aggregates=AsyncMock(return_value=[]),
        ),
        reports=reports_repository,
        projects=projects_repository,
        members=members_repository,
        stages=stages_repository,
        tasks=tasks_repository,
        attachments=AsyncMock(get_for_tasks=AsyncMock(return_value=[])),
        participants=AsyncMock(get_by_task_ids=AsyncMock(return_value={})),
        comments=comments_repository,
        activity=activity_repository,
        dependencies=dependencies_repository,
        wbs_nodes=wbs_nodes_repository,
        milestones=milestones_repository,
        stickers=stickers_repository,
        documents=documents_repository,
        document_links=document_links_repository,
        unit_of_work=AsyncMock(),
    )

    @asynccontextmanager
    async def scope():
        yield db

    service = AnalyticsService(scope=scope, llm_client=llm_client)
    return service, reports_repository, db


@pytest.mark.parametrize("portfolio", [False, True])
async def test_risk_only_project_is_analyzed_with_full_counters_and_no_invented_task_keys(
    portfolio,
):
    from tests.unit.services.test_project_risks_service import risk

    service, reports, db = build_service()
    db.risks.get_by_project.return_value = [risk(task_id=None)]
    db.risks.get_aggregates.return_value = [
        {
            "project_id": PROJECT_ID,
            "status": "OPEN",
            "probability": "HIGH",
            "impact": "HIGH",
            "risk_level": "HIGH",
            "count": 42,
            "without_owner": 42,
            "without_mitigation": 42,
            "due_for_review": 0,
            "review_overdue": 0,
            "linked": 0,
            "ai_suggested": 0,
            "latest_update": datetime.now(UTC),
        }
    ]
    await service.generate(**actor(), project_id=None if portfolio else PROJECT_ID)
    content = json.loads(service.llm_client.get_structured_response.await_args.kwargs["content"])
    entry = content["projects"][0]
    assert entry["registered_risks"][0]["key"] == "RISK-12"
    assert entry["registered_risks"][0]["task_key"] is None
    saved = reports.save.await_args.kwargs["data"]["payload"]
    assert saved["signals"]["high_risks"] == 42
    assert saved["signals"]["total_tasks"] == 0


@pytest.mark.asyncio
async def test_generate_counts_signals_from_database_not_from_model() -> None:
    stages = [stage(1, "В работе", False), stage(2, "Готово", True)]
    tasks = [
        task(11, stage_id=1, due_date=TODAY - timedelta(days=3)),
        task(12, stage_id=1, due_date=TODAY + timedelta(days=2)),
        task(13, stage_id=1, due_date=None, assignee=None, updated_days_ago=30),
        task(14, stage_id=2, due_date=TODAY - timedelta(days=9)),
    ]
    dependencies = [
        SimpleNamespace(
            predecessor_task_id=11,
            successor_task_id=12,
            dependency_type=TaskDependencyType.FINISH_TO_START,
            lag_days=2,
        )
    ]
    milestones = [
        SimpleNamespace(
            id=1,
            description_md="Условия приёмки",
            wbs_node_id=None,
            title="Веха",
            due_date=TODAY - timedelta(days=1),
            status=ProjectMilestoneStatus.PLANNED,
        )
    ]
    service, reports_repository, _db = build_service(
        stages=stages,
        tasks=tasks,
        dependencies=dependencies,
        milestones=milestones,
    )

    generated = await service.generate(**actor(), project_id=PROJECT_ID)
    assert generated.created_by_user_id == USER_ID

    signals = reports_repository.save.call_args.kwargs["data"]["payload"]["signals"]
    assert signals["total_tasks"] == 4
    assert signals["done_tasks"] == 1
    assert signals["overdue_tasks"] == 1
    assert signals["due_soon_tasks"] == 1
    assert signals["no_due_date_tasks"] == 1
    assert signals["unassigned_tasks"] == 1
    assert signals["stale_tasks"] == 1
    assert signals["blocked_tasks"] == 1
    assert signals["unplaced_tasks"] == 3
    assert signals["milestones_at_risk"] == 1


@pytest.mark.asyncio
async def test_generate_puts_overdue_tasks_first_in_model_context() -> None:
    stages = [stage(1, "В работе", False)]
    tasks = [
        task(11, stage_id=1, due_date=None),
        task(12, stage_id=1, due_date=TODAY - timedelta(days=4)),
        task(13, stage_id=1, due_date=TODAY + timedelta(days=1)),
    ]
    service, _, db = build_service(stages=stages, tasks=tasks)

    await service.generate(**actor(), project_id=PROJECT_ID)

    llm_client = service.llm_client
    content = json.loads(llm_client.get_structured_response.call_args.kwargs["content"])
    assert [item["key"] for item in content["projects"][0]["tasks"]] == [
        "PROJ-12",
        "PROJ-13",
        "PROJ-11",
    ]


@pytest.mark.asyncio
async def test_generate_reports_context_boundaries_to_user() -> None:
    """Большой портфель сообщает и модели, и пользователю границы среза."""
    stages = [stage(1, "В работе", False)]
    tasks = [task(index, stage_id=1) for index in range(1, 121)]
    for item in tasks:
        item.description_md = "Подробное описание работы. " * 100
    service, reports_repository, _db = build_service(
        projects=[project(), SimpleNamespace(**vars(project()) | {"id": 2, "key": "SECOND"})],
        stages=stages,
        tasks=tasks,
    )

    await service.generate(**actor(), project_id=None)

    context = reports_repository.save.call_args.kwargs["data"]["context_summary"]
    assert context["tasks_total"] == 240
    assert 0 < context["tasks_included"] < 240
    assert context["truncated"] is True
    assert context["omitted"]
    content = service.llm_client.get_structured_response.await_args.kwargs["content"]
    assert len(content) <= MAX_CONTEXT_CHARS
    assert json.loads(content)["context"] == context


@pytest.mark.asyncio
async def test_generate_distinguishes_empty_scope_absence_and_failure() -> None:
    """Пустой срез, чужой проект и сбой базы отвечают тремя разными ошибками."""
    empty = project()
    empty.description_md = None
    service, _, _db = build_service(
        projects=[empty], stages=[stage(1, "В работе", False)], tasks=[]
    )
    with pytest.raises(AnalyticsEmptyScopeError):
        await service.generate(**actor(), project_id=PROJECT_ID)

    service, _, _db = build_service(allowed_ids=set())
    with pytest.raises(ProjectNotFoundError):
        await service.generate(**actor(), project_id=PROJECT_ID)

    service, _, db = build_service()
    db.projects.get_by_id.side_effect = ProjectsRepositoryError("сбой")
    with pytest.raises(AnalyticsServiceError):
        await service.generate(**actor(), project_id=PROJECT_ID)


@pytest.mark.asyncio
async def test_get_latest_checks_access_and_picks_the_right_report() -> None:
    """Отсутствие отчёта — не ошибка, чужой проект — 404, портфель — по автору."""
    service, reports_repository, _db = build_service()
    reports_repository.get_latest_for_project.return_value = None
    assert await service.get_latest(user_id=USER_ID, project_id=PROJECT_ID) is None

    service, _, _db = build_service(allowed_ids=set())
    with pytest.raises(ProjectNotFoundError):
        await service.get_latest(user_id=USER_ID, project_id=PROJECT_ID)

    service, reports_repository, _db = build_service()
    reports_repository.get_latest_portfolio.return_value = None
    await service.get_latest(user_id=USER_ID, project_id=None)
    reports_repository.get_latest_portfolio.assert_awaited_once_with(user_id=USER_ID)


def _stored(report: AnalyticsReport, data: dict) -> AnalyticsReport:
    """Возвращает запись так, как её вернул бы репозиторий после сохранения."""
    report.payload = data["payload"]
    report.context_summary = data["context_summary"]
    report.llm_model = data["llm_model"]
    report.duration_ms = data["duration_ms"]
    return report


@pytest.mark.asyncio
async def test_report_payload_keeps_only_verifiable_references() -> None:
    """Ссылки на задачи разрешаются, несуществующие отбрасываются, отчёт возвращается схемой."""

    stages = [stage(1, "В работе", False), stage(2, "Готово", True)]
    tasks = [task(11, stage_id=1, due_date=TODAY - timedelta(days=5))]
    service, reports_repository, _db = build_service(
        stages=stages,
        tasks=tasks,
        llm_response=draft(["PROJ-11"]),
    )

    await service.generate(**actor(), project_id=PROJECT_ID)

    payload = reports_repository.save.call_args.kwargs["data"]["payload"]
    assert payload["findings"][0]["tasks"] == [
        {
            "id": 11,
            "key": "PROJ-11",
            "title": "Задача 11",
            "project_key": "PROJ",
            "due_date": (TODAY - timedelta(days=5)).isoformat(),
            "is_overdue": True,
        }
    ]
    # Ссылки модели на несуществующие задачу и проект отбрасываются. Модель свободна назвать любой ключ, а отчёт обязан ссылаться только на то, что действительно попало в срез: иначе в интерфейсе появится ссылка в никуда.
    stages = [stage(1, "В работе", False)]
    tasks = [task(11, stage_id=1)]
    service, reports_repository, _db = build_service(
        stages=stages,
        tasks=tasks,
        llm_response=draft(["PROJ-999", "OTHER-1"]),
    )

    await service.generate(**actor(), project_id=PROJECT_ID)

    payload = reports_repository.save.call_args.kwargs["data"]["payload"]
    assert payload["findings"][0]["tasks"] == []

    service, reports_repository, _db = build_service(stages=stages, tasks=[task(11, stage_id=1)])

    await service.generate(**actor(), project_id=PROJECT_ID)

    payload = reports_repository.save.call_args.kwargs["data"]["payload"]
    assert payload["recommendations"][0]["project_key"] is None

    stages = [stage(1, "В работе", False)]
    saved = AnalyticsReport(
        id=42,
        project_id=PROJECT_ID,
        created_by_user_id=USER_ID,
        created_by_display_name_snapshot="Тестов Тест",
        llm_model="test-model",
        duration_ms=10,
        payload={},
        context_summary={},
        created_at=datetime.now(UTC),
    )
    service, _, db = build_service(
        stages=stages,
        tasks=[task(11, stage_id=1)],
        saved_report=saved,
    )

    result = await service.generate(**actor(), project_id=PROJECT_ID)

    assert result.id == 42
    assert result.scope is AnalyticsScope.PROJECT
    assert result.project_key == "PROJ"
    assert result.health is AnalyticsHealth.RISK
    assert result.findings[0].kind is AnalyticsFindingKind.OVERDUE


@pytest.mark.asyncio
async def test_portfolio_takes_only_projects_in_work() -> None:
    """Сводка дашборда разбирает активные проекты и молчит про остальные.

    Приостановленный, завершённый и ещё не начатый проект решения на
    сегодня не требуют, а место в контексте занимают наравне с активными.
    Если активных нет вовсе, это не пустой отчёт, а явный отказ.
    """
    active = project()
    paused = SimpleNamespace(
        **vars(project()) | {"id": 2, "key": "PAUSE", "status": ProjectStatus.PAUSED}
    )
    service, reports_repository, _db = build_service(
        projects=[active, paused],
        stages=[stage(1, "В работе", False)],
        tasks=[task(11, stage_id=1)],
    )

    await service.generate(**actor(), project_id=None)

    content = json.loads(service.llm_client.get_structured_response.call_args.kwargs["content"])
    assert [item["key"] for item in content["projects"]] == ["PROJ"]
    assert reports_repository.save.call_args.kwargs["data"]["context_summary"]["projects"] == 1

    service, _, _db = build_service(
        projects=[paused],
        stages=[stage(1, "В работе", False)],
        tasks=[task(11, stage_id=1)],
    )

    with pytest.raises(AnalyticsEmptyScopeError) as error:
        await service.generate(**actor(), project_id=None)

    assert "в работе" in error.value.error_details


@pytest.mark.asyncio
async def test_each_scope_gets_its_own_prompt() -> None:
    """Портфель и проект разбираются разными prompt-ами.

    Вопросы у областей разные: «за какой проект браться» против «что
    происходит внутри проекта». Общий prompt отвечал на оба посредственно.
    """
    service, _, _db = build_service(
        stages=[stage(1, "В работе", False)],
        tasks=[task(11, stage_id=1)],
    )

    await service.generate(**actor(), project_id=None)
    portfolio_prompt = service.llm_client.get_structured_response.call_args.kwargs["system_prompt"]

    await service.generate(**actor(), project_id=PROJECT_ID)
    project_prompt = service.llm_client.get_structured_response.call_args.kwargs["system_prompt"]

    assert portfolio_prompt == ANALYTICS_PORTFOLIO_SYSTEM_PROMPT
    assert project_prompt == ANALYTICS_PROJECT_SYSTEM_PROMPT
    assert portfolio_prompt != project_prompt


@pytest.mark.asyncio
async def test_portfolio_slice_carries_project_signals_and_task_details() -> None:
    """Портфель получает полные виды источников и отдельные показатели проектов."""
    stages = [stage(1, "В работе", False), stage(2, "Готово", True)]
    tasks = [
        task(11, stage_id=1, due_date=TODAY - timedelta(days=3)),
        task(12, stage_id=1, due_date=TODAY + timedelta(days=2)),
        task(13, stage_id=2),
    ]
    milestones = [
        SimpleNamespace(
            id=1,
            description_md="Условия приёмки",
            wbs_node_id=None,
            title="Веха",
            due_date=TODAY - timedelta(days=1),
            status=ProjectMilestoneStatus.PLANNED,
        )
    ]
    service, _, db = build_service(stages=stages, tasks=tasks, milestones=milestones)

    await service.generate(**actor(), project_id=None)

    content = json.loads(service.llm_client.get_structured_response.call_args.kwargs["content"])
    entry = content["projects"][0]
    assert {
        "registered_risks",
        "tasks",
        "comments",
        "documents",
        "stickers",
        "wbs",
        "recent_activity",
        "team",
        "participants",
        "attachments",
        "dependencies",
        "entity_counts",
        "stages",
    } <= entry.keys()
    assert entry["signals"]["overdue_tasks"] == 1
    assert entry["signals"]["done_tasks"] == 1
    assert [item["title"] for item in entry["milestones"]] == ["Веха"]
    assert [item["key"] for item in entry["tasks"]] == ["PROJ-11", "PROJ-12", "PROJ-13"]
    assert entry["tasks"][0]["overdue_days"] == 3
    assert entry["tasks"][-1]["done"] is True
    db.comments.get_for_tasks.assert_awaited_once_with(task_ids={11, 12, 13})
    db.documents.get_by_project.assert_awaited_once_with(project_id=PROJECT_ID)
    db.stickers.list_by_project_id.assert_awaited_once_with(project_id=PROJECT_ID)
    db.activity.get_recent_by_project.assert_awaited_once()
    db.wbs_nodes.get_by_project.assert_awaited_once_with(project_id=PROJECT_ID)


def supply_all_sources(db: AnalyticsDbScope) -> None:
    """Заполняет каждый вид проектных данных различимым содержанием."""
    from src.schemas.task_checklists import TaskChecklistSchema
    for item in db.tasks.get_by_project.return_value:
        if item.id == 30:
            item.checklist = TaskChecklistSchema(items=[
                {"text": "Согласовать приёмку", "is_completed": True},
                {"text": "Подписать акт", "is_completed": False},
            ]).model_dump(mode="json")
    member = SimpleNamespace(
        id=1,
        user_id=USER_ID,
        role=ProjectRole.OWNER,
        user=SimpleNamespace(
            username="analyst",
            last_name="Тестов",
            first_name="Тест",
            middle_name=None,
            password_hash="PRIVATE_HASH",
            email="private@example.test",
            phone="PRIVATE_PHONE",
        ),
    )
    db.members.get_for_project.return_value = [member]
    db.participants.get_by_task_ids.return_value = {
        30: [SimpleNamespace(project_member=member, role=TaskParticipantRole.EXECUTOR)]
    }
    db.attachments.get_for_tasks.return_value = [
        SimpleNamespace(
            task_id=30,
            original_name="Требования.pdf",
            content_type="application/pdf",
            size=123,
            created_at=datetime.now(UTC),
            storage_key="PRIVATE_STORAGE_KEY",
        )
    ]
    db.comments.get_for_tasks.return_value = [
        SimpleNamespace(
            task_id=30,
            body_md="Комментарий: согласовано с заказчиком.",
            author_name="Тестов Тест",
            created_at=datetime.now(UTC),
        )
    ]
    db.documents.get_by_project.return_value = [
        SimpleNamespace(
            id=3,
            title="Требования",
            slug="requirements",
            content_md="Документ: условия запуска.",
        )
    ]
    db.document_links.get_for_documents.return_value = [SimpleNamespace(document_id=3, task_id=30)]
    db.stickers.list_by_project_id.return_value = [
        SimpleNamespace(
            id=4,
            body="Стикер: согласовать окно запуска.",
            created_by_display_name_snapshot="Автор",
            created_at=datetime.now(UTC),
            task_links=[SimpleNamespace(task_id=30)],
        )
    ]
    db.wbs_nodes.get_by_project.return_value = [
        SimpleNamespace(id=5, title="Запуск", parent_id=None, position=0),
        SimpleNamespace(id=6, title="Подготовка", parent_id=5, position=0),
    ]
    db.milestones.get_by_project.return_value = [
        SimpleNamespace(
            id=7,
            title="Приёмка",
            description_md="Веха: требуется подпись заказчика.",
            due_date=TODAY,
            status=ProjectMilestoneStatus.ACHIEVED,
            wbs_node_id=6,
        )
    ]
    db.dependencies.get_by_project.return_value = [
        SimpleNamespace(
            predecessor_task_id=1,
            successor_task_id=30,
            dependency_type=TaskDependencyType.FINISH_TO_START,
            lag_days=3,
        )
    ]
    db.activity.get_recent_by_project.return_value = [
        SimpleNamespace(
            task_id=30,
            event_type=TaskActivityEventType.DUE_DATE_CHANGED,
            from_value="2026-09-01",
            to_value="2026-09-12",
            created_at=datetime.now(UTC),
        )
    ]
    db.activity.get_count_by_project.return_value = 1
    # Больше старых жёстких лимитов и портфельного (6), и проектного (20) среза.
    db.risks.get_by_project.return_value = [
        risk(
            id=index,
            key=f"RISK-{index}",
            title=f"Риск {index}",
            task_id=30,
            owner_user_id=USER_ID,
            mitigation_plan="Митигация: согласовать резерв.",
            response_plan="Реагирование: использовать резерв.",
        )
        for index in range(1, 25)
    ]
    db.risks.get_aggregates.return_value = [
        {
            "project_id": PROJECT_ID,
            "status": "OPEN",
            "probability": "HIGH",
            "impact": "HIGH",
            "risk_level": "HIGH",
            "count": 24,
            "without_owner": 0,
            "without_mitigation": 0,
            "due_for_review": 0,
            "review_overdue": 0,
            "linked": 24,
            "ai_suggested": 0,
            "latest_update": datetime.now(UTC),
        }
    ]


@pytest.mark.parametrize("project_id", [PROJECT_ID, None])
async def test_both_scopes_include_every_source_with_descriptions_and_links(project_id) -> None:
    tasks = [task(index, stage_id=1, wbs_node_id=6) for index in range(1, 31)]
    for item in tasks:
        item.description_md = f"Описание задачи {item.id}: критерии готовности."
    tasks[-1].baseline_start_date = TODAY - timedelta(days=10)
    tasks[-1].baseline_due_date = TODAY - timedelta(days=1)
    tasks[-1].completed_at = datetime.now(UTC)
    service, reports, db = build_service(stages=[stage(1, "Работа", False)], tasks=tasks)
    supply_all_sources(db)
    active = False

    @asynccontextmanager
    async def scope():
        nonlocal active
        active = True
        try:
            yield db
        finally:
            active = False

    async def llm(**kwargs):
        assert active is False
        db.unit_of_work.commit.assert_not_awaited()
        return draft([])

    service.scope = scope
    service.llm_client.get_structured_response.side_effect = llm
    report = await service.generate(**actor(), project_id=project_id)
    content = service.llm_client.get_structured_response.await_args.kwargs["content"]
    entry = json.loads(content)["projects"][0]
    assert entry["description"] == "Описание проекта."
    assert entry["checklists"][0]["task"] == "PROJ-30"
    assert entry["checklists"][0]["completed_items"] == 1
    assert entry["checklists"][0]["items"][1] == {"text": "Подписать акт", "is_completed": False}
    assert len(entry["tasks"]) == 30
    assert all(
        row["description"] == tasks[int(row["key"].split("-")[1]) - 1].description_md
        for row in entry["tasks"]
    )
    final_task = next(row for row in entry["tasks"] if row["key"] == "PROJ-30")
    assert final_task["baseline_due"] == tasks[-1].baseline_due_date.isoformat()
    assert final_task["completed_at"] == tasks[-1].completed_at.isoformat()
    assert entry["team"] == [{"username": "analyst", "name": "Тестов Тест", "role": "OWNER"}]
    assert entry["participants"][0]["task"] == "PROJ-30"
    assert entry["participants"][0]["role"] == "EXECUTOR"
    assert entry["attachments"][0]["name"] == "Требования.pdf"
    assert entry["attachments"][0]["task"] == "PROJ-30"
    assert entry["comments"][0]["text"] == "Комментарий: согласовано с заказчиком."
    assert entry["documents"][0]["excerpt"] == "Документ: условия запуска."
    assert entry["documents"][0]["linked_tasks"] == ["PROJ-30"]
    assert entry["stickers"][0]["text"] == "Стикер: согласовать окно запуска."
    assert entry["stickers"][0]["tasks"] == ["PROJ-30"]
    assert entry["wbs"][1]["path"] == "1.1 Подготовка"
    assert entry["wbs"][1]["parent_id"] == 5
    assert entry["milestones"][0]["description"] == "Веха: требуется подпись заказчика."
    assert entry["milestones"][0]["wbs"] == "1.1 Подготовка"
    assert entry["dependencies"] == [
        {
            "predecessor": "PROJ-1",
            "successor": "PROJ-30",
            "type": "FINISH_TO_START",
            "lag_days": 3,
        }
    ]
    assert entry["recent_activity"][0]["to"] == "2026-09-12"
    assert len(entry["registered_risks"]) == 24
    for row in entry["registered_risks"]:
        assert row["description"] == "Поставщик может задержать API."
        assert row["mitigation_plan"] == "Митигация: согласовать резерв."
        assert row["response_plan"] == "Реагирование: использовать резерв."
        assert row["owner"]["username"] == "analyst"
        assert row["task_key"] == "PROJ-30"
    assert all(count["total"] == count["included"] > 0 for count in entry["entity_counts"].values())
    assert not report.context.truncated
    assert all(
        token not in content
        for token in (
            "PRIVATE_HASH",
            "private@example.test",
            "PRIVATE_PHONE",
            "PRIVATE_STORAGE_KEY",
        )
    )
    assert report.context.model_dump(mode="json") == json.loads(content)["context"]
    reports.save.assert_awaited_once()
    db.unit_of_work.commit.assert_awaited_once()


@pytest.mark.parametrize("project_id", [PROJECT_ID, None])
async def test_large_context_preserves_source_types_and_explains_every_reduction(
    project_id,
) -> None:
    tasks = [task(index, stage_id=1, updated_days_ago=index) for index in range(1, 301)]
    for item in tasks:
        item.description_md = "Большое описание. " * 1000
    projects = [project()]
    if project_id is None:
        projects.append(SimpleNamespace(**vars(project()) | {"id": 2, "key": "SECOND"}))
    service, _, db = build_service(
        projects=projects, tasks=tasks, stages=[stage(1, "Работа", False)]
    )
    supply_all_sources(db)
    db.documents.get_by_project.return_value[0].content_md = "Большой документ. " * 10000
    db.activity.get_count_by_project.return_value = 100
    report = await service.generate(**actor(), project_id=project_id)
    content = service.llm_client.get_structured_response.await_args.kwargs["content"]
    assert len(content) <= MAX_CONTEXT_CHARS
    payload = json.loads(content)
    assert len(payload["projects"]) == len(projects)
    for entry in payload["projects"]:
        assert all(count["included"] > 0 for count in entry["entity_counts"].values())
        assert entry["entity_counts"]["tasks"]["total"] == 300
        assert entry["entity_counts"]["activity"] == {"total": 100, "included": 1}
        assert entry["comments"][0]["task"].endswith("-30")
        assert entry["attachments"][0]["task"].endswith("-30")
        assert entry["documents"][0]["excerpt"].endswith("…")
    assert report.context.tasks_included < report.context.tasks_total
    assert report.context.truncated
    assert report.context.omitted
    assert payload["context"] == report.context.model_dump(mode="json")


@pytest.mark.parametrize("project_id", [PROJECT_ID, None])
@pytest.mark.parametrize("source", ["description", "documents", "milestones", "wbs", "stickers"])
async def test_project_without_tasks_can_be_analyzed_from_other_sources(project_id, source) -> None:
    item = project()
    item.description_md = None
    service, _, db = build_service(projects=[item])
    if source == "description":
        item.description_md = "Цель проекта: запуск новой услуги."
    elif source == "documents":
        db.documents.get_by_project.return_value = [
            SimpleNamespace(
                id=1,
                title="Обоснование",
                slug="brief",
                content_md="Цели и ограничения проекта.",
            )
        ]
    elif source == "milestones":
        db.milestones.get_by_project.return_value = [
            SimpleNamespace(
                id=1,
                title="Запуск",
                due_date=TODAY,
                description_md="Первый клиент.",
                status=ProjectMilestoneStatus.PLANNED,
                wbs_node_id=None,
            )
        ]
    elif source == "wbs":
        db.wbs_nodes.get_by_project.return_value = [
            SimpleNamespace(
                id=1,
                title="Подготовка",
                parent_id=None,
                position=0,
            )
        ]
    else:
        db.stickers.list_by_project_id.return_value = [
            SimpleNamespace(
                id=1,
                body="Обсудить рамки проекта.",
                task_links=[],
                created_by_display_name_snapshot="Автор",
                created_at=datetime.now(UTC),
            )
        ]
    report = await service.generate(**actor(), project_id=project_id)
    assert report.context.tasks_total == 0
    assert report.context.projects == 1
    service.llm_client.get_structured_response.assert_awaited_once()


async def test_new_source_failure_aborts_analysis_instead_of_silently_omitting_it() -> None:
    service, reports, db = build_service(tasks=[task(1, 1)])
    db.attachments.get_for_tasks.side_effect = TaskAttachmentsRepositoryError("Чтение недоступно")
    with pytest.raises(AnalyticsServiceError):
        await service.generate(**actor(), project_id=PROJECT_ID)
    service.llm_client.get_structured_response.assert_not_awaited()
    reports.save.assert_not_awaited()
