from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Any

from src.clients.llm import LlmClient
from src.db.models.analytics_reports import AnalyticsReport
from src.db.models.documents import Document
from src.db.models.project_members import ProjectMember
from src.db.models.project_milestones import ProjectMilestone, ProjectMilestoneStatus
from src.db.models.project_risks import ProjectRisk
from src.db.models.project_stages import ProjectStage
from src.db.models.project_stickers import ProjectSticker
from src.db.models.projects import Project, ProjectStatus
from src.db.models.task_activity import TaskActivity
from src.db.models.task_attachments import TaskAttachment
from src.db.models.task_comments import TaskComment
from src.db.models.task_dependencies import TaskDependency
from src.db.models.task_participants import TaskParticipant
from src.db.models.tasks import Task
from src.db.models.wbs_nodes import WbsNode
from src.exceptions.analytics import (
    AnalyticsEmptyScopeError,
    AnalyticsGenerationError,
    AnalyticsReportsRepositoryError,
    AnalyticsServiceError,
)
from src.exceptions.clients import ClientError
from src.exceptions.document_links import DocumentLinksRepositoryError
from src.exceptions.documents import DocumentsRepositoryError
from src.exceptions.knowledge import KnowledgeProviderError
from src.exceptions.milestones import MilestonesRepositoryError
from src.exceptions.project_risks import ProjectRiskRepositoryError
from src.exceptions.project_stages import ProjectStagesRepositoryError
from src.exceptions.project_stickers import ProjectStickersRepositoryError
from src.exceptions.projects import ProjectNotFoundError, ProjectsRepositoryError
from src.exceptions.task_activity import TaskActivityRepositoryError
from src.exceptions.task_attachments import TaskAttachmentsRepositoryError
from src.exceptions.task_comments import TaskCommentsRepositoryError
from src.exceptions.task_dependencies import TaskDependenciesRepositoryError
from src.exceptions.tasks import TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.exceptions.wbs_nodes import WbsNodesRepositoryError
from src.prompts.analytics import (
    ANALYTICS_PORTFOLIO_SYSTEM_PROMPT,
    ANALYTICS_PROJECT_SYSTEM_PROMPT,
)
from src.schemas.analytics import (
    AnalyticsContextSchema,
    AnalyticsDraftSchema,
    AnalyticsEntityCountSchema,
    AnalyticsFindingSchema,
    AnalyticsHealth,
    AnalyticsProgressSchema,
    AnalyticsRecommendationSchema,
    AnalyticsReportSchema,
    AnalyticsScope,
    AnalyticsSignalsSchema,
    AnalyticsTaskRefSchema,
)
from src.schemas.project_risks import ProjectRiskFilters, ProjectRiskSchema
from src.services.db_scope import AnalyticsDbScope, AnalyticsDbScopeFactory
from src.services.project_risks import build_risk_summary
from src.services.tasks import MY_TASK_ROLES, PERSONAL_TASK_KEY, build_task_key
from src.utils.checklists import checklist_context
from src.utils.deadlines import DUE_SOON_DAYS, is_task_due_soon, is_task_overdue

logger = logging.getLogger(__name__)

OUTSIDE_TASKS_LIMIT = 40
MAX_COMPLETION_TOKENS = 6000
# Порог, за которым срез перестраивается по ужатым лимитам. Считаем в символах:
# точный подсчёт токенов потребовал бы токенизатора конкретной модели, а нам
# нужна лишь защита от контекста, который модель не примет целиком.
MAX_CONTEXT_CHARS = 90_000
STALE_TASK_DAYS = 14
NAME_LIMIT = 302

# Портфельная сводка отвечает на вопрос «за какой проект браться сейчас»,
# поэтому берёт только проекты в работе. Запланированный, приостановленный
# и завершённый проект решения сегодняшнего дня не требуют, а место в
# контексте занимают наравне с активными.
PORTFOLIO_STATUSES = frozenset({ProjectStatus.ACTIVE})

RepositoryErrors = (
    AnalyticsReportsRepositoryError,
    DocumentLinksRepositoryError,
    DocumentsRepositoryError,
    MilestonesRepositoryError,
    ProjectStagesRepositoryError,
    ProjectStickersRepositoryError,
    ProjectRiskRepositoryError,
    ProjectsRepositoryError,
    TaskActivityRepositoryError,
    TaskAttachmentsRepositoryError,
    TaskCommentsRepositoryError,
    TaskDependenciesRepositoryError,
    TasksRepositoryError,
    UnitOfWorkRepositoryError,
    WbsNodesRepositoryError,
)


@dataclass(frozen=True, slots=True)
class _Limits:
    """Сколько данных каждого вида помещается в один срез для модели."""

    tasks: int
    comments_per_task: int
    comment_chars: int
    documents: int
    document_chars: int
    stickers: int
    sticker_chars: int
    activity: int
    description_chars: int
    risks: int
    items: int


# Начинаем со всех записей и ограниченных текстовых фрагментов. Численные
# лимиты ниже используются при переполнении, чтобы один крупный проект
# не вытеснил остальные проекты или целый вид источников.
PROJECT_LIMITS = _Limits(
    tasks=180,
    comments_per_task=3,
    comment_chars=1000,
    documents=12,
    document_chars=6000,
    stickers=40,
    sticker_chars=1200,
    activity=50,
    description_chars=2000,
    risks=20,
    items=100,
)
PORTFOLIO_LIMITS = _Limits(
    tasks=60,
    comments_per_task=2,
    comment_chars=600,
    documents=4,
    document_chars=3000,
    stickers=15,
    sticker_chars=800,
    activity=20,
    description_chars=1000,
    risks=6,
    items=40,
)


