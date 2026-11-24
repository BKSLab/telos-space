"""Настоящий PostgreSQL: журнал фиксирует смысл действий и переживает источник."""

from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from src.api.middleware.api_key_usage import ApiKeyUsageMiddleware
from src.db.models.board_shapes import BoardShape
from src.db.models.board_stickers import BoardSticker, BoardStickerTaskLink
from src.db.models.boards import Board
from src.db.models.document_links import DocumentLink
from src.db.models.documents import Document
from src.db.models.project_files import ProjectFile
from src.db.models.project_history_events import ProjectHistoryEvent
from src.db.models.project_members import ProjectMember
from src.db.models.project_milestones import ProjectMilestone
from src.db.models.project_risks import ProjectRisk
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.task_activity import TaskActivity, TaskActivityEventType
from src.db.models.task_attachments import TaskAttachment
from src.db.models.task_comments import TaskComment
from src.db.models.task_dependencies import TaskDependency
from src.db.models.task_participants import TaskParticipant
from src.db.models.task_reports import TaskReport
from src.db.models.tasks import Task
from src.db.models.users import User
from src.db.models.wbs_nodes import WbsNode
from src.dependencies.access import require_project_access
from src.dependencies.auth import get_principal
from src.dependencies.db_session import get_db_session
from src.dependencies.services import get_auth_service
from src.repositories.project_history import ProjectHistoryRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.tasks import TasksRepository
from src.schemas.project_history import ProjectHistoryQuery
from src.schemas.task_transfers import TaskTransferTargetSchema
from src.services.auth import ApiKeyGrant, AuthService
from src.services.task_comments import TaskCommentsService
from src.utils.request_actor import RequestActor, reset_request_actor, set_request_actor
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_personal_tasks_and_moves import services


async def events(session, project_id):
    return list(
        (
            await session.scalars(
                select(ProjectHistoryEvent)
                .where(ProjectHistoryEvent.project_id == project_id)
                .order_by(ProjectHistoryEvent.id)
            )
        ).all()
    )


async def task(session, project, stage, number=1, title="Исходное название"):
    return await TasksRepository(session).save(
        data={
            "project_id": project.id,
            "stage_id": stage.id,
            "number": number,
            "title": title,
            "priority": "MEDIUM",
            "position": 0.0,
        }
    )


async def test_task_snapshots_survive_edits_cascade_and_rollback(db_session, project, stage, user):
    """Создание, тексты, роли и удаление остаются в проекте, транзакция атомарна."""
    token = set_request_actor(RequestActor(user.id, "Автор запроса"))
    try:
        row = await task(db_session, project, stage)
        task_id = row.id
        member = ProjectMember(project_id=project.id, user_id=user.id)
        db_session.add(member)
        await db_session.flush()
        comment = TaskComment(
            task_id=task_id,
            body_md="Первый комментарий",
            author_name="Автор запроса",
            author_id=user.id,
        )
        report = TaskReport(
            task_id=task_id,
            revision=1,
            body_md="Итог работы",
            author_name="Автор запроса",
            author_id=user.id,
        )
        db_session.add_all([comment, report])
        await db_session.flush()
        sequence_before_rename = await db_session.scalar(
            text("SELECT last_value FROM project_history_events_id_seq")
        )
        before_rename = {
            item.public_id: item.title for item in await events(db_session, project.id)
        }
        await db_session.execute(
            update(Task).where(Task.id == task_id).values(title="Новое название")
        )
        assert (
            await db_session.scalar(text("SELECT last_value FROM project_history_events_id_seq"))
            == sequence_before_rename + 1
        )
        await db_session.execute(
            update(TaskComment)
            .where(TaskComment.id == comment.id)
            .values(body_md="Изменённый комментарий")
        )
        participants = [
            TaskParticipant(task_id=task_id, project_member_id=member.id, role=role)
            for role in ("REPORTER", "OBSERVER")
        ]
        db_session.add_all(participants)
        await db_session.flush()
        for assignment in participants:
            await db_session.execute(
                delete(TaskParticipant).where(TaskParticipant.id == assignment.id)
            )
        await db_session.execute(delete(TaskComment).where(TaskComment.id == comment.id))
        deleted_comment = (
            await ProjectHistoryRepository(db_session).read(
                project.id, ProjectHistoryQuery(event_id=f"task_comment:{comment.id}")
            )
        )[0]
        assert not deleted_comment.source_available
        assert deleted_comment.comment_id is None and deleted_comment.task_id == task_id
        snapshots = await events(db_session, project.id)
        assert {
            item.public_id: item.title for item in snapshots if item.public_id in before_rename
        } == before_rename
        # Rewriting equal values and database bookkeeping must not fabricate activity.
        before_noop = [item.public_id for item in snapshots]
        sequence_before_noop = await db_session.scalar(
            text("SELECT last_value FROM project_history_events_id_seq")
        )
        await db_session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(title="Новое название", updated_at=datetime.now(UTC))
        )
        assert [item.public_id for item in await events(db_session, project.id)] == before_noop
        assert (
            await db_session.scalar(text("SELECT last_value FROM project_history_events_id_seq"))
            == sequence_before_noop
        )
        nested = await db_session.begin_nested()
        await db_session.execute(
            update(Task).where(Task.id == task_id).values(title="Не состоялось")
        )
        assert len(await events(db_session, project.id)) > len(before_noop)
        await nested.rollback()
        assert [item.public_id for item in await events(db_session, project.id)] == before_noop
        await db_session.execute(delete(Task).where(Task.id == task_id))
        final = await events(db_session, project.id)
        assert set(before_noop) <= {item.public_id for item in final}
        assert (
            await db_session.scalar(select(TaskReport.id).where(TaskReport.task_id == task_id))
            is None
        )
        assert all(
            item.actor_user_id == user.id and item.actor_name == "Автор запроса"
            for item in final
            if item.task_id == task_id
        )
        types = {item.event_type for item in final if item.task_id == task_id}
        assert {
            "TASK_CREATED",
            "TITLE_CHANGED",
            "TASK_DELETED",
            "COMMENT_ADDED",
            "COMMENT_EDITED",
            "COMMENT_DELETED",
            "REPORT_PUBLISHED",
            "TASK_PARTICIPANT_CREATED",
            "TASK_PARTICIPANT_DELETED",
        } <= types
        assert len({item.public_id for item in final}) == len(final)
        assert all(item.detail is None or len(item.detail) <= 500 for item in final)
        visible = await ProjectHistoryRepository(db_session).read(
            project.id, ProjectHistoryQuery(task_id=task_id, limit=100)
        )
        assert {item.id for item in visible} == {
            item.public_id for item in final if item.task_id == task_id
        }
        assert all(not item.source_available for item in visible)
        assert all(
            item.task_id is None and item.comment_id is None and item.report_id is None
            for item in visible
        )
    finally:
        reset_request_actor(token)


