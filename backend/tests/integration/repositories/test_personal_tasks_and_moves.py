from unittest.mock import AsyncMock

from sqlalchemy import select

from src.db.models.task_attachments import TaskAttachment
from src.db.models.task_comments import TaskComment
from src.db.models.task_participants import TaskParticipant, TaskParticipantRole
from src.db.models.tasks import Task
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.task_reports import TaskReportsRepository
from src.repositories.task_transfers import TaskTransfersRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.repositories.wbs_nodes import WbsNodesRepository
from src.schemas.enums import TaskKind, TaskStatus
from src.schemas.task_transfers import TaskTransferTargetSchema
from src.services.access import AccessService
from src.services.knowledge_events import KnowledgeEvents
from src.services.task_transfers import TaskTransfersService
from src.services.tasks import MY_TASKS_LIMIT, TasksService
from src.storage.task_attachments import TaskAttachmentStorage


def services(session, tmp_path):
    tasks = TasksRepository(session)
    participants = TaskParticipantsRepository(session)
    members = ProjectMembersRepository(session)
    projects = ProjectsRepository(session)
    stages = ProjectStagesRepository(session)
    activity = TaskActivityRepository(session)
    users = UsersRepository(session)
    knowledge = AsyncMock(spec=KnowledgeEvents)
    uow = UnitOfWork(session)
    task_service = TasksService(
        tasks_repository=tasks, participants_repository=participants, members_repository=members,
        projects_repository=projects, stages_repository=stages, activity_repository=activity,
        comments_repository=TaskCommentsRepository(session), wbs_nodes_repository=WbsNodesRepository(session),
        unit_of_work=uow, attachment_storage=TaskAttachmentStorage(tmp_path), knowledge_events=knowledge,
        users_repository=users, reports_repository=TaskReportsRepository(session),
    )
    moves = TaskTransfersService(
        tasks_repository=tasks, task_transfers_repository=TaskTransfersRepository(session),
        participants_repository=participants, members_repository=members, projects_repository=projects,
        stages_repository=stages, activity_repository=activity, users_repository=users,
        unit_of_work=uow, knowledge_events=knowledge, access_service=AccessService(members_repository=members, tasks_repository=tasks, stages_repository=stages, documents_repository=DocumentsRepository(session), comments_repository=TaskCommentsRepository(session), links_repository=DocumentLinksRepository(session)),
    )
    return task_service, moves


async def test_personal_creation_status_and_my_tasks_use_local_assignments(db_session, user, tmp_path):
    service, _ = services(db_session, tmp_path)
    task = await service.create_personal_task(
        data={"title": "Личное дело", "executor_id": user.id}, created_by_user_id=user.id,
    )
    assert task.key == "T-1" and task.kind is TaskKind.PERSONAL
    assert task.project_id is task.stage_id is task.wbs_node_id is None
    assert task.status is TaskStatus.IN_PROGRESS
    assert {person.role for person in task.participants} == {TaskParticipantRole.REPORTER, TaskParticipantRole.EXECUTOR}
    assert (await service.get_my_tasks(user_id=user.id, role="doing"))[0].id == task.id
    done = await service.change_status(task_id=task.id, status=TaskStatus.DONE)
    assert done.completed_at is not None
    assert await service.get_my_tasks(user_id=user.id) == []
    assert (await service.get_my_tasks(user_id=user.id, include_closed=True))[0].state is TaskStatus.DONE


