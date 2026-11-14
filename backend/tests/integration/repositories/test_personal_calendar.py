from datetime import date

import pytest

from src.db.models.project_members import ProjectMember
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.task_participants import TaskParticipant
from src.db.models.tasks import Task
from src.db.models.users import User
from src.exceptions.calendar_feed_tokens import CalendarFeedNotFoundError
from src.repositories.calendar_feed_tokens import CalendarFeedTokensRepository
from src.repositories.milestones import MilestonesRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.users import UsersRepository
from src.services.calendar_feed import CalendarFeedService
from src.services.calendar_workload import CalendarWorkloadService
from src.services.personal_calendar import PersonalCalendarService

TODAY = date(2026, 10, 5)
PERIOD = {"date_from": date(2026, 10, 1), "date_to": date(2026, 10, 31), "today": TODAY}


def calendar(session):
    return PersonalCalendarService(
        users_repository=UsersRepository(session),
        members_repository=ProjectMembersRepository(session),
        projects_repository=ProjectsRepository(session),
        stages_repository=ProjectStagesRepository(session),
        tasks_repository=TasksRepository(session),
        milestones_repository=MilestonesRepository(session),
        participants_repository=TaskParticipantsRepository(session),
    )


async def test_personal_calendar_scope_portfolio_and_revocable_feed(
    db_session, project, stage, user
):
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="OWNER"))
    mine = Task(number=1, title="Мой срок", status="IN_PROGRESS", start_date=TODAY, due_date=TODAY)
    other_role = Task(number=2, title="Поручено другому", status="NEW", due_date=TODAY)
    undated = Task(number=3, title="Без дат", status="NEW")
    project_task = Task(
        project_id=project.id, stage_id=stage.id, number=1, title="Проектная", due_date=TODAY
    )
    db_session.add_all([mine, other_role, undated, project_task])
    await db_session.flush()
    member = (await ProjectMembersRepository(db_session).get_for_project(project.id))[0]
    db_session.add_all(
        [
            TaskParticipant(task_id=mine.id, user_id=user.id, role="EXECUTOR"),
            TaskParticipant(task_id=other_role.id, user_id=user.id, role="REPORTER"),
            TaskParticipant(task_id=undated.id, user_id=user.id, role="EXECUTOR"),
            TaskParticipant(task_id=project_task.id, project_member_id=member.id, role="EXECUTOR"),
        ]
    )
    await db_session.commit()
    service = calendar(db_session)
    result = await service.get_my_range(user_id=user.id, **PERIOD)
    assert {task.title for task in result.tasks} == {"Мой срок", "Проектная"}
    assert result.summary.unscheduled == 1
    assert (
        await service.get_my_unscheduled(user_id=user.id, today=TODAY, cursor=None, limit=10)
    ).items[0].id == undated.id
    portfolio = await service.get_portfolio(user_id=user.id, **PERIOD)
    assert {item.id for item in portfolio.projects} == {project.id}
    feed = CalendarFeedService(
        tokens_repository=CalendarFeedTokensRepository(db_session),
        users_repository=UsersRepository(db_session),
        calendar_service=service,
    )
    issued = await feed.issue(user.id)
    body = await feed.render_feed(secret=issued.secret, app_url="https://tracker.test", today=TODAY)
    assert "Мой срок" in body and "Поручено другому" not in body
    assert f"https://tracker.test/my-tasks?task={mine.id}" in body
    await feed.revoke(user.id)
    with pytest.raises(CalendarFeedNotFoundError):
        await feed.render_feed(secret=issued.secret, app_url="https://tracker.test", today=TODAY)


async def test_workload_counts_hidden_sources_without_revealing_titles(
    db_session, project, stage, user
):
    worker = User(
        username="worker", password_hash="test", last_name="Исполнителей", first_name="Иван"
    )
    db_session.add(worker)
    await db_session.flush()
    secret = Project(
        owner_id=worker.id, key="SECRET", name="Скрытый проект", status="ACTIVE", color="#58a6ff"
    )
    db_session.add(secret)
    await db_session.flush()
    secret_stage = ProjectStage(
        project_id=secret.id, name="Работа", order_index=0, color="#58a6ff", is_done_stage=False
    )
    own_member = ProjectMember(project_id=project.id, user_id=worker.id, role="MEMBER")
    secret_member = ProjectMember(project_id=secret.id, user_id=worker.id, role="OWNER")
    db_session.add_all(
        [
            secret_stage,
            own_member,
            secret_member,
            ProjectMember(project_id=project.id, user_id=user.id, role="OWNER"),
        ]
    )
    await db_session.flush()
    own = Task(project_id=project.id, stage_id=stage.id, number=1, title="Видимая", due_date=TODAY)
    hidden = Task(
        project_id=secret.id,
        stage_id=secret_stage.id,
        number=1,
        title="Не раскрывать",
        due_date=TODAY,
    )
    db_session.add_all([own, hidden])
    await db_session.flush()
    db_session.add_all(
        [
            TaskParticipant(task_id=own.id, project_member_id=own_member.id, role="EXECUTOR"),
            TaskParticipant(task_id=hidden.id, project_member_id=secret_member.id, role="EXECUTOR"),
        ]
    )
    await db_session.commit()
    service = CalendarWorkloadService(
        projects_repository=ProjectsRepository(db_session),
        members_repository=ProjectMembersRepository(db_session),
        users_repository=UsersRepository(db_session),
        tasks_repository=TasksRepository(db_session),
    )
    result = await service.get_workload(
        project_id=project.id, viewer_id=user.id, date_from=TODAY, date_to=TODAY
    )
    person = next(item for item in result.people if item.user_id == worker.id)
    assert person.days[0].project_tasks == person.days[0].other_tasks == 1
    assert person.days[0].level == 2 and person.days[0].other_keys == []
    assert person.other_hidden == 1 and person.other_tasks == []
    assert "Не раскрывать" not in result.model_dump_json()