async def test_public_domain_matrix_preserves_removed_sources(
    db_session, project, stage, user, board
):
    """Матрица пропусков: источники, план, структура, команда, стадии и доска."""
    row = await task(db_session, project, stage)
    successor = await task(db_session, project, stage, 2)
    document = Document(
        project_id=project.id, slug="public", title="Документ", content_md="Содержание"
    )
    original = ProjectFile(
        project_id=project.id,
        original_name="plan.txt",
        content_type="text/plain",
        size=1,
        storage_key="journal-plan",
        content_hash="a" * 64,
    )
    attachment = TaskAttachment(
        task_id=row.id,
        original_name="result.txt",
        content_type="text/plain",
        size=1,
        storage_key="journal-result",
    )
    risk = ProjectRisk(
        project_id=project.id,
        title="Риск",
        description="Описание",
        probability="LOW",
        impact="LOW",
        risk_level="LOW",
        response_strategy="ACCEPT",
    )
    milestone = ProjectMilestone(project_id=project.id, title="Веха", due_date=date(2026, 12, 1))
    node = WbsNode(project_id=project.id, title="Раздел", position=0.0)
    member = ProjectMember(project_id=project.id, user_id=user.id, role="OWNER")
    extra_stage = ProjectStage(
        project_id=project.id, name="Приёмка", color="#123456", order_index=2
    )
    sticker = BoardSticker(
        project_id=project.id,
        board_id=board.id,
        body="Заметка",
        created_by_username_snapshot="owner",
        created_by_display_name_snapshot="Автор",
    )
    shape = BoardShape(
        board_id=board.id, kind="rect", x=0, y=0, w=100, h=80, label="Схема", style={}
    )
    db_session.add_all(
        [document, original, attachment, risk, milestone, node, member, extra_stage, sticker, shape]
    )
    await db_session.flush()
    link = DocumentLink(document_id=document.id, task_id=row.id)
    dependency = TaskDependency(
        project_id=project.id, predecessor_task_id=row.id, successor_task_id=successor.id
    )
    db_session.add_all(
        [link, dependency, BoardStickerTaskLink(sticker_id=sticker.id, task_id=row.id)]
    )
    await db_session.flush()
    # Content changes are public; operational indexing/version bookkeeping is not.
    for model, identity, fields in [
        (Document, document.id, {"title": "Новый документ", "content_md": "Новый текст"}),
        (ProjectFile, original.id, {"display_title": "Новый файл", "caption": "Примечание"}),
        (
            TaskAttachment,
            attachment.id,
            {"display_title": "Новый результат", "caption": "Примечание"},
        ),
        (ProjectRisk, risk.id, {"title": "Уточнённый риск", "status": "CLOSED"}),
        (ProjectMilestone, milestone.id, {"title": "Новая веха", "status": "ACHIEVED"}),
        (TaskDependency, dependency.id, {"lag_days": 3}),
        (WbsNode, node.id, {"title": "Новый раздел"}),
        (ProjectStage, extra_stage.id, {"name": "Новая приёмка"}),
        (ProjectMember, member.id, {"role": "MEMBER"}),
        (Board, board.id, {"title": "Новая доска"}),
        (BoardSticker, sticker.id, {"body": "Новая заметка"}),
        (BoardShape, shape.id, {"label": "Новая схема"}),
        (Project, project.id, {"name": "Новое имя проекта", "status": "ACTIVE"}),
    ]:
        await db_session.execute(update(model).where(model.id == identity).values(**fields))
    # Geometry, revisions and processing bookkeeping are not public actions.
    before_bookkeeping = {item.public_id for item in await events(db_session, project.id)}
    for model, identity, fields in [
        (Document, document.id, {"version": 9}),
        (TaskAttachment, attachment.id, {"version": 9}),
        (Board, board.id, {"revision": 9}),
        (BoardSticker, sticker.id, {"canvas_x": 900.0, "z_index": 3}),
        (BoardShape, shape.id, {"x": 900.0, "y": 700.0, "style": {"fill": "#ffffff"}}),
    ]:
        await db_session.execute(update(model).where(model.id == identity).values(**fields))
    assert {item.public_id for item in await events(db_session, project.id)} == before_bookkeeping
    preserved = {
        item.public_id: (item.title, item.detail, item.actor_user_id, item.actor_name)
        for item in await events(db_session, project.id)
    }
    await db_session.execute(
        delete(BoardStickerTaskLink).where(BoardStickerTaskLink.sticker_id == sticker.id)
    )
    for model, identity in [
        (BoardShape, shape.id),
        (DocumentLink, link.id),
        (Document, document.id),
        (ProjectFile, original.id),
        (TaskAttachment, attachment.id),
        (ProjectRisk, risk.id),
        (ProjectMilestone, milestone.id),
        (TaskDependency, dependency.id),
        (WbsNode, node.id),
        (ProjectStage, extra_stage.id),
        (ProjectMember, member.id),
        (Board, board.id),
    ]:
        await db_session.execute(delete(model).where(model.id == identity))
    final = await events(db_session, project.id)
    assert {
        item.public_id: (item.title, item.detail, item.actor_user_id, item.actor_name)
        for item in final
        if item.public_id in preserved
    } == preserved
    types = {item.event_type for item in final}
    expected = {
        "DOCUMENT_CREATED",
        "DOCUMENT_UPDATED",
        "DOCUMENT_DELETED",
        "PROJECT_FILE_UPLOADED",
        "PROJECT_FILE_UPDATED",
        "PROJECT_FILE_DELETED",
        "TASK_ATTACHMENT_CREATED",
        "TASK_ATTACHMENT_UPDATED",
        "TASK_ATTACHMENT_DELETED",
        "DOCUMENT_LINK_CREATED",
        "DOCUMENT_LINK_DELETED",
        "RISK_CREATED",
        "RISK_UPDATED",
        "RISK_DELETED",
        "MILESTONE_CREATED",
        "MILESTONE_UPDATED",
        "MILESTONE_DELETED",
        "TASK_DEPENDENCY_CREATED",
        "TASK_DEPENDENCY_UPDATED",
        "TASK_DEPENDENCY_DELETED",
        "WBS_NODE_CREATED",
        "WBS_NODE_UPDATED",
        "WBS_NODE_DELETED",
        "PROJECT_MEMBER_CREATED",
        "PROJECT_MEMBER_UPDATED",
        "PROJECT_MEMBER_DELETED",
        "STAGE_CREATED",
        "STAGE_UPDATED",
        "STAGE_DELETED",
        "BOARD_CREATED",
        "BOARD_UPDATED",
        "BOARD_DELETED",
        "BOARD_SHAPE_CREATED",
        "BOARD_SHAPE_UPDATED",
        "BOARD_SHAPE_DELETED",
        "STICKER_TASK_LINK_CREATED",
        "STICKER_TASK_LINK_DELETED",
        "STICKER_CREATED",
        "STICKER_UPDATED",
        "STICKER_DELETED",
        "PROJECT_UPDATED",
    }
    assert expected <= types, sorted(expected - types)
    assert all(item.actor_user_id is None and item.actor_name is None for item in final)
    visible = await ProjectHistoryRepository(db_session).read(
        project.id, ProjectHistoryQuery(limit=100)
    )
    assert {item.id for item in visible} == {item.public_id for item in final}


