"""SQL-проекция публичных источников истории проекта."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, Text, case, cast, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.documents import Document
from src.db.models.project_deadline_changes import ProjectDeadlineChange
from src.db.models.project_files import ProjectFile
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

    async def read(self, project_id: int, filters: ProjectHistoryQuery) -> list[ProjectHistoryItem]:
        # Источники задачи не хранят project_id в момент записи. После переноса
        # принадлежность старого содержимого нельзя выводить из текущего Task.
        moved = (
            select(TaskActivity.id)
            .where(
                TaskActivity.task_id == Task.id,
                TaskActivity.event_type == TaskActivityEventType.TASK_MOVED,
            )
            .exists()
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
        selected = branches.values() if filters.kind is None else [branches[filters.kind]]
        events = union_all(*(query for group in selected for query in group)).subquery("events")
        query = select(events)
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
        return [ProjectHistoryItem.model_validate(row) for row in rows.mappings()]
