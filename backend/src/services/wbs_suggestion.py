import json
import logging
from time import perf_counter

from src.clients.llm import LlmClient
from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.projects import Project
from src.db.models.task_activity import TaskActivityEventType
from src.db.models.tasks import Task
from src.db.models.wbs_nodes import WbsNode
from src.exceptions.ai import AiProviderError
from src.exceptions.clients import ClientError
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.project_stages import ProjectStagesRepositoryError
from src.exceptions.projects import ProjectNotFoundError, ProjectsRepositoryError
from src.exceptions.task_activity import TaskActivityRepositoryError
from src.exceptions.tasks import TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.exceptions.wbs_nodes import (
    WbsNodesRepositoryError,
    WbsNodesServiceError,
    WbsSuggestionEmptyError,
    WbsSuggestionError,
    WbsSuggestionInvalidError,
)
from src.knowledge.documents import build_wbs_paths
from src.prompts.wbs_suggestion import WBS_SUGGESTION_SYSTEM_PROMPT
from src.schemas.wbs_suggestion import (
    MAX_SUGGESTED_DEPTH,
    MAX_SUGGESTED_NODES,
    WbsSuggestedAssignmentSchema,
    WbsSuggestedNodeSchema,
    WbsSuggestionApplyResultSchema,
    WbsSuggestionSchema,
)
from src.services.db_scope import WbsSuggestionScope, WbsSuggestionScopeFactory
from src.services.tasks import build_task_key
from src.services.wbs_nodes import POSITION_STEP
from src.utils.checklists import checklist_context

logger = logging.getLogger(__name__)

MAX_SUGGESTION_TASKS = 300
MAX_COMPLETION_TOKENS = 6000
TITLE_LIMIT = 255

RepositoryErrors = (
    WbsNodesRepositoryError,
    ProjectsRepositoryError,
    ProjectStagesRepositoryError,
    TasksRepositoryError,
    TaskActivityRepositoryError,
    KnowledgeEventsServiceError,
    UnitOfWorkRepositoryError,
)


