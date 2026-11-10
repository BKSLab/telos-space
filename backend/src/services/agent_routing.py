"""Выбор проекта и личный обзор по данным локального пользователя.

Контекст страницы проверяется отдельно от текста и не даёт дополнительных прав.
"""

import json
import logging
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from src.agent.aris import ArisMode
from src.agent.context import AgentContextBudget
from src.agent.progress import ProgressSink
from src.agent.tools import AgentToolContext
from src.clients.llm import LlmClient
from src.exceptions.ai import AgentAnswerError, AiProviderError
from src.exceptions.base import ServiceError
from src.exceptions.clients import ClientError
from src.prompts.agent_routing import AGENT_ROUTING_SYSTEM_PROMPT
from src.schemas.knowledge import KnowledgeAnswerSchema, KnowledgeChatMessageSchema
from src.services.agent_tool_scope import AgentProjectToolScope
from src.services.board_selection import checked_board_selection
from src.services.project_agent import API_CHANNEL, ProjectAgentService

logger = logging.getLogger(__name__)

#: Ключ задачи в тексте: KEY-12, где KEY — ключ проекта или «T».
TASK_KEY_PATTERN = re.compile(r"(?<![\w-])([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_]{0,31})-(\d{1,9})\b")
#: Сколько открытых задач пользователя уходит в контекст выбора.
MAX_OVERVIEW_TASKS = 80
ROUTER_TEXT_LIMIT = 1200

#: «Кроме EVL», «без EVL-3»: названный так ключ исключает проект, а не выбирает его.
EXCLUSION_BEFORE_KEY = re.compile(
    r"(?:кроме|помимо|исключая|без|не\s+считая|except|excluding)"
    r"(?:\s+(?:проект|задач)[а-яё]*)?[\s:«\"']*$",
    re.IGNORECASE,
)

AgentRoutingScopeFactory = Callable[[], AbstractAsyncContextManager[AgentProjectToolScope]]


def _excluded(text: str, start: int) -> bool:
    """Ключ стоит после слова-исключения: такой вопрос решает модель выбора."""
    return EXCLUSION_BEFORE_KEY.search(text[max(0, start - 40) : start]) is not None


class AgentRoutingDecision(BaseModel):
    """Решение модели: куда направить вопрос без проекта."""

    mode: Literal["project", "overview", "clarify"] = Field(
        description="project — один проект; overview — обзор на чтение; clarify — уточнение."
    )
    project_key: str | None = Field(default=None, max_length=64)
    answer: str = Field(default="", max_length=20000)

    @field_validator("answer", mode="before")
    @classmethod
    def null_answer_is_empty(cls, value: object) -> object:
        """Gemini отдаёт ``answer: null`` при mode=project — это пустой текст, а не сбой."""
        return "" if value is None else value


@dataclass(frozen=True, slots=True)
class AgentRoutingAnswer:
    """Ответ по трекеру и проект, в котором он получен (None — обзор или уточнение)."""

    answer: KnowledgeAnswerSchema
    project_id: int | None
    project_key: str | None
    mode: str


@dataclass(frozen=True, slots=True)
class _Overview:
    projects: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    truncated: bool


@dataclass(frozen=True, slots=True)
class _PageContext:
    """Проверенный контекст страницы: только то, что пользователю доступно."""

    route: str | None = None
    project_id: int | None = None
    project_key: str | None = None
    task_id: int | None = None
    task_project_id: int | None = None
    board_id: int | None = None
    board_title: str | None = None
    board_selection: list[dict[str, Any]] | None = None

    def for_model(self) -> dict[str, Any]:
        """Контекст для модели: маршрут и проверенные поля; пусто — ключа нет."""
        data = {
            "route": self.route,
            "project_key": self.project_key,
            "task_id": self.task_id,
            "board_id": self.board_id,
            "board_title": self.board_title,
            "board_selection": self.board_selection if self.board_id is not None else None,
        }
        return {key: value for key, value in data.items() if value is not None}


