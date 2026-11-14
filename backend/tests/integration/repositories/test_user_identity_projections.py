"""Публичные identities происходят из сохранённых FK и назначений, а не из подписи."""

from datetime import date
from unittest.mock import AsyncMock

from src.db.models.project_members import ProjectMember
from src.db.models.task_participants import TaskParticipant, TaskParticipantRole
from src.db.models.tasks import Task
from src.db.models.users import User
from src.repositories.analytics_reports import AnalyticsReportsRepository
from src.repositories.knowledge_index_jobs import KnowledgeIndexJobsRepository
from src.repositories.milestones import MilestonesRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_dependencies import TaskDependenciesRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.task_reports import TaskReportsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.repositories.wbs_nodes import WbsNodesRepository
from src.schemas.task_reports import TaskReportSchema
from src.services.analytics import _to_report_schema
from src.services.calendar import CalendarService
from src.services.knowledge_events import KnowledgeEvents
from src.services.personal_calendar import PersonalCalendarService
from src.services.wbs_nodes import WbsNodesService

TODAY = date(2026, 11, 15)
PERIOD = {"date_from": TODAY, "date_to": TODAY, "today": TODAY}


async def test_calendar_and_wbs_executors_use_accessible_batch_assignments(
    db_session, project, stage, user
):
    executor = User(
        username="assigned-person",
        password_hash="hash",
        first_name="Другой",
        last_name="Исполнитель",
        avatar_key="assigned/photo.png",
        email="private@example.test",
    )
    db_session.add(executor)
    await db_session.flush()
    member = ProjectMember(project_id=project.id, user_id=executor.id, role="MEMBER")
    db_session.add(member)
    await db_session.flush()
    assigned, freeform, undated = [
        Task(
            project_id=project.id,
            stage_id=stage.id,
            number=number,
            title=title,
            assignee="Исполнитель Другой",
            due_date=due,
        )
        for number, title, due in (
            (1, "С назначением", TODAY),
            (2, "Только подпись", TODAY),
            (3, "Без срока", None),
        )
    ]
    personal = Task(number=1, title="Личная назначенная", status="NEW", due_date=TODAY)
    personal_undated = Task(number=2, title="Личная без срока", status="NEW")
    inaccessible = Task(number=3, title="Чужая личная", status="NEW", due_date=TODAY)
    db_session.add_all([assigned, freeform, undated, personal, personal_undated, inaccessible])
    await db_session.flush()
    db_session.add_all(
        [
            TaskParticipant(
                task_id=item.id,
                project_member_id=member.id,
                role=TaskParticipantRole.EXECUTOR,
            )
            for item in (assigned, undated)
        ]
        + [
            TaskParticipant(task_id=item.id, user_id=executor.id, role=TaskParticipantRole.EXECUTOR)
            for item in (personal, personal_undated)
        ]
        + [
            TaskParticipant(
                task_id=inaccessible.id, user_id=user.id, role=TaskParticipantRole.EXECUTOR
            )
        ]
    )
    await db_session.commit()
    participants = TaskParticipantsRepository(db_session)
    participants.get_by_task_ids = AsyncMock(wraps=participants.get_by_task_ids)
    projects, tasks = ProjectsRepository(db_session), TasksRepository(db_session)
    stages, nodes = ProjectStagesRepository(db_session), WbsNodesRepository(db_session)
    activity, milestones = TaskActivityRepository(db_session), MilestonesRepository(db_session)
    calendar = CalendarService(
        projects,
        tasks,
        stages,
        nodes,
        activity,
        milestones,
        TaskDependenciesRepository(db_session),
        participants,
    )
    page = await calendar.get_range(project_id=project.id, **PERIOD)
    by_id = {item.id: item for item in page.tasks}
    identity = by_id[assigned.id].executor
    assert identity.id == executor.id and identity.has_avatar
    assert set(identity.model_dump()) == {
        "id",
        "username",
        "first_name",
        "last_name",
        "middle_name",
        "has_avatar",
    }
    assert by_id[freeform.id].executor is None
    participants.get_by_task_ids.assert_awaited_once_with([assigned.id, freeform.id])
    participants.get_by_task_ids.reset_mock()
    page = await calendar.get_unscheduled(project_id=project.id, today=TODAY, cursor=None, limit=1)
    assert page.items[0].id == undated.id and page.items[0].executor.id == executor.id
    participants.get_by_task_ids.assert_awaited_once_with([undated.id])

    wbs = WbsNodesService(
        nodes,
        projects,
        stages,
        tasks,
        activity,
        UnitOfWork(db_session),
        KnowledgeEvents(KnowledgeIndexJobsRepository(db_session), enabled=False),
        participants,
    )
    participants.get_by_task_ids.reset_mock()
    structure = await wbs.get_structure(project.id)
    assert {item.id: item.executor.id if item.executor else None for item in structure.tasks} == {
        assigned.id: executor.id,
        freeform.id: None,
        undated.id: executor.id,
    }
    assert participants.get_by_task_ids.await_count == 1
    assert (await wbs.unassign_task(project.id, assigned.id)).executor.id == executor.id

    mine = PersonalCalendarService(
        users_repository=UsersRepository(db_session),
        members_repository=ProjectMembersRepository(db_session),
        projects_repository=projects,
        stages_repository=stages,
        tasks_repository=tasks,
        milestones_repository=milestones,
        participants_repository=participants,
    )
    participants.get_by_task_ids.reset_mock()
    own_page = await mine.get_my_range(user_id=executor.id, **PERIOD)
    assert {item.id for item in own_page.tasks} == {assigned.id, personal.id}
    assert all(item.executor.id == executor.id for item in own_page.tasks)
    assert participants.get_by_task_ids.await_count == 1
    assert set(participants.get_by_task_ids.call_args.args[0]) == {assigned.id, personal.id}
    participants.get_by_task_ids.reset_mock()
    undated_page = await mine.get_my_unscheduled(
        user_id=executor.id, today=TODAY, cursor=None, limit=1
    )
    assert len(undated_page.items) == 1 and undated_page.items[0].executor.id == executor.id
    participants.get_by_task_ids.assert_awaited_once_with([undated_page.items[0].id])


