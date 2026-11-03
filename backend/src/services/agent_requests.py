"""Результат вопроса внешнего агента: текст, ссылки, выполненное и ждущее решения.

Внешний клиент не видит карточек чата трекера, поэтому то, что в чате
показывают карточки, здесь отдаётся структурой: задачи ответа со ссылками,
выполненные действия (``done``) и предложенные (``pending``) — со ссылкой, по
которой сотрудник сам подтверждает действие (``/?confirm=<id>``). Параметры
действия берутся из журнала действий, а не из текста модели.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from src.agent.wakeup import wake_agent_worker
from src.db.models.agent_conversations import AgentConversation
from src.db.models.agent_messages import AgentMessage
from src.db.models.agent_tool_runs import AgentToolRun
from src.exceptions.access import AccessServiceError, ResourceNotAvailableError
from src.exceptions.agent_conversations import (
    AgentConversationNotFoundError,
    AgentConversationsServiceError,
    AgentMessageConflictError,
    AgentRequestBusyError,
    AgentRequestNotFoundError,
)
from src.exceptions.base import RepositoryError
from src.schemas.agent_requests import (
    AgentActionLinkSchema,
    AgentAskSchema,
    AgentRequestResultSchema,
    AgentTaskLinkSchema,
)
from src.services.auth import Principal
from src.services.db_scope import AgentConversationScope, AgentConversationScopeFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RequestLinks:
    """Ссылки на интерфейс трекера из ``APP_PUBLIC_URL``; пусто — ссылок нет."""

    base_url: str

    def task(self, key: str | None) -> str | None:
        """Карточка задачи по ключу: ``/?task=KEY``."""
        base = self.base_url.rstrip("/")
        return f"{base}/?task={quote(key, safe='')}" if base and key else None

    def confirm(self, action_id: Any) -> str | None:
        """Страница решения по предложенному действию: ``/?confirm=<id>``."""
        base = self.base_url.rstrip("/")
        return f"{base}/?confirm={action_id}" if base else None


def principal_key_scopes(principal: Principal) -> list[str] | None:
    """Права, которыми ограничены изменения агента; None — без ограничения ключом.

    Сервисный ключ — ровно его права (ключ = права сотрудника ∩ права ключа).
    Личный токен на чтение изменений не разрешает. Вход через локальная сессия и токен на
    запись ограничены только ролью сотрудника в проекте.
    """
    if principal.api_key is not None:
        return sorted(principal.api_key.scopes)
    return None if principal.can_write else []


def principal_key_id(principal: Principal) -> UUID | None:
    """Ключ, которому принадлежат беседа и результат; None — не сервисный ключ."""
    return principal.api_key.key_id if principal.api_key is not None else None


class AgentRequestsService:
    """Вопросы внешнего агента: постановка в очередь ответов и чтение результата.

    Ответ готовит тот же worker, что и в виджете Ариса; сервис лишь заводит
    беседу канала api, кладёт вопрос в очередь и отдаёт результат его автору.
    """

    def __init__(self, *, scope: AgentConversationScopeFactory, links: RequestLinks) -> None:
        self.scope = scope
        self.links = links

    async def ask_external(
        self,
        *,
        user_id: int,
        api_key_id: UUID | None,
        data: AgentAskSchema,
        key_scopes: list[str] | None,
    ) -> UUID:
        """Ставит вопрос внешнего агента в очередь ответов (канал api).

        Беседа ищется по ключу ``conversation_id`` в пределах сотрудника и ключа
        доступа: чужой ключ с тем же значением получает свою беседу. Без ключа
        беседы каждый вопрос — новый диалог.

        Args:
            user_id: Сотрудник, от имени которого задан вопрос (владелец ключа ).
            api_key_id: Сервисный ключ; None — локальная сессия или личный токен.
            data: Пересказ, дословная реплика, ключ беседы и контекст экрана.
            key_scopes: Права ключа на момент вопроса; None — без ограничения ключом.
        Returns:
            ID вопроса для получения результата.
        Raises:
            AgentRequestBusyError: В беседе ещё готовится прошлый ответ.
            AgentConversationsServiceError: При ошибке хранения.
        """
        request_id = data.request_id or uuid4()
        async with self._scope() as db:
            user = await db.users.get_by_id(user_id)
            if user is None or not user.is_active:
                raise AgentConversationNotFoundError("Сотрудник недоступен.")
            await db.conversations.lock_external(user_id, api_key_id, f"request:{request_id}")
            existing = await db.messages.get_answer_by_request(request_id, user_id=user_id, api_key_id=api_key_id)
            if existing is not None:
                previous = await db.conversations.get_by_id(existing.conversation_id)
                if (previous is None or previous.user_id != user_id or previous.api_key_id != api_key_id
                        or previous.channel != "api" or previous.external_key != data.conversation_id):
                    raise AgentMessageConflictError("Ключ запроса уже использован.")
                pair = await db.messages.get_request(previous.id, request_id)
                question = next(item for item in pair if item.role == "user")
                page = data.page_context.model_dump(mode="json") if data.page_context else None
                if (question.content != data.text or question.verbatim != ((data.user_text or "").strip() or None)
                        or question.page_context != page):
                    raise AgentMessageConflictError("Ключ запроса использован с другими параметрами.")
                return request_id
            conversation = None
            if data.conversation_id is not None:
                await db.conversations.lock_external(user_id, api_key_id, f"conversation:{data.conversation_id}")
                conversation = await db.conversations.get_external(
                    user_id=user_id,
                    api_key_id=api_key_id,
                    external_key=data.conversation_id,
                    lock=True,
                )
            if conversation is None:
                conversation = await db.conversations.save(
                    dict(
                        project_id=None,
                        user_id=user_id,
                        title=data.text[:120],
                        channel="api",
                        external_key=data.conversation_id,
                        api_key_id=api_key_id,
                    )
                )
            else:
                active = await db.messages.get_active(conversation.id)
                if active is not None:
                    raise AgentRequestBusyError(str(active.request_id))
                latest = await db.messages.get_page(conversation.id, before_id=None, limit=1)
                if latest:
                    # Как в чате: новый вопрос закрывает неподтверждённые действия
                    # прошлого ответа, иначе решение по старой ссылке применилось бы
                    # к уже ушедшему вперёд разговору.
                    await db.runs.reject_pending(latest[0].id)
            common = dict(conversation_id=conversation.id, request_id=request_id)
            verbatim = (data.user_text or "").strip() or None
            await db.messages.save_pair(
                [
                    {
                        **common,
                        "role": "user",
                        "content": data.text,
                        "status": "completed",
                        "files": [],
                        "verbatim": verbatim,
                        "key_scopes": sorted(key_scopes) if key_scopes is not None else None,
                        # Экран трекера у сотрудника — подсказка «эта задача», не право.
                        "page_context": (
                            data.page_context.model_dump(mode="json") if data.page_context else None
                        ),
                    },
                    {
                        **common,
                        "role": "assistant",
                        "content": "",
                        "status": "queued",
                        "files": [],
                        "verbatim": None,
                        "key_scopes": None,
                        "page_context": None,
                    },
                ]
            )
            # Поднимает беседу в updated_at: по нему видно, когда в ней спрашивали последний раз.
            await db.conversations.update(conversation.id, {"title": conversation.title})
            await db.unit_of_work.commit()
        wake_agent_worker()
        logger.info(
            "📨 Вопрос агенту трекера принят: request_id=%s, участник=%s, беседа=%s, запись=%s.",
            request_id,
            user_id,
            data.conversation_id,
            verbatim is not None,
        )
        return request_id

    async def get_external_result(
        self, *, user_id: int, api_key_id: UUID | None, request_id: UUID
    ) -> AgentRequestResultSchema:
        """Возвращает состояние вопроса только тому ключу и сотруднику, что его задали.

        Args:
            user_id: Сотрудник запроса результата.
            api_key_id: Ключ запроса результата; None — локальная сессия или личный токен.
            request_id: ID вопроса.
        Returns:
            Состояние, ответ, ссылки, выполненные и ожидающие решения действия.
        Raises:
            AgentRequestNotFoundError: Вопроса нет или он чужой.
            AgentConversationsServiceError: При ошибке хранения.
        """
        async with self._scope() as db:
            message = await db.messages.get_answer_by_request(request_id, user_id=user_id, api_key_id=api_key_id)
            conversation = (
                await db.conversations.get_by_id(message.conversation_id) if message else None
            )
            if (
                message is None
                or conversation is None
                or conversation.channel != "api"
                or conversation.user_id != user_id
                or conversation.api_key_id != api_key_id
            ):
                raise AgentRequestNotFoundError(f"request_id={request_id}")
            pair = await db.messages.get_request(conversation.id, request_id)
            question = next(item for item in pair if item.role == "user")
            runs = await db.runs.get_for_messages([message.id])
            project_key = None
            if message.project_id is not None:
                project = await db.projects.get_by_id(project_id=message.project_id)
                project_key = project.key if project else None
        return build_request_result(
            message=message,
            question=question,
            conversation=conversation,
            runs=runs,
            project_key=project_key,
            links=self.links,
        )

    async def wait_external_result(
        self,
        *,
        user_id: int,
        api_key_id: UUID | None,
        request_id: UUID,
        timeout_seconds: float,
        poll_seconds: float = 0.25,
    ) -> AgentRequestResultSchema:
        """Ждёт готовности ответа не дольше ``timeout_seconds``, иначе отдаёт состояние.

        Ответ готовит фоновый worker backend; ждущий вызов лишь перечитывает
        состояние короткими транзакциями и соединение между ними не держит.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout_seconds, 0)
        while True:
            result = await self.get_external_result(
                user_id=user_id, api_key_id=api_key_id, request_id=request_id
            )
            remaining = deadline - loop.time()
            if result.status in {"completed", "failed"} or remaining <= 0:
                return result
            await asyncio.sleep(min(poll_seconds, remaining))

    @asynccontextmanager
    async def _scope(self) -> AsyncIterator[AgentConversationScope]:
        try:
            async with self.scope() as db:
                yield db
        except ResourceNotAvailableError as error:
            raise AgentConversationNotFoundError(str(error)) from error
        except (RepositoryError, AccessServiceError) as error:
            raise AgentConversationsServiceError(str(error)) from error