async def test_semantic_task_activity_replaces_raw_changes_once(db_session, project, stage, user):
    """Общий сервис пишет расширенное событие, не дублируя триггер поля задачи."""
    row = await task(db_session, project, stage)
    await db_session.execute(
        update(Task)
        .where(Task.id == row.id)
        .values(
            start_date=date(2026, 12, 1),
            due_date=date(2026, 12, 2),
            checklist={"items": []},
            checklist_revision=1,
        )
    )
    db_session.add_all(
        [
            TaskActivity(
                task_id=row.id,
                event_type=TaskActivityEventType.DATES_CHANGED,
                from_value="Без дат",
                to_value="01–02 декабря",
                reason="Перенос поставки",
                actor_user_id=user.id,
                actor_name="Автор дат",
            ),
            TaskActivity(
                task_id=row.id,
                event_type=TaskActivityEventType.CHECKLIST_CHANGED,
                from_value="0",
                to_value="1",
                actor_user_id=user.id,
                actor_name="Автор списка",
            ),
        ]
    )
    await db_session.flush()
    items = [item for item in await events(db_session, project.id) if item.task_id == row.id]
    assert [item.event_type for item in items].count("DATES_CHANGED") == 1
    assert not {"START_DATE_CHANGED", "DUE_DATE_CHANGED"} & {item.event_type for item in items}
    assert [item.event_type for item in items].count("CHECKLIST_CHANGED") == 1
    dates = next(item for item in items if item.event_type == "DATES_CHANGED")
    assert dates.from_value == "Без дат" and dates.to_value == "01–02 декабря"
    assert "Перенос поставки" in dates.detail
    assert dates.actor_name == "Автор дат" and dates.actor_user_id == user.id