async def test_saved_report_author_ids_survive_projection_without_name_matching(
    db_session, project, stage, user
):
    author = User(
        username="report-author", password_hash="hash", first_name="Автор", last_name="Отчётов"
    )
    task = Task(project_id=project.id, stage_id=stage.id, number=1, title="Результат")
    db_session.add_all([author, task])
    await db_session.flush()
    reports = TaskReportsRepository(db_session)
    row = await reports.append(
        {
            "task_id": task.id,
            "revision": 1,
            "body_md": "Готово",
            "author_id": author.id,
            "author_name": "Имя как у другого пользователя",
            "source_author_name": "Только снимок",
        }
    )
    projected = TaskReportSchema.model_validate(row)
    assert projected.author_id == author.id and projected.source_author_name == "Только снимок"
    assert TaskReportSchema(task_id=task.id).author_id is None
    signals = dict.fromkeys(
        (
            "total_tasks",
            "done_tasks",
            "overdue_tasks",
            "due_soon_tasks",
            "no_due_date_tasks",
            "unassigned_tasks",
            "stale_tasks",
            "blocked_tasks",
            "unplaced_tasks",
            "milestones_at_risk",
        ),
        0,
    )
    context = dict.fromkeys(
        (
            "projects",
            "tasks_total",
            "tasks_included",
            "comments_included",
            "documents_included",
            "stickers_included",
            "wbs_nodes_included",
            "milestones_included",
            "activity_included",
        ),
        0,
    ) | {"truncated": False}
    analytics = AnalyticsReportsRepository(db_session)
    saved = []
    for project_id in (project.id, None):
        report = await analytics.save(
            {
                "project_id": project_id,
                "created_by_user_id": author.id,
                "created_by_display_name_snapshot": "Имя как у другого пользователя",
                "llm_model": "synthetic",
                "duration_ms": 0,
                "payload": {"signals": signals},
                "context_summary": context,
            }
        )
        assert (
            _to_report_schema(report, project if project_id else None).created_by_user_id
            == author.id
        )
        saved.append(report)
    await db_session.delete(author)
    await db_session.flush()
    await db_session.refresh(row)
    assert TaskReportSchema.model_validate(row).author_id is None
    for report in saved:
        await db_session.refresh(report)
        assert (
            _to_report_schema(report, project if report.project_id else None).created_by_user_id
            is None
        )
