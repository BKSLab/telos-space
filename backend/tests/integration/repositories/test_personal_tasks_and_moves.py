from unittest.mock import AsyncMock

from sqlalchemy import select

from src.db.models.task_attachments import TaskAttachment
from src.db.models.task_comments import TaskComment
from src.db.models.task_participants import TaskParticipantRole
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
from src.services.tasks import TasksService
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