def build_request_result(
    *,
    message: AgentMessage,
    question: AgentMessage,
    conversation: AgentConversation,
    runs: list[AgentToolRun],
    project_key: str | None,
    links: RequestLinks,
) -> AgentRequestResultSchema:
    """Собирает ответ внешнему агенту из сохранённой реплики и журнала действий."""
    ready = message.status in {"completed", "failed"}
    task_links: dict[str, AgentTaskLinkSchema] = {}
    if message.status == "completed":
        for source in message.sources or []:
            card = source.get("card") or {}
            key = card.get("key")
            if source.get("entity_type") == "task" and key and key not in task_links:
                task_links[key] = AgentTaskLinkSchema(
                    key=key, title=source.get("title", ""), url=links.task(key)
                )
    done, pending, rejected = [], [], []
    for run in runs:
        key = _task_key(run.result)
        item = AgentActionLinkSchema(
            id=run.id,
            tool=run.tool_name,
            title=run.title,
            status=run.status,
            task_key=key,
            url=links.task(key),
            confirm_url=links.confirm(run.id) if run.status == "pending" else None,
            arguments=dict(run.arguments) if run.status == "pending" else {},
        )
        {"completed": done, "pending": pending}.get(run.status, rejected).append(item)
    return AgentRequestResultSchema(
        request_id=message.request_id,
        status=message.status,
        conversation_id=conversation.external_key,
        answer=message.content if message.status == "completed" else None,
        error=message.error if message.status == "failed" else None,
        progress=message.progress if message.status in {"queued", "processing"} else None,
        project_key=project_key if ready else None,
        write_allowed=question.verbatim is not None,
        links=list(task_links.values()),
        done=done,
        pending=pending,
        rejected=rejected,
    )


def _task_key(result: dict[str, Any] | None) -> str | None:
    """Ключ задачи из сохранённого результата действия, если он там есть."""
    if not result:
        return None
    task = result.get("task")
    if isinstance(task, dict) and task.get("key"):
        return str(task["key"])
    preview = result.get("preview")
    if isinstance(preview, dict):
        for value in preview.values():
            if isinstance(value, dict) and value.get("key"):
                return str(value["key"])
    return str(result["task_key"]) if result.get("task_key") else None