class WbsSuggestionService:
    """Черновик ИСР от модели и его применение к проекту.

    Предложение и применение разделены намеренно: модель ничего не меняет в
    проекте, а пользователь применяет уже отредактированный черновик. Поэтому
    ``apply`` заново проверяет структуру целиком и не доверяет тому, что
    когда-то ответила модель.
    """

    def __init__(
        self,
        *,
        scope: WbsSuggestionScopeFactory,
        llm_client: LlmClient,
    ):
        """Создаёт сервис предложения структуры ИСР.

        Args:
            scope: Фабрика короткой области работы с базой. Сессия не
                передаётся: она не должна оставаться открытой во время
                вызова модели.
            llm_client: Клиент chat completions.
        """
        self.scope = scope
        self.llm_client = llm_client

    async def suggest(self, project_id: int) -> WbsSuggestionSchema:
        """Просит модель разложить задачи проекта по разделам ИСР.

        Проект не изменяется: ответ модели нормализуется и возвращается как
        черновик.

        Args:
            project_id: Идентификатор проекта.

        Returns:
            Предложенная структура и размещение задач.

        Raises:
            ProjectNotFoundError: Если проект не найден.
            WbsSuggestionEmptyError: Если в проекте нет задач.
            WbsSuggestionError: Если модель вернула непригодный ответ.
            WbsNodesServiceError: Если подготовить данные не удалось.
        """
        started_at = perf_counter()
        # Короткая DB-фаза: снимок собирается, и область закрывается до
        # обращения к модели. Иначе соединение оставалось бы занятым всё
        # время ожидания ответа.
        try:
            async with self.scope() as db:
                project = await self._get_project(db, project_id=project_id)
                nodes = await db.wbs_nodes.get_by_project(project_id=project_id)
                tasks = await db.tasks.get_by_project(project_id=project_id)
                stages = await db.stages.get_by_project(project_id=project_id)
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка подготовки данных для предложения ИСР проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise WbsNodesServiceError(str(error)) from error

        if not tasks:
            raise WbsSuggestionEmptyError(error_details=f"В проекте id={project_id} нет задач.")

        limited_tasks = tasks[:MAX_SUGGESTION_TASKS]
        content = self._build_content(
            project=project,
            nodes=nodes,
            tasks=limited_tasks,
            stage_names={stage.id: stage.name for stage in stages},
        )
        try:
            output = await self.llm_client.get_structured_response(
                system_prompt=WBS_SUGGESTION_SYSTEM_PROMPT,
                content=content,
                schema=WbsSuggestionSchema,
                max_completion_tokens=MAX_COMPLETION_TOKENS,
            )
        except ClientError as error:
            logger.error("❌ LLM недоступен при подготовке предложения ИСР.", exc_info=True)
            raise AiProviderError(str(error)) from error
        except Exception as error:
            logger.error("❌ Модель не вернула предложение ИСР.", exc_info=True)
            raise WbsSuggestionError(str(error)) from error

        suggestion = self._normalize(output=output, tasks=limited_tasks)
        logger.info(
            "🤖 Предложение ИСР для проекта id=%s: разделов %s, задач %s, %s мс.",
            project_id,
            len(suggestion.nodes),
            len(suggestion.assignments),
            round((perf_counter() - started_at) * 1000),
        )
        return suggestion

    async def apply(
        self,
        project_id: int,
        nodes: list[WbsSuggestedNodeSchema],
        assignments: list[WbsSuggestedAssignmentSchema],
    ) -> WbsSuggestionApplyResultSchema:
        """Создаёт предложенные разделы и переносит в них задачи.

        Разделы добавляются к существующей структуре, задачи переносятся
        одной транзакцией: частично применённое предложение хуже, чем
        неприменённое.

        Args:
            project_id: Идентификатор проекта.
            nodes: Разделы черновика, отредактированные пользователем.
            assignments: Размещение задач по разделам черновика.

        Returns:
            Количество созданных разделов и перенесённых задач.

        Raises:
            ProjectNotFoundError: Если проект не найден.
            WbsSuggestionInvalidError: Если черновик не проходит проверку.
            WbsNodesServiceError: Если применить черновик не удалось.
        """
        ordered_nodes = _validate_draft(nodes=nodes, assignments=assignments)

        # Применение к модели не обращается, поэтому чтение и запись идут
        # в одной короткой области и одной транзакции.
        try:
            async with self.scope() as db:
                project = await self._get_project(db, project_id=project_id)
                existing_nodes = await db.wbs_nodes.get_by_project(project_id=project_id)
                tasks = await db.tasks.get_by_project(project_id=project_id)
                tasks_by_id = {task.id: task for task in tasks}
                unknown = [
                    item.task_id for item in assignments if item.task_id not in tasks_by_id
                ]
                if unknown:
                    raise WbsSuggestionInvalidError(
                        reason=f"задачи {unknown} не принадлежат проекту.",
                    )
                root_position = _next_root_position(existing_nodes)
                created_ids: dict[str, int] = {}
                for index, draft in enumerate(ordered_nodes):
                    parent_id = (
                        created_ids[draft.parent_temp_id] if draft.parent_temp_id is not None else None
                    )
                    position = (
                        root_position + index * POSITION_STEP
                        if parent_id is None
                        else (index + 1) * POSITION_STEP
                    )
                    created = await db.wbs_nodes.save(
                        data={
                            "project_id": project_id,
                            "parent_id": parent_id,
                            "title": draft.title.strip()[:TITLE_LIMIT],
                            "position": position,
                        }
                    )
                    created_ids[draft.temp_id] = created.id

                nodes_by_temp_id = {draft.temp_id: draft for draft in ordered_nodes}
                titles_by_id = {node.id: node.title for node in existing_nodes} | {
                    created_ids[temp_id]: draft.title for temp_id, draft in nodes_by_temp_id.items()
                }

                positions: dict[int, float] = {}
                moved_task_ids: list[int] = []
                for item in assignments:
                    task = tasks_by_id[item.task_id]
                    node_id = created_ids[item.node_temp_id]
                    if task.wbs_node_id == node_id:
                        continue
                    positions[node_id] = positions.get(node_id, 0.0) + POSITION_STEP
                    await db.activity.save(
                        task_id=task.id,
                        event_type=TaskActivityEventType.WBS_NODE_CHANGED,
                        from_value=titles_by_id.get(task.wbs_node_id) if task.wbs_node_id else None,
                        to_value=titles_by_id.get(node_id),
                    )
                    await db.tasks.update(
                        task=task,
                        data={
                            "wbs_node_id": node_id,
                            "wbs_position": positions[node_id],
                            "canvas_x": None,
                            "canvas_y": None,
                        },
                    )
                    moved_task_ids.append(task.id)

                if moved_task_ids:
                    await db.knowledge_events.upsert_many(
                        project_id=project_id,
                        entity_type=KnowledgeEntityType.TASK,
                        entity_ids=moved_task_ids,
                    )
                await db.unit_of_work.commit()
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка применения предложения ИСР в проекте id=%s.",
                project_id,
                exc_info=True,
            )
            raise WbsNodesServiceError(str(error)) from error

        logger.info(
            "✅ Предложение ИСР применено в проекте %s: разделов %s, задач %s.",
            project.key,
            len(ordered_nodes),
            len(moved_task_ids),
        )
        return WbsSuggestionApplyResultSchema(
            created_nodes=len(ordered_nodes),
            assigned_tasks=len(moved_task_ids),
        )

    @staticmethod
    async def _get_project(db: WbsSuggestionScope, *, project_id: int) -> Project:
        """Возвращает проект или поднимает доменную ошибку."""
        project = await db.projects.get_by_id(project_id=project_id)
        if project is None:
            raise ProjectNotFoundError(project_id=project_id)
        return project

    @staticmethod
    def _build_content(
        project: Project,
        nodes: list[WbsNode],
        tasks: list[Task],
        stage_names: dict[int, str],
    ) -> str:
        """Собирает вход модели: текущая структура и задачи проекта."""
        paths = build_wbs_paths(nodes)
        return json.dumps(
            {
                "project": {
                    "name": project.name,
                    "description": project.description_md,
                },
                "existing_structure": [
                    {"node_id": node.id, "path": paths.get(node.id, node.title)} for node in nodes
                ],
                "tasks": [
                    {
                        "task_id": task.id,
                        "key": build_task_key(project_key=project.key, number=task.number),
                        "title": task.title,
                        "checklist": checklist_context(getattr(task, "checklist", None)),
                        "priority": task.priority.value,
                        "stage": stage_names.get(task.stage_id),
                        "current_section": (
                            paths.get(task.wbs_node_id) if task.wbs_node_id else None
                        ),
                    }
                    for task in tasks
                ],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _normalize(output: WbsSuggestionSchema, tasks: list[Task]) -> WbsSuggestionSchema:
        """Приводит ответ модели к структуре, которую можно показать и применить.

        Ответ модели — недоверенные данные: лишние разделы, ссылки на чужие
        задачи и повторные размещения отбрасываются, а не исправляются
        додумыванием.
        """
        task_ids = {task.id for task in tasks}
        nodes: list[WbsSuggestedNodeSchema] = []
        seen_temp_ids: set[str] = set()
        for node in output.nodes[:MAX_SUGGESTED_NODES]:
            title = node.title.strip()[:TITLE_LIMIT]
            if title == "" or node.temp_id in seen_temp_ids:
                continue
            seen_temp_ids.add(node.temp_id)
            nodes.append(node.model_copy(update={"title": title}))

        # Ссылка на несуществующего родителя превращает раздел в корневой:
        # так пользователь хотя бы увидит его в черновике.
        known_ids = {node.temp_id for node in nodes}
        nodes = [
            node
            if node.parent_temp_id in known_ids and node.parent_temp_id != node.temp_id
            else node.model_copy(update={"parent_temp_id": None})
            for node in nodes
        ]
        nodes = _drop_cycles(nodes)
        nodes = _drop_deep_nodes(nodes)

        known_ids = {node.temp_id for node in nodes}
        assignments: list[WbsSuggestedAssignmentSchema] = []
        assigned_ids: set[int] = set()
        for assignment in output.assignments:
            if (
                assignment.task_id not in task_ids
                or assignment.task_id in assigned_ids
                or assignment.node_temp_id not in known_ids
            ):
                continue
            assigned_ids.add(assignment.task_id)
            assignments.append(assignment)

        return WbsSuggestionSchema(
            nodes=nodes,
            assignments=assignments,
            summary=output.summary.strip(),
            skipped_task_ids=sorted(task_ids - assigned_ids),
        )


def _next_root_position(nodes: list[WbsNode]) -> float:
    """Возвращает позицию, с которой начинаются новые корневые разделы."""
    roots = [node.position for node in nodes if node.parent_id is None]
    return max(roots) + POSITION_STEP if roots else POSITION_STEP


def _drop_cycles(nodes: list[WbsSuggestedNodeSchema]) -> list[WbsSuggestedNodeSchema]:
    """Убирает разделы, чьи ссылки на родителей образуют цикл."""
    by_temp_id = {node.temp_id: node for node in nodes}
    result: list[WbsSuggestedNodeSchema] = []
    for node in nodes:
        visited: set[str] = {node.temp_id}
        current = node.parent_temp_id
        while current is not None and current not in visited:
            visited.add(current)
            current = by_temp_id[current].parent_temp_id if current in by_temp_id else None
        if current is None:
            result.append(node)
    return result


def _drop_deep_nodes(nodes: list[WbsSuggestedNodeSchema]) -> list[WbsSuggestedNodeSchema]:
    """Отбрасывает разделы глубже допустимого уровня вместе с их ветками."""
    by_temp_id = {node.temp_id: node for node in nodes}

    def depth(node: WbsSuggestedNodeSchema) -> int:
        level = 1
        current = node.parent_temp_id
        while current is not None and current in by_temp_id:
            level += 1
            current = by_temp_id[current].parent_temp_id
        return level

    kept = {node.temp_id for node in nodes if depth(node) <= MAX_SUGGESTED_DEPTH}
    return [
        node
        for node in nodes
        if node.temp_id in kept and (node.parent_temp_id is None or node.parent_temp_id in kept)
    ]


def _validate_draft(
    nodes: list[WbsSuggestedNodeSchema],
    assignments: list[WbsSuggestedAssignmentSchema],
) -> list[WbsSuggestedNodeSchema]:
    """Проверяет черновик и возвращает разделы в порядке создания.

    Порядок важен: родитель должен быть создан раньше потомка, иначе для
    вложенного раздела неоткуда взять ``parent_id``.

    Args:
        nodes: Разделы черновика.
        assignments: Размещение задач по разделам черновика.

    Returns:
        Разделы, упорядоченные от корней к листьям.

    Raises:
        WbsSuggestionInvalidError: Если структура черновика некорректна.
    """
    if len(nodes) > MAX_SUGGESTED_NODES:
        raise WbsSuggestionInvalidError(reason=f"разделов больше {MAX_SUGGESTED_NODES}.")

    by_temp_id: dict[str, WbsSuggestedNodeSchema] = {}
    for node in nodes:
        if node.temp_id in by_temp_id:
            raise WbsSuggestionInvalidError(reason=f"раздел {node.temp_id} повторяется.")
        if node.title.strip() == "":
            raise WbsSuggestionInvalidError(reason=f"у раздела {node.temp_id} пустое название.")
        by_temp_id[node.temp_id] = node

    for node in nodes:
        if node.parent_temp_id is not None and node.parent_temp_id not in by_temp_id:
            raise WbsSuggestionInvalidError(
                reason=f"родитель {node.parent_temp_id} отсутствует в черновике.",
            )

    seen_tasks: set[int] = set()
    for assignment in assignments:
        if assignment.node_temp_id not in by_temp_id:
            raise WbsSuggestionInvalidError(
                reason=f"раздел {assignment.node_temp_id} отсутствует в черновике.",
            )
        if assignment.task_id in seen_tasks:
            raise WbsSuggestionInvalidError(
                reason=f"задача {assignment.task_id} размещена дважды.",
            )
        seen_tasks.add(assignment.task_id)

    ordered: list[WbsSuggestedNodeSchema] = []
    placed: set[str] = set()
    remaining = list(nodes)
    while remaining:
        ready = [
            node
            for node in remaining
            if node.parent_temp_id is None or node.parent_temp_id in placed
        ]
        if not ready:
            raise WbsSuggestionInvalidError(reason="разделы ссылаются друг на друга по кругу.")
        for node in ready:
            depth = _draft_depth(node=node, by_temp_id=by_temp_id)
            if depth > MAX_SUGGESTED_DEPTH:
                raise WbsSuggestionInvalidError(
                    reason=f"вложенность глубже {MAX_SUGGESTED_DEPTH} уровней.",
                )
            ordered.append(node)
            placed.add(node.temp_id)
        remaining = [node for node in remaining if node.temp_id not in placed]
    return ordered


def _draft_depth(
    node: WbsSuggestedNodeSchema,
    by_temp_id: dict[str, WbsSuggestedNodeSchema],
) -> int:
    """Возвращает уровень вложенности раздела черновика, считая от единицы."""
    level = 1
    current = node.parent_temp_id
    while current is not None and current in by_temp_id:
        level += 1
        current = by_temp_id[current].parent_temp_id
    return level