async def test_my_tasks_project_filter_applies_before_the_500_task_limit(
    db_session, project, stage, user, tmp_path
):
    """Задача выбранного проекта находится за пределами общего ограниченного списка."""
    service, _ = services(db_session, tmp_path)
    await ProjectMembersRepository(db_session).save(
        data={"project_id": project.id, "user_id": user.id, "role": "OWNER"}
    )
    target = await service.create_task(
        project_id=project.id,
        data={"title": "Задача выбранного проекта"},
        created_by_user_id=user.id,
    )
    personal = [
        Task(number=number, title=f"Разовая {number}", status=TaskStatus.NEW, priority="MEDIUM")
        for number in range(1, MY_TASKS_LIMIT + 1)
    ]
    db_session.add_all(personal)
    await db_session.flush()
    db_session.add_all(
        TaskParticipant(task_id=task.id, user_id=user.id, role=TaskParticipantRole.OBSERVER)
        for task in personal
    )
    await db_session.flush()
    common = await service.get_my_tasks(user_id=user.id)
    assert len(common) == MY_TASKS_LIMIT
    assert target.id not in {task.id for task in common}
    selected = await service.get_my_tasks(user_id=user.id, project_id=project.id)
    assert [task.id for task in selected] == [target.id]
    assert selected[0].context.id == project.id
    reset = await service.get_my_tasks(user_id=user.id)
    assert {task.id for task in reset} == {task.id for task in common}
    assert all(task.context is None for task in reset)


async def test_my_tasks_project_filter_preserves_participation_and_other_filters(
    db_session, project, stage, user, tmp_path
):
    """Проект не расширяет доступ и сочетается с ролями, закрытыми задачами и событиями."""
    service, _ = services(db_session, tmp_path)
    members = ProjectMembersRepository(db_session)
    owner = await members.save(
        data={"project_id": project.id, "user_id": user.id, "role": "OWNER"}
    )
    other = await UsersRepository(db_session).save(
        data={
            "username": "other", "password_hash": "hash", "is_active": True,
            "last_name": "Другов", "first_name": "Другой",
        }
    )
    other_member = await members.save(
        data={"project_id": project.id, "user_id": other.id, "role": "MEMBER"}
    )
    second = await ProjectsRepository(db_session).save(
        data={"owner_id": user.id, "key": "SECOND", "name": "Второй проект", "color": "#58a6ff"}
    )
    second_member = await members.save(
        data={"project_id": second.id, "user_id": user.id, "role": "OWNER"}
    )
    second_stage = await ProjectStagesRepository(db_session).save(
        data={"project_id": second.id, "name": "В работе", "order_index": 1, "color": "#58a6ff"}
    )
    private = await ProjectsRepository(db_session).save(
        data={"owner_id": other.id, "key": "PRIVATE", "name": "Чужой проект", "color": "#58a6ff"}
    )
    private_member = await members.save(
        data={"project_id": private.id, "user_id": other.id, "role": "OWNER"}
    )
    private_stage = await ProjectStagesRepository(db_session).save(
        data={"project_id": private.id, "name": "В работе", "order_index": 1, "color": "#58a6ff"}
    )
    done_stage = await ProjectStagesRepository(db_session).save(
        data={"project_id": project.id, "name": "Готово", "order_index": 2, "is_done_stage": True, "color": "#58a6ff"}
    )
    doing, assigned, watching, closed, unrelated = [
        Task(
            project_id=project.id,
            stage_id=done_stage.id if number == 4 else stage.id,
            number=number,
            title=f"Проектная {number}",
            priority="MEDIUM",
        )
        for number in range(1, 6)
    ]
    second_task = Task(
        project_id=second.id, stage_id=second_stage.id, number=1,
        title="Задача второго проекта", priority="MEDIUM",
    )
    private_task = Task(
        project_id=private.id, stage_id=private_stage.id, number=1,
        title="Чужая задача", priority="MEDIUM",
    )
    personal = Task(number=1, title="Разовая", status=TaskStatus.NEW, priority="MEDIUM")
    db_session.add_all([
        doing, assigned, watching, closed, unrelated, second_task, private_task, personal,
    ])
    await db_session.flush()
    db_session.add_all([
        TaskParticipant(task_id=doing.id, project_member_id=owner.id, role=TaskParticipantRole.EXECUTOR),
        TaskParticipant(task_id=assigned.id, project_member_id=owner.id, role=TaskParticipantRole.REPORTER),
        TaskParticipant(task_id=watching.id, project_member_id=owner.id, role=TaskParticipantRole.OBSERVER),
        TaskParticipant(task_id=closed.id, project_member_id=owner.id, role=TaskParticipantRole.EXECUTOR),
        TaskParticipant(task_id=unrelated.id, project_member_id=other_member.id, role=TaskParticipantRole.EXECUTOR),
        TaskParticipant(task_id=second_task.id, project_member_id=second_member.id, role=TaskParticipantRole.EXECUTOR),
        TaskParticipant(task_id=private_task.id, project_member_id=private_member.id, role=TaskParticipantRole.EXECUTOR),
        TaskParticipant(task_id=personal.id, user_id=user.id, role=TaskParticipantRole.EXECUTOR),
    ])
    await db_session.flush()
    expected_by_role = {
        "all": {doing.id, assigned.id, watching.id},
        "doing": {doing.id},
        "assigned": {assigned.id},
        "watching": {watching.id},
    }
    for role, expected in expected_by_role.items():
        rows = await service.get_my_tasks(user_id=user.id, project_id=project.id, role=role)
        assert {task.id for task in rows} == expected
    rows = await service.get_my_tasks(
        user_id=user.id, project_id=project.id, role="doing", include_closed=True,
        attention_task_ids={closed.id, assigned.id, second_task.id, unrelated.id, private_task.id},
    )
    assert [task.id for task in rows] == [closed.id]
    assert await service.get_my_tasks(
        user_id=user.id, project_id=project.id, attention_task_ids=set(),
    ) == []
    assert await service.get_my_tasks(user_id=user.id, project_id=private.id) == []
    assert await service.get_my_tasks(user_id=user.id, project_id=private.id + 1000) == []
    rows = await service.get_my_tasks(user_id=user.id, project_id=second.id)
    assert [task.id for task in rows] == [second_task.id]
    common = await service.get_my_tasks(user_id=user.id, include_closed=True)
    assert {task.id for task in common} == {
        doing.id, assigned.id, watching.id, closed.id, second_task.id, personal.id,
    }


