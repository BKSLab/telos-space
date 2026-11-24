"""SQL-проекция публичных источников истории проекта."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import Boolean, Integer, Text, case, cast, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.base import Base
from src.db.models.documents import Document
from src.db.models.project_deadline_changes import ProjectDeadlineChange
from src.db.models.project_files import ProjectFile
from src.db.models.project_history_events import ProjectHistoryEvent
from src.db.models.project_risks import ProjectRisk
from src.db.models.projects import ProjectContextHistory
from src.db.models.task_activity import TaskActivity, TaskActivityEventType
from src.db.models.task_comments import TaskComment
from src.db.models.task_reports import TaskReport
from src.db.models.tasks import Task
from src.db.models.users import User
from src.schemas.project_history import ProjectHistoryItem, ProjectHistoryQuery

_TASK_TITLES = {
    "STAGE_CHANGED": "Смена стадии",
    "DUE_DATE_CHANGED": "Изменение срока",
    "START_DATE_CHANGED": "Изменение даты начала",
    "DATES_CHANGED": "Изменение дат",
    "BASELINE_CHANGED": "Изменение базового плана",
    "DESCRIPTION_CHANGED": "Изменение описания",
    "PRIORITY_CHANGED": "Изменение приоритета",
    "ASSIGNEE_CHANGED": "Изменение исполнителя",
    "WBS_NODE_CHANGED": "Изменение размещения",
    "CHECKLIST_CHANGED": "Изменение чек-листа",
}


def _event(
    source,
    kind,
    title,
    *,
    detail=None,
    actor=None,
    actor_user_id=None,
    task_id=None,
    key=None,
    prefix=None,
    event_type=None,
    from_value=None,
    to_value=None,
    comment_id=None,
    report_id=None,
    report_revision=None,
):
    return select(
        func.concat(prefix or kind, ":", source.id if key is None else key).label("id"),
        literal(kind, type_=Text).label("kind"),
        cast(title, Text).label("title"),
        func.left(cast(detail, Text), 500).label("detail"),
        source.created_at.label("created_at"),
        cast(actor, Text).label("actor_name"),
        cast(task_id, Integer).label("task_id"),
        cast(actor_user_id, Integer).label("actor_user_id"),
        cast(event_type, Text).label("event_type"),
        cast(from_value, Text).label("from_value"),
        cast(to_value, Text).label("to_value"),
        cast(comment_id, Integer).label("comment_id"),
        cast(report_id, Integer).label("report_id"),
        cast(report_revision, Integer).label("report_revision"),
        cast(literal(None), Text).label("document_ref"),
        literal(True, type_=Boolean).label("_source_available"),
        literal(True, type_=Boolean).label("_task_available"),
    ).select_from(source)


def _user_name():
    return func.coalesce(
        func.nullif(
            func.trim(func.concat_ws(" ", User.last_name, User.first_name, User.middle_name)), ""
        ),
        User.username,
    )


class ProjectHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def version(self, project_id: int) -> int:
        """Число зафиксированных событий не теряет поздний commit меньшего ID."""
        return await self.session.scalar(
            select(func.count(ProjectHistoryEvent.id)).where(
                ProjectHistoryEvent.project_id == project_id
            )
        )

    def _journal(self, project_id: int):
        event = ProjectHistoryEvent
        task_available = (
            select(Task.id)
            .where(Task.id == event.task_id, Task.project_id == project_id)
            .correlate(event)
            .exists()
        )
        available = [(
            event.entity_type == "project_context_history",
            select(ProjectContextHistory.project_id).where(
                ProjectContextHistory.project_id == project_id,
                func.concat(
                    "project_context:", project_id, ":", ProjectContextHistory.revision
                ) == event.public_id,
            ).correlate(event).exists(),
        )]
        # Historical IDs never grant access to the object's current project.
        for name in (
            "projects", "tasks", "task_activity", "task_comments", "task_reports",
            "project_deadline_changes", "project_context_history", "project_risks",
            "documents", "project_files", "task_attachments", "document_links",
            "task_comment_documents", "task_comment_attachments", "project_members",
            "project_stages", "project_milestones", "task_dependencies", "wbs_nodes",
            "project_boards", "project_stickers", "board_shapes", "project_sticker_task_links",
        ):
            table = Base.metadata.tables.get(name)
            if table is None or "id" not in table.c:
                continue
            conditions = [table.c.id == event.entity_id]
            if name == "projects":
                conditions.append(table.c.id == project_id)
            elif name == "documents":
                conditions.append(or_(
                    table.c.project_id == project_id,
                    select(Task.id).where(
                        Task.id == table.c.owner_task_id, Task.project_id == project_id
                    ).correlate(table).exists(),
                ))
            elif "project_id" in table.c:
                conditions.append(table.c.project_id == project_id)
            elif "task_id" in table.c:
                conditions.append(select(Task.id).where(
                    Task.id == table.c.task_id, Task.project_id == project_id
                ).correlate(table).exists())
            elif "board_id" in table.c:
                board = Base.metadata.tables["project_boards"]
                conditions.append(select(board.c.id).where(
                    board.c.id == table.c.board_id, board.c.project_id == project_id
                ).correlate(table).exists())
            elif "comment_id" in table.c:
                conditions.append(select(TaskComment.id).join(
                    Task, Task.id == TaskComment.task_id
                ).where(TaskComment.id == table.c.comment_id, Task.project_id == project_id)
                    .correlate(table).exists())
            else:
                continue
            if name == "project_files":
                conditions.append(table.c.deleted_at.is_(None))
            available.append((event.entity_type == name,
                              select(table.c.id).where(*conditions).correlate(event).exists()))
        source_available = case(*available, else_=False)
        document_ref = case(
            (event.entity_type == "documents", func.concat("document:", event.entity_id)),
            (event.entity_type == "project_files", func.concat("project_file:", event.entity_id)),
            (event.entity_type == "task_attachments", func.concat("attachment:", event.entity_id)),
            else_=event.document_ref,
        )
        return select(
            event.public_id.label("id"), event.kind, event.title, event.detail, event.created_at,
            event.actor_name, event.task_id, event.actor_user_id, event.event_type,
            event.from_value, event.to_value, event.comment_id, event.report_id,
            event.report_revision, document_ref.label("document_ref"),
            source_available.label("_source_available"), task_available.label("_task_available"),
        ).where(event.project_id == project_id)

    async def read(self, project_id: int, filters: ProjectHistoryQuery) -> list[ProjectHistoryItem]:
        # Источники задачи не хранят project_id в момент записи. После переноса
        # принадлежность старого содержимого нельзя выводить из текущего Task.
        legacy_transfer = (
            select(TaskActivity.id)
            .where(
                TaskActivity.task_id == Task.id,
                TaskActivity.event_type == TaskActivityEventType.TASK_MOVED,
            )
            .exists()
        )
        moved = or_(
            legacy_transfer,
            select(ProjectHistoryEvent.id).where(
                ProjectHistoryEvent.task_id == Task.id,
                ProjectHistoryEvent.event_type.in_(("TASK_TRANSFERRED_IN", "TASK_TRANSFERRED_OUT")),
            ).exists(),
        )
        tasks = select(Task.id, Task.title).where(Task.project_id == project_id, ~moved).cte()
        activity = TaskActivity
        comment = TaskComment
        report = TaskReport
        deadline = ProjectDeadlineChange
        context = ProjectContextHistory
        risk = ProjectRisk
        branches = {
            "task_activity": [
                # created_at proves creation, but today's reporter does not prove its author.
                # Keep the same conservative project boundary as other legacy task sources.
                _event(
                    Task,
                    "task_activity",
                    func.concat("Создана задача · ", Task.title),
                    prefix="task_created",
                    event_type=literal("TASK_CREATED"),
                    task_id=Task.id,
                ).join(tasks, tasks.c.id == Task.id),
                _event(
                    activity,
                    "task_activity",
                    func.concat(
                        case(
                            _TASK_TITLES,
                            value=cast(activity.event_type, Text),
                            else_="Изменение задачи",
                        ),
                        " · ",
                        tasks.c.title,
                    ),
                    detail=func.concat_ws(
                        " · ",
                        func.nullif(
                            func.concat_ws(" → ", activity.from_value, activity.to_value), ""
                        ),
                        activity.reason,
                    ),
                    actor=activity.actor_name,
                    actor_user_id=activity.actor_user_id,
                    event_type=activity.event_type,
                    from_value=activity.from_value,
                    to_value=activity.to_value,
                    task_id=activity.task_id,
                )
                .join(tasks, tasks.c.id == activity.task_id)
                .where(
                    activity.event_type.not_in(
                        (
                            TaskActivityEventType.COMMENT_ADDED,
                            TaskActivityEventType.TASK_MOVED,
                            TaskActivityEventType.LINKS_REMOVED,
                        )
                    )
                )
            ],
            "task_comment": [
                _event(
                    comment,
                    "task_comment",
                    func.concat("Комментарий · ", tasks.c.title),
                    detail=comment.body_md,
                    actor=comment.author_name,
                    actor_user_id=comment.author_id,
                    comment_id=comment.id,
                    task_id=comment.task_id,
                ).join(tasks, tasks.c.id == comment.task_id)
            ],
            "task_report": [
                _event(
                    report,
                    "task_report",
                    func.concat("Отчёт · ", tasks.c.title, " · v", report.revision),
                    detail=report.body_md,
                    actor=report.author_name,
                    actor_user_id=report.author_id,
                    report_id=report.id,
                    report_revision=report.revision,
                    task_id=report.task_id,
                ).join(tasks, tasks.c.id == report.task_id)
            ],
            "project_deadline": [
                _event(
                    deadline,
                    "project_deadline",
                    "Изменение срока проекта",
                    detail=func.concat_ws(
                        " · ",
                        func.concat(
                            func.coalesce(
                                func.to_char(deadline.previous_due_date, "YYYY-MM-DD"), "—"
                            ),
                            " → ",
                            func.coalesce(func.to_char(deadline.new_due_date, "YYYY-MM-DD"), "—"),
                        ),
                        deadline.comment,
                    ),
                    actor=deadline.changed_by_name,
                    actor_user_id=deadline.changed_by_user_id,
                    from_value=func.to_char(deadline.previous_due_date, "YYYY-MM-DD"),
                    to_value=func.to_char(deadline.new_due_date, "YYYY-MM-DD"),
                ).where(deadline.project_id == project_id)
            ],
            "project_context": [
                _event(
                    context,
                    "project_context",
                    "Изменение паспорта проекта",
                    detail=cast(context.changed_fields, Text),
                    actor=context.changed_by_name,
                    actor_user_id=context.changed_by_user_id,
                    key=func.concat(context.project_id, ":", context.revision),
                ).where(context.project_id == project_id, context.revision > 0)
            ],
            "risk_created": [
                _event(
                    risk,
                    "risk_created",
                    func.concat("Создан риск · ", risk.title),
                    task_id=tasks.c.id,
                )
                .outerjoin(tasks, tasks.c.id == risk.task_id)
                .where(risk.project_id == project_id)
            ],
            "document_created": [
                _event(
                    Document,
                    "document_created",
                    func.concat("Создан документ · ", Document.title),
                    actor=_user_name(),
                    prefix="document",
                    actor_user_id=Document.authored_by_user_id,
                )
                .outerjoin(User, User.id == Document.authored_by_user_id)
                .where(
                    Document.project_id == project_id,
                    Document.owner_task_id.is_(None),
                    Document.origin_attachment_id.is_(None),
                ),
                _event(
                    ProjectFile,
                    "document_created",
                    func.concat(
                        "Загружен файл · ",
                        func.coalesce(ProjectFile.display_title, ProjectFile.original_name),
                    ),
                    actor=_user_name(),
                    prefix="project_file",
                    actor_user_id=ProjectFile.uploaded_by_user_id,
                )
                .outerjoin(User, User.id == ProjectFile.uploaded_by_user_id)
                .where(ProjectFile.project_id == project_id, ProjectFile.deleted_at.is_(None)),
            ],
        }
        selected = branches.values() if filters.kind is None else [branches.get(filters.kind, [])]
        legacy_queries = [query for group in selected for query in group]
        journal = self._journal(project_id)
        if legacy_queries:
            legacy = union_all(*legacy_queries).subquery("legacy_history")
            remaining = select(legacy).where(~select(ProjectHistoryEvent.id).where(
                ProjectHistoryEvent.project_id == project_id,
                ProjectHistoryEvent.public_id == legacy.c.id,
            ).exists())
            events = union_all(remaining, journal).subquery("events")
        else:
            events = journal.subquery("events")
        query = select(events)
        if filters.kind:
            query = query.where(events.c.kind == filters.kind)
        if filters.q:
            query = query.where(
                or_(
                    events.c.title.icontains(filters.q, autoescape=True),
                    events.c.detail.icontains(filters.q, autoescape=True),
                )
            )
        if filters.actor:
            query = query.where(events.c.actor_name.icontains(filters.actor, autoescape=True))
        if filters.actor_user_id is not None:
            query = query.where(events.c.actor_user_id == filters.actor_user_id)
        if filters.task_id is not None:
            query = query.where(events.c.task_id == filters.task_id)
        if filters.event_id:
            query = query.where(events.c.id == filters.event_id)
        timezone = ZoneInfo(filters.timezone)
        if filters.since:
            query = query.where(
                events.c.created_at
                >= datetime.combine(filters.since, time.min, timezone).astimezone(UTC)
            )
        if filters.until:
            query = query.where(
                events.c.created_at
                <= datetime.combine(filters.until, time.max, timezone).astimezone(UTC)
            )
        rows = await self.session.execute(
            query.order_by(events.c.created_at.desc(), events.c.id.collate("C").desc())
            .offset(filters.offset)
            .limit(filters.limit + 1)
        )
        items = []
        for row in rows.mappings():
            item = dict(row)
            item["source_available"] = item.pop("_source_available")
            if not item.pop("_task_available"):
                item["task_id"] = item["comment_id"] = item["report_id"] = None
            if not item["source_available"]:
                item["comment_id"] = item["report_id"] = item["document_ref"] = None
            items.append(ProjectHistoryItem.model_validate(item))
        return items