async def test_transfer_keeps_scoped_snapshots_without_importing_private_history(
    db_session,
    project,
    stage,
    user,
    tmp_path,
):
    """Перенос не переписывает старый проект и не импортирует личные тексты."""
    target_project = await ProjectsRepository(db_session).save(
        data={
            "owner_id": user.id,
            "key": "TARGET",
            "name": "Секретное имя цели",
            "status": "ACTIVE",
            "color": "#123456",
            "order_index": 1,
        }
    )
    target_stage = ProjectStage(
        project_id=target_project.id, name="Работа", color="#123456", order_index=0
    )
    db_session.add_all(
        [
            target_stage,
            ProjectMember(project_id=project.id, user_id=user.id, role="OWNER"),
            ProjectMember(project_id=target_project.id, user_id=user.id, role="OWNER"),
        ]
    )
    await db_session.flush()
    task_service, transfers = services(db_session, tmp_path)
    moved = await task_service.create_task(
        project_id=project.id, data={"title": "Переносимая задача"}, created_by_user_id=user.id
    )
    old_comment = TaskComment(
        task_id=moved.id, body_md="Текст только исходного проекта", author_name="Чужой автор"
    )
    db_session.add(old_comment)
    await db_session.flush()
    original_ids = {item.public_id for item in await events(db_session, project.id)}
    await transfers.move(
        task_id=moved.id,
        target=TaskTransferTargetSchema(project_id=target_project.id),
        user_id=user.id,
    )
    source = await events(db_session, project.id)
    target = await events(db_session, target_project.id)
    assert original_ids <= {item.public_id for item in source}
    source_out = [
        item
        for item in source
        if item.task_id == moved.id and item.event_type == "TASK_TRANSFERRED_OUT"
    ]
    target_in = [
        item
        for item in target
        if item.task_id == moved.id and item.event_type == "TASK_TRANSFERRED_IN"
    ]
    assert len(source_out) == len(target_in) == 1
    assert "Секретное имя цели" not in str(
        [(item.title, item.detail, item.from_value, item.to_value) for item in source_out]
    )
    assert old_comment.id not in {item.comment_id for item in target}
    source_view = await ProjectHistoryRepository(db_session).read(
        project.id, ProjectHistoryQuery(task_id=moved.id, limit=100)
    )
    assert source_view and all(
        not item.source_available
        and item.task_id is None
        and item.comment_id is None
        and item.report_id is None
        for item in source_view
    )
    assert "Текст только исходного проекта" not in str(
        [(item.title, item.detail) for item in target]
    )
    await db_session.execute(
        update(Task).where(Task.id == moved.id).values(title="Будущее целевого проекта")
    )
    assert all(
        "Будущее целевого проекта" not in item.title
        for item in await events(db_session, project.id)
    )
    assert any(
        item.event_type == "TITLE_CHANGED"
        and "Будущее целевого проекта" in str((item.title, item.detail, item.to_value))
        for item in await events(db_session, target_project.id)
    )

    private = await task_service.create_personal_task(
        data={"title": "Личная задача"}, created_by_user_id=user.id
    )
    private_comment = TaskComment(
        task_id=private.id, body_md="Личный текст навсегда приватен", author_name="Личный автор"
    )
    db_session.add(private_comment)
    await db_session.flush()
    await transfers.move(
        task_id=private.id,
        target=TaskTransferTargetSchema(project_id=target_project.id),
        user_id=user.id,
    )
    await db_session.execute(
        update(TaskComment)
        .where(TaskComment.id == private_comment.id)
        .values(body_md="Правка старого личного текста")
    )
    await db_session.execute(delete(TaskComment).where(TaskComment.id == private_comment.id))
    public = await ProjectHistoryRepository(db_session).read(
        target_project.id, ProjectHistoryQuery(task_id=private.id, limit=100)
    )
    assert "Личный текст навсегда приватен" not in str(public) and "Личный автор" not in str(public)
    new_comment = TaskComment(
        task_id=private.id, body_md="Новый публичный комментарий", author_name="Публичный автор"
    )
    db_session.add(new_comment)
    await db_session.flush()
    public = await ProjectHistoryRepository(db_session).read(
        target_project.id, ProjectHistoryQuery(task_id=private.id, limit=100)
    )
    assert any(item.comment_id == new_comment.id for item in public)


