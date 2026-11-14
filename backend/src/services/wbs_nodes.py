import logging
from collections import defaultdict
from datetime import date

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.projects import Project
from src.db.models.task_activity import TaskActivityEventType
from src.db.models.tasks import Task
from src.db.models.wbs_nodes import WbsNode
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.project_stages import ProjectStagesRepositoryError
from src.exceptions.projects import ProjectNotFoundError, ProjectsRepositoryError
from src.exceptions.task_activity import TaskActivityRepositoryError
from src.exceptions.tasks import (
    TaskForeignProjectError,
    TaskNotFoundError,
    TasksRepositoryError,
)
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.exceptions.wbs_nodes import (
    WbsNodeCycleError,
    WbsNodeForeignProjectError,
    WbsNodeNotFoundError,
    WbsNodesRepositoryError,
    WbsNodesServiceError,
)
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.wbs_nodes import WbsNodesRepository
from src.schemas.tasks import TaskCompactSchema
from src.schemas.users import UserSummarySchema
from src.schemas.wbs_nodes import (
    WbsNodeDeleteResultSchema,
    WbsNodeSchema,
    WbsStatsSchema,
    WbsStructureSchema,
)
from src.services.knowledge_events import KnowledgeEvents
from src.services.tasks import build_task_key, executor_summary
from src.utils.deadlines import is_task_overdue

logger = logging.getLogger(__name__)

POSITION_STEP = 1000.0
MIN_POSITION_GAP = 0.001

RepositoryErrors = (
    WbsNodesRepositoryError,
    ProjectsRepositoryError,
    ProjectStagesRepositoryError,
    TasksRepositoryError,
    TaskActivityRepositoryError,
    KnowledgeEventsServiceError,
    UnitOfWorkRepositoryError,
)


