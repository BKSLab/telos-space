"""Проектные и разовые задачи."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.task_activity import TaskActivityEventType
from src.db.models.task_participants import TaskParticipant, TaskParticipantRole
from src.db.models.tasks import Task
from src.db.models.users import User
from src.exceptions.base import ServiceError
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.project_stages import (
    ProjectStageForeignProjectError,
    ProjectStageNotFoundError,
    ProjectStagesRepositoryError,
)
from src.exceptions.projects import ProjectNotFoundError, ProjectsRepositoryError
from src.exceptions.task_activity import TaskActivityRepositoryError
from src.exceptions.task_transfers import (
    TaskTransferOutsidersError,
    TaskTransferOwnerRequiredError,
    TaskTransferSameTargetError,
    TaskTransfersRepositoryError,
    TaskTransfersServiceError,
)
from src.exceptions.tasks import (
    TaskNotFoundError,
    TaskNumberAllocationError,
    TaskNumberAlreadyExistsRepositoryError,
    TasksRepositoryError,
)
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.exceptions.users import UsersRepositoryError
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.task_transfers import TaskProjectLinks, TaskTransfersRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.schemas.enums import TaskKind, TaskStatus
from src.schemas.task_transfers import (
    TaskTransferLossesSchema,
    TaskTransferPreviewSchema,
    TaskTransferResultSchema,
    TaskTransferTargetSchema,
)
from src.services.access import AccessService
from src.services.auth import to_user_summary
from src.services.knowledge_events import KnowledgeEvents
from src.services.tasks import (
    NUMBER_ALLOCATION_ATTEMPTS,
    PERSONAL_TASK_KEY,
    POSITION_STEP,
    STATUS_LABELS,
    build_task_key,
    participant_user,
    task_state,
    to_task_schema,
    user_display_name,
)

logger = logging.getLogger(__name__)

RepositoryErrors = (
    TasksRepositoryError,
    TaskTransfersRepositoryError,
    TaskActivityRepositoryError,
    ProjectsRepositoryError,
    ProjectStagesRepositoryError,
    UsersRepositoryError,
    KnowledgeEventsServiceError,
    UnitOfWorkRepositoryError,
)
# Любой доменный отказ (доступ, задача, проект, стадия, перенос) откатывает
# транзакцию сразу: в ней уже держатся блокировки задачи и цели.
DomainErrors = (ServiceError,)
OPEN_STATUSES = frozenset({TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW})
CLOSED_STATUSES = frozenset({TaskStatus.DONE, TaskStatus.CANCELLED})
ROLE_ORDER = (
    TaskParticipantRole.EXECUTOR,
    TaskParticipantRole.REPORTER,
    TaskParticipantRole.OBSERVER,
)


def _today() -> date:
    """Операция с проектными и разовыми задачами."""
    return datetime.now(UTC).date()


def _name(user: User | None) -> str | None:
    """Подпись человека для истории и поля ``assignee``."""
    return user_display_name(user) if user is not None else None


@dataclass
class _TransferPlan:
    """Всё, что перенос изменит; собирается одинаково для предпросмотра и переноса."""

    kind: TaskKind
    key_prefix: str
    target_name: str
    project: Project | None = None
    stage: ProjectStage | None = None
    status: TaskStatus | None = None
    completed_at: datetime | None = None
    due_date: date | None = None
    people: list[tuple[TaskParticipantRole, User]] = field(default_factory=list)
    members_by_user: dict[int, int] = field(default_factory=dict)
    outsiders: list[User] = field(default_factory=list)
    executor: User | None = None
    losses: TaskProjectLinks = field(default_factory=TaskProjectLinks)
    notes: list[str] = field(default_factory=list)

    def assignments(self, *, drop_outsiders: bool) -> list[dict]:
        """Назначения людей в формате ``task_participants`` для цели."""
        outsider_ids = {user.id for user in self.outsiders}
        rows: list[dict] = []
        seen: set[tuple[TaskParticipantRole, int]] = set()
        for role, user in self.people:
            if (role, user.id) in seen:
                continue
            seen.add((role, user.id))
            if self.kind is TaskKind.PROJECT:
                if user.id in outsider_ids and drop_outsiders:
                    continue
                rows.append({"project_member_id": self.members_by_user[user.id], "role": role})
            else:
                rows.append({"user_id": user.id, "role": role})
        return rows


class TaskTransfersService:
    """Сценарий переноса задачи и его предпросмотр."""

    def __init__(
        self,
        *,
        tasks_repository: TasksRepository,
        task_transfers_repository: TaskTransfersRepository,
        participants_repository: TaskParticipantsRepository,
        members_repository: ProjectMembersRepository,
        projects_repository: ProjectsRepository,
        stages_repository: ProjectStagesRepository,
        users_repository: UsersRepository,
        activity_repository: TaskActivityRepository,
        knowledge_events: KnowledgeEvents,
        access_service: AccessService,
        unit_of_work: UnitOfWork,
    ) -> None:
        self.tasks_repository = tasks_repository
        self.task_transfers_repository = task_transfers_repository
        self.participants_repository = participants_repository
        self.members_repository = members_repository
        self.projects_repository = projects_repository
        self.stages_repository = stages_repository
        self.users_repository = users_repository
        self.activity_repository = activity_repository
        self.knowledge_events = knowledge_events
        self.access_service = access_service
        self.unit_of_work = unit_of_work

    # Блок публичных сценариев

    async def preview(
        self, *, task_id: int, target: TaskTransferTargetSchema, user_id: int
    ) -> TaskTransferPreviewSchema:
        """Операция с проектными и разовыми задачами."""
        try:
            await self._ensure_rights(task_id=task_id, target=target, user_id=user_id)
            task = await self._get_task(task_id)
            plan = await self._plan(task, target=target, user_id=user_id, lock=False)
            return TaskTransferPreviewSchema(
                from_key=build_task_key(await self._key_prefix(task), task.number),
                target_kind=plan.kind,
                target_name=plan.target_name,
                target_key=plan.key_prefix,
                stage_id=plan.stage.id if plan.stage else None,
                stage_name=plan.stage.name if plan.stage else None,
                status=plan.status,
                executor=to_user_summary(plan.executor) if plan.executor else None,
                losses=_losses_schema(plan.losses),
                outsiders=[to_user_summary(user) for user in plan.outsiders],
                notes=plan.notes,
            )
        except RepositoryErrors as error:
            logger.error("❌ Ошибка предпросмотра переноса задачи id=%s.", task_id, exc_info=True)
            raise TaskTransfersServiceError(str(error)) from error

    async def move(
        self, *, task_id: int, target: TaskTransferTargetSchema, user_id: int
    ) -> TaskTransferResultSchema:
        """Переносит задачу с новым номером и очищает прежние проектные связи."""
        try:
            await self._ensure_rights(task_id=task_id, target=target, user_id=user_id)
            task = await self.tasks_repository.get_for_update(task_id=task_id)
            if task is None:
                raise TaskNotFoundError(task_id=task_id)
            source_project_id = task.project_id
            from_key = build_task_key(await self._key_prefix(task), task.number)
            old_executor = await self._current_executor_name(task)
            plan = await self._plan(task, target=target, user_id=user_id, lock=True)
            if plan.outsiders and not target.remove_outsiders:
                raise TaskTransferOutsidersError(names=[_name(user) or "" for user in plan.outsiders])

            fields = await self._task_fields(task, plan)
            number = await self._relocate(task, plan, fields)
            task = await self.tasks_repository.get_for_update(task_id=task_id)
            to_key = build_task_key(plan.key_prefix, number)

            await self.participants_repository.delete_for_task(task.id)
            await self.participants_repository.save_many(
                task.id, plan.assignments(drop_outsiders=True)
            )
            if source_project_id is not None:
                await self._detach_from_project(task.id)
            await self.activity_repository.save(
                task_id=task.id,
                event_type=TaskActivityEventType.TASK_MOVED,
                from_value=from_key,
                to_value=to_key,
            )
            removed = _removed_summary(plan)
            if removed:
                await self.activity_repository.save(
                    task_id=task.id,
                    event_type=TaskActivityEventType.LINKS_REMOVED,
                    from_value=None,
                    to_value=removed[:255],
                )
            if old_executor != fields["assignee"]:
                await self.activity_repository.save(
                    task_id=task.id,
                    event_type=TaskActivityEventType.ASSIGNEE_CHANGED,
                    from_value=old_executor,
                    to_value=fields["assignee"],
                )
            await self.knowledge_events.delete(
                project_id=source_project_id,
                entity_type=KnowledgeEntityType.TASK,
                entity_id=task.id,
            )
            await self.knowledge_events.upsert(
                project_id=task.project_id, entity_type=KnowledgeEntityType.TASK, entity_id=task.id
            )
            await self._notify_chats(
                task_id=task.id,
                source_project_id=source_project_id,
                plan=plan,
                from_key=from_key,
                to_key=to_key,
                user_id=user_id,
            )
            await self.unit_of_work.commit()
            logger.info("✅ Задача id=%s перенесена: %s → %s.", task_id, from_key, to_key)

            participants = await self.participants_repository.get_by_task_ids([task.id])
            return TaskTransferResultSchema(
                task=to_task_schema(
                    task=task,
                    project_key=plan.key_prefix,
                    participants=participants.get(task.id, []),
                ),
                from_key=from_key,
                to_key=to_key,
                losses=_losses_schema(plan.losses),
                notes=plan.notes,
            )
        except RepositoryErrors as error:
            logger.error("❌ Ошибка переноса задачи id=%s.", task_id, exc_info=True)
            await self.unit_of_work.rollback()
            raise TaskTransfersServiceError(str(error)) from error
        except DomainErrors:
            # Снимаем блокировки задачи и цели сразу, а не в конце запроса.
            await self.unit_of_work.rollback()
            raise

    # Блок прав

    async def _ensure_rights(
        self, *, task_id: int, target: TaskTransferTargetSchema, user_id: int
    ) -> None:
        """Проверяет права записи в источник и целевой проект."""
        grant = await self.access_service.ensure_task_access(task_id=task_id, user_id=user_id)
        self.access_service.require_write(grant)
        if grant.project_id is None and not grant.is_owner:
            raise TaskTransferOwnerRequiredError(task_id=task_id)
        if target.project_id is not None:
            await self.access_service.ensure_project_write(
                project_id=target.project_id, user_id=user_id
            )

    # Блок плана переноса

    async def _plan(
        self, task: Task, *, target: TaskTransferTargetSchema, user_id: int, lock: bool
    ) -> _TransferPlan:
        """Собирает план переноса; при ``lock`` блокирует цель до конца транзакции."""
        if (
            (target.project_id is not None and target.project_id == task.project_id)
            or (target.personal and task.project_id is None)
        ):
            raise TaskTransferSameTargetError(task_id=task.id)
        source_status = await self._source_status(task)
        participants = (await self.participants_repository.get_by_task_ids([task.id])).get(
            task.id, []
        )
        people = _people(participants)
        losses = (
            await self.task_transfers_repository.get_project_links(task.id)
            if task.project_id is not None
            else TaskProjectLinks()
        )
        if target.project_id is not None:
            plan = await self._plan_project(
                task, target=target, status=source_status, people=people, lock=lock
            )
        else:
            if lock:
                await self.tasks_repository.lock_personal_numbers()
            plan = _TransferPlan(
                kind=TaskKind.PERSONAL,
                key_prefix=PERSONAL_TASK_KEY,
                target_name="Разовая задача",
                status=source_status,
                people=people,
                executor=_executor(people),
            )
            await self._default_reporter(plan, user_id=user_id)
        plan.losses = losses
        if plan.kind is not TaskKind.PROJECT:
            plan.completed_at = (
                (task.completed_at or datetime.now(UTC)) if plan.status is TaskStatus.DONE else None
            )
            if plan.status is not None:
                plan.notes.insert(0, f"Статус: «{STATUS_LABELS[plan.status]}».")
        return plan

    async def _plan_project(
        self,
        task: Task,
        *,
        target: TaskTransferTargetSchema,
        status: TaskStatus,
        people: list[tuple[TaskParticipantRole, User]],
        lock: bool,
    ) -> _TransferPlan:
        """Перенос в проект: стадия по состоянию и люди из команды проекта."""
        project_id = int(target.project_id or 0)
        project = await self.projects_repository.get_by_id(project_id, for_update=lock)
        if project is None:
            raise ProjectNotFoundError(project_id=project_id)
        stages = await self.stages_repository.get_by_project(project_id=project_id)
        if not stages:
            raise ProjectStageNotFoundError(stage_id=0)
        if target.stage_id is not None:
            stage = next((item for item in stages if item.id == target.stage_id), None)
            if stage is None:
                if await self.stages_repository.get_by_id(stage_id=target.stage_id) is None:
                    raise ProjectStageNotFoundError(stage_id=target.stage_id)
                raise ProjectStageForeignProjectError(
                    stage_id=target.stage_id, project_id=project_id
                )
        else:
            stage = stage_for_status(stages, status)
        members = await self.members_repository.get_for_project(project_id=project_id)
        members_by_user = {member.user_id: member.id for member in members}
        outsiders: list[User] = []
        for _, user in people:
            if user.id not in members_by_user and user.id not in {item.id for item in outsiders}:
                outsiders.append(user)
        executor = _executor(people)
        if executor is not None and executor.id not in members_by_user:
            executor = None
        plan = _TransferPlan(
            kind=TaskKind.PROJECT,
            key_prefix=project.key,
            target_name=project.name,
            project=project,
            stage=stage,
            completed_at=(
                (task.completed_at or datetime.now(UTC)) if stage.is_done_stage else None
            ),
            people=people,
            members_by_user=members_by_user,
            outsiders=outsiders,
            executor=executor,
        )
        plan.notes.append(f"Стадия: «{stage.name}».")
        return plan


    async def _default_reporter(self, plan: _TransferPlan, *, user_id: int) -> None:
        """Вне проекта у задачи должен быть постановщик: без него — тот, кто переносит."""
        if any(role is TaskParticipantRole.REPORTER for role, _ in plan.people):
            return
        mover = next(iter(await self.users_repository.get_by_ids({user_id})), None)
        if mover is not None:
            plan.people.append((TaskParticipantRole.REPORTER, mover))
            plan.notes.append(f"Постановщиком станет {_name(mover)}.")

    # Блок записи

    async def _task_fields(self, task: Task, plan: _TransferPlan) -> dict:
        """Новые поля задачи без номера."""
        position = 0.0
        if plan.stage is not None:
            position = (
                await self.tasks_repository.get_max_position_by_stage(stage_id=plan.stage.id)
                + POSITION_STEP
            )
        fields = {
            "project_id": plan.project.id if plan.project else None,
            "stage_id": plan.stage.id if plan.stage else None,
            "wbs_node_id": None,
            "wbs_position": None,
            "canvas_x": None,
            "canvas_y": None,
            "status": plan.status if plan.kind is not TaskKind.PROJECT else None,
            "completed_at": plan.completed_at,
            "position": position,
            "assignee": _name(plan.executor),
        }
        if plan.due_date is not None:
            fields["due_date"] = plan.due_date
        return fields

    async def _relocate(self, task: Task, plan: _TransferPlan, fields: dict) -> int:
        """Переписывает место задачи с новым номером, повторяя при занятом номере."""
        for _ in range(NUMBER_ALLOCATION_ATTEMPTS):
            if plan.project is not None:
                number = await self.tasks_repository.get_next_number(project_id=plan.project.id)
            else:
                number = await self.tasks_repository.get_next_personal_number()
            try:
                await self.tasks_repository.relocate(
                    task_id=task.id, data={**fields, "number": number}
                )
                return number
            except TaskNumberAlreadyExistsRepositoryError:
                logger.warning("⚠️ Номер %s занят, повторяю перенос задачи id=%s.", number, task.id)
        raise TaskNumberAllocationError(project_id=plan.project.id if plan.project else 0)

    async def _detach_from_project(self, task_id: int) -> None:
        """Снимает связи задачи со старым проектом.

        Без оглядки на счётчики плана: их считали без блокировки таблиц связей,
        и связь, добавленную параллельно, условное снятие пропустило бы.
        """
        await self.task_transfers_repository.delete_dependencies(task_id)
        await self.task_transfers_repository.delete_document_links(task_id)
        await self.task_transfers_repository.unlink_risks(task_id)
        await self.task_transfers_repository.delete_sticker_links(task_id)

    async def _notify_chats(
        self,
        *,
        task_id: int,
        source_project_id: int | None,
        plan: _TransferPlan,
        from_key: str,
        to_key: str,
        user_id: int,
    ) -> None:
        """Записывает события переноса в чаты исходного и целевого проекта."""
        base = {"task_id": task_id, "from_key": from_key, "user_id": user_id}
        events: list[tuple[int, dict]] = []
        if source_project_id is not None:
            hidden = plan.kind is TaskKind.PROJECT
            events.append(
                (
                    source_project_id,
                    {
                        **base,
                        "direction": "out",
                        "target_kind": plan.kind.value,
                        "target_name": None if hidden else plan.target_name,
                        "to_key": None if hidden else to_key,
                    },
                )
            )
        if plan.project is not None:
            events.append(
                (
                    plan.project.id,
                    {
                        **base,
                        "direction": "in",
                        "target_kind": plan.kind.value,
                        "target_name": plan.target_name,
                        "to_key": to_key,
                    },
                )
            )
        for project_id, payload in sorted(events, key=lambda item: item[0]):
            await self.task_transfers_repository.emit_chat_event(
                project_id=project_id, event_type="task.moved", payload=payload
            )

    # Блок чтения

    async def _get_task(self, task_id: int) -> Task:
        """Возвращает задачу или поднимает доменную ошибку."""
        task = await self.tasks_repository.get_by_id(task_id=task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)
        return task

    async def _key_prefix(self, task: Task) -> str:
        """Префикс текущего номера задачи."""
        if task.project_id is not None:
            project = await self.projects_repository.get_by_id(task.project_id)
            if project is None:
                raise ProjectNotFoundError(project_id=task.project_id)
            return project.key
        return PERSONAL_TASK_KEY

    async def _source_status(self, task: Task) -> TaskStatus:
        """Состояние задачи в терминах статуса: у проектной — по категории стадии."""
        stages = (
            await self.stages_repository.get_by_project(project_id=task.project_id)
            if task.project_id is not None
            else []
        )
        return task_state(task, stages)[0]

    async def _current_executor_name(self, task: Task) -> str | None:
        """Подпись текущего исполнителя для истории."""
        participants = (await self.participants_repository.get_by_task_ids([task.id])).get(
            task.id, []
        )
        return _name(_executor(_people(participants)))


def stage_for_status(stages: list[ProjectStage], status: TaskStatus) -> ProjectStage:
    """Стадия проекта для состояния задачи.

    Новая — первая стадия доски; готова или отменена — первая завершающая
    (нет завершающей — последняя); в работе и на проверке — первая
    незавершающая после первой, а если такой нет — первая.
    """
    if status is TaskStatus.NEW:
        return stages[0]
    if status in CLOSED_STATUSES:
        return next((stage for stage in stages if stage.is_done_stage), stages[-1])
    return next(
        (stage for stage in stages[1:] if not stage.is_done_stage),
        stages[0],
    )


def _people(participants: list[TaskParticipant]) -> list[tuple[TaskParticipantRole, User]]:
    """Люди задачи парами «роль — пользователь» в порядке ролей."""
    rows = [(item.role, participant_user(item)) for item in participants]
    return sorted(
        [(role, user) for role, user in rows if user is not None],
        key=lambda row: ROLE_ORDER.index(row[0]),
    )


def _executor(people: list[tuple[TaskParticipantRole, User]]) -> User | None:
    """Исполнитель среди людей задачи."""
    return next((user for role, user in people if role is TaskParticipantRole.EXECUTOR), None)


def _losses_schema(losses: TaskProjectLinks) -> TaskTransferLossesSchema:
    """Счётчики снимаемых связей для ответа."""
    return TaskTransferLossesSchema(
        dependencies=losses.dependencies,
        document_links=losses.document_links,
        risks=losses.risks,
        board_stickers=losses.board_stickers,
        wbs_placements=losses.wbs_placements,
    )


def _losses_text(losses: TaskProjectLinks) -> str:
    """Снимаемые связи человеческим языком."""
    parts = [
        (losses.dependencies, "зависимостей"),
        (losses.document_links, "связей с документами"),
        (losses.risks, "рисков"),
        (losses.board_stickers, "стикеров доски"),
        (losses.wbs_placements, "размещений в ИСР"),
    ]
    return ", ".join(f"{label} — {count}" for count, label in parts if count)


def _removed_summary(plan: _TransferPlan) -> str:
    """Что снято переносом — для записи в истории."""
    parts = []
    if plan.losses.total:
        parts.append("связи со старым проектом: " + _losses_text(plan.losses))
    if plan.outsiders:
        parts.append(
            "убраны не из команды проекта: "
            + ", ".join(_name(user) or "" for user in plan.outsiders)
        )
    return "; ".join(parts)