async def test_history_http_checks_current_project_membership_and_exact_event_scope(
    db_session,
    project,
    stage,
    user,
    current_principal,
    monkeypatch,
):
    """Известный ID события или чужой задачи не даёт доступ к другому проекту."""
    row = await task(db_session, project, stage)
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id))
    await db_session.flush()
    own_event = next(
        item for item in await events(db_session, project.id) if item.task_id == row.id
    )
    other = await ProjectsRepository(db_session).save(
        data={
            "owner_id": user.id,
            "key": "OTHER",
            "name": "Другой проект",
            "status": "ACTIVE",
            "color": "#123456",
            "order_index": 1,
        }
    )
    foreign_event = (await events(db_session, other.id))[0]
    principal = replace(current_principal, user_id=user.id)
    monkeypatch.delitem(app.dependency_overrides, require_project_access)
    monkeypatch.setitem(app.dependency_overrides, get_db_session, lambda: db_session)
    monkeypatch.setitem(app.dependency_overrides, get_principal, lambda: principal)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = f"/api/v1/projects/{project.id}/history"
        response = await client.get(path, params={"event_id": own_event.public_id})
        assert response.status_code == 200 and len(response.json()["items"]) == 1
        response = await client.get(path, params={"event_id": foreign_event.public_id})
        assert response.status_code == 200 and response.json()["items"] == []
        assert (await client.get(f"/api/v1/projects/{other.id}/history")).status_code == 404
        await db_session.execute(
            delete(ProjectMember).where(
                ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
            )
        )
        assert (await client.get(path, params={"event_id": own_event.public_id})).status_code == 404


async def test_actor_is_statement_local_after_late_auth_and_clears_on_reused_session(
    db_session,
    project,
    stage,
    user,
):
    """Сессия могла читать до auth; смена ContextVar не оставляет автора предыдущего DML."""
    await db_session.scalar(select(Project.id).where(Project.id == project.id))
    first = await task(db_session, project, stage)
    token = set_request_actor(RequestActor(user.id, "Настоящий автор запроса"))
    try:
        await db_session.execute(
            update(Task).where(Task.id == first.id).values(title="Авторизованная правка")
        )
        db_session.add(
            TaskActivity(
                task_id=first.id,
                event_type=TaskActivityEventType.PRIORITY_CHANGED,
                actor_name="Устаревшая подпись",
                from_value="MEDIUM",
                to_value="HIGH",
            )
        )
        await db_session.flush()
    finally:
        reset_request_actor(token)
    await db_session.execute(
        update(Task).where(Task.id == first.id).values(title="Системная правка")
    )
    recorded = [
        item for item in await events(db_session, project.id) if item.event_type == "TITLE_CHANGED"
    ]
    assert len(recorded) == 2
    assert (recorded[0].actor_user_id, recorded[0].actor_name) == (
        user.id,
        "Настоящий автор запроса",
    )
    assert (recorded[1].actor_user_id, recorded[1].actor_name) == (None, None)
    created = next(
        item
        for item in await events(db_session, project.id)
        if item.public_id == f"task_created:{first.id}"
    )
    assert created.actor_user_id is None and created.actor_name is None

    semantic = next(
        item
        for item in await events(db_session, project.id)
        if item.event_type == "PRIORITY_CHANGED"
    )
    assert (semantic.actor_user_id, semantic.actor_name) == (user.id, "Настоящий автор запроса")


async def test_history_version_is_transactional_and_enforces_current_access_and_all_scopes(
    db_session,
    project,
    stage,
    user,
    current_principal,
    monkeypatch,
):
    """Версия 0 у старого проекта, рост при записи и восстановление при rollback; scopes не расширяют ACL."""
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id))
    await db_session.flush()
    # This fixture represents an existing project immediately after the schema-only migration.
    await db_session.execute(
        delete(ProjectHistoryEvent).where(ProjectHistoryEvent.project_id == project.id)
    )
    all_scopes = frozenset({"projects:read", "tasks:read", "knowledge:read"})
    principal = replace(
        current_principal,
        user_id=user.id,
        api_key=ApiKeyGrant(
            key_id=uuid4(),
            label="Тестовый клиент",
            key_prefix="12345678",
            owner_user_id=user.id,
            scopes=all_scopes,
        ),
    )
    # Usage telemetry has its own session factory; keep this test entirely in its PostgreSQL fixture.
    monkeypatch.setattr(ApiKeyUsageMiddleware, "_schedule", lambda *args: None)
    auth = AsyncMock(spec=AuthService)
    auth.resolve_principal.return_value = principal
    monkeypatch.delitem(app.dependency_overrides, get_principal)
    monkeypatch.delitem(app.dependency_overrides, require_project_access)
    monkeypatch.setitem(app.dependency_overrides, get_auth_service, lambda: auth)
    monkeypatch.setitem(app.dependency_overrides, get_db_session, lambda: db_session)
    token = set_request_actor(None)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            path = f"/api/v1/projects/{project.id}/history"
            initial = await client.get(path + "/version")
            assert initial.status_code == 200 and initial.json() == {"revision": "0"}
            row = await task(db_session, project, stage)
            current = (await client.get(path + "/version")).json()["revision"]
            assert int(current) > 0
            response = await client.get(path)
            assert response.status_code == 200 and response.json()["revision"] == current
            nested = await db_session.begin_nested()
            await db_session.execute(
                update(Task).where(Task.id == row.id).values(title="Отменяемая правка")
            )
            assert int((await client.get(path + "/version")).json()["revision"]) > int(current)
            await nested.rollback()
            assert (await client.get(path + "/version")).json() == {"revision": current}
            for missing in all_scopes:
                auth.resolve_principal.return_value = replace(
                    principal, api_key=replace(principal.api_key, scopes=all_scopes - {missing})
                )
                for suffix in ("", "/version"):
                    response = await client.get(path + suffix)
                    assert response.status_code == 403, (missing, suffix, response.text)
            auth.resolve_principal.return_value = principal
            await db_session.execute(
                delete(ProjectMember).where(
                    ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
                )
            )
            for suffix in ("", "/version"):
                assert (await client.get(path + suffix)).status_code == 404
    finally:
        reset_request_actor(token)