class WbsNodesService:
    """Сервис сценариев работы со структурой ИСР проекта.

    Сервис владеет всей логикой позиционирования: клиент передаёт только
    целевого родителя и соседа, а разреженные позиции и их уплотнение
    рассчитываются здесь.
    """

    def __init__(
        self,
        wbs_nodes_repository: WbsNodesRepository,
        projects_repository: ProjectsRepository,
        stages_repository: ProjectStagesRepository,
        tasks_repository: TasksRepository,
        activity_repository: TaskActivityRepository,
        unit_of_work: UnitOfWork,
        knowledge_events: KnowledgeEvents,
        participants_repository: TaskParticipantsRepository,
    ):
        self.wbs_nodes_repository = wbs_nodes_repository
        self.projects_repository = projects_repository
        self.stages_repository = stages_repository
        self.tasks_repository = tasks_repository
        self.activity_repository = activity_repository
        self.unit_of_work = unit_of_work
        self.knowledge_events = knowledge_events
        self.participants_repository = participants_repository

    async def get_structure(self, project_id: int) -> WbsStructureSchema:
        """Возвращает структуру проекта одним ответом.

        Ответ содержит плоские списки узлов и компактных задач, включая
        нераспределённые: этого достаточно, чтобы клиент собрал дерево,
        посчитал номера и прогресс без дополнительных запросов.

        Args:
            project_id: Идентификатор проекта.

        Returns:
            Узлы, задачи и сводка по структуре проекта.

        Raises:
            ProjectNotFoundError: Если проект не найден.
            WbsNodesServiceError: Если собрать структуру не удалось.
        """
        try:
            project = await self._get_project(project_id=project_id)
            nodes = await self.wbs_nodes_repository.get_by_project(project_id=project_id)
            tasks = await self.tasks_repository.get_by_project(project_id=project_id)
            stages = await self.stages_repository.get_by_project(project_id=project_id)
            done_stage_ids = {stage.id for stage in stages if stage.is_done_stage}
            today = date.today()
            participants = await self.participants_repository.get_by_task_ids(
                [task.id for task in tasks]
            )

            compact_tasks = [
                _to_compact_task(
                    task=task,
                    project_key=project.key,
                    is_done=task.stage_id in done_stage_ids,
                    executor=executor_summary(participants.get(task.id, [])),
                )
                for task in tasks
            ]
            assigned = sum(1 for task in tasks if task.wbs_node_id is not None)
            done = sum(1 for task in tasks if task.stage_id in done_stage_ids)
            overdue = sum(
                1
                for task in tasks
                if is_task_overdue(
                    due_date=task.due_date,
                    is_done=task.stage_id in done_stage_ids,
                    today=today,
                )
            )
            return WbsStructureSchema(
                nodes=[WbsNodeSchema.model_validate(node) for node in nodes],
                tasks=compact_tasks,
                stats=WbsStatsSchema(
                    total_nodes=len(nodes),
                    total_tasks=len(tasks),
                    assigned_tasks=assigned,
                    unassigned_tasks=len(tasks) - assigned,
                    done_tasks=done,
                    overdue_tasks=overdue,
                ),
            )
        except RepositoryErrors as error:
            logger.error("❌ Ошибка получения структуры проекта id=%s.", project_id, exc_info=True)
            raise WbsNodesServiceError(str(error)) from error

    async def create_node(
        self,
        project_id: int,
        title: str,
        parent_id: int | None,
    ) -> WbsNodeSchema:
        """Создаёт раздел ИСР в конце выбранного уровня.

        Args:
            project_id: Идентификатор проекта.
            title: Название раздела.
            parent_id: Родительский раздел или ``None`` для верхнего уровня.

        Returns:
            Созданный раздел.

        Raises:
            ProjectNotFoundError: Если проект не найден.
            WbsNodeNotFoundError: Если родительский раздел не найден.
            WbsNodeForeignProjectError: Если родитель принадлежит другому проекту.
            WbsNodesServiceError: Если создать раздел не удалось.
        """
        try:
            await self._get_project(project_id=project_id)
            nodes = await self.wbs_nodes_repository.get_by_project(project_id=project_id)
            if parent_id is not None:
                self._require_node(nodes=nodes, node_id=parent_id, project_id=project_id)
            siblings = [node for node in nodes if node.parent_id == parent_id]
            position = (
                max(node.position for node in siblings) + POSITION_STEP
                if siblings
                else POSITION_STEP
            )
            node = await self.wbs_nodes_repository.save(
                data={
                    "project_id": project_id,
                    "parent_id": parent_id,
                    "title": title,
                    "position": position,
                }
            )
            await self.unit_of_work.commit()
            logger.info("✅ Раздел ИСР %r создан в проекте id=%s.", title, project_id)
            return WbsNodeSchema.model_validate(node)
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка создания раздела ИСР в проекте id=%s.", project_id, exc_info=True
            )
            raise WbsNodesServiceError(str(error)) from error

    async def update_node(self, project_id: int, node_id: int, title: str) -> WbsNodeSchema:
        """Переименовывает раздел ИСР.

        Args:
            project_id: Идентификатор проекта.
            node_id: Идентификатор раздела.
            title: Новое название раздела.

        Returns:
            Обновлённый раздел.

        Raises:
            WbsNodeNotFoundError: Если раздел не найден.
            WbsNodeForeignProjectError: Если раздел принадлежит другому проекту.
            WbsNodesServiceError: Если обновить раздел не удалось.
        """
        try:
            node = await self._get_node_in_project(node_id=node_id, project_id=project_id)
            nodes = await self.wbs_nodes_repository.get_by_project(project_id=project_id)
            updated = await self.wbs_nodes_repository.update(node=node, data={"title": title})
            affected_ids = _collect_subtree_ids(nodes=nodes, root_id=node_id)
            task_ids = await self.tasks_repository.get_ids_by_wbs_nodes(affected_ids)
            await self.knowledge_events.upsert_many(
                project_id=project_id,
                entity_type=KnowledgeEntityType.TASK,
                entity_ids=task_ids,
            )
            await self.unit_of_work.commit()
            return WbsNodeSchema.model_validate(updated)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка переименования раздела ИСР id=%s.", node_id, exc_info=True)
            raise WbsNodesServiceError(str(error)) from error

    async def move_node(
        self,
        project_id: int,
        node_id: int,
        parent_id: int | None,
        before_id: int | None,
    ) -> WbsNodeSchema:
        """Переносит раздел в структуре и пересчитывает его позицию.

        Args:
            project_id: Идентификатор проекта.
            node_id: Перемещаемый раздел.
            parent_id: Новый родитель или ``None`` для верхнего уровня.
            before_id: Сосед, перед которым нужно встать, или ``None`` — в конец.

        Returns:
            Перемещённый раздел.

        Raises:
            WbsNodeNotFoundError: Если раздел, родитель или сосед не найдены.
            WbsNodeForeignProjectError: Если узел принадлежит другому проекту.
            WbsNodeCycleError: Если перенос создал бы цикл.
            WbsNodesServiceError: Если переместить раздел не удалось.
        """
        try:
            nodes = await self.wbs_nodes_repository.get_by_project(project_id=project_id)
            node = self._require_node(nodes=nodes, node_id=node_id, project_id=project_id)
            parent_changed = node.parent_id != parent_id
            if parent_id is not None:
                self._require_node(nodes=nodes, node_id=parent_id, project_id=project_id)
                self._ensure_no_cycle(nodes=nodes, node_id=node_id, parent_id=parent_id)

            siblings = sorted(
                (item for item in nodes if item.parent_id == parent_id and item.id != node_id),
                key=lambda item: (item.position, item.id),
            )
            before_index = self._resolve_before_index(
                nodes=nodes,
                siblings=siblings,
                before_id=before_id,
                project_id=project_id,
            )
            position = _next_position(
                positions=[sibling.position for sibling in siblings],
                before_index=before_index,
            )
            if position is None:
                position = await self._compact_positions(
                    siblings=siblings,
                    before_index=before_index,
                )
            updated = await self.wbs_nodes_repository.update(
                node=node,
                data={"parent_id": parent_id, "position": position},
            )
            if parent_changed:
                affected_ids = _collect_subtree_ids(nodes=nodes, root_id=node_id)
                task_ids = await self.tasks_repository.get_ids_by_wbs_nodes(affected_ids)
                await self.knowledge_events.upsert_many(
                    project_id=project_id,
                    entity_type=KnowledgeEntityType.TASK,
                    entity_ids=task_ids,
                )
            await self.unit_of_work.commit()
            logger.info("✅ Раздел ИСР id=%s перемещён в родителя %s.", node_id, parent_id)
            return WbsNodeSchema.model_validate(updated)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка перемещения раздела ИСР id=%s.", node_id, exc_info=True)
            raise WbsNodesServiceError(str(error)) from error

    async def delete_node(self, project_id: int, node_id: int) -> WbsNodeDeleteResultSchema:
        """Удаляет раздел с подразделами и возвращает его задачи в пул.

        Задачи никогда не удаляются вместе с разделом: они лишь теряют
        привязку и снова становятся нераспределёнными.

        Args:
            project_id: Идентификатор проекта.
            node_id: Идентификатор удаляемого раздела.

        Returns:
            Количество удалённых разделов и освобождённых задач.

        Raises:
            WbsNodeNotFoundError: Если раздел не найден.
            WbsNodeForeignProjectError: Если раздел принадлежит другому проекту.
            WbsNodesServiceError: Если удалить раздел не удалось.
        """
        try:
            nodes = await self.wbs_nodes_repository.get_by_project(project_id=project_id)
            node = self._require_node(nodes=nodes, node_id=node_id, project_id=project_id)
            affected_ids = _collect_subtree_ids(nodes=nodes, root_id=node_id)
            task_ids = await self.tasks_repository.get_ids_by_wbs_nodes(affected_ids)
            released_tasks = await self.tasks_repository.clear_wbs_node(node_ids=affected_ids)
            await self.wbs_nodes_repository.delete(node=node)
            await self.knowledge_events.upsert_many(
                project_id=project_id,
                entity_type=KnowledgeEntityType.TASK,
                entity_ids=task_ids,
            )
            await self.unit_of_work.commit()
            logger.info(
                "✅ Раздел ИСР id=%s удалён: разделов %s, задач возвращено в пул %s.",
                node_id,
                len(affected_ids),
                released_tasks,
            )
            return WbsNodeDeleteResultSchema(
                deleted_nodes=len(affected_ids),
                released_tasks=released_tasks,
            )
        except RepositoryErrors as error:
            logger.error("❌ Ошибка удаления раздела ИСР id=%s.", node_id, exc_info=True)
            raise WbsNodesServiceError(str(error)) from error

    async def assign_task(
        self,
        project_id: int,
        task_id: int,
        wbs_node_id: int,
        before_task_id: int | None = None,
    ) -> TaskCompactSchema:
        """Помещает задачу в раздел ИСР того же проекта.

        Args:
            project_id: Идентификатор проекта.
            task_id: Идентификатор задачи.
            wbs_node_id: Целевой раздел ИСР.
            before_task_id: Задача раздела, перед которой встаёт перемещаемая.

        Returns:
            Компактное представление обновлённой задачи.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            TaskForeignProjectError: Если задача принадлежит другому проекту.
            WbsNodeNotFoundError: Если раздел не найден.
            WbsNodeForeignProjectError: Если раздел принадлежит другому проекту.
            WbsNodesServiceError: Если назначить задачу не удалось.
        """
        return await self.place_task(
            project_id=project_id,
            task_id=task_id,
            wbs_node_id=wbs_node_id,
            before_task_id=before_task_id,
        )

    async def unassign_task(self, project_id: int, task_id: int) -> TaskCompactSchema:
        """Возвращает задачу в пул нераспределённых.

        Args:
            project_id: Идентификатор проекта.
            task_id: Идентификатор задачи.

        Returns:
            Компактное представление обновлённой задачи.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            TaskForeignProjectError: Если задача принадлежит другому проекту.
            WbsNodesServiceError: Если снять привязку не удалось.
        """
        return await self.place_task(
            project_id=project_id,
            task_id=task_id,
            wbs_node_id=None,
        )

    async def place_task(
        self,
        project_id: int,
        task_id: int,
        wbs_node_id: int | None,
        before_task_id: int | None = None,
        canvas_x: float | None = None,
        canvas_y: float | None = None,
    ) -> TaskCompactSchema:
        """Размещает задачу в разделе ИСР, на холсте или в списке-пуле.

        Все три состояния задачи описываются одной операцией, потому что для
        пользователя это одно и то же перетаскивание карточки. Привязка к
        разделу очищает координаты холста: положение блока внутри структуры
        считает раскладка.

        Args:
            project_id: Идентификатор проекта.
            task_id: Идентификатор задачи.
            wbs_node_id: Целевой раздел ИСР или ``None`` для места вне структуры.
            before_task_id: Задача раздела, перед которой встаёт перемещаемая.
            canvas_x: Координата X на холсте; учитывается вне структуры.
            canvas_y: Координата Y на холсте; учитывается вне структуры.

        Returns:
            Компактное представление обновлённой задачи.

        Raises:
            ProjectNotFoundError: Если проект не найден.
            TaskNotFoundError: Если задача не найдена.
            TaskForeignProjectError: Если задача принадлежит другому проекту.
            WbsNodeNotFoundError: Если раздел не найден.
            WbsNodeForeignProjectError: Если раздел принадлежит другому проекту.
            WbsNodesServiceError: Если разместить задачу не удалось.
        """
        try:
            project = await self._get_project(project_id=project_id)
            task = await self.tasks_repository.get_by_id(task_id=task_id)
            if task is None:
                raise TaskNotFoundError(task_id=task_id)
            if task.project_id != project_id:
                raise TaskForeignProjectError(task_id=task_id, project_id=project_id)

            nodes = await self.wbs_nodes_repository.get_by_project(project_id=project_id)
            nodes_by_id = {node.id: node for node in nodes}
            if wbs_node_id is not None:
                self._require_node(nodes=nodes, node_id=wbs_node_id, project_id=project_id)

            changes = await self._build_placement(
                task=task,
                wbs_node_id=wbs_node_id,
                before_task_id=before_task_id,
                canvas_x=canvas_x,
                canvas_y=canvas_y,
            )
            if changes is None:
                return await self._to_compact(task=task, project=project)

            node_changed = task.wbs_node_id != wbs_node_id
            if node_changed:
                from_node = nodes_by_id.get(task.wbs_node_id) if task.wbs_node_id else None
                to_node = nodes_by_id.get(wbs_node_id) if wbs_node_id else None
                await self.activity_repository.save(
                    task_id=task_id,
                    event_type=TaskActivityEventType.WBS_NODE_CHANGED,
                    from_value=from_node.title if from_node else None,
                    to_value=to_node.title if to_node else None,
                )
            updated = await self.tasks_repository.update(task=task, data=changes)
            # Перекладывание карточки по холсту не меняет содержания задачи,
            # поэтому переиндексация нужна только при смене раздела.
            if node_changed:
                await self.knowledge_events.upsert(
                    project_id=project_id,
                    entity_type=KnowledgeEntityType.TASK,
                    entity_id=task_id,
                )
            result = await self._to_compact(task=updated, project=project)
            await self.unit_of_work.commit()
            return result
        except RepositoryErrors as error:
            logger.error("❌ Ошибка размещения задачи id=%s.", task_id, exc_info=True)
            raise WbsNodesServiceError(str(error)) from error

    async def _build_placement(
        self,
        task: Task,
        wbs_node_id: int | None,
        before_task_id: int | None,
        canvas_x: float | None,
        canvas_y: float | None,
    ) -> dict | None:
        """Считает новые поля размещения задачи.

        Returns:
            Изменяемые поля либо ``None``, если размещение не поменялось.
        """
        if wbs_node_id is None:
            # Точку задаёт пара координат: одна без другой смысла не имеет.
            on_canvas = canvas_x is not None and canvas_y is not None
            changes = {
                "wbs_node_id": None,
                "wbs_position": None,
                "canvas_x": canvas_x if on_canvas else None,
                "canvas_y": canvas_y if on_canvas else None,
            }
        elif task.wbs_node_id == wbs_node_id and before_task_id is None:
            # Возврат задачи в собственный раздел без соседа ничего не меняет.
            return None
        else:
            position = await self._resolve_task_position(
                node_id=wbs_node_id,
                task_id=task.id,
                before_task_id=before_task_id,
            )
            changes = {
                "wbs_node_id": wbs_node_id,
                "wbs_position": position,
                "canvas_x": None,
                "canvas_y": None,
            }
        if all(getattr(task, field) == value for field, value in changes.items()):
            return None
        return changes

    async def _resolve_task_position(
        self,
        node_id: int,
        task_id: int,
        before_task_id: int | None,
    ) -> float:
        """Возвращает позицию задачи среди задач раздела.

        Неизвестный ``before_task_id`` означает вставку в конец: задача могла
        быть перенесена другим пользователем, пока карточку тащили.
        """
        siblings = [
            sibling
            for sibling in await self.tasks_repository.get_by_wbs_node(node_id=node_id)
            if sibling.id != task_id
        ]
        # Задачи, размещённые до появления порядка внутри раздела, позиций не
        # имеют — расставляем их в текущем порядке отображения.
        if any(sibling.wbs_position is None for sibling in siblings):
            await self._compact_task_positions(siblings=siblings)

        before_index = len(siblings)
        for index, sibling in enumerate(siblings):
            if sibling.id == before_task_id:
                before_index = index
                break

        position = _next_position(
            positions=[sibling.wbs_position or 0.0 for sibling in siblings],
            before_index=before_index,
        )
        if position is not None:
            return position
        await self._compact_task_positions(siblings=siblings)
        return (
            _next_position(
                positions=[sibling.wbs_position or 0.0 for sibling in siblings],
                before_index=before_index,
            )
            or POSITION_STEP
        )

    async def _compact_task_positions(self, siblings: list[Task]) -> None:
        """Переприсваивает задачам раздела равномерные разреженные позиции."""
        positions = {
            sibling.id: float(index + 1) * POSITION_STEP for index, sibling in enumerate(siblings)
        }
        await self.tasks_repository.update_wbs_positions(positions=positions)
        for sibling in siblings:
            sibling.wbs_position = positions[sibling.id]
        logger.info("✅ Позиции задач раздела ИСР уплотнены: %s задач.", len(siblings))

    async def _to_compact(self, task: Task, project: Project) -> TaskCompactSchema:
        """Строит компактное представление задачи с признаком выполнения."""
        stages = await self.stages_repository.get_by_project(project_id=project.id)
        done_stage_ids = {stage.id for stage in stages if stage.is_done_stage}
        participants = await self.participants_repository.get_by_task_ids([task.id])
        return _to_compact_task(
            task=task,
            project_key=project.key,
            is_done=task.stage_id in done_stage_ids,
            executor=executor_summary(participants.get(task.id, [])),
        )

    async def _get_project(self, project_id: int) -> Project:
        """Возвращает проект или поднимает доменную ошибку."""
        project = await self.projects_repository.get_by_id(project_id=project_id)
        if project is None:
            raise ProjectNotFoundError(project_id=project_id)
        return project

    async def _get_node_in_project(self, node_id: int, project_id: int) -> WbsNode:
        """Возвращает узел, проверив его принадлежность проекту."""
        node = await self.wbs_nodes_repository.get_by_id(node_id=node_id)
        if node is None:
            raise WbsNodeNotFoundError(node_id=node_id)
        if node.project_id != project_id:
            raise WbsNodeForeignProjectError(node_id=node_id, project_id=project_id)
        return node

    @staticmethod
    def _require_node(nodes: list[WbsNode], node_id: int, project_id: int) -> WbsNode:
        """Находит узел среди узлов проекта или поднимает доменную ошибку."""
        for node in nodes:
            if node.id == node_id:
                return node
        raise WbsNodeNotFoundError(node_id=node_id)

    @staticmethod
    def _ensure_no_cycle(nodes: list[WbsNode], node_id: int, parent_id: int) -> None:
        """Запрещает перенос узла внутрь самого себя или собственного потомка."""
        if node_id == parent_id:
            raise WbsNodeCycleError(node_id=node_id, parent_id=parent_id)
        if parent_id in _collect_subtree_ids(nodes=nodes, root_id=node_id):
            raise WbsNodeCycleError(node_id=node_id, parent_id=parent_id)

    def _resolve_before_index(
        self,
        nodes: list[WbsNode],
        siblings: list[WbsNode],
        before_id: int | None,
        project_id: int,
    ) -> int:
        """Возвращает индекс вставки среди соседей уровня."""
        if before_id is None:
            return len(siblings)
        self._require_node(nodes=nodes, node_id=before_id, project_id=project_id)
        for index, sibling in enumerate(siblings):
            if sibling.id == before_id:
                return index
        return len(siblings)

    async def _compact_positions(self, siblings: list[WbsNode], before_index: int) -> float:
        """Уплотняет позиции уровня и возвращает свободную позицию вставки."""
        positions = {
            sibling.id: float(index + 1) * POSITION_STEP for index, sibling in enumerate(siblings)
        }
        await self.wbs_nodes_repository.update_positions(positions=positions)
        for sibling in siblings:
            sibling.position = positions[sibling.id]
        logger.info("✅ Позиции уровня ИСР уплотнены: %s узлов.", len(siblings))
        return (
            _next_position(
                positions=[sibling.position for sibling in siblings],
                before_index=before_index,
            )
            or POSITION_STEP
        )


