"""Реальный SQL-снимок аналитики сохраняет источники после закрытия сессии."""

import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.clients.llm import LlmClient
from src.db.models.board_stickers import BoardSticker, BoardStickerTaskLink
from src.db.models.document_links import DocumentLink
from src.db.models.documents import Document
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.project_milestones import ProjectMilestone
from src.db.models.project_risks import ProjectRisk
from src.db.models.projects import ProjectStatus
from src.db.models.task_activity import TaskActivity, TaskActivityEventType
from src.db.models.task_attachments import TaskAttachment
from src.db.models.task_comments import TaskComment
from src.db.models.task_dependencies import TaskDependency
from src.db.models.task_participants import TaskParticipant, TaskParticipantRole
from src.db.models.tasks import Task
from src.db.models.wbs_nodes import WbsNode
from src.dependencies.scopes import build_analytics_scope
from src.schemas.analytics import AnalyticsDraftSchema, AnalyticsHealth
from src.schemas.task_checklists import TaskChecklistSchema
from src.services.analytics import AnalyticsService


@pytest.mark.parametrize("portfolio", [False, True])
async def test_complete_snapshot_survives_closed_database_scope(
    db_session,
    project,
    stage,
    user,
    board,
    portfolio,
):
    project.status = ProjectStatus.ACTIVE
    project.description_md = "Описание настоящего проекта."
    member = ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.OWNER)
    node = WbsNode(project_id=project.id, title="Подготовка", position=0)
    document = Document(
        project_id=project.id, slug="brief", title="Документ", content_md="Условия."
    )
    sticker = BoardSticker(
        project_id=project.id,
        board_id=board.id,
        body="Заметка.",
        created_by_user_id=user.id,
        created_by_username_snapshot=user.username,
        created_by_display_name_snapshot="Автор",
    )
    db_session.add_all([member, node, document, sticker])
    await db_session.flush()
    tasks = [
        Task(
            project_id=project.id,
            stage_id=stage.id,
            wbs_node_id=node.id,
            number=index,
            title=f"Работа {index}",
            description_md=f"Описание работы {index}.",
            checklist=TaskChecklistSchema(items=[{"text": "Проверить результат"}]).model_dump(
                mode="json"
            ),
            baseline_start_date=date(2026, 9, 1),
            baseline_due_date=date(2026, 9, 6),
        )
        for index in (1, 2)
    ]
    db_session.add_all(tasks)
    await db_session.flush()
    db_session.add_all(
        [
            ProjectMilestone(
                project_id=project.id,
                title="Приёмка",
                description_md="Подписать акт.",
                wbs_node_id=node.id,
                due_date=date.today(),
            ),
            TaskParticipant(
                task_id=tasks[0].id, project_member_id=member.id, role=TaskParticipantRole.EXECUTOR
            ),
            TaskAttachment(
                task_id=tasks[0].id,
                original_name="Требования.pdf",
                content_type="application/pdf",
                storage_key="internal-file-key",
                size=512,
            ),
            TaskComment(task_id=tasks[0].id, body_md="Уточнение.", author_name="Автор"),
            DocumentLink(document_id=document.id, task_id=tasks[0].id),
            BoardStickerTaskLink(sticker_id=sticker.id, task_id=tasks[0].id),
            TaskDependency(
                project_id=project.id,
                predecessor_task_id=tasks[0].id,
                successor_task_id=tasks[1].id,
                lag_days=2,
            ),
            TaskActivity(
                task_id=tasks[0].id,
                event_type=TaskActivityEventType.DUE_DATE_CHANGED,
                from_value="2026-09-01",
                to_value="2026-09-06",
            ),
            ProjectRisk(
                project_id=project.id,
                task_id=tasks[0].id,
                owner_user_id=user.id,
                title="Задержка",
                description="Угроза поставки.",
                probability="HIGH",
                impact="HIGH",
                risk_level="HIGH",
                response_strategy="MITIGATE",
                mitigation_plan="Подготовить резерв.",
                response_plan="Использовать резерв.",
            ),
        ]
    )
    await db_session.flush()
    project_id, user_id = project.id, user.id
    # Новые короткие сессии используют изолированную транзакцию теста.
    session_factory = async_sessionmaker(
        bind=await db_session.connection(),
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    llm = AsyncMock(spec=LlmClient)
    llm.model = "test-model"
    llm.get_structured_response.return_value = AnalyticsDraftSchema(
        headline="Есть риск поставки.",
        health=AnalyticsHealth.WATCH,
        health_note="Подготовлен резерв.",
        findings=[],
        progress=[],
        recommendations=[],
    )
    service = AnalyticsService(
        scope=build_analytics_scope(session_factory=session_factory), llm_client=llm
    )
    report = await service.generate(
        actor_id=user_id,
        actor_name="Автор",
        project_id=None if portfolio else project_id,
    )
    content = json.loads(llm.get_structured_response.await_args.kwargs["content"])
    entry = content["projects"][0]
    assert all(count["included"] == count["total"] > 0 for count in entry["entity_counts"].values())
    assert entry["documents"][0]["excerpt"] == "Условия."
    assert entry["participants"][0]["username"] == user.username
    assert entry["registered_risks"][0]["owner"]["username"] == user.username
    assert entry["registered_risks"][0]["task_key"] == "PROJ-1"
    assert entry["milestones"][0]["description"] == "Подписать акт."
    assert entry["stickers"][0]["tasks"] == ["PROJ-1"]
    assert entry["tasks"][0]["baseline_due"] == "2026-09-06"
    assert report.created_at <= datetime.now(UTC)
    assert report.context.entity_counts["activity"].total == 1
    assert report.context.entity_counts["attachments"].included == 1
    latest = await service.get_latest(user_id=user_id, project_id=None if portfolio else project_id)
    assert latest.id == report.id
    assert latest.context == report.context