async def test_history_version_changes_when_lower_event_id_commits_after_a_higher_one(engine):
    """Sequence order is not commit order: every committed action invalidates history."""
    project_id = None
    user_id = None
    suffix = uuid4().hex
    try:
        async with AsyncSession(engine, expire_on_commit=False) as setup:
            owner = User(
                username=f"history-race-{suffix}",
                password_hash="!",
                first_name="Тест",
                last_name="Версии",
                is_active=True,
            )
            setup.add(owner)
            await setup.flush()
            user_id = owner.id
            project_row = Project(
                owner_id=user_id,
                key=f"V{suffix[:9]}".upper(),
                name="Commit order",
                status="ACTIVE",
                color="#123456",
                order_index=0,
            )
            setup.add(project_row)
            await setup.flush()
            project_id = project_row.id
            await setup.commit()

        # Independent connections see only committed rows. The slower writer reserves
        # the lower ID first; the other writer commits a higher ID before it.
        async with (
            AsyncSession(engine) as slow,
            AsyncSession(engine) as fast,
            AsyncSession(engine) as poller,
        ):
            repository = ProjectHistoryRepository(poller)
            before = await repository.version(project_id)
            await slow.execute(text("SET LOCAL statement_timeout='10s'"))
            await fast.execute(text("SET LOCAL statement_timeout='10s'"))
            # Invoke the real trigger emitter directly: domain source updates also
            # acquire the unrelated shared knowledge-generation lock for a project.
            await slow.execute(
                text(
                    "SELECT project_history_emit(:pid, 'project_activity', 'PROJECT_UPDATED', 'Медленный commit')"
                ),
                {"pid": project_id},
            )
            low_id = await slow.scalar(
                select(ProjectHistoryEvent.id).where(
                    ProjectHistoryEvent.project_id == project_id,
                    ProjectHistoryEvent.title == "Медленный commit",
                )
            )
            await fast.execute(
                text(
                    "SELECT project_history_emit(:pid, 'project_activity', 'PROJECT_UPDATED', 'Быстрый commit')"
                ),
                {"pid": project_id},
            )
            high_id = await fast.scalar(
                select(ProjectHistoryEvent.id).where(
                    ProjectHistoryEvent.project_id == project_id,
                    ProjectHistoryEvent.title == "Быстрый commit",
                )
            )
            assert low_id < high_id
            await fast.commit()
            first_poll = await repository.version(project_id)
            assert first_poll > before
            first_page = await repository.read(project_id, ProjectHistoryQuery(limit=100))
            assert any(item.id == f"project_event:{high_id}" for item in first_page)
            assert all(item.id != f"project_event:{low_id}" for item in first_page)
            await slow.commit()
            second_poll = await repository.version(project_id)
            assert second_poll > first_poll
            second_page = await repository.read(project_id, ProjectHistoryQuery(limit=100))
            assert {f"project_event:{low_id}", f"project_event:{high_id}"} <= {
                item.id for item in second_page
            }
    finally:
        async with engine.begin() as cleanup:
            if project_id is not None:
                await cleanup.execute(delete(Project).where(Project.id == project_id))
            if user_id is not None:
                await cleanup.execute(delete(User).where(User.id == user_id))


async def test_updating_task_assignments_records_only_the_actual_role_delta(
    db_session,
    project,
    stage,
    user,
    tmp_path,
):
    """Повтор форм не снимает роли; добавление наблюдателя не переписывает постановщика."""
    newcomer = User(
        username="new-history-observer",
        password_hash="!",
        first_name="Новый",
        last_name="Наблюдатель",
        is_active=True,
    )
    db_session.add(newcomer)
    await db_session.flush()
    db_session.add_all(
        [
            ProjectMember(project_id=project.id, user_id=user.id, role="OWNER"),
            ProjectMember(project_id=project.id, user_id=newcomer.id, role="MEMBER"),
        ]
    )
    await db_session.flush()
    service, _ = services(db_session, tmp_path)
    created = await service.create_task(
        project_id=project.id,
        data={
            "title": "Роли без ложной истории",
            "reporter_id": user.id,
            "observer_ids": [user.id],
        },
        created_by_user_id=user.id,
    )
    assignments = list(
        (
            await db_session.scalars(
                select(TaskParticipant).where(TaskParticipant.task_id == created.id)
            )
        ).all()
    )
    initial_assignment_ids = {assignment.id for assignment in assignments}
    before = {item.public_id for item in await events(db_session, project.id)}
    await service.update_task(
        created.id, {"reporter_id": user.id, "observer_ids": [user.id]}, updated_by_user_id=user.id
    )
    assert {item.public_id for item in await events(db_session, project.id)} == before
    assert (
        set(
            (
                await db_session.scalars(
                    select(TaskParticipant.id).where(TaskParticipant.task_id == created.id)
                )
            ).all()
        )
        == initial_assignment_ids
    )

    await service.update_task(
        created.id,
        {"reporter_id": user.id, "observer_ids": [user.id, newcomer.id]},
        updated_by_user_id=user.id,
    )
    delta = [item for item in await events(db_session, project.id) if item.public_id not in before]
    assert len(delta) == 1 and delta[0].event_type == "TASK_PARTICIPANT_CREATED"
    final_assignments = list(
        (
            await db_session.scalars(
                select(TaskParticipant).where(TaskParticipant.task_id == created.id)
            )
        ).all()
    )
    assert initial_assignment_ids < {assignment.id for assignment in final_assignments}
    assert len(final_assignments) == len(assignments) + 1
    assert (
        delta[0].entity_id
        in {assignment.id for assignment in final_assignments} - initial_assignment_ids
    )