def _next_position(positions: list[float], before_index: int) -> float | None:
    """Возвращает позицию вставки или ``None``, если промежуток исчерпан.

    Правило одно и для разделов уровня, и для задач внутри раздела, поэтому
    функция работает с голым списком позиций.

    Args:
        positions: Позиции соседей, отсортированные по возрастанию.
        before_index: Индекс, перед которым выполняется вставка.

    Returns:
        Свободная позиция либо ``None``, когда требуется уплотнение.
    """
    if not positions:
        return POSITION_STEP
    if before_index >= len(positions):
        return positions[-1] + POSITION_STEP
    next_position = positions[before_index]
    if before_index == 0:
        return next_position / 2 if next_position > MIN_POSITION_GAP else None
    previous_position = positions[before_index - 1]
    if next_position - previous_position <= MIN_POSITION_GAP:
        return None
    return (previous_position + next_position) / 2


def _collect_subtree_ids(nodes: list[WbsNode], root_id: int) -> set[int]:
    """Возвращает идентификаторы узла и всех его потомков."""
    children_by_parent: dict[int | None, list[WbsNode]] = defaultdict(list)
    for node in nodes:
        children_by_parent[node.parent_id].append(node)

    collected = {root_id}
    queue = [root_id]
    while queue:
        current_id = queue.pop()
        for child in children_by_parent.get(current_id, []):
            if child.id not in collected:
                collected.add(child.id)
                queue.append(child.id)
    return collected


def _to_compact_task(
    task: Task,
    project_key: str,
    is_done: bool,
    executor: UserSummarySchema | None = None,
) -> TaskCompactSchema:
    """Строит компактное представление задачи для структуры ИСР."""
    return TaskCompactSchema(
        id=task.id,
        key=build_task_key(project_key=project_key, number=task.number),
        checklist=getattr(task, "checklist", None),
        title=task.title,
        stage_id=task.stage_id,
        wbs_node_id=task.wbs_node_id,
        wbs_position=task.wbs_position,
        canvas_x=task.canvas_x,
        canvas_y=task.canvas_y,
        priority=task.priority,
        assignee=task.assignee,
        executor=executor,
        start_date=task.start_date,
        due_date=task.due_date,
        is_done=is_done,
    )