class AgentRoutingService:
    """Выбирает проект для вопроса без проекта и отвечает в пределах прав пользователя."""

    def __init__(
        self,
        *,
        scope: AgentRoutingScopeFactory,
        agent: ProjectAgentService,
        llm_client: LlmClient,
        context_tokens: int = 12000,
        history_tokens: int = 1200,
    ) -> None:
        self.scope = scope
        self.agent = agent
        self.llm_client = llm_client
        self.context_tokens = context_tokens
        self.history_tokens = history_tokens

    async def ask(
        self,
        *,
        user_id: int,
        question: str,
        verbatim: str | None,
        history: list[KnowledgeChatMessageSchema],
        memory: str,
        actor: dict[str, Any],
        execution: AgentToolContext,
        action_history: list[dict[str, Any]],
        pinned_project_id: int | None = None,
        page_context: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        on_progress: ProgressSink | None = None,
    ) -> AgentRoutingAnswer:
        """Отвечает на вопрос без проекта: в проекте, обзором или уточнением.

        Args:
            user_id: Пользователь, от имени которого задан вопрос.
            question: Пересказ вызывающего агента.
            verbatim: Дословная реплика пользователя; без неё — только чтение.
            history: Недавние реплики беседы.
            memory: Свёрнутая память ранней части беседы.
            actor: Участник для контекста модели.
            execution: Попытка очереди и права ключа; проект подставит сервис.
            action_history: Сохранённые действия беседы.
            pinned_project_id: Проект продолжения после решения по действию —
                повторный выбор не нужен и не должен увести в другой проект.
            page_context: Экран трекера, открытый у пользователя (route,
                project_key, task_id). Права не даёт: недоступное
                отбрасывается, проект из него — только если ключа нет в тексте.
            conversation_id: Беседа для трассировки (идентификатора диалога).
            on_progress: Запись живого статуса ответа в проекте (передаётся агенту).
        Returns:
            Ответ и проект, в котором он получен.
        Raises:
            AgentAnswerError: Данные трекера прочитать не удалось.
            AiProviderError: Модель не смогла подготовить ответ.
        """
        overview = await self._load_overview(user_id)
        keys = {item["key"].upper(): item for item in overview.projects}
        page = await self._check_page(user_id, page_context or {}, overview)
        by_id = {item["id"]: item for item in overview.projects}
        target = None
        if pinned_project_id is not None:
            target = next(
                (item for item in overview.projects if item["id"] == pinned_project_id), None
            )
            if target is None:
                raise AgentAnswerError("Проект действия больше недоступен пользователю.")
        else:
            mentioned = self._mentioned_projects(f"{verbatim or ''}\n{question}", keys)
            if len(mentioned) == 1:
                target = keys[mentioned.pop()]
            elif not mentioned:
                # Ключ в тексте главнее страницы; без ключа — проект страницы,
                # затем проект открытой задачи. Оба уже проверены на доступ.
                target = by_id.get(page.project_id) or by_id.get(page.task_project_id)
        route = None
        if (
            target is None
            and page.board_id is not None
            and page.project_id is None
            and not self._mentioned_projects(f"{verbatim or ''}\n{question}", keys)
        ):
            route = AgentRoutingDecision(mode="overview", answer="")
        if target is None and route is None:
            route = await self._route(
                question=question,
                verbatim=verbatim,
                history=history,
                memory=memory,
                actor=actor,
                overview=overview,
                page=page,
            )
            if route.mode == "project":
                target = keys.get((route.project_key or "").strip().upper())
                if target is None:
                    # Модель назвала проект вне списка пользователя: не угадываем.
                    route = AgentRoutingDecision(
                        mode="clarify",
                        answer=self._clarify_text(overview),
                    )
        if target is None:
            assert route is not None
            if route.mode == "overview":
                answer = await self.agent.ask_personal(
                    question=verbatim or question,
                    history=history,
                    memory=memory,
                    actor=actor,
                    execution=replace(
                        execution,
                        project_id=None,
                        can_write=execution.can_write and verbatim is not None,
                    ),
                    action_history=action_history,
                    page_context={
                        **{
                            key: value
                            for key, value in (page_context or {}).items()
                            if key
                            not in {
                                "board_id",
                                "board_title",
                                "board_selection",
                                "project_key",
                                "task_id",
                            }
                        },
                        **page.for_model(),
                    },
                    conversation_id=conversation_id,
                    on_progress=on_progress,
                )
                return AgentRoutingAnswer(
                    answer=answer, project_id=None, project_key=None, mode="overview"
                )
            answer = route.answer.strip() or self._clarify_text(overview)
            logger.info(
                "🧭 Агент трекера: ответ без проекта (%s), участник=%s.", route.mode, user_id
            )
            return AgentRoutingAnswer(
                answer=KnowledgeAnswerSchema(answer=answer, sources=[]),
                project_id=None,
                project_key=None,
                mode=route.mode,
            )
        can_write = await self._can_write(user_id, target["id"])
        logger.info(
            "🧭 Агент трекера: вопрос направлен в проект %s, участник=%s, роль с записью=%s, "
            "дословная реплика=%s.",
            target["key"],
            user_id,
            can_write and execution.can_write,
            verbatim is not None,
        )
        answer = await self.agent.ask(
            project_id=target["id"],
            question=question,
            history=history,
            memory=memory,
            actor=actor,
            execution=replace(
                execution,
                project_id=target["id"],
                can_write=execution.can_write and can_write,
            ),
            action_history=action_history,
            mode=ArisMode.PROJECT_CHAT,
            extra_context={"page_context": page.for_model()},
            channel=API_CHANNEL,
            verbatim=verbatim,
            conversation_id=conversation_id,
            on_progress=on_progress,
        )
        return AgentRoutingAnswer(
            answer=answer,
            project_id=target["id"],
            project_key=target["key"],
            mode="project",
        )

    async def _load_overview(self, user_id: int) -> _Overview:
        try:
            async with self.scope() as db:
                projects = await db.projects.get_project_list(user_id)
                tasks = await db.tasks.get_my_tasks(user_id=user_id, role="all")
        except ServiceError as error:
            raise AgentAnswerError(str(error)) from error
        return _Overview(
            projects=[
                {
                    "id": project.id,
                    "key": project.key,
                    "name": project.name,
                    "status": str(project.status),
                    "my_role": "OWNER" if project.owner_id == user_id else "MEMBER",
                }
                for project in projects
            ],
            tasks=[
                {
                    "key": task.key,
                    "title": task.title,
                    "where": (
                        f"{task.context.kind}:{task.context.key} {task.context.name}"
                        if task.context
                        else "разовая"
                    ),
                    "state_label": task.state_label,
                    "priority": str(task.priority) if task.priority else None,
                    "due_date": task.due_date.isoformat() if task.due_date else None,
                    "my_roles": [str(role) for role in task.my_roles],
                }
                for task in tasks[:MAX_OVERVIEW_TASKS]
            ],
            truncated=len(tasks) > MAX_OVERVIEW_TASKS,
        )

    async def _check_page(
        self, user_id: int, raw: dict[str, Any], overview: _Overview
    ) -> _PageContext:
        """Оставляет из контекста страницы только доступное пользователю.

        Проект — из списка проектов пользователя; задача — через проверку доступа
        к ней (проект задачи учитывается, только если он тоже в списке).
        Остальное молча отбрасывается.
        """
        by_key = {item["key"].upper(): item for item in overview.projects}
        by_id = {item["id"]: item for item in overview.projects}
        project = by_key.get(str(raw.get("project_key") or "").strip().upper())
        task_id = raw.get("task_id")
        task_project_id = None
        board_id = raw.get("board_id")
        if not isinstance(board_id, int) or isinstance(board_id, bool) or board_id <= 0:
            board_id = None
        board_title = None
        board_selection = None
        try:
            async with self.scope() as db:
                if task_id is not None:
                    try:
                        grant = await db.access.ensure_task_access(
                            task_id=int(task_id), user_id=user_id
                        )
                    except ServiceError:
                        task_id = None
                    else:
                        if grant.project_id is not None and grant.project_id not in by_id:
                            task_id = None
                        else:
                            task_project_id = grant.project_id
                if board_id is not None:
                    try:
                        if project is not None:
                            scene = await db.boards.get_content(
                                project_id=project["id"], board_id=board_id
                            )
                        elif not raw.get("project_key"):
                            scene = await db.boards.get_free_content(
                                board_id=board_id, user_id=user_id
                            )
                        else:
                            scene = None
                    except ServiceError:
                        scene = None
                    if scene is None:
                        board_id = None
                    else:
                        board_title = scene.board.title
                        if raw.get("board_selection") is not None:
                            board_selection = checked_board_selection(
                                raw["board_selection"],
                                board_id=board_id,
                                shapes=scene.shapes,
                                stickers=scene.stickers,
                            )
        except ServiceError as error:
            raise AgentAnswerError(str(error)) from error
        route = raw.get("route")
        return _PageContext(
            route=str(route) if route else None,
            project_id=project["id"] if project else None,
            project_key=project["key"] if project else None,
            task_id=int(task_id) if task_id is not None else None,
            task_project_id=task_project_id,
            board_id=board_id,
            board_title=board_title,
            board_selection=board_selection,
        )

    async def _can_write(self, user_id: int, project_id: int) -> bool:
        try:
            async with self.scope() as db:
                grant = await db.access.ensure_project_access(
                    project_id=project_id, user_id=user_id
                )
        except ServiceError as error:
            raise AgentAnswerError("Проект недоступен пользователю.") from error
        return grant.can_write

    async def _route(
        self,
        *,
        question: str,
        verbatim: str | None,
        history: list[KnowledgeChatMessageSchema],
        memory: str,
        actor: dict[str, Any],
        overview: _Overview,
        page: _PageContext,
    ) -> AgentRoutingDecision:
        recent = {
            "question": question,
            "dialog_history": [message.model_dump() for message in history],
            "dialog_memory": memory,
        }
        AgentContextBudget(
            system_prompt=AGENT_ROUTING_SYSTEM_PROMPT,
            max_tokens=self.context_tokens,
            history_tokens=self.history_tokens,
        ).prepare(recent)
        content = json.dumps(
            {
                "current_date": date.today().isoformat(),
                "question": question,
                "participant_verbatim": verbatim,
                "current_participant": actor,
                "page_context": page.for_model(),
                "dialog_memory": recent["dialog_memory"],
                "dialog_history": [
                    {"role": item["role"], "content": item["content"][:ROUTER_TEXT_LIMIT]}
                    for item in recent["dialog_history"]
                ],
                "projects": [
                    {key: value for key, value in item.items() if key != "id"}
                    for item in overview.projects
                ],
                "my_tasks": overview.tasks,
                "my_tasks_truncated": overview.truncated,
                "task_link_template": self.agent.task_link_template(),
            },
            ensure_ascii=False,
        )
        try:
            return await self.llm_client.get_structured_response(
                system_prompt=AGENT_ROUTING_SYSTEM_PROMPT,
                content=content,
                schema=AgentRoutingDecision,
                max_completion_tokens=3000,
            )
        except ClientError as error:
            raise AiProviderError(str(error)) from error

    @staticmethod
    def _mentioned_projects(text: str, keys: dict[str, dict[str, Any]]) -> set[str]:
        """Проекты пользователя, названные прямо в тексте ключом задачи или проекта.

        Ключ проекта засчитывается только отдельным словом и точно как записан
        (``EVL``, не ``evl``): так обычное слово не уводит вопрос в проект.
        Раньше голый ключ проекта отдавался выбору моделью — лишние ~4–6 с
        на вопрос, у которого ответ однозначен (замер 30.09.2026). Ключ после
        «кроме», «без», «помимо» проект исключает — его не считаем.
        """
        tasks = {
            match.group(1).upper()
            for match in TASK_KEY_PATTERN.finditer(text)
            if match.group(1).upper() in keys and not _excluded(text, match.start())
        }
        named = {
            key
            for key in keys
            for match in re.finditer(rf"(?<![\w-]){re.escape(key)}(?![\w-])", text)
            if not _excluded(text, match.start())
        }
        return tasks | named

    @staticmethod
    def _clarify_text(overview: _Overview) -> str:
        if not overview.projects:
            return (
                "У пользователя нет доступных проектов в трекере. Уточните задачу по ключу "
                "или спросите о его разовых задачах."
            )
        names = ", ".join(f"{item['key']} — {item['name']}" for item in overview.projects[:15])
        return f"Уточните, о каком проекте речь. Доступные проекты пользователя: {names}."