@dataclass(slots=True)
class _ProjectSlice:
    """Сырые данные одного проекта, из которых собирается срез для модели."""

    project: Project
    stages: list[ProjectStage]
    tasks: list[Task]
    comments: dict[int, list[TaskComment]]
    activity: list[TaskActivity]
    dependencies: list[TaskDependency]
    nodes: list[WbsNode]
    milestones: list[ProjectMilestone]
    stickers: list[ProjectSticker]
    documents: list[Document]
    document_task_ids: dict[int, list[int]] = field(default_factory=dict)
    risks: list[ProjectRisk] = field(default_factory=list)
    risk_groups: list[dict[str, Any]] = field(default_factory=list)
    members: list[ProjectMember] = field(default_factory=list)
    participants: dict[int, list[TaskParticipant]] = field(default_factory=dict)
    attachments: dict[int, list[TaskAttachment]] = field(default_factory=dict)
    activity_total: int = 0


@dataclass(slots=True)
class _OutsideTasks:
    """Открытые задачи пользователя вне проектов: потоки и разовые."""

    tasks: list[Task] = field(default_factory=list)
    my_roles: dict[int, set[Any]] = field(default_factory=dict)

    def key(self, task: Task) -> str:
        """Отображаемый ключ: по ключу потока или ``T`` у разовой задачи."""
        return build_task_key(project_key=PERSONAL_TASK_KEY, number=task.number)


class AnalyticsReportReader:
    """Читает сохранённые аналитические своды без клиента модели."""

    def __init__(
        self,
        *,
        scope: AnalyticsDbScopeFactory,
    ) -> None:
        """Получает фабрику короткой области чтения сохранённого анализа.

        Args:
            scope: Фабрика области с журналом сводов и актуальным членством.
        """
        self.scope = scope

    async def get_latest(
        self,
        *,
        user_id: int,
        project_id: int | None,
    ) -> AnalyticsReportSchema | None:
        """Возвращает последний сохранённый свод выбранной области.

        Args:
            user_id: Идентификатор пользователя.
            project_id: Проект анализа; ``None`` — весь портфель пользователя.

        Returns:
            Последний свод или ``None``, если анализ ещё не запускали.

        Raises:
            ProjectNotFoundError: Если проект недоступен пользователю.
            AnalyticsServiceError: Если прочитать свод не удалось.
        """
        try:
            async with self.scope() as db:
                allowed_ids = await db.members.get_project_ids_for_user(user_id=user_id)
                if project_id is not None:
                    if project_id not in allowed_ids:
                        raise ProjectNotFoundError(project_id=project_id)
                    report = await db.reports.get_latest_for_project(project_id=project_id)
                else:
                    report = await db.reports.get_latest_portfolio(user_id=user_id)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка чтения аналитического свода.", exc_info=True)
            raise AnalyticsServiceError(str(error)) from error

        if report is None:
            return None
        return _to_report_schema(report=report, project=report.project)