async def test_task_service_records_one_semantic_fact_before_updating_each_task_field(
    db_session,
    project,
    stage,
    user,
    tmp_path,
):
    """Порядок настоящего сервиса activity→UPDATE не добавляет сырые дубликаты."""
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="OWNER"))
    await db_session.flush()
    service, _ = services(db_session, tmp_path)
    created = await service.create_task(
        project.id,
        {
            "title": "Составное изменение задачи",
            "stage_id": stage.id,
            "description_md": "Первоначальное описание",
            "priority": "MEDIUM",
            "start_date": date(2026, 12, 1),
            "due_date": date(2026, 12, 2),
        },
        created_by_user_id=user.id,
    )
    previous_ids = {item.public_id for item in await events(db_session, project.id)}
    token = set_request_actor(RequestActor(user.id, "Автор составной правки"))
    try:
        updated = await service.update_task(
            created.id,
            {
                "title": "Уточнённая задача",
                "start_date": date(2026, 12, 3),
                "due_date": date(2026, 12, 4),
                "date_change_reason": "Согласована новая поставка",
                "description_md": "Уточнённое описание",
                "priority": "HIGH",
                "checklist_revision": created.checklist_revision,
                "checklist": {
                    "title": "Приёмка",
                    "items": [
                        {"id": str(uuid4()), "text": "Проверить результат", "is_completed": False}
                    ],
                },
            },
            updated_by_user_id=user.id,
        )
    finally:
        reset_request_actor(token)
    delta = [
        item for item in await events(db_session, project.id) if item.public_id not in previous_ids
    ]
    assert Counter(item.event_type for item in delta) == {
        "DATES_CHANGED": 1,
        "DESCRIPTION_CHANGED": 1,
        "PRIORITY_CHANGED": 1,
        "CHECKLIST_CHANGED": 1,
        "TITLE_CHANGED": 1,
    }
    assert all(
        item.public_id.startswith("task_activity:")
        for item in delta
        if item.event_type != "TITLE_CHANGED"
    )
    assert all(
        (item.actor_user_id, item.actor_name) == (user.id, "Автор составной правки")
        for item in delta
    )
    dates = next(item for item in delta if item.event_type == "DATES_CHANGED")
    assert dates.from_value == "начало: 2026-12-01; завершение: 2026-12-02"
    assert dates.to_value == "начало: 2026-12-03; завершение: 2026-12-04"
    assert dates.detail.count("Согласована новая поставка") == 1
    assert updated.priority.value == "HIGH" and updated.checklist.title == "Приёмка"
    assert updated.description_md == "Уточнённое описание"

    # A consumed canonical event cannot suppress later independent SQL in the same
    # database transaction (the fixture's service commits release savepoints).
    semantic_ids = {item.public_id for item in await events(db_session, project.id)}
    await db_session.execute(
        update(Task).where(Task.id == created.id).values(priority="LOW", due_date=date(2026, 12, 5))
    )
    later = [
        item for item in await events(db_session, project.id) if item.public_id not in semantic_ids
    ]
    assert Counter(item.event_type for item in later) == {
        "PRIORITY_CHANGED": 1,
        "DUE_DATE_CHANGED": 1,
    }
    assert all(item.public_id.startswith("project_event:") for item in later)
    assert (
        next(item for item in later if item.event_type == "DUE_DATE_CHANGED").to_value
        == "2026-12-05"
    )