async def test_move_clears_wbs_and_preserves_files_comments_report(db_session, project, stage, user, tmp_path):
    service, moves = services(db_session, tmp_path)
    await ProjectMembersRepository(db_session).save(data={"project_id": project.id, "user_id": user.id, "role": "OWNER"})
    node = await WbsNodesRepository(db_session).save(data={"project_id": project.id, "title": "Раздел", "position": 1000})
    task = await service.create_task(project_id=project.id, data={"title": "Задача с итогом", "wbs_node_id": node.id}, created_by_user_id=user.id)
    storage = TaskAttachmentStorage(tmp_path)
    key = await storage.save(task.id, ".txt", b"saved attachment")
    db_session.add_all([
        TaskComment(task_id=task.id, author_id=user.id, author_name="Автор", body_md="Комментарий"),
        TaskAttachment(task_id=task.id, original_name="result.txt", storage_key=key, content_type="text/plain", size=16),
    ])
    await TaskReportsRepository(db_session).append({"task_id": task.id, "revision": 1, "body_md": "Итог работы", "author_id": user.id, "author_name": "Автор"})
    await db_session.commit()
    target = TaskTransferTargetSchema(personal=True)
    preview = await moves.preview(task_id=task.id, target=target, user_id=user.id)
    assert preview.losses.wbs_placements == 1
    moved = await moves.move(task_id=task.id, target=target, user_id=user.id)
    assert moved.task.id == task.id and moved.task.kind is TaskKind.PERSONAL
    row = await TasksRepository(db_session).get_by_id(task.id)
    assert (row.wbs_node_id, row.wbs_position, row.canvas_x, row.canvas_y) == (None, None, None, None)
    detail = await service.get_task(task.id)
    assert detail.report.body_md == "Итог работы"
    assert len(await TaskCommentsRepository(db_session).get_for_task(task.id)) == 1
    attachment = (await db_session.execute(select(TaskAttachment).where(TaskAttachment.task_id == task.id))).scalar_one()
    assert attachment.storage_key == key and storage.resolve(key).read_bytes() == b"saved attachment"
    returned = await moves.move(task_id=task.id, target=TaskTransferTargetSchema(project_id=project.id), user_id=user.id)
    assert returned.task.project_id == project.id and returned.task.status is None
    assert returned.task.wbs_node_id is None