class AnalyticsService(AnalyticsReportReader):
    """Аналитический свод дашборда: разбор состояния работ моделью.

    Сервис ничего не меняет в проектах. Он собирает срез рабочего пространства,
    просит модель объяснить, что в нём важно, сверяет ответ с реальными
    задачами и сохраняет результат, чтобы свод не пропадал при перезагрузке
    страницы.
    """

    def __init__(
        self,
        *,
        scope: AnalyticsDbScopeFactory,
        llm_client: LlmClient,
    ) -> None:
        """Создаёт сервис аналитического свода.

        Args:
            scope: Фабрика короткой области работы с базой. Между сбором
                данных и записью результата стоит вызов модели, поэтому
                соединение на это время удерживаться не должно.
            llm_client: Клиент chat completions.
        """
        super().__init__(scope=scope)
        self.llm_client = llm_client

    async def generate(
        self,
        *,
        actor_id: int,
        actor_name: str,
        project_id: int | None,
    ) -> AnalyticsReportSchema:
        """Формирует новый аналитический свод по проекту или всему портфелю.

        Args:
            actor_id: Идентификатор пользователя, запросившего анализ.
            actor_name: Имя автора на момент формирования свода.
            project_id: Проект анализа; ``None`` — весь портфель пользователя.

        Returns:
            Сформированный и сохранённый свод.

        Raises:
            ProjectNotFoundError: Если проект недоступен пользователю.
            AnalyticsEmptyScopeError: Если в области нет содержательных данных проекта.
            KnowledgeProviderError: Если LLM-сервис недоступен.
            AnalyticsGenerationError: Если ответ модели непригоден.
            AnalyticsServiceError: Если собрать данные не удалось.
        """
        started_at = perf_counter()
        today = date.today()
        scope = AnalyticsScope.PROJECT if project_id is not None else AnalyticsScope.PORTFOLIO

        # Первая короткая DB-фаза: собирается полный снимок области
        # анализа, после чего соединение возвращается в пул.
        try:
            async with self.scope() as db:
                projects = await self._resolve_projects(
                    db,
                    user_id=actor_id,
                    project_id=project_id,
                )
                slices = [
                    await self._collect_project(db, project=project, scope=scope)
                    for project in projects
                ]
                outside = await self._collect_outside_tasks(db, user_id=actor_id) if scope is AnalyticsScope.PORTFOLIO else _OutsideTasks()
        except RepositoryErrors as error:
            logger.error("❌ Ошибка сбора данных для аналитического свода.", exc_info=True)
            raise AnalyticsServiceError(str(error)) from error

        if not any(
            item.tasks
            or item.risks
            or item.documents
            or item.stickers
            or item.nodes
            or item.milestones
            or item.project.description_md
            or item.project.start_date
            or item.project.due_date
            for item in slices
        ) and not outside.tasks:
            raise AnalyticsEmptyScopeError(
                error_details=f"В области анализа (project_id={project_id}) нет проектов в работе и открытых разовых задач.",
            )

        signals = _build_signals(slices=slices, today=today)
        content, context = _build_content(slices=slices, scope=scope, signals=signals, today=today, outside=outside)

        system_prompt = (
            ANALYTICS_PROJECT_SYSTEM_PROMPT
            if scope is AnalyticsScope.PROJECT
            else ANALYTICS_PORTFOLIO_SYSTEM_PROMPT
        )
        try:
            draft = await self.llm_client.get_structured_response(
                system_prompt=system_prompt,
                content=content,
                schema=AnalyticsDraftSchema,
                max_completion_tokens=MAX_COMPLETION_TOKENS,
            )
        except ClientError as error:
            logger.error("❌ LLM недоступен при сборе аналитического свода.", exc_info=True)
            raise KnowledgeProviderError(str(error)) from error
        except Exception as error:
            logger.error("❌ Модель не вернула аналитический свод.", exc_info=True)
            raise AnalyticsGenerationError(str(error)) from error

        payload = _resolve_draft(draft=draft, slices=slices, today=today, outside=outside)
        duration_ms = round((perf_counter() - started_at) * 1000)

        # Вторая короткая DB-фаза: результат сохраняется уже после того,
        # как модель ответила.
        try:
            async with self.scope() as db:
                report = await db.reports.save(
                    data={
                        "project_id": project_id,
                        "created_by_user_id": actor_id,
                        "created_by_display_name_snapshot": actor_name[:NAME_LIMIT],
                        "llm_model": self.llm_client.model,
                        "duration_ms": duration_ms,
                        "payload": payload | {"signals": signals.model_dump(mode="json")},
                        "context_summary": context.model_dump(mode="json"),
                    }
                )
                await db.unit_of_work.commit()
        except RepositoryErrors as error:
            logger.error("❌ Не удалось сохранить аналитический свод.", exc_info=True)
            raise AnalyticsServiceError(str(error)) from error

        logger.info(
            "🤖 Аналитический свод %s: проектов %s, задач в контексте %s, находок %s, %s мс.",
            scope.value,
            context.projects,
            context.tasks_included,
            len(payload["findings"]),
            duration_ms,
        )
        return _to_report_schema(
            report=report,
            project=slices[0].project if scope is AnalyticsScope.PROJECT else None,
        )

    @staticmethod
    async def _resolve_projects(
        db: AnalyticsDbScope,
        *,
        user_id: int,
        project_id: int | None,
    ) -> list[Project]:
        """Возвращает проекты области анализа с проверкой доступа."""
        allowed_ids = await db.members.get_project_ids_for_user(user_id=user_id)
        if project_id is not None:
            if project_id not in allowed_ids:
                # Чужой проект неотличим от несуществующего: наличие чужих
                # данных не подтверждается (см. правила доступа проекта).
                raise ProjectNotFoundError(project_id=project_id)
            project = await db.projects.get_by_id(project_id=project_id)
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            return [project]

        available = [
            project for project in await db.projects.get_all() if project.id in allowed_ids
        ]
        projects = [project for project in available if project.status in PORTFOLIO_STATUSES]
        return projects

    @staticmethod
    async def _collect_outside_tasks(db: AnalyticsDbScope, *, user_id: int) -> _OutsideTasks:
        """Читает открытые разовые задачи пользователя вне проектов."""
        tasks = [
            task
            for task in await db.tasks.get_for_person(
                user_id=user_id,
                roles=set(MY_TASK_ROLES["all"]),
                include_closed=False,
                limit=OUTSIDE_TASKS_LIMIT,
                outside_projects_only=True,
            )
            if task.project_id is None
        ]
        my_roles = (
            await db.participants.get_person_roles(
                task_ids=[task.id for task in tasks], user_id=user_id
            )
            if tasks
            else {}
        )
        return _OutsideTasks(tasks=tasks, my_roles=my_roles)

    @staticmethod
    async def _collect_project(
        db: AnalyticsDbScope,
        *,
        project: Project,
        scope: AnalyticsScope,
    ) -> _ProjectSlice:
        """Читает одинаковые виды источников для проекта и портфеля.

        Связи читаются пакетно; все нужные отношения загружены до закрытия
        DB-области. Ограничение контекста применяется после сбора, когда
        известен размер всего портфеля. История ограничена свежими событиями.
        """
        limits = PROJECT_LIMITS if scope is AnalyticsScope.PROJECT else PORTFOLIO_LIMITS
        stages = await db.stages.get_by_project(project_id=project.id)
        tasks = await db.tasks.get_by_project(project_id=project.id)
        dependencies = await db.dependencies.get_by_project(project_id=project.id)
        milestones = await db.milestones.get_by_project(project_id=project.id)
        risk_groups = await db.risks.get_aggregates(
            project_ids={project.id}, filters=ProjectRiskFilters(), today=date.today()
        )
        risks = await db.risks.get_by_project(project_id=project.id)
        members = await db.members.get_for_project(project_id=project.id)

        activity = await db.activity.get_recent_by_project(
            project_id=project.id,
            limit=limits.activity,
        )
        activity_total = await db.activity.get_count_by_project(project_id=project.id)
        nodes = await db.wbs_nodes.get_by_project(project_id=project.id)
        stickers = await db.stickers.list_by_project_id(project_id=project.id)
        documents = await db.documents.get_by_project(project_id=project.id)

        task_ids = {task.id for task in tasks}
        comments = await db.comments.get_for_tasks(task_ids=task_ids)
        comments_by_task: dict[int, list[TaskComment]] = {}
        for comment in comments:
            comments_by_task.setdefault(comment.task_id, []).append(comment)
        participants = await db.participants.get_by_task_ids(task_ids=sorted(task_ids))
        attachments = await db.attachments.get_for_tasks(task_ids=task_ids)
        attachments_by_task: dict[int, list[TaskAttachment]] = {}
        for attachment in attachments:
            attachments_by_task.setdefault(attachment.task_id, []).append(attachment)

        links = await db.document_links.get_for_documents(
            document_ids={document.id for document in documents}
        )
        document_task_ids: dict[int, list[int]] = {}
        for link in links:
            document_task_ids.setdefault(link.document_id, []).append(link.task_id)

        return _ProjectSlice(
            project=project,
            stages=stages,
            tasks=tasks,
            comments=comments_by_task,
            activity=activity,
            dependencies=dependencies,
            nodes=nodes,
            milestones=milestones,
            stickers=stickers,
            documents=documents,
            document_task_ids=document_task_ids,
            risks=risks,
            risk_groups=risk_groups,
            members=members,
            participants=participants,
            attachments=attachments_by_task,
            activity_total=activity_total,
        )