async def test_old_comments_edited_and_deleted_after_committed_transfer_stay_in_their_original_scope(
    agent_env,  # noqa: F811 -- pytest injects the imported fixture by its name.
    tmp_path,
):
    """После commit переноса правки/удаления не импортируют прежний текст и автора."""
    env = agent_env
    async with env.factory() as setup:
        stages = [
            ProjectStage(
                project_id=pid, name="Работа", color="#123456", order_index=0, is_done_stage=False
            )
            for pid in (1, 2)
        ]
        setup.add_all(stages)
        await setup.flush()
        stage_ids = {stage.project_id: stage.id for stage in stages}
        await setup.commit()

    token = set_request_actor(None)
    try:
        for origin in ("project", "personal", "sql_transfer"):
            async with env.factory() as session:
                task_service, transfers = services(session, tmp_path)
                comments = TaskCommentsService(
                    comments_repository=task_service.comments_repository,
                    tasks_repository=task_service.tasks_repository,
                    activity_repository=task_service.activity_repository,
                    unit_of_work=task_service.unit_of_work,
                    knowledge_events=task_service.knowledge_events,
                    users_repository=task_service.users_repository,
                )
                if origin == "personal":
                    moved = await task_service.create_personal_task(
                        data={"title": "Перенос личной задачи"}, created_by_user_id=1
                    )
                else:
                    moved = await task_service.create_task(
                        1,
                        {"title": f"Перенос {origin}", "stage_id": stage_ids[1]},
                        created_by_user_id=1,
                    )
                previous_author = f"Прежний автор {origin}"
                old_body = f"Прежний текст {origin}, не переносить в журнал цели"
                deleted_body = f"Неизменённый прежний текст {origin}"
                old_report_body = f"Прежний отчёт {origin}"
                edited = await comments.add_comment(
                    moved.id, author_name=previous_author, body_md=old_body
                )
                untouched = await comments.add_comment(
                    moved.id, author_name=previous_author, body_md=deleted_body
                )
                report = TaskReport(
                    task_id=moved.id,
                    revision=1,
                    body_md=old_report_body,
                    author_name=previous_author,
                )
                session.add(report)
                await session.commit()
                source_snapshots = {
                    item.public_id: (item.title, item.detail, item.actor_name)
                    for item in await events(session, 1)
                    if item.task_id == moved.id
                }
                if origin == "sql_transfer":
                    await session.execute(
                        update(Task)
                        .where(Task.id == moved.id)
                        .values(project_id=2, stage_id=stage_ids[2], number=99)
                    )
                    await session.commit()
                else:
                    await transfers.move(
                        task_id=moved.id, target=TaskTransferTargetSchema(project_id=2), user_id=1
                    )
                after_move = await ProjectHistoryRepository(session).read(
                    2, ProjectHistoryQuery(task_id=moved.id, limit=100)
                )
                old_ids = {
                    f"task_created:{moved.id}",
                    f"task_comment:{edited.id}",
                    f"task_comment:{untouched.id}",
                    f"task_report:{report.id}",
                }
                assert old_ids.isdisjoint(item.id for item in after_move)
                assert all(
                    value not in str(after_move)
                    for value in (old_body, deleted_body, old_report_body, previous_author)
                )
                await session.execute(
                    update(Task)
                    .where(Task.id == moved.id)
                    .values(title=f"Новый заголовок {origin}")
                )
                await session.commit()
                # Each service commits a real transaction: a transaction-local
                # transfer marker cannot accidentally conceal the regression.
                actor = set_request_actor(RequestActor(1, "Автор действия после переноса"))
                try:
                    await comments.update_comment(
                        edited.id,
                        body_md=f"Новая публичная правка {origin}",
                        expected_body_md=old_body,
                    )
                    await comments.delete_comment(edited.id)
                    await comments.delete_comment(untouched.id)
                    await session.execute(delete(TaskReport).where(TaskReport.id == report.id))
                    await session.commit()
                finally:
                    reset_request_actor(actor)
                target = [item for item in await events(session, 2) if item.task_id == moved.id]
                assert f"task_created:{moved.id}" not in {item.public_id for item in target}
                assert any(item.event_type == "TITLE_CHANGED" for item in target)
                assert not {
                    f"task_comment:{edited.id}",
                    f"task_comment:{untouched.id}",
                    f"task_report:{report.id}",
                } & {item.public_id for item in target}
                assert all(
                    value
                    not in str([(item.title, item.detail, item.actor_name) for item in target])
                    for value in (old_body, deleted_body, old_report_body, previous_author)
                )
                assert Counter(
                    item.event_type
                    for item in target
                    if item.event_type in {"COMMENT_EDITED", "COMMENT_DELETED"}
                ) == {"COMMENT_EDITED": 1, "COMMENT_DELETED": 2}
                assert all(
                    item.actor_name == "Автор действия после переноса"
                    for item in target
                    if item.event_type in {"COMMENT_EDITED", "COMMENT_DELETED"}
                )
                assert {
                    item.public_id: (item.title, item.detail, item.actor_name)
                    for item in await events(session, 1)
                    if item.public_id in source_snapshots
                } == source_snapshots
                visible = await ProjectHistoryRepository(session).read(
                    2, ProjectHistoryQuery(task_id=moved.id, limit=100)
                )
                assert all(
                    value not in str(visible)
                    for value in (old_body, deleted_body, old_report_body, previous_author)
                )

                # New comments belong to the target and keep their original
                # canonical snapshot when edited in a later transaction.
                new_body = f"Публичный комментарий после переноса {origin}"
                public = await comments.add_comment(
                    moved.id, author_name="Публичный автор", body_md=new_body
                )
                await comments.update_comment(
                    public.id, body_md="Уточнённый публичный текст", expected_body_md=new_body
                )
                saved = [
                    item
                    for item in await events(session, 2)
                    if item.public_id == f"task_comment:{public.id}"
                ]
                assert (
                    len(saved) == 1
                    and saved[0].detail == new_body
                    and saved[0].actor_name == "Публичный автор"
                )
    finally:
        reset_request_actor(token)
