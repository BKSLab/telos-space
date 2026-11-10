"""Личный диалог с Арисом в виджете: хранение, очередь ответов и контекст страницы."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, Field

from src.agent.action_scopes import tools_for_scopes
from src.agent.aris import ArisMode
from src.agent.context import compact_dialogue_history
from src.agent.progress import ProgressSink
from src.agent.tools import AgentToolContext
from src.agent.wakeup import wake_agent_worker
from src.clients.llm import LlmClient
from src.db.models.agent_conversations import AgentConversation
from src.db.models.agent_messages import AgentMessage
from src.exceptions.access import AccessServiceError, ResourceNotAvailableError
from src.exceptions.agent_conversations import (
    AgentConversationBusyError,
    AgentConversationNotFoundError,
    AgentConversationsServiceError,
    AgentMessageConflictError,
)
from src.exceptions.base import RepositoryError
from src.prompts.agent_memory import AGENT_MEMORY_PROMPT
from src.schemas.agent_conversations import (
    AgentConversationListSchema,
    AgentConversationSchema,
    AgentMessageAcceptedSchema,
    AgentMessageCreateSchema,
    AgentMessagePageSchema,
    AgentMessageSchema,
)
from src.schemas.agent_files import AgentFileSchema
from src.schemas.agent_tools import AgentToolRunSchema
from src.schemas.knowledge import KnowledgeAnswerSchema, KnowledgeChatMessageSchema
from src.services.agent_routing import AgentRoutingAnswer, AgentRoutingService
from src.services.board_selection import checked_board_selection, normalize_board_selection
from src.services.db_scope import AgentConversationScope, AgentConversationScopeFactory
from src.services.project_agent import API_CHANNEL, ProjectAgentService
from src.services.tasks import user_display_name

logger = logging.getLogger(__name__)

#: Канал личного диалога; внешние клиенты используют отдельный канал ``api``.
WIDGET_CHANNEL = "web"


@dataclass(frozen=True, slots=True)
class AgentConversationConfig:
    """Явные бюджеты обработки и памяти диалога."""

    turn_timeout_seconds: float
    history_messages: int
    summary_batch_size: int


class ConversationMemory(BaseModel):
    """Память о разговоре, которая никогда не индексируется как факт проекта."""

    summary: str = Field(min_length=1, max_length=12000)


class AgentConversationsService:
    """Владеет транзакциями диалогов виджета; освобождает БД перед каждым вызовом модели.

    Диалог сохраняет заданный проект и личную историю владельца. Для общего
    личного диалога проект ответа определяется страницей (page_context) заново
    и только в пределах текущего доступа; иначе Арис отвечает без проекта.
    """

    DEFAULT_TITLE = "Новый диалог"
    HISTORY_MESSAGE_CHARS = 8000
    MEMORY_FALLBACK_MESSAGES = 20
    MEMORY_FALLBACK_TOKENS = 1000

    def __init__(
        self,
        *,
        scope: AgentConversationScopeFactory,
        agent: ProjectAgentService,
        llm_client: LlmClient,
        config: AgentConversationConfig,
        routing: AgentRoutingService | None = None,
    ) -> None:
        self.scope = scope
        self.agent = agent
        self.llm_client = llm_client
        self.config = config
        self.routing = routing
        # Память беседы к следующему вопросу готовится после ответа, пока
        # участник его читает: иначе её пересказ моделью ждал бы следующий вопрос.
        self._memory_tasks: dict[int, asyncio.Task[None]] = {}

    async def list_conversations(
        self, *, user_id: int, offset: int, limit: int, project_id: int | None = None
    ) -> AgentConversationListSchema:
        """Возвращает страницу диалогов пользователя с Арисом.

        Args:
            user_id: Текущий пользователь.
            offset: Смещение страницы.
            limit: Размер страницы.
        Returns:
            Доступные диалоги и смещение продолжения.
        Raises:
            AgentConversationsServiceError: При отказе доступа или ошибке хранения.
        """
        async with self._scope() as db:
            await self._require_user(db, user_id)
            if project_id is not None:
                await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
            rows = await db.conversations.get_page(
                user_id, offset=offset, limit=limit + 1, project_id=project_id
            )
            return AgentConversationListSchema(
                items=[AgentConversationSchema.model_validate(row) for row in rows[:limit]],
                next_offset=offset + limit if len(rows) > limit else None,
            )

    async def create_conversation(
        self, *, user_id: int, project_id: int | None = None
    ) -> AgentConversationSchema:
        """Начинает новый диалог с Арисом; он становится текущим.

        Args:
            user_id: Автор из аутентифицированной сессии.
        Returns:
            Сохранённый диалог.
        Raises:
            AgentConversationsServiceError: При отказе доступа или ошибке хранения.
        """
        async with self._scope() as db:
            await self._require_user(db, user_id)
            if project_id is not None:
                await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
            row = await db.conversations.save(
                dict(
                    project_id=project_id,
                    user_id=user_id,
                    title=self.DEFAULT_TITLE,
                    channel=WIDGET_CHANNEL,
                )
            )
            result = AgentConversationSchema.model_validate(row)
            await db.unit_of_work.commit()
            return result

    async def get_messages(
        self,
        *,
        user_id: int,
        conversation_id: int,
        before_id: int | None,
        limit: int,
        project_id: int | None = None,
    ) -> AgentMessagePageSchema:
        """Восстанавливает переписку, включая состояние незавершённого ответа.

        Args:
            user_id: Текущий пользователь.
            conversation_id: Выбранный диалог.
            before_id: Граница чтения ранних реплик.
            limit: Размер страницы.
        Returns:
            Хронологическая страница и курсор ранних реплик.
        Raises:
            AgentConversationsServiceError: При отказе доступа или ошибке хранения.
        """
        async with self._scope() as db:
            await self._require(db, user_id, conversation_id, project_id=project_id)
            rows = await db.messages.get_page(conversation_id, before_id=before_id, limit=limit + 1)
            page = rows[:limit]
            actions = await db.runs.get_for_messages([row.id for row in page])
            return AgentMessagePageSchema(
                items=[
                    AgentMessageSchema.model_validate(row).model_copy(
                        update={
                            "actions": [
                                AgentToolRunSchema.model_validate(action)
                                for action in actions
                                if action.message_id == row.id
                            ]
                        }
                    )
                    for row in reversed(page)
                ],
                next_before_id=page[-1].id if len(rows) > limit else None,
            )

    async def send_message(
        self,
        *,
        user_id: int,
        conversation_id: int,
        data: AgentMessageCreateSchema,
        project_id: int | None = None,
    ) -> AgentMessageAcceptedSchema:
        """Атомарно сохраняет вопрос и очередь ответа; повтор идемпотентен.

        Args:
            user_id: Текущий пользователь, которого модель не может подменить.
            conversation_id: Диалог вопроса.
            data: Текст, ключ отправки и контекст страницы без клиентской истории.
        Returns:
            Сохранённые вопрос и ответ в очереди.
        Raises:
            AgentConversationBusyError: Если предыдущий ответ ещё готовится.
            AgentMessageConflictError: Если ключ отправки повторён с другим текстом.
            AgentConversationsServiceError: При отказе доступа или ошибке хранения.
        """
        async with self._scope() as db:
            conversation = await self._require(
                db, user_id, conversation_id, lock=True, project_id=project_id
            )
            existing = await db.messages.get_request(conversation_id, data.request_id)
            if existing:
                result = self._accepted(existing)
                if (
                    result.user_message.content != data.content
                    or [item.id for item in result.user_message.files] != data.file_ids
                ):
                    raise AgentMessageConflictError("Ключ отправки использован для другого текста.")
                return result
            if await db.messages.get_active(conversation_id):
                raise AgentConversationBusyError("Диалог уже имеет незавершённый ответ.")
            latest = await db.messages.get_page(conversation_id, before_id=None, limit=1)
            if latest:
                await db.runs.reject_pending(latest[0].id)
            common = dict(conversation_id=conversation_id, request_id=data.request_id)
            files = []
            for file_id in data.file_ids:
                uploaded = await db.uploads.get(file_id)
                if uploaded is None or uploaded.conversation_id != conversation_id:
                    raise AgentConversationNotFoundError("Файл принадлежит другому диалогу.")
                files.append(AgentFileSchema.model_validate(uploaded).model_dump(mode="json"))
            rows = await db.messages.save_pair(
                [
                    {
                        **common,
                        "role": "user",
                        "content": data.content,
                        "status": "completed",
                        "files": files,
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
                        "page_context": None,
                    },
                ]
            )
            title = (
                data.content[:120]
                if conversation.title == self.DEFAULT_TITLE
                else conversation.title
            )
            await db.conversations.update(conversation_id, {"title": title})
            result = self._accepted(rows)
            for file_id in data.file_ids:
                await db.uploads.bind_message(file_id, result.user_message.id)
            await db.unit_of_work.commit()
        wake_agent_worker()
        return result

    async def retry_message(
        self, *, user_id: int, conversation_id: int, message_id: int, project_id: int | None = None
    ) -> AgentMessageSchema:
        """Повторяет последний неудачный ответ без дублирования вопроса.

        Args:
            user_id: Текущий пользователь.
            conversation_id: Диалог ответа.
            message_id: Реплика, которую нужно повторить.
        Returns:
            Та же реплика в очереди.
        Raises:
            AgentMessageConflictError: Если диалог уже продолжился или ответ не завершился ошибкой.
            AgentConversationsServiceError: При отказе доступа или ошибке хранения.
        """
        async with self._scope() as db:
            await self._require(db, user_id, conversation_id, lock=True, project_id=project_id)
            latest = await db.messages.get_page(conversation_id, before_id=None, limit=1)
            if not latest or latest[0].id != message_id:
                raise AgentMessageConflictError("Можно повторить только последний ответ.")
            if latest[0].status in {"queued", "processing"}:
                return AgentMessageSchema.model_validate(latest[0])
            if latest[0].role != "assistant" or latest[0].status != "failed":
                raise AgentMessageConflictError("Ответ не ожидает повтора.")
            result = AgentMessageSchema.model_validate(await db.messages.retry(message_id))
            await db.unit_of_work.commit()
        wake_agent_worker()
        return result

    async def process_next(self) -> bool:
        """Выполняет один ответ независимо от времени жизни браузерного запроса.

        Returns:
            True, если очередь содержала задание.
        Raises:
            AgentConversationsServiceError: Если недоступна сама очередь.
        """
        async with self._scope() as db:
            await db.messages.fail_expired(self.config.turn_timeout_seconds)
            message = await db.messages.claim_next(uuid4())
            await db.unit_of_work.commit()
        if message is None:
            return False
        logger.info(
            "🤖 Арис начал ответ в виджете id=%s, диалог=%s.", message.id, message.conversation_id
        )
        try:
            async with asyncio.timeout(self.config.turn_timeout_seconds):
                answer, project_id = await self._generate(message)
                await self._finish(message, answer=answer, project_id=project_id)
            logger.info("✅ Ответ агента id=%s сохранён.", message.id)
            self._schedule_memory(message)
        except asyncio.CancelledError:
            try:
                await self._finish(
                    message, error="Подготовка ответа прервалась. Можно повторить запрос."
                )
            except Exception:
                # Недоступная БД не должна скрывать отмену и мешать lifespan
                # закрыть остальные ресурсы. Состояние восстановит fail_expired.
                logger.error(
                    "❌ Не удалось сохранить остановку ответа id=%s.", message.id, exc_info=True
                )
            raise
        except Exception:
            # Граница фонового задания: незнакомая ошибка тоже должна стать видимой
            # в сохранённой реплике, а не оставить бесконечный индикатор ожидания.
            logger.error("❌ Ответ агента id=%s не подготовлен.", message.id, exc_info=True)
            await self._finish(
                message, error="Не удалось подготовить ответ. Можно повторить запрос."
            )
        return True

    async def _generate(self, message: AgentMessage) -> tuple[KnowledgeAnswerSchema, int | None]:
        async with self._scope() as db:
            conversation = await db.conversations.get_by_id(message.conversation_id)
            if conversation is None:
                raise AgentConversationNotFoundError("Диалог задания удалён.")
            await self._require(
                db, conversation.user_id, conversation.id, channel=conversation.channel
            )
            user = await db.users.get_by_id(conversation.user_id)
            actor = {
                "user_id": user.id,
                "username": user.username,
                "name": user_display_name(user),
            }
            key_scopes = None
            if conversation.channel == API_CHANNEL and conversation.api_key_id is not None:
                if db.api_keys is None:
                    raise AgentConversationsServiceError("Проверка API-ключа не подключена.")
                key = await db.api_keys.get_by_id(conversation.api_key_id)
                from datetime import UTC, datetime

                if (
                    key is None
                    or key.owner_user_id != conversation.user_id
                    or not key.is_active
                    or key.revoked_at is not None
                    or (key.expires_at is not None and key.expires_at <= datetime.now(UTC))
                ):
                    raise AgentConversationNotFoundError("API-ключ больше недоступен.")
                key_scopes = set(key.scopes)
                if "agent:ask" not in key_scopes:
                    raise AgentConversationNotFoundError(
                        "API-ключ больше не разрешает вопросы агенту."
                    )
            pair = await db.messages.get_request(conversation.id, message.request_id)
            question = next(item for item in pair if item.role == "user")
            if key_scopes is not None:
                question.key_scopes = sorted(set(question.key_scopes or []) & key_scopes)
            page_context = dict(question.page_context or {})
            if conversation.channel == API_CHANNEL:
                # Внешняя беседа: маршрутизатор выбирает проект и проверяет роль.
                project_id, can_write = None, True
            else:
                # Проект страницы пересчитывается на каждый ответ и только в пределах
                # прав пользователя: недоступный проект Арису не показывается вовсе.
                if conversation.project_id is not None:
                    grant = await db.access.ensure_project_access(
                        project_id=conversation.project_id, user_id=user.id
                    )
                    project_id, can_write = conversation.project_id, grant.can_write
                    project = await db.projects.get_by_id(project_id=project_id)
                    page_context["project_key"] = project.key
                else:
                    project_id, can_write = await self._page_project(db, user.id, page_context)
                await self._check_board_page(db, user.id, page_context, project_id)
            recent = await db.messages.get_history(
                conversation.id, before_id=question.id, limit=self.config.history_messages
            )
            boundary = recent[-1].id if recent else question.id
            earlier_history = await self._unsummarized_history(db, conversation, boundary)
            action_history = [
                AgentToolRunSchema.model_validate(item).model_dump(mode="json")
                for item in await db.runs.get_for_messages(
                    [message.id, *[item.id for item in recent]]
                )
            ]
            current_actions = await db.runs.get_for_messages([message.id])
            # После решения карточки worker только объясняет результат. Продолжение
            # не получает нового разрешения записи из исходной команды пользователя.
            can_write = can_write and not any(
                "participant_decision" in item.result for item in current_actions
            )
        # Пересказ остаётся фоновым: следующий вопрос не ждёт его LLM.
        # Пока сводка не готова, точные выдержки сохраняют ранние факты в контексте.
        summary = conversation.summary
        history = earlier_history + [self._history(item) for item in reversed(recent)]
        if conversation.channel == API_CHANNEL:
            result = await self._generate_routed(
                message=message,
                conversation=conversation,
                question=question,
                history=history,
                summary=summary,
                actor=actor,
                can_write=can_write,
                action_history=action_history,
                pinned_project_id=current_actions[0].project_id if current_actions else None,
                page_context=page_context,
            )
            return result.answer, result.project_id
        on_progress = self._progress_sink(message)
        execution = AgentToolContext(
            project_id=project_id,
            user_id=conversation.user_id,
            can_write=can_write,
            message_id=message.id,
            run_id=message.run_id,
        )
        if project_id is None:
            answer = await self.agent.ask_personal(
                question=question.content,
                history=history,
                memory=summary,
                actor=actor,
                execution=execution,
                action_history=action_history,
                page_context=page_context,
                conversation_id=f"conversation:{conversation.id}",
                on_progress=on_progress,
            )
            return answer, None
        answer = await self.agent.ask(
            project_id=project_id,
            question=question.content,
            history=history,
            memory=summary,
            actor=actor,
            execution=execution,
            action_history=action_history,
            uploaded_files=[file for item in [*reversed(recent), question] for file in item.files],
            mode=ArisMode.WIDGET_PROJECT,
            extra_context={"page_context": page_context},
            conversation_id=f"conversation:{conversation.id}",
            on_progress=on_progress,
        )
        return answer, project_id

    async def _generate_routed(
        self,
        *,
        message: AgentMessage,
        conversation: AgentConversation,
        question: AgentMessage,
        history: list[KnowledgeChatMessageSchema],
        summary: str,
        actor: dict,
        can_write: bool,
        action_history: list[dict],
        pinned_project_id: int | None,
        page_context: dict,
    ) -> AgentRoutingAnswer:
        """Отвечает в беседе без проекта: маршрутизатор выбирает доступный контекст.

        Запись разрешают сразу три условия: дословная реплика пользователя
        (проверяет проектный агент), роль в выбранном проекте и права ключа
        на момент вопроса — снимок в ``key_scopes`` реплики. Клиентский контекст
        (``page_context``) права не даёт: его проверяет маршрутизатор.
        """
        if self.routing is None:
            raise AgentConversationsServiceError("Маршрутизация вопросов Арису не подключена.")
        allowed = tools_for_scopes(question.key_scopes) if question.key_scopes is not None else None
        result = await self.routing.ask(
            user_id=conversation.user_id,
            question=question.content,
            verbatim=question.verbatim,
            history=history,
            memory=summary,
            actor=actor,
            execution=AgentToolContext(
                # Проект подставит маршрутизатор после выбора и проверки доступа.
                project_id=None,
                user_id=conversation.user_id,
                can_write=can_write,
                message_id=message.id,
                run_id=message.run_id,
                allowed_tools=allowed,
            ),
            action_history=action_history,
            pinned_project_id=pinned_project_id,
            page_context=page_context,
            conversation_id=conversation.external_key or f"api:{conversation.id}",
            on_progress=self._progress_sink(message),
        )
        return result

    def _progress_sink(self, message: AgentMessage) -> ProgressSink:
        """Запись живого статуса в реплику ответа — только пока попытка активна."""

        async def write(progress: str) -> None:
            async with self.scope() as db:
                if await db.messages.set_progress(message.id, message.run_id, progress):
                    await db.unit_of_work.commit()

        return write

    @staticmethod
    async def _check_board_page(db, user_id, page_context, project_id):
        board_id = page_context.get("board_id")
        selection = page_context.pop("board_selection", None)
        page_context.pop("board_title", None)
        if board_id is None:
            return
        repository = getattr(db, "boards", None)
        board = None
        if (
            repository is not None
            and isinstance(board_id, int)
            and not isinstance(board_id, bool)
            and board_id > 0
        ):
            if project_id is not None:
                board = await repository.get_active(project_id=project_id, board_id=board_id)
            elif not page_context.get("project_key"):
                board = await repository.get_active_free(board_id=board_id, owner_user_id=user_id)
        if board is None:
            page_context.pop("board_id", None)
        else:
            page_context["board_id"] = board.id
            page_context["board_title"] = board.title
            if selection is not None:
                refs = normalize_board_selection(selection)
                shapes, stickers = [], []
                shape_ids = {ref["id"] for ref in refs if ref["type"] == "shape"}
                sticker_ids = {ref["id"] for ref in refs if ref["type"] == "sticker"}
                if shape_ids and db.board_shapes is not None:
                    shapes = await db.board_shapes.list_by_ids(
                        board_id=board.id, shape_ids=shape_ids
                    )
                if sticker_ids and db.board_stickers is not None:
                    stickers = await db.board_stickers.list_by_ids(
                        board_id=board.id, sticker_ids=sticker_ids
                    )
                page_context["board_selection"] = checked_board_selection(
                    refs, board_id=board.id, shapes=shapes, stickers=stickers
                )

    @staticmethod
    async def _page_project(
        db: AgentConversationScope, user_id: int, page_context: dict
    ) -> tuple[int | None, bool]:
        """Проект страницы виджета и право записи в нём, если он доступен пользователю.

        Returns:
            (project_id, can_write); без проекта — (None, True): личные действия
            проверяют права сами, а проектные спросят проект по ключу.
        """
        project_id = None
        try:
            if page_context.get("project_key"):
                project = await db.projects.get_by_key(
                    str(page_context["project_key"]).strip().upper()
                )
                project_id = project.id if project is not None else None
            elif page_context.get("task_id"):
                grant = await db.access.ensure_task_access(
                    task_id=int(page_context["task_id"]), user_id=user_id
                )
                project_id = grant.project_id
            if project_id is None:
                return None, True
            grant = await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
            return project_id, grant.can_write
        except ResourceNotAvailableError:
            return None, True

    async def _unsummarized_history(
        self, db: AgentConversationScope, conversation: AgentConversation, boundary: int
    ) -> list[KnowledgeChatMessageSchema]:
        """Даёт ограниченные выдержки ещё не свёрнутых реплик без вызова LLM.

        Читаем начало и конец отставшей сводки: число SQL-запросов и объём
        данных не растут с длиной беседы. При большом отставании пропуск
        середины отмечен явно; исходная переписка остаётся в PostgreSQL.
        """
        limit = self.MEMORY_FALLBACK_MESSAGES
        earlier = await db.messages.get_for_summary(
            conversation.id,
            after_id=conversation.summary_through_id,
            before_id=boundary,
            limit=limit + 1,
        )
        if not earlier:
            return []
        omitted = False
        if len(earlier) > limit:
            latest = await db.messages.get_history(conversation.id, before_id=boundary, limit=limit)
            latest = [
                item for item in reversed(latest) if item.id > conversation.summary_through_id
            ]
            omitted = bool(latest and earlier[limit].id < latest[0].id)
            earlier = sorted(
                {item.id: item for item in [*earlier[:limit], *latest]}.values(),
                key=lambda item: item.id,
            )
        compacted = compact_dialogue_history(
            [self._history(item).model_dump() for item in earlier],
            max_tokens=self.MEMORY_FALLBACK_TOKENS,
            preserve_latest_user=False,
        )
        history = [KnowledgeChatMessageSchema.model_validate(item) for item in compacted]
        if omitted or len(compacted) < len(earlier):
            history.insert(
                0,
                KnowledgeChatMessageSchema(
                    role="assistant",
                    content=(
                        "[Ранние реплики переданы ограниченными выдержками; часть "
                        "переписки пропущена. Полная история сохранена.]"
                    ),
                ),
            )
        return history

    async def _remember(self, conversation_id: int, boundary: int) -> str:
        """Сворачивает в память беседы все реплики раньше ``boundary``.

        Порция пишется только поверх той сводки, с которой её начинали: если
        сводку уже продвинул другой проход, дальше идём от его результата.

        Args:
            conversation_id: Беседа.
            boundary: Первая реплика недавней истории — она и новее в пересказ не входят.
        Returns:
            Актуальная сводка ранней части беседы.
        Raises:
            AgentConversationNotFoundError: Беседа удалена или недоступна владельцу.
        """
        async with self._scope() as db:
            conversation = await db.conversations.get_by_id(conversation_id)
            if conversation is None:
                raise AgentConversationNotFoundError("Диалог задания удалён.")
            await self._require(
                db, conversation.user_id, conversation.id, channel=conversation.channel
            )
        user_id, channel = conversation.user_id, conversation.channel
        summary = conversation.summary
        through_id = conversation.summary_through_id
        while True:
            async with self._scope() as db:
                await self._require(db, user_id, conversation_id, channel=channel)
                earlier = await db.messages.get_for_summary(
                    conversation_id,
                    after_id=through_id,
                    before_id=boundary,
                    limit=self.config.summary_batch_size,
                )
            if not earlier:
                return summary
            memory = await self.llm_client.get_structured_response(
                system_prompt=AGENT_MEMORY_PROMPT,
                content=json.dumps(
                    {
                        "previous_summary": summary,
                        "messages": [self._history(item).model_dump() for item in earlier],
                    },
                    ensure_ascii=False,
                ),
                schema=ConversationMemory,
                max_completion_tokens=4000,
            )
            next_summary = memory.summary.replace("\x00", "")
            async with self._scope() as db:
                await self._require(db, user_id, conversation_id, channel=channel)
                saved = await db.conversations.save_summary(
                    conversation_id,
                    expected_through_id=through_id,
                    summary=next_summary,
                    through_id=earlier[-1].id,
                )
                if saved:
                    await db.unit_of_work.commit()
                    summary, through_id = next_summary, earlier[-1].id
                    continue
                current = await db.conversations.get_by_id(conversation_id)
            if current is None:
                raise AgentConversationNotFoundError("Диалог задания удалён.")
            summary, through_id = current.summary, current.summary_through_id

    def _schedule_memory(self, message: AgentMessage) -> None:
        """Запускает пересказ ранних реплик к следующему вопросу этой беседы."""
        if message.conversation_id in self._memory_tasks:
            return
        task = asyncio.create_task(
            self._prepare_memory(message.conversation_id, after_id=message.id),
            name=f"aris-memory-{message.conversation_id}",
        )
        self._memory_tasks[message.conversation_id] = task
        task.add_done_callback(lambda _: self._memory_tasks.pop(message.conversation_id, None))

    async def _prepare_memory(self, conversation_id: int, *, after_id: int) -> None:
        """Сворачивает всё, что уже не войдёт в недавнюю историю следующего вопроса.

        Граница — та же, что посчитает ``_generate`` для следующего вопроса:
        самая старая из последних ``history_messages`` завершённых реплик.
        При сбое следующий вопрос использует точные выдержки; после ответа
        фоновая подготовка памяти запускается снова.
        """
        try:
            async with self._scope() as db:
                recent = await db.messages.get_history(
                    conversation_id, before_id=after_id + 1, limit=self.config.history_messages
                )
            if len(recent) < self.config.history_messages:
                return
            await self._remember(conversation_id, recent[-1].id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "⚠️ Память беседы %s заранее не подготовлена; ответ использует выдержки истории.",
                conversation_id,
                exc_info=True,
            )

    async def drain(self) -> None:
        """Останавливает фоновые пересказы при остановке приложения."""
        tasks = list(self._memory_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _finish(
        self,
        message: AgentMessage,
        *,
        answer: KnowledgeAnswerSchema | None = None,
        error: str | None = None,
        project_id: int | None = None,
    ) -> None:
        async with self._scope() as db:
            if answer is not None:
                conversation = await db.conversations.get_by_id(message.conversation_id)
                if conversation is None:
                    return
                await self._require(
                    db, conversation.user_id, conversation.id, channel=conversation.channel
                )
            data = (
                {"status": "failed", "error": error}
                if error
                else {
                    "status": "completed",
                    "content": answer.answer.replace("\x00", ""),
                    "sources": [source.model_dump(mode="json") for source in answer.sources],
                    "error": None,
                    "project_id": project_id,
                }
            )
            await db.messages.finish(message.id, message.run_id, data)
            await db.unit_of_work.commit()

    @staticmethod
    async def _require_user(db: AgentConversationScope, user_id: int) -> None:
        user = await db.users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise AgentConversationNotFoundError("Пользователь больше недоступен.")

    @classmethod
    async def _require(
        cls,
        db: AgentConversationScope,
        user_id: int,
        conversation_id: int,
        *,
        lock: bool = False,
        channel: str = WIDGET_CHANNEL,
        project_id: int | None = None,
    ) -> AgentConversation:
        await cls._require_user(db, user_id)
        # Виджет видит только свои диалоги канала web; внешнюю беседу читает worker
        # с её каналом, а маршруты виджета её не находят.
        conversation = await db.conversations.get_owned(
            conversation_id, user_id, channel=channel, lock=lock
        )
        if conversation is None or (
            project_id is not None and conversation.project_id != project_id
        ):
            raise AgentConversationNotFoundError(f"Недоступный диалог id={conversation_id}.")
        if conversation.project_id is not None:
            await db.access.ensure_project_access(
                project_id=conversation.project_id, user_id=user_id
            )
        return conversation

    @classmethod
    def _history(cls, message: AgentMessage) -> KnowledgeChatMessageSchema:
        content = message.content
        if message.role == "assistant" and message.sources:
            # Карточка без текста тоже должна оставаться понятной при уточнении
            # «измени эту задачу» и при свёртке раннего разговора в память.
            references = [
                f"{source.get('source_id', '')}: {source.get('title', '')}"
                for source in message.sources[:20]
            ]
            content = "Показаны карточки: " + "; ".join(references) + "\n" + content
        if not content.strip():
            content = "Ответ показан карточкой действия."
        if len(content) > cls.HISTORY_MESSAGE_CHARS:
            content = (
                content[: cls.HISTORY_MESSAGE_CHARS - 64]
                + "\n[Длинная реплика сокращена в контексте модели.]"
            )
        return KnowledgeChatMessageSchema(role=message.role, content=content)

    @staticmethod
    def _accepted(rows: list[AgentMessage]) -> AgentMessageAcceptedSchema:
        by_role = {row.role: AgentMessageSchema.model_validate(row) for row in rows}
        return AgentMessageAcceptedSchema(
            user_message=by_role["user"], assistant_message=by_role["assistant"]
        )

    @asynccontextmanager
    async def _scope(self) -> AsyncIterator[AgentConversationScope]:
        try:
            async with self.scope() as db:
                yield db
        except ResourceNotAvailableError as error:
            raise AgentConversationNotFoundError(str(error)) from error
        except (RepositoryError, AccessServiceError) as error:
            raise AgentConversationsServiceError(str(error)) from error