# Блок расчёта фактов.


def _build_signals(slices: list[_ProjectSlice], today: date) -> AnalyticsSignalsSchema:
    """Считает проверяемые показатели по всей области анализа."""
    soon_until = today + timedelta(days=DUE_SOON_DAYS)
    stale_before = datetime.now(UTC) - timedelta(days=STALE_TASK_DAYS)
    totals = dict.fromkeys(
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

    for project_slice in slices:
        done_stage_ids = _done_stage_ids(project_slice.stages)
        done_task_ids = {task.id for task in project_slice.tasks if task.stage_id in done_stage_ids}
        for task in project_slice.tasks:
            is_done = task.id in done_task_ids
            totals["total_tasks"] += 1
            if is_done:
                totals["done_tasks"] += 1
                continue
            if is_task_overdue(due_date=task.due_date, is_done=is_done, today=today):
                totals["overdue_tasks"] += 1
            if is_task_due_soon(
                due_date=task.due_date,
                is_done=is_done,
                today=today,
                soon_until=soon_until,
            ):
                totals["due_soon_tasks"] += 1
            if task.due_date is None:
                totals["no_due_date_tasks"] += 1
            if not task.assignee:
                totals["unassigned_tasks"] += 1
            if task.updated_at and task.updated_at < stale_before:
                totals["stale_tasks"] += 1
            if task.wbs_node_id is None:
                totals["unplaced_tasks"] += 1

        blocked_ids = {
            dependency.successor_task_id
            for dependency in project_slice.dependencies
            if dependency.predecessor_task_id not in done_task_ids
            and dependency.successor_task_id not in done_task_ids
        }
        totals["blocked_tasks"] += len(blocked_ids)
        totals["milestones_at_risk"] += sum(
            1
            for milestone in project_slice.milestones
            if milestone.status is not ProjectMilestoneStatus.ACHIEVED
            and milestone.due_date < today
        )

    risk_summary = build_risk_summary(
        [group for project_slice in slices for group in project_slice.risk_groups]
    )
    return AnalyticsSignalsSchema(**totals, **risk_summary.model_dump())


# Блок сборки среза для модели.


# Имена совпадают с entity_counts в сохранённом отчёте.
_ENTITY_LABELS = {
    "checklists": "чек-листы задач",
    "checklist_items": "пункты чек-листов",
    "tasks": "задачи",
    "comments": "комментарии",
    "documents": "документы",
    "document_links": "связи документов с задачами",
    "stickers": "стикеры",
    "sticker_links": "связи стикеров с задачами",
    "wbs_nodes": "разделы ИСР",
    "milestones": "вехи",
    "activity": "события истории",
    "risks": "риски",
    "stages": "стадии",
    "members": "участники команды",
    "participants": "ролевые назначения задач",
    "attachments": "метаданные вложений",
    "dependencies": "зависимости задач",
    "outside_tasks": "разовые задачи",
}


def _build_content(
    slices: list[_ProjectSlice],
    scope: AnalyticsScope,
    signals: AnalyticsSignalsSchema,
    today: date,
    outside: _OutsideTasks | None = None,
) -> tuple[str, AnalyticsContextSchema]:
    """Собирает оба анализа из одинаковых источников в пределах бюджета.

    Сначала включаются все записи с текстовыми фрагментами. При переполнении
    лимиты последовательно уменьшаются для каждого проекта и вида данных.
    Счётчики всегда относятся к полному снимку, а границы видны и модели,
    и пользователю. Ни один проект или вид источника целиком не отбрасывается.
    """
    base = PROJECT_LIMITS if scope is AnalyticsScope.PROJECT else PORTFOLIO_LIMITS
    limits = _all_records_limits(slices, base)
    compressed = False
    while True:
        payload, context = _render(
            slices=slices, limits=limits, scope=scope, signals=signals, today=today
        )
        if outside is not None and outside.tasks:
            payload["outside_projects"] = _render_outside(outside, limits=limits, today=today)
            context.entity_counts["outside_tasks"] = AnalyticsEntityCountSchema(total=len(outside.tasks), included=len(outside.tasks))
        if compressed:
            context.truncated = True
            context.omitted.append(
                "срез пересобран по ужатым лимитам: полный объём данных не помещается "
                "в контекст модели"
            )
        payload["context"] = context.model_dump(mode="json")
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(content) <= MAX_CONTEXT_CHARS:
            return content, context
        tighter = _tighten(limits)
        if tighter == limits:
            raise AnalyticsGenerationError(
                "Даже минимальный срез превышает бюджет контекста; "
                "выполните анализ отдельных проектов."
            )
        limits = tighter
        compressed = True


def _all_records_limits(slices: list[_ProjectSlice], base: _Limits) -> _Limits:
    """Вмещает все прочитанные записи до проверки общего бюджета."""
    return replace(
        base,
        tasks=max((len(item.tasks) for item in slices), default=1) or 1,
        comments_per_task=max(
            (len(rows) for item in slices for rows in item.comments.values()), default=1
        )
        or 1,
        documents=max((len(item.documents) for item in slices), default=1) or 1,
        stickers=max((len(item.stickers) for item in slices), default=1) or 1,
        risks=max((len(item.risks) for item in slices), default=1) or 1,
        items=max(
            (
                len(rows)
                for item in slices
                for rows in (
                    item.stages,
                    item.nodes,
                    item.milestones,
                    item.members,
                    item.dependencies,
                    *(
                        (getattr(task, "checklist", None) or {}).get("items", [])
                        for task in item.tasks
                    ),
                    *item.attachments.values(),
                    *item.participants.values(),
                )
            ),
            default=1,
        )
        or 1,
    )


def _tighten(limits: _Limits) -> _Limits:
    """Ужимает все списки и тексты, сохраняя хотя бы одну запись каждого вида."""
    return _Limits(
        tasks=max(limits.tasks // 2, 1),
        comments_per_task=max(limits.comments_per_task // 2, 1),
        comment_chars=max(limits.comment_chars // 2, 80),
        documents=max(limits.documents // 2, 1),
        document_chars=max(limits.document_chars // 2, 200),
        stickers=max(limits.stickers // 2, 1),
        sticker_chars=max(limits.sticker_chars // 2, 80),
        activity=max(limits.activity // 2, 1),
        description_chars=max(limits.description_chars // 2, 80),
        risks=max(limits.risks // 2, 1),
        items=max(limits.items // 2, 1),
    )


def _render(
    slices: list[_ProjectSlice],
    limits: _Limits,
    scope: AnalyticsScope,
    signals: AnalyticsSignalsSchema,
    today: date,
) -> tuple[dict[str, Any], AnalyticsContextSchema]:
    """Передаёт содержание и связи сущностей вместе с точным учётом сокращений."""
    soon_until = today + timedelta(days=DUE_SOON_DAYS)
    coverage = {key: {"total": 0, "included": 0} for key in _ENTITY_LABELS}
    omitted: list[str] = []
    rendered_projects: list[dict[str, Any]] = []

    for project_slice in slices:
        shortened = 0
        project_coverage: dict[str, dict[str, int]] = {}

        def clip(value: str | None, chars: int = limits.description_chars) -> str | None:
            nonlocal shortened
            if value is None:
                return None
            if len(value.strip()) > chars:
                shortened += 1
            return _cut(value, chars)

        def track(
            name: str,
            total: int,
            included: int,
            target: dict[str, dict[str, int]] = project_coverage,
        ) -> None:
            target[name] = {"total": total, "included": included}
            coverage[name]["total"] += total
            coverage[name]["included"] += included

        done_stage_ids = _done_stage_ids(project_slice.stages)
        stage_names = {stage.id: stage.name for stage in project_slice.stages}
        node_paths = _node_paths(project_slice.nodes)
        task_keys = {
            task.id: build_task_key(project_key=project_slice.project.key, number=task.number)
            for task in project_slice.tasks
        }
        selected_tasks = _select_tasks(
            tasks=project_slice.tasks,
            stages=project_slice.stages,
            limit=len(project_slice.tasks),
            today=today,
        )
        blocked_by = _blocked_by(
            dependencies=project_slice.dependencies,
            done_stage_ids=done_stage_ids,
            tasks=project_slice.tasks,
            project_key=project_slice.project.key,
        )
        rendered_tasks = []
        for task in selected_tasks[: limits.tasks]:
            is_done = task.stage_id in done_stage_ids
            entry = {
                "key": task_keys[task.id],
                "title": clip(task.title),
                "description": clip(task.description_md),
                "stage": clip(stage_names.get(task.stage_id, "—")),
                "done": is_done,
                "priority": task.priority.value,
                "assignee": clip(task.assignee) or None,
                "role": task.role.value if task.role else None,
                "start": _iso(task.start_date),
                "due": _iso(task.due_date),
                "baseline_start": _iso(task.baseline_start_date),
                "baseline_due": _iso(task.baseline_due_date),
                "completed_at": _iso(task.completed_at),
                "updated": _iso(task.updated_at),
                "wbs": clip(node_paths.get(task.wbs_node_id)),
            }
            if is_task_overdue(due_date=task.due_date, is_done=is_done, today=today):
                entry["overdue_days"] = (today - task.due_date).days
            if is_task_due_soon(
                due_date=task.due_date, is_done=is_done, today=today, soon_until=soon_until
            ):
                entry["due_soon"] = True
            if task.id in blocked_by:
                entry["blocked_by"] = blocked_by[task.id][: limits.items]
            rendered_tasks.append(entry)
        track("tasks", len(project_slice.tasks), len(rendered_tasks))
        checklist_tasks = [
            task for task in selected_tasks if getattr(task, "checklist", None) is not None
        ]
        rendered_checklists = []
        for task in checklist_tasks[: limits.tasks]:
            data = checklist_context(
                task.checklist, limit=limits.items, chars=limits.description_chars
            )
            if any(
                len(item["text"]) > limits.description_chars for item in task.checklist["items"]
            ):
                shortened += 1
            rendered_checklists.append({"task": task_keys[task.id], **data})
        track("checklists", len(checklist_tasks), len(rendered_checklists))
        track(
            "checklist_items",
            sum(len(task.checklist["items"]) for task in checklist_tasks),
            sum(item["included_items"] for item in rendered_checklists),
        )
        # Источники выбираются независимо от карточек задач: комментарий
        # или файл закрытой задачи не должен исчезнуть целиком при ужатии.
        rendered_comments = [
            {
                "task": task_keys[task.id],
                "when": _iso(comment.created_at),
                "who": clip(comment.author_name),
                "text": clip(comment.body_md, limits.comment_chars),
            }
            for task in [task for task in selected_tasks if project_slice.comments.get(task.id)][
                : limits.tasks
            ]
            for comment in project_slice.comments[task.id][-limits.comments_per_task :]
        ]
        rendered_participants = [
            _member_identity(participant.project_member)
            | {"task": task_keys[task.id], "role": participant.role.value}
            for task in [
                task for task in selected_tasks if project_slice.participants.get(task.id)
            ][: limits.tasks]
            for participant in project_slice.participants[task.id][: limits.items]
        ]
        rendered_attachments = [
            {
                "task": task_keys[task.id],
                "name": clip(attachment.original_name),
                "content_type": clip(attachment.content_type),
                "size_bytes": attachment.size,
                "uploaded_at": _iso(attachment.created_at),
            }
            for task in [task for task in selected_tasks if project_slice.attachments.get(task.id)][
                : limits.tasks
            ]
            for attachment in project_slice.attachments[task.id][: limits.items]
        ]
        track("comments", sum(map(len, project_slice.comments.values())), len(rendered_comments))
        track(
            "participants",
            sum(map(len, project_slice.participants.values())),
            len(rendered_participants),
        )
        track(
            "attachments",
            sum(map(len, project_slice.attachments.values())),
            len(rendered_attachments),
        )

        selected_documents = _select_documents(
            documents=project_slice.documents,
            document_task_ids=project_slice.document_task_ids,
            limit=limits.documents,
        )
        rendered_documents = [
            {
                "slug": document.slug,
                "title": clip(document.title),
                "linked_tasks": _linked_keys(
                    project_slice.document_task_ids.get(document.id, []), task_keys, limits.items
                ),
                "excerpt": clip(document.content_md, limits.document_chars),
            }
            for document in selected_documents
        ]
        track("documents", len(project_slice.documents), len(rendered_documents))
        track(
            "document_links",
            sum(map(len, project_slice.document_task_ids.values())),
            sum(len(item["linked_tasks"]) for item in rendered_documents),
        )
        rendered_stickers = [
            {
                "id": sticker.id,
                "author": clip(sticker.created_by_display_name_snapshot),
                "when": _iso(sticker.created_at),
                "text": clip(sticker.body, limits.sticker_chars),
                "tasks": _linked_keys(
                    [link.task_id for link in sticker.task_links], task_keys, limits.items
                ),
            }
            for sticker in sorted(project_slice.stickers, key=lambda item: not item.task_links)[
                : limits.stickers
            ]
        ]
        track("stickers", len(project_slice.stickers), len(rendered_stickers))
        track(
            "sticker_links",
            sum(len(item.task_links) for item in project_slice.stickers),
            sum(len(item["tasks"]) for item in rendered_stickers),
        )
        wbs_counts = _wbs_task_counts(project_slice.tasks)
        rendered_nodes = [
            {
                "id": node.id,
                "parent_id": node.parent_id,
                "title": clip(node.title),
                "path": clip(node_paths.get(node.id)),
                "tasks_count": wbs_counts.get(node.id, 0),
            }
            for node in project_slice.nodes[: limits.items]
        ]
        track("wbs_nodes", len(project_slice.nodes), len(rendered_nodes))
        rendered_milestones = [
            {
                "id": milestone.id,
                "title": clip(milestone.title),
                "description": clip(milestone.description_md),
                "due": _iso(milestone.due_date),
                "status": milestone.status.value,
                "wbs": clip(node_paths.get(milestone.wbs_node_id)),
                "days_left": (milestone.due_date - today).days,
            }
            for milestone in sorted(
                project_slice.milestones,
                key=lambda item: (item.status is ProjectMilestoneStatus.ACHIEVED, item.due_date),
            )[: limits.items]
        ]
        track("milestones", len(project_slice.milestones), len(rendered_milestones))
        rendered_activity = [
            {
                "when": _iso(event.created_at),
                "task": task_keys[event.task_id],
                "event": event.event_type.value,
                "from": clip(event.from_value),
                "to": clip(event.to_value),
            }
            for event in project_slice.activity[: limits.activity]
            if event.task_id in task_keys
        ]
        track("activity", project_slice.activity_total, len(rendered_activity))
        rendered_risks = _render_risks(project_slice, limit=limits.risks)
        for item in rendered_risks:
            for name in ("title", "description", "mitigation_plan", "response_plan"):
                item[name] = clip(item[name])
        track(
            "risks", build_risk_summary(project_slice.risk_groups).total_risks, len(rendered_risks)
        )

        rendered_stages = [
            {
                "id": stage.id,
                "name": clip(stage.name),
                "is_done": stage.is_done_stage,
                "order": stage.order_index,
            }
            for stage in project_slice.stages[: limits.items]
        ]
        track("stages", len(project_slice.stages), len(rendered_stages))
        rendered_members = [
            _member_identity(member) | {"role": member.role.value}
            for member in project_slice.members[: limits.items]
        ]
        track("members", len(project_slice.members), len(rendered_members))
        rendered_dependencies = [
            {
                "predecessor": task_keys[item.predecessor_task_id],
                "successor": task_keys[item.successor_task_id],
                "type": item.dependency_type.value,
                "lag_days": item.lag_days,
            }
            for item in project_slice.dependencies[: limits.items]
            if item.predecessor_task_id in task_keys and item.successor_task_id in task_keys
        ]
        track("dependencies", len(project_slice.dependencies), len(rendered_dependencies))
        rendered_projects.append(
            {
                "key": project_slice.project.key,
                "name": clip(project_slice.project.name),
                "status": project_slice.project.status.value,
                "description": clip(project_slice.project.description_md),
                "start_date": _iso(project_slice.project.start_date),
                "due_date": _iso(project_slice.project.due_date),
                "signals": _build_signals(slices=[project_slice], today=today).model_dump(
                    mode="json"
                ),
                "stages": rendered_stages,
                "team": rendered_members,
                "wbs": rendered_nodes,
                "milestones": rendered_milestones,
                "tasks": rendered_tasks,
                "checklists": rendered_checklists,
                "comments": rendered_comments,
                "participants": rendered_participants,
                "attachments": rendered_attachments,
                "dependencies": rendered_dependencies,
                "stickers": rendered_stickers,
                "documents": rendered_documents,
                "recent_activity": rendered_activity,
                "registered_risks": rendered_risks,
                "entity_counts": project_coverage,
            }
        )
        gaps = [
            f"{_ENTITY_LABELS[name]} {count['included']} из {count['total']}"
            for name, count in project_coverage.items()
            if count["included"] < count["total"]
        ]
        if gaps:
            omitted.append(f"{project_slice.project.key}: в контекст вошли " + "; ".join(gaps))
        if shortened:
            omitted.append(
                f"{project_slice.project.key}: сокращено текстовых фрагментов — {shortened}; "
                "многоточие означает, что передано начало текста"
            )

    context = AnalyticsContextSchema(
        projects=len(slices),
        tasks_total=coverage["tasks"]["total"],
        tasks_included=coverage["tasks"]["included"],
        comments_included=coverage["comments"]["included"],
        documents_included=coverage["documents"]["included"],
        stickers_included=coverage["stickers"]["included"],
        wbs_nodes_included=coverage["wbs_nodes"]["included"],
        milestones_included=coverage["milestones"]["included"],
        activity_included=coverage["activity"]["included"],
        risks_total=signals.total_risks,
        risks_included=coverage["risks"]["included"],
        entity_counts=coverage,
        truncated=bool(omitted),
        omitted=omitted,
    )
    return {
        "today": today.isoformat(),
        "scope": scope.value,
        "signals": signals.model_dump(mode="json"),
        "projects": rendered_projects,
    }, context


def _linked_keys(task_ids: list[int], task_keys: dict[int, str], limit: int) -> list[str]:
    """Сохраняет только реальные связи с задачами текущего проекта."""
    return [task_keys[task_id] for task_id in task_ids if task_id in task_keys][:limit]


def _iso(value: date | datetime | None) -> str | None:
    """Сериализует календарные даты и фактическое время без потери точности."""
    return value.isoformat() if value else None


def _member_identity(member: ProjectMember) -> dict[str, str]:
    """Передаёт только рабочую идентичность без контактов, паролей и аватара."""
    user = member.user
    return {
        "username": user.username,
        "name": " ".join(filter(None, (user.last_name, user.first_name, user.middle_name))),
    }


def _render_risks(project_slice: _ProjectSlice, *, limit: int) -> list[dict[str, Any]]:
    """Добавляет текущий реестр, ответственных и реальные ключи связанных задач."""
    task_keys = {
        task.id: build_task_key(project_key=project_slice.project.key, number=task.number)
        for task in project_slice.tasks
    }
    members = {member.user_id: member for member in project_slice.members}
    risks = sorted(
        project_slice.risks,
        key=lambda item: (
            item.status == "CLOSED",
            {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[item.risk_level],
            item.review_date or date.max,
            -item.id,
        ),
    )
    result = []
    for risk in risks[:limit]:
        item = ProjectRiskSchema.model_validate(risk).model_dump(
            mode="json", exclude={"created_at", "updated_at", "id", "project_id"}
        )
        item["task_key"] = task_keys.get(item.pop("task_id"))
        owner = members.get(item.pop("owner_user_id"))
        item["owner"] = _member_identity(owner) if owner else None
        result.append(item)
    return result


def _select_tasks(
    tasks: list[Task],
    stages: list[ProjectStage],
    limit: int,
    today: date,
) -> list[Task]:
    """Отбирает задачи для среза: сначала то, что требует решения.

    Порядок отбора и есть порядок важности: просроченные, затем срочные, затем
    остальные открытые по свежести, и только потом закрытые.
    """
    done_stage_ids = _done_stage_ids(stages)
    soon_until = today + timedelta(days=DUE_SOON_DAYS)

    def rank(task: Task) -> tuple[int, float]:
        is_done = task.stage_id in done_stage_ids
        if is_task_overdue(due_date=task.due_date, is_done=is_done, today=today):
            group = 0
        elif is_task_due_soon(
            due_date=task.due_date,
            is_done=is_done,
            today=today,
            soon_until=soon_until,
        ):
            group = 1
        elif not is_done:
            group = 2
        else:
            group = 3
        updated = task.updated_at.timestamp() if task.updated_at else 0.0
        return (group, -updated)

    return sorted(tasks, key=rank)[:limit]


def _select_documents(
    documents: list[Document],
    document_task_ids: dict[int, list[int]],
    limit: int,
) -> list[Document]:
    """Отбирает документы для среза, начиная со связанных с задачами.

    Документ, прикреплённый к задаче, почти всегда объясняет саму работу;
    остальные добираются следом, пока не исчерпан лимит.
    """
    ordered = sorted(
        documents,
        key=lambda document: (0 if document_task_ids.get(document.id) else 1, document.id),
    )
    return ordered[:limit]


def _done_stage_ids(stages: list[ProjectStage]) -> set[int]:
    """Возвращает идентификаторы завершающих стадий проекта."""
    return {stage.id for stage in stages if stage.is_done_stage}


def _node_paths(nodes: list[WbsNode]) -> dict[int, str]:
    """Строит номер и путь каждого раздела ИСР вида ``1.2 Проектирование``."""
    children: dict[int | None, list[WbsNode]] = {}
    for node in nodes:
        children.setdefault(node.parent_id, []).append(node)
    for group in children.values():
        group.sort(key=lambda node: (node.position, node.id))

    paths: dict[int, str] = {}

    def walk(parent_id: int | None, prefix: str) -> None:
        for index, node in enumerate(children.get(parent_id, []), start=1):
            number = f"{prefix}{index}"
            paths[node.id] = f"{number} {node.title}"
            walk(node.id, f"{number}.")

    walk(None, "")
    return paths


def _wbs_task_counts(tasks: list[Task]) -> dict[int, int]:
    """Считает число задач в каждом разделе ИСР."""
    counts: dict[int, int] = {}
    for task in tasks:
        if task.wbs_node_id is not None:
            counts[task.wbs_node_id] = counts.get(task.wbs_node_id, 0) + 1
    return counts


def _blocked_by(
    dependencies: list[TaskDependency],
    done_stage_ids: set[int],
    tasks: list[Task],
    project_key: str,
) -> dict[int, list[str]]:
    """Возвращает ключи незакрытых предшественников для каждой задачи."""
    tasks_by_id = {task.id: task for task in tasks}
    blocked: dict[int, list[str]] = {}
    for dependency in dependencies:
        predecessor = tasks_by_id.get(dependency.predecessor_task_id)
        if predecessor is None or predecessor.stage_id in done_stage_ids:
            continue
        key = build_task_key(project_key=project_key, number=predecessor.number)
        blocked.setdefault(dependency.successor_task_id, []).append(key)
    return blocked


def _cut(value: str, limit: int) -> str:
    """Обрезает текст по лимиту, помечая обрыв многоточием."""
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


# Блок сверки ответа модели с реальными данными.


def _resolve_draft(
    draft: AnalyticsDraftSchema,
    slices: list[_ProjectSlice],
    today: date,
    outside: _OutsideTasks | None = None,
) -> dict[str, Any]:
    """Заменяет ключи из ответа модели проверенными ссылками на задачи.

    Ключ, которого нет в области анализа, молча отбрасывается: выдуманная
    ссылка хуже её отсутствия, потому что выглядит достоверно.
    """
    tasks_by_key: dict[str, AnalyticsTaskRefSchema] = {}
    project_names: dict[str, str] = {}
    for project_slice in slices:
        project_names[project_slice.project.key] = project_slice.project.name
        done_stage_ids = _done_stage_ids(project_slice.stages)
        for task in project_slice.tasks:
            key = build_task_key(project_key=project_slice.project.key, number=task.number)
            tasks_by_key[key] = AnalyticsTaskRefSchema(
                id=task.id,
                key=key,
                title=task.title,
                project_key=project_slice.project.key,
                due_date=task.due_date,
                is_overdue=is_task_overdue(
                    due_date=task.due_date,
                    is_done=task.stage_id in done_stage_ids,
                    today=today,
                ),
            )

    for task in outside.tasks if outside is not None else []:
        key = outside.key(task)
        if key not in tasks_by_key:
            tasks_by_key[key] = AnalyticsTaskRefSchema(id=task.id, key=key, title=task.title, project_key=PERSONAL_TASK_KEY, due_date=task.due_date, is_overdue=is_task_overdue(due_date=task.due_date, is_done=False, today=today))

    def refs(task_keys: list[str]) -> list[AnalyticsTaskRefSchema]:
        seen: set[str] = set()
        resolved = []
        for raw in task_keys:
            key = raw.strip().upper()
            if key in tasks_by_key and key not in seen:
                seen.add(key)
                resolved.append(tasks_by_key[key])
        return resolved

    def project_key(raw: str | None) -> str | None:
        return raw if raw in project_names else None

    findings = [
        AnalyticsFindingSchema(
            kind=item.kind,
            severity=item.severity,
            title=item.title,
            detail=item.detail,
            project_key=project_key(item.project_key),
            project_name=project_names.get(item.project_key or ""),
            tasks=refs(item.task_keys),
        )
        for item in draft.findings
    ]
    progress = [
        AnalyticsProgressSchema(
            title=item.title,
            detail=item.detail,
            project_key=project_key(item.project_key),
            project_name=project_names.get(item.project_key or ""),
            tasks=refs(item.task_keys),
        )
        for item in draft.progress
    ]
    recommendations = [
        AnalyticsRecommendationSchema(
            horizon=item.horizon,
            title=item.title,
            detail=item.detail,
            project_key=project_key(item.project_key),
            project_name=project_names.get(item.project_key or ""),
            tasks=refs(item.task_keys),
        )
        for item in draft.recommendations
    ]
    return {
        "headline": draft.headline,
        "health": draft.health.value,
        "health_note": draft.health_note,
        "findings": [item.model_dump(mode="json") for item in findings],
        "progress": [item.model_dump(mode="json") for item in progress],
        "recommendations": [item.model_dump(mode="json") for item in recommendations],
    }


def _to_report_schema(report: AnalyticsReport, project: Project | None) -> AnalyticsReportSchema:
    """Собирает публичный контракт свода из сохранённой записи.

    Проект передаётся отдельно, а не берётся из связи записи: ленивая загрузка
    в асинхронной сессии за пределами репозитория недопустима.
    """
    payload = report.payload or {}
    return AnalyticsReportSchema(
        id=report.id,
        scope=AnalyticsScope.PROJECT if report.project_id else AnalyticsScope.PORTFOLIO,
        project_id=report.project_id,
        project_key=project.key if project else None,
        project_name=project.name if project else None,
        created_at=report.created_at,
        created_by=report.created_by_display_name_snapshot,
        llm_model=report.llm_model,
        duration_ms=report.duration_ms,
        headline=payload.get("headline", ""),
        health=AnalyticsHealth(payload.get("health", AnalyticsHealth.WATCH.value)),
        health_note=payload.get("health_note", ""),
        findings=payload.get("findings", []),
        progress=payload.get("progress", []),
        recommendations=payload.get("recommendations", []),
        signals=payload.get("signals", {}),
        context=report.context_summary or {},
    )


def _render_outside(outside: _OutsideTasks, *, limits: _Limits, today: date) -> dict[str, Any]:
    """Короткие карточки задач пользователя в потоках и разовых.

    Счётчики считаются здесь же: signals среза относятся только к проектам,
    а смешивать с ними задачи без стадий и вех значило бы исказить показатели
    карточек проектов.
    """
    soon_until = today + timedelta(days=DUE_SOON_DAYS)
    rendered = []
    overdue = due_soon = no_due = 0
    for task in outside.tasks:
        entry: dict[str, Any] = {
            "key": outside.key(task),
            "title": _cut(task.title, limits.description_chars),
            "status": task.status.value if task.status else None,
            "priority": task.priority.value,
            "assignee": task.assignee or None,
            "due": _iso(task.due_date),
            "updated": _iso(task.updated_at),
            "my_roles": sorted(role.value for role in outside.my_roles.get(task.id, set())),
        }
        if is_task_overdue(due_date=task.due_date, is_done=False, today=today):
            entry["overdue_days"] = (today - task.due_date).days
            overdue += 1
        elif is_task_due_soon(
            due_date=task.due_date, is_done=False, today=today, soon_until=soon_until
        ):
            entry["due_soon"] = True
            due_soon += 1
        if task.due_date is None:
            no_due += 1
        rendered.append(entry)
    return {
        "open_tasks": len(rendered),
        "overdue_tasks": overdue,
        "due_soon_tasks": due_soon,
        "no_due_date_tasks": no_due,
        "limit": OUTSIDE_TASKS_LIMIT,
        "tasks": rendered,
    }
