from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, replace
from datetime import date
from functools import wraps
from time import perf_counter
from typing import Any

from pydantic import BaseModel
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models import Model

from src.agent.aris import ArisMode
from src.agent.context import AgentContextBudget
from src.agent.progress import ProgressReporter, ProgressSink
from src.agent.runtime import (
    PROJECT_ACTION_TOOL,
    ArisAnswer,
    ArisDeps,
    ArisRunResult,
    ArisRuntime,
    WriteGate,
)
from src.agent.runtime import metrics as runtime_metrics
from src.agent.tools import AgentTool, AgentToolContext, AgentToolExecutor, AgentToolRequest
from src.clients.embedding import EmbeddingClient
from src.clients.llm import LlmClient
from src.clients.qdrant import ProjectQdrantClient
from src.db.models.knowledge_index_jobs import (
    KnowledgeIndexStatus,
)
from src.db.models.projects import Project
from src.exceptions.ai import AgentAnswerError, AgentContextChangedError, AiProviderError
from src.exceptions.base import RepositoryError, ServiceError
from src.exceptions.clients import ClientError
from src.exceptions.knowledge import (
    KnowledgeDisabledError,
    KnowledgeEventsServiceError,
    KnowledgeIndexJobsRepositoryError,
    KnowledgeServiceError,
)
from src.exceptions.project_knowledge import ProjectKnowledgeStaleError
from src.exceptions.projects import ProjectNotFoundError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.knowledge.catalog import build_catalog, digest
from src.knowledge.context import (
    DOCUMENT_SOURCE_TYPES,
    citation,
    coverage,
    document_fragment,
    file_issues,
)
from src.prompts.project_agent import (
    ARIS_BASE_PROMPTS,
    PROJECT_AGENT_API_BASE_PROMPT,
    PROJECT_AGENT_WRITE_INTENT_PROMPT,
)
from src.schemas.document_catalog import DocumentCatalogItemSchema, DocumentTextPageSchema
from src.schemas.document_references import DocumentFragmentSchema
from src.schemas.knowledge import (
    KnowledgeAnswerSchema,
    KnowledgeChatMessageSchema,
    KnowledgeSourceCardSchema,
    KnowledgeSourceSchema,
    KnowledgeStatusSchema,
)
from src.schemas.tasks import TaskSchema
from src.services.agent_cards import source_card
from src.services.agent_chat_history import AgentChatHistoryService
from src.services.agent_chat_reference_registry import ChatReferenceRegistry
from src.services.agent_context_tools import AgentContextToolsService
from src.services.context_evidence import (
    ContextEvidence,
    ContextEvidenceService,
    document_signature,
    source_signature,
    task_signature,
)
from src.services.db_scope import ProjectAgentScope, ProjectAgentScopeFactory
from src.utils.document_refs import decode_document_ref

logger = logging.getLogger(__name__)

TEXT_FRAGMENT_LIMIT = 1200
SOURCE_EXCERPT_LIMIT = 500
#: Канал ответа внешнему клиенту.
API_CHANNEL = "api"
EMPTY_ANSWER = (
    "Не удалось завершить проверку источников. Уточните вопрос или сузьте перечень объектов."
)
INCOMPLETE_ANSWER = (
    "Проверка неполная: не удалось дочитать все запрошенные данные в пределах "
    "одного ответа. Уточните вопрос или сузьте период либо перечень объектов."
)


class WriteIntent(BaseModel):
    """Ответ проверки: поручил ли участник изменить данные в текущем вопросе."""

    write_intent: bool = False


def _git_read_budget(operation):
    """Один server-owned бюджет на generation и publication, включая retries."""

    @wraps(operation)
    async def run(self, *args, **kwargs):
        if self.context_tools is None:
            return await operation(self, *args, **kwargs)
        async with self.context_tools.git.read_budget() as budget:
            answer = await operation(self, *args, **kwargs)
            if answer._context_evidence is not None:
                answer._context_evidence[0].git_budget = budget
            return answer

    return run


class _SourceRegistry:
    """Связывает реальные source_id с непредсказуемыми хэндлами одного запроса."""

    def __init__(self) -> None:
        self.evidence = ContextEvidence()
        self.nonce = secrets.token_hex(8)
        self._by_handle: dict[str, KnowledgeSourceSchema] = {}
        self._handle_by_source_id: dict[str, str] = {}
        self._fragments: dict[str, tuple[str, DocumentFragmentSchema]] = {}

    def register(self, source: KnowledgeSourceSchema) -> str:
        """Возвращает стабильный внутри запроса хэндл источника."""
        existing = self._handle_by_source_id.get(source.source_id)
        if existing is not None:
            return existing
        handle = f"SRC_{self.nonce}_{len(self._by_handle) + 1}"
        self._by_handle[handle] = source
        self._handle_by_source_id[source.source_id] = handle
        return handle

    def update(self, source_id: str, **changes: Any) -> KnowledgeSourceSchema | None:
        """Обновляет серверные метаданные уже зарегистрированного источника."""
        handle = self._handle_by_source_id.get(source_id)
        if handle is None:
            return None
        source = self._by_handle[handle].model_copy(update=changes)
        self._by_handle[handle] = source
        return source

    def resolve(self, handle: str) -> KnowledgeSourceSchema | None:
        """Разрешает только хэндл, выданный в текущем запросе."""
        return self._by_handle.get(handle)

    def get_handle(self, source_id: str) -> str | None:
        """Возвращает выданный хэндл по внутреннему source_id."""
        return self._handle_by_source_id.get(source_id)

    def register_fragment(self, source_id: str, fragment: DocumentFragmentSchema) -> str:
        """Фрагмент неизменяем: другое чтение источника не подменяет его цитату."""
        for handle, record in self._fragments.items():
            if record == (source_id, fragment):
                return handle
        handle = f"FRAG_{self.nonce}_{len(self._fragments) + 1}"
        self._fragments[handle] = source_id, fragment
        return handle

    def resolve_reference(self, handle: str | None) -> KnowledgeSourceSchema | None:
        """Разрешает ссылку на источник или конкретный выданный сервером фрагмент."""
        if handle in self._fragments:
            source_id, fragment = self._fragments[handle]
            source_handle = self.get_handle(source_id)
            source = self.resolve(source_handle) if source_handle else None
            return source.model_copy(update={"reference": fragment}) if source else None
        return self.resolve(handle) if handle else None


@dataclass(frozen=True, slots=True)
class ProjectAgentConfig:
    """Настройки семантического поиска, нужные Арис.

    Сервис получает только те значения, которые действительно использует,
    а не весь объект настроек приложения.
    """

    knowledge_enabled: bool
    chunk_target_chars: int = 2200
    chunk_overlap_chars: int = 300
    tool_rounds: int = 4
    context_tokens: int = 30000
    history_tokens: int = 1200
    app_public_url: str = ""


class ProjectAgentService:
    """Арис с компактным начальным контекстом и адресным чтением инструментами."""

    def __init__(
        self,
        *,
        scope: ProjectAgentScopeFactory,
        llm_client: LlmClient,
        embedding_client: EmbeddingClient,
        qdrant_client: ProjectQdrantClient,
        config: ProjectAgentConfig,
        tools: AgentToolExecutor,
        agent_model: Model,
        chat_history: AgentChatHistoryService | None = None,
        context_tools: AgentContextToolsService | None = None,
    ) -> None:
        """Создаёт Арис.

        Args:
            scope: Фабрика короткой области работы с базой. Между сбором
                данных и ответом модели соединение удерживаться не должно.
            llm_client: Клиент chat completions.
            embedding_client: Клиент API эмбеддингов.
            qdrant_client: Клиент векторного индекса.
            config: Настройки семантического поиска.
            tools: Исполнитель зарегистрированных инструментов проекта.
            agent_model: Модель PydanticAI, которая ведёт цикл инструментов.
        """
        self.scope = scope
        self.llm_client = llm_client
        self.embedding_client = embedding_client
        self.qdrant_client = qdrant_client
        self.config = config
        self.tools = tools
        self.agent_model = agent_model
        self.chat_history = chat_history
        self.context_tools = context_tools
        self._runtime: ArisRuntime | None = None

    @property
    def runtime(self) -> ArisRuntime:
        """Агент PydanticAI над текущим реестром инструментов (строится один раз)."""
        if self._runtime is None or self._runtime.executor is not self.tools:
            self._runtime = ArisRuntime(model=self.agent_model, executor=self.tools)
        return self._runtime

    @_git_read_budget
    async def ask(
        self,
        *,
        project_id: int,
        question: str,
        history: list[KnowledgeChatMessageSchema],
        memory: str = "",
        actor: dict[str, Any] | None = None,
        execution: AgentToolContext | None = None,
        action_history: list[dict[str, Any]] | None = None,
        uploaded_files: list[dict[str, Any]] | None = None,
        chat_context: dict[str, Any] | None = None,
        mode: ArisMode = ArisMode.WIDGET_PROJECT,
        extra_context: dict[str, Any] | None = None,
        channel: str = "web",
        verbatim: str | None = None,
        conversation_id: str | None = None,
        on_progress: ProgressSink | None = None,
    ) -> KnowledgeAnswerSchema:
        """Запускает ответ с адресным чтением данных через инструменты.

        Это ответ Ариса в проектном режиме: общий чат проекта или личный виджет
        на странице проекта. ``mode`` выбирает секцию системного промпта и
        набор инструментов; ``extra_context`` добавляет контекст режима
        (page_context, quoted_message) без права менять проект запроса.

        Args:
            project_id: Проверенный вызывающим сервисом проект.
            question: Текущий вопрос участника.
            history: Недавние реплики для понимания продолжения разговора.
            memory: Краткая память раннего обсуждения, отдельно от фактов проекта.
            actor: Участник из серверного контекста; для прежнего API может отсутствовать.
            execution: Доверенная область инструментов и текущая попытка очереди.
            action_history: Сохранённые результаты действий и решений участника.
            uploaded_files: Файлы, отправленные участником в диалог.
            mode: Режим Ариса; только проектные режимы. Канал ``api`` идёт в режиме
                ``project_chat``: только инструменты проекта, без личных действий.
            extra_context: Дополнительные поля входного JSON модели.
            channel: ``web`` — ответ в интерфейсе трекера с карточками; ``api`` —
                ответ внешнему клиенту полным текстом со ссылками.
            verbatim: Дословная реплика пользователя в канале ``api``. Только она
                разрешает запись; без неё вопрос выполняется на чтение.
            conversation_id: Диалог или ход чата — для трассировки.
            on_progress: Запись живого статуса ответа; сбой записи ответ не роняет.
        Returns:
            Ответ с источниками, проверенными в текущем проекте.
        Raises:
            AgentAnswerError: При ошибке чтения данных проекта.
            AiProviderError: Если модель не смогла подготовить ответ.
        """
        started = perf_counter()
        phases = {
            name: None for name in ("write_intent", "ranked_fts", "embedding", "qdrant", "llm")
        }
        question = question.strip()
        api_channel = channel == API_CHANNEL
        verbatim = (verbatim or "").strip() or None
        async with self.scope() as db:
            project = await self._require_project(db, project_id=project_id)
        tool_context = execution or AgentToolContext(
            project_id=project_id, user_id=(actor or {}).get("user_id")
        )
        if tool_context.project_id != project_id:
            raise AgentAnswerError("Область инструментов не совпадает с проектом вопроса.")
        if mode is ArisMode.WIDGET_PERSONAL:
            raise AgentAnswerError("Проектный ответ требует проектного режима.")
        # В канале API пересказ вызывающего агента запись не разрешает: без
        # дословной реплики пользователя вопрос выполняется только на чтение.
        write_possible = tool_context.can_write and (not api_channel or verbatim is not None)
        tool_context = replace(
            tool_context, mode=mode, can_write=write_possible, request_text=question
        )
        if tool_context.principal is None and tool_context.user_id is not None:
            tool_context = replace(
                tool_context,
                principal=ContextEvidenceService.session_principal(tool_context.user_id),
            )
        progress = ProgressReporter(on_progress)
        deps: ArisDeps | None = None
        try:
            registry = _SourceRegistry()
            registry.evidence.policy(tool_context.origin_read_scopes)
            registry.evidence.project(project_id)
            chat_references = ChatReferenceRegistry()
            content = {
                "mode": mode.value,
                "current_date": date.today().isoformat(),
                "question": question,
                **(extra_context or {}),
                "dialog_history": [message.model_dump() for message in history],
                "dialog_memory": memory,
                "current_participant": actor,
                "project_reference": {
                    "id": project.id,
                    "key": project.key,
                    "name": project.name[:512],
                },
                "writes_allowed": tool_context.can_write,
                "previous_actions": action_history or [],
                "uploaded_files": uploaded_files or [],
            }
            if chat_context is not None:
                registry.evidence.register(chat_context)
                content["chat_context"] = chat_context
                content["current_date"] = chat_context.get(
                    "current_datetime", content["current_date"]
                )[:10]
                content["dialog_history"] = []
                content.pop("uploaded_files", None)
                chat_references.register(chat_context)
            system_prompt = ARIS_BASE_PROMPTS[mode]
            if api_channel:
                system_prompt = PROJECT_AGENT_API_BASE_PROMPT
                content.pop("mode", None)
                content["channel"] = API_CHANNEL
                content["participant_verbatim"] = verbatim
                content["task_link_template"] = self.task_link_template()
            budget = AgentContextBudget(
                system_prompt=system_prompt,
                max_tokens=self.config.context_tokens,
                history_tokens=self.config.history_tokens,
            )
            budget.prepare(content)
            # Каталог служит только серверной проверке прочитанных источников.
            # Загрузка capability открывает схемы, но сама не читает данные проекта.
            state: dict[str, Any] = {"catalog": None, "stale": False}

            deps = ArisDeps(
                execution=tool_context,
                executor=self.tools,
                system_prompt=system_prompt,
                content=content,
                budget=budget,
                tool_rounds=self.config.tool_rounds,
                defer_workflows=True,
                write_gate_factory=(
                    lambda: self._write_gate(
                        question=question,
                        history=history,
                        memory=memory,
                        verbatim=verbatim if api_channel else None,
                        phases=phases,
                    )
                )
                if write_possible
                else None,
                progress=progress,
            )
            member_handles: list[str] = []
            completed_actions: set[str] = set()
            directly_read_tasks: set[str] = set()

            async def validate_before_write():
                snapshot = KnowledgeAnswerSchema(answer="", sources=[])
                snapshot._context_evidence = registry.evidence, tool_context.principal
                await self.validate_answer(snapshot)

            deps.before_write = validate_before_write

            async def register(tool: AgentTool, result: dict[str, Any]) -> None:
                registry.evidence.register(result)
                if tool.name in {
                    "telos_list_chat_messages",
                    "telos_get_chat_message",
                    "telos_read_chat_file",
                }:
                    chat_references.register(result)
                    return
                mutating = tool.mutating or tool.name == PROJECT_ACTION_TOOL
                # Новая сущность должна получить проверенный handle уже в результате
                # создавшего её инструмента, до следующего шага модели.
                if self._needs_source_catalog(result) and (
                    state["catalog"] is None or state["stale"] or mutating
                ):
                    async with self.scope() as db:
                        await self._require_project(db, project_id=project_id)
                        state["catalog"] = build_catalog(
                            project_id, await db.sources.get_project_rows(project_id)
                        )
                    state["stale"] = False
                if state["catalog"] is not None:
                    self._register_views(result, state["catalog"], registry)
                if tool.name in {"telos_get_task", "telos_get_any_task"}:
                    source_id = result.get("source_id")
                    if (
                        isinstance(source_id, str)
                        and source_id.startswith("task:")
                        and registry.get_handle(source_id)
                    ):
                        directly_read_tasks.add(source_id)
                if tool.name in {"telos_list_members", "telos_list_sources"}:
                    for item in result.get("items", []):
                        source_id = item.get("source_id", "")
                        if source_id.startswith("member:"):
                            handle = registry.get_handle(source_id)
                            if handle and handle not in member_handles:
                                member_handles.append(handle)
                if mutating:
                    action = result.get("action")
                    if isinstance(action, dict) and action.get("status") == "completed":
                        action_id = action.get("id")
                        if isinstance(action_id, str) and action_id:
                            completed_actions.add(action_id)
                        if self.context_tools is not None:
                            try:
                                await self.context_tools.evidence.revalidate_after_action(
                                    registry.evidence, principal=tool_context.principal
                                )
                            except ServiceError:
                                result["context_validation"] = {
                                    "status": "stale",
                                    "message": "Действие выполнено, но источники изменились; для актуального вывода нужен повторный вопрос.",
                                }
                    state["stale"] = True

            deps.on_result = register
            phase = perf_counter()
            run: ArisRunResult | None = None
            try:
                run = await self.runtime.run(
                    deps,
                    metadata={
                        "channel": channel,
                        "mode": mode.value,
                        "project_id": project_id,
                        "conversation_id": conversation_id,
                    },
                )
                output = run.output or ArisAnswer()
                message_references = await chat_references.select(
                    handles=output.message_references,
                    history=self.chat_history,
                    context=tool_context,
                )
                requested_handles = [*output.source_ids]
                if output.primary_reference:
                    requested_handles.append(output.primary_reference)
                fallback_task = None
                if (
                    run.output is not None
                    and not requested_handles
                    and len(directly_read_tasks) == 1
                ):
                    fallback_task = registry.get_handle(next(iter(directly_read_tasks)))
                    if fallback_task:
                        requested_handles.append(fallback_task)
                # Модель могла ждать после первого чтения. Текущий ACL и credentials
                # проверяет тот же доменный инструмент до новой выборки карточки.
                if tool_context.user_id is not None:
                    checked_tasks = set()
                    for handle in requested_handles:
                        source = registry.resolve_reference(handle)
                        if (
                            source
                            and source.entity_type == "task"
                            and source.entity_id not in checked_tasks
                        ):
                            verified_task = await self.tools.execute(
                                tool_context,
                                AgentToolRequest(
                                    name="telos_get_task",
                                    arguments={"task_id": source.entity_id},
                                ),
                            )
                            if (
                                verified_task.get("error")
                                or verified_task.get("source_id") != source.source_id
                            ):
                                raise AgentAnswerError(
                                    "Доступ к задаче изменился. Повторите вопрос."
                                )
                            checked_tasks.add(source.entity_id)
                needs_current_document = any(
                    source
                    and (
                        source.entity_type in DOCUMENT_SOURCE_TYPES or source.entity_type == "task"
                    )
                    for source in (
                        registry.resolve_reference(handle) for handle in requested_handles
                    )
                )
                if state["stale"] or needs_current_document:
                    async with self.scope() as db:
                        await self._require_project(db, project_id=project_id)
                        state["catalog"] = build_catalog(
                            project_id, await db.sources.get_project_rows(project_id)
                        )
                catalog = state["catalog"]
                selected = []
                seen_sources = set()
                primary = registry.resolve_reference(output.primary_reference or fallback_task)
                for handle in [*requested_handles, *(member_handles if run.output else [])]:
                    source = registry.resolve_reference(handle)
                    if (
                        source
                        and catalog is not None
                        and source.source_id in catalog.sources
                        and source.source_id not in seen_sources
                    ):
                        current_source = catalog.sources[source.source_id]
                        is_primary = bool(primary and primary.source_id == source.source_id)
                        reference = primary.reference if is_primary else source.reference
                        selected.append(
                            source.model_copy(
                                update={
                                    "title": current_source.title,
                                    "card": source_card(current_source, catalog),
                                    "reference": reference,
                                    "is_primary": is_primary,
                                }
                            )
                        )
                        seen_sources.add(source.source_id)
                answer = output.answer.strip()
                if run.output is None:
                    answer = INCOMPLETE_ANSWER
                elif not answer and not selected and not message_references and completed_actions:
                    answer = (
                        "Действие выполнено. Результат — в карточке действия."
                        if len(completed_actions) == 1
                        else f"Выполнено действий: {len(completed_actions)}. "
                        "Результаты — в карточках действий."
                    )
                elif not answer and not selected and not message_references:
                    answer = EMPTY_ANSWER
                response = KnowledgeAnswerSchema(
                    answer=answer,
                    sources=selected,
                    message_references=message_references,
                    **registry.evidence.references(
                        output.primary_reference, source_handles=output.source_ids
                    ),
                )
                response._context_evidence = (registry.evidence, tool_context.principal)
                await self.validate_answer(response)
                return response
            except AgentRunError as error:
                raise AiProviderError(str(error)) from error
            except ClientError as error:
                raise AiProviderError(str(error)) from error
            except RepositoryError as error:
                raise AgentAnswerError(str(error)) from error
            except ValueError as error:
                raise AgentAnswerError(str(error)) from error
            finally:
                phases["llm"] = self._elapsed_ms(phase)
                logger.info(
                    "Метрики Арис: %s",
                    json.dumps(
                        {
                            "event": "project_agent.ask",
                            "project_id": project_id,
                            "mode": mode.value,
                            "channel": channel,
                            "phases_ms": phases,
                            "total_ms": self._elapsed_ms(started),
                            "context_chars": run.prompt_chars if run else 0,
                            "context_budget_tokens_estimate": self.config.context_tokens,
                            "agent": runtime_metrics(run) if run else None,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
        finally:
            if deps is not None and deps.write_gate is not None:
                deps.write_gate.close()
            await progress.close()

    @_git_read_budget
    async def ask_personal(
        self,
        *,
        question: str,
        history: list[KnowledgeChatMessageSchema],
        memory: str,
        actor: dict[str, Any],
        execution: AgentToolContext,
        action_history: list[dict[str, Any]] | None = None,
        page_context: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        on_progress: ProgressSink | None = None,
        uploaded_files: list[dict[str, Any]] | None = None,
        chat_context: dict[str, Any] | None = None,
    ) -> KnowledgeAnswerSchema:
        """Ответ Ариса в личном виджете, когда страница не указывает на проект.

        Каталога и RAG проекта здесь нет: модель видит только результаты
        личных инструментов (мои задачи, поиск по всем проектам,
        разовые задачи) и telos_run_project_action для проекта по ключу.

        Args:
            question: Текущий вопрос пользователя.
            history: Недавние реплики диалога виджета.
            memory: Краткая память ранней части разговора.
            actor: Пользователь из серверного контекста.
            execution: Доверенная область инструментов без проекта.
            action_history: Сохранённые результаты действий этого обсуждения.
            page_context: Страница, с которой пользователь написал.
            conversation_id: Идентификатор диалога для журнала выполнения.
            on_progress: Запись живого статуса ответа; сбой записи ответ не роняет.
        Returns:
            Ответ с проверенными карточками личных документов; без проектного RAG.
        Raises:
            AgentAnswerError: При ошибке чтения данных.
            AiProviderError: Если модель не смогла подготовить ответ.
        """
        started = perf_counter()
        question = question.strip()
        phases: dict[str, float | None] = {"write_intent": None}
        mode = ArisMode.WIDGET_PERSONAL
        tool_context = replace(execution, project_id=None, mode=mode, request_text=question)
        if tool_context.principal is None and tool_context.user_id is not None:
            tool_context = replace(
                tool_context,
                principal=ContextEvidenceService.session_principal(tool_context.user_id),
            )
        content = {
            "mode": mode.value,
            "current_date": date.today().isoformat(),
            "question": question,
            "page_context": page_context or {},
            "dialog_history": [message.model_dump() for message in history],
            "dialog_memory": memory,
            "current_participant": actor,
            "writes_allowed": tool_context.can_write,
            "previous_actions": action_history or [],
            "uploaded_files": uploaded_files or [],
        }
        chat_references = ChatReferenceRegistry()
        if chat_context is not None:
            content["chat_context"] = chat_context
            content["current_date"] = chat_context.get("current_datetime", content["current_date"])[
                :10
            ]
            content["dialog_history"] = []
            content.pop("uploaded_files", None)
            chat_references.register(chat_context)
        system_prompt = ARIS_BASE_PROMPTS[mode]
        budget = AgentContextBudget(
            system_prompt=system_prompt,
            max_tokens=self.config.context_tokens,
            history_tokens=self.config.history_tokens,
        )
        budget.prepare(content)
        deps = ArisDeps(
            execution=tool_context,
            executor=self.tools,
            system_prompt=system_prompt,
            content=content,
            budget=budget,
            tool_rounds=self.config.tool_rounds,
            defer_workflows=True,
            write_gate_factory=(
                lambda: self._write_gate(
                    question=question, history=history, memory=memory, phases=phases
                )
            )
            if tool_context.can_write
            else None,
            progress=ProgressReporter(on_progress),
        )
        registry = _SourceRegistry()
        registry.evidence.policy(tool_context.origin_read_scopes)
        if chat_context is not None:
            registry.evidence.register(chat_context)
        directly_read_tasks: set[str] = set()
        project_document_scopes: dict[str, int] = {}

        async def register_personal(_tool, result):
            registry.evidence.register(result)
            if _tool.name in {"telos_list_chat_messages", "telos_get_chat_message"}:
                chat_references.register(result)
            self._register_personal_views(
                result,
                registry,
                project_document_scopes=project_document_scopes
                if _tool.name == PROJECT_ACTION_TOOL
                else None,
            )
            if _tool.name == "telos_get_any_task" and result.get("source_handle"):
                directly_read_tasks.add(result["source_handle"])
            action = result.get("action")
            if (
                self.context_tools is not None
                and isinstance(action, dict)
                and action.get("status") == "completed"
            ):
                try:
                    await self.context_tools.evidence.revalidate_after_action(
                        registry.evidence, principal=tool_context.principal
                    )
                except ServiceError:
                    result["context_validation"] = {
                        "status": "stale",
                        "message": "Действие выполнено, но источники изменились; для актуального вывода нужен повторный вопрос.",
                    }

        deps.on_result = register_personal

        async def validate_before_write():
            snapshot = KnowledgeAnswerSchema(answer="", sources=[])
            snapshot._context_evidence = registry.evidence, tool_context.principal
            await self.validate_answer(snapshot)

        deps.before_write = validate_before_write
        run: ArisRunResult | None = None
        try:
            run = await self.runtime.run(
                deps,
                metadata={
                    "channel": "web",
                    "mode": mode.value,
                    "conversation_id": conversation_id,
                },
            )
            if run.output is None:
                return KnowledgeAnswerSchema(answer=INCOMPLETE_ANSWER, sources=[])
            output = run.output
            message_references = await chat_references.select(
                handles=output.message_references,
                history=self.chat_history,
                context=tool_context,
            )
            fallback_task = (
                next(iter(directly_read_tasks))
                if not output.source_ids
                and not output.primary_reference
                and len(directly_read_tasks) == 1
                else None
            )
            primary = registry.resolve_reference(output.primary_reference or fallback_task)
            selected = []
            seen_sources = set()
            handles = [
                *output.source_ids,
                *(
                    [output.primary_reference or fallback_task]
                    if output.primary_reference or fallback_task
                    else []
                ),
            ]
            selected_references = {}
            for handle in handles:
                source = registry.resolve_reference(handle)
                if source and source.document_ref:
                    is_primary = bool(primary and primary.source_id == source.source_id)
                    selected_references.setdefault(
                        source.document_ref,
                        primary.reference if is_primary else source.reference,
                    )
            checked_documents = {}
            # Текст ответа может использовать чтения даже при пустых source_ids.
            # Поэтому доступ проверяется для всех project DTO, увиденных моделью.
            for document_ref, document_project_id in project_document_scopes.items():
                kind, identifier = decode_document_ref(document_ref)
                source_handle = registry.get_handle(f"{kind}:{identifier}")
                source = registry.resolve(source_handle) if source_handle else None
                if source is None:
                    raise AgentAnswerError("Доступ к документу изменился. Повторите вопрос.")
                checked_documents[document_ref] = await self._checked_personal_document(
                    tool_context,
                    source,
                    project_id=document_project_id,
                    reference=selected_references.get(document_ref, source.reference),
                )
            for handle in handles:
                source = registry.resolve_reference(handle)
                if source is None or source.source_id in seen_sources:
                    continue
                if source.entity_type == "task":
                    current = await self.tools.execute(
                        tool_context,
                        AgentToolRequest(
                            name="telos_get_any_task",
                            arguments={"task_id": source.entity_id},
                        ),
                    )
                    verified = self._personal_task_source(current)
                    if verified is None:
                        raise AgentAnswerError("Доступ к задаче изменился. Повторите вопрос.")
                    selected.append(
                        verified.model_copy(
                            update={
                                "is_primary": bool(
                                    primary and primary.source_id == verified.source_id
                                )
                            }
                        )
                    )
                    seen_sources.add(verified.source_id)
                    continue
                if not source.document_ref:
                    continue
                is_primary = bool(primary and primary.source_id == source.source_id)
                reference = primary.reference if is_primary else source.reference
                document = checked_documents.get(source.document_ref)
                if document is None:
                    document = await self._checked_personal_document(
                        tool_context,
                        source,
                        project_id=None,
                        reference=reference,
                    )
                selected.append(
                    source.model_copy(
                        update={
                            "title": document.title,
                            "is_primary": is_primary,
                            "reference": reference,
                        }
                    )
                )
                seen_sources.add(source.source_id)
            answer = output.answer.strip() or (
                ""
                if selected or message_references
                else ("Не удалось завершить проверку данных. Уточните вопрос.")
            )
            response = KnowledgeAnswerSchema(
                answer=answer,
                sources=selected,
                message_references=message_references,
                **registry.evidence.references(
                    output.primary_reference, source_handles=output.source_ids
                ),
            )
            response._context_evidence = (registry.evidence, tool_context.principal)
            await self.validate_answer(response)
            return response
        except AgentRunError as error:
            raise AiProviderError(str(error)) from error
        except ClientError as error:
            raise AiProviderError(str(error)) from error
        except RepositoryError as error:
            raise AgentAnswerError(str(error)) from error
        except ValueError as error:
            raise AgentAnswerError(str(error)) from error
        finally:
            if deps.write_gate is not None:
                deps.write_gate.close()
            await deps.progress.close()
            logger.info(
                "Метрики Ариса: %s",
                json.dumps(
                    {
                        "event": "aris.ask_personal",
                        "total_ms": self._elapsed_ms(started),
                        "phases_ms": phases,
                        "agent": runtime_metrics(run) if run else None,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )

    async def _checked_personal_document(
        self,
        context: AgentToolContext,
        source: KnowledgeSourceSchema,
        *,
        project_id: int | None,
        reference: DocumentFragmentSchema | None,
    ) -> DocumentCatalogItemSchema:
        """Повторяет ACL и точное чтение выбранного server range до публикации ответа."""
        document_request = AgentToolRequest(
            name="telos_get_document",
            arguments={
                "document_ref": source.document_ref,
                "offset": reference.start if reference else 0,
                "max_chars": min(8000, max(500, len(reference.quote))) if reference else 500,
            },
        )
        if project_id is not None:
            async with self.scope() as db:
                project = await self._require_project(db, project_id=project_id)
            document_request = AgentToolRequest(
                name=PROJECT_ACTION_TOOL,
                arguments={
                    "project_key": project.key,
                    "name": document_request.name,
                    "arguments": document_request.arguments,
                },
            )
        current = await self.tools.execute(context, document_request)
        try:
            document = DocumentCatalogItemSchema.model_validate(current.get("document"))
            page = DocumentTextPageSchema.model_validate(current)
        except ValueError as error:
            raise AgentAnswerError("Доступ к документу изменился. Повторите вопрос.") from error
        if (
            document.ref != source.document_ref
            or current.get("source_id") != source.source_id
            or document.ownership.project_id != project_id
        ):
            raise AgentAnswerError("Доступ к документу изменился. Повторите вопрос.")
        if reference and (
            not document.text.available
            or reference.document_ref != document.ref
            or reference.content_hash != document.text.content_hash
            or reference.rendition != document.text.rendition
            or page.ref != document.ref
            or page.content_hash != reference.content_hash
            or page.rendition != reference.rendition
            or page.offset != reference.start
            or reference.end > page.total_chars
            or page.text[: len(reference.quote)] != reference.quote
        ):
            raise AgentAnswerError("Документ изменился после чтения. Повторите вопрос.")
        return document

    async def validate_answer(self, answer: KnowledgeAnswerSchema) -> None:
        """Вызывается перед возвратом модели и повторно перед записью очереди."""
        if answer._context_evidence is None:
            return
        evidence, principal = answer._context_evidence
        try:
            if self.context_tools is not None:
                if principal is None and evidence.saved.projects:
                    raise AgentAnswerError("Пользователь ответа недоступен. Повторите вопрос.")
                for project_id, page in evidence.web_pages:
                    await self.context_tools.web.validate_page_grants(
                        project_id=project_id, principal=principal, page_tokens=[page.page_token]
                    )
                    if page.coverage != "search_snippet":
                        current = await self.context_tools.web.read(
                            project_id=project_id,
                            principal=principal,
                            page_token=page.page_token,
                            max_chars=8000,
                        )
                        if (
                            current.content_hash != page.content_hash
                            or current.source_id != page.source_id
                        ):
                            raise AgentAnswerError(
                                "Публичный источник изменился. Повторите вопрос."
                            )
                async with self.context_tools.git.read_budget(evidence.git_budget) as budget:
                    evidence.git_budget = budget
                    await self.context_tools.evidence.validate(evidence.saved, principal=principal)
                return
            if (
                evidence.saved.observations
                or evidence.saved.git_files
                or evidence.saved.git_changes
                or evidence.web_pages
            ):
                raise AgentAnswerError("Проверка контекста не подключена.")
            for item in evidence.saved.tasks:
                actor = AgentToolContext(
                    project_id=None,
                    user_id=principal.user_id if principal else None,
                    principal=principal,
                    mode=ArisMode.WIDGET_PERSONAL,
                )
                current = await self.tools.execute(
                    actor,
                    AgentToolRequest(
                        name="telos_get_any_task", arguments={"task_id": item.task_id}
                    ),
                )
                if (
                    current.get("error")
                    or not isinstance(current.get("task"), dict)
                    or task_signature(TaskSchema.model_validate(current["task"]), item.projection)
                    != item.task_hash
                ):
                    raise AgentAnswerError("Доступ или версия задачи изменились. Повторите вопрос.")
            for item in evidence.saved.documents:
                kind, identifier = decode_document_ref(item.document_ref)
                source = KnowledgeSourceSchema(
                    source_id=f"{kind}:{identifier}",
                    entity_type=kind,
                    entity_id=identifier,
                    title="Проверка текущего доступа",
                )
                current = await self._checked_personal_document(
                    AgentToolContext(
                        project_id=None,
                        user_id=principal.user_id if principal else None,
                        principal=principal,
                        mode=ArisMode.WIDGET_PERSONAL,
                    ),
                    source,
                    project_id=item.project_id,
                    reference=None,
                )
                if document_signature(current) != item.document_hash:
                    raise AgentAnswerError(
                        "Доступ или версия документа изменились. Повторите вопрос."
                    )
            # Изолированные старые вызовы сервиса без новых adapters всё равно
            # сверяют каждую прочитанную SQL-версию, а не только selected handles.
            for project_id in evidence.saved.projects:
                items = [item for item in evidence.saved.internal if item.project_id == project_id]
                if not items:
                    continue
                async with self.scope() as db:
                    await self._require_project(db, project_id=project_id)
                    current = build_catalog(
                        project_id, await db.sources.get_project_rows(project_id)
                    )
                for item in items:
                    if item.project_id == project_id and (
                        item.source_id not in current.sources
                        or source_signature(current.sources[item.source_id], item.projection)
                        != item.source_hash
                    ):
                        raise AgentAnswerError("Источники изменились. Повторите вопрос.")
        except ProjectKnowledgeStaleError as error:
            raise AgentContextChangedError() from error
        except ServiceError as error:
            if isinstance(error, AgentAnswerError):
                raise
            raise AgentAnswerError(
                "Источники изменились или сейчас недоступны. Повторите вопрос."
            ) from error

    def task_link_template(self) -> str | None:
        """Адрес карточки задачи в интерфейсе с местом для ключа; None без APP_PUBLIC_URL."""
        base = self.config.app_public_url.rstrip("/")
        return f"{base}/?task={{key}}" if base else None

    @staticmethod
    def _needs_source_catalog(value: Any) -> bool:
        """Нужна ли проверка source handles, включая родителя текущего отчёта."""
        if isinstance(value, dict):
            if isinstance(value.get("source_id"), str):
                return True
            report = value.get("report")
            if isinstance(report, dict) and isinstance(report.get("task_id"), int):
                return True
            return any(
                ProjectAgentService._needs_source_catalog(item)
                for key, item in value.items()
                if key not in {"properties", "dialog_history"}
            )
        if isinstance(value, list):
            return any(ProjectAgentService._needs_source_catalog(item) for item in value)
        return False

    @staticmethod
    def _register_views(value, catalog, registry) -> None:
        if isinstance(value, dict):
            report = value.get("report")
            if isinstance(report, dict) and isinstance(report.get("task_id"), int):
                parent = catalog.sources.get(f"task:{report['task_id']}")
                if parent is not None:
                    registry.evidence.internal(catalog.project_id, parent)
            # Результаты доменных инструментов тоже содержат серверный source_id,
            # хотя их компактный ответ может не дублировать entity_type.
            if value.get("source_id") in catalog.sources:
                source = catalog.sources[value["source_id"]]
                registry.evidence.internal(catalog.project_id, source)
                if source.kind == "task_report" and isinstance(value.get("report"), dict):
                    # Current-report DTO обещает текущую голову задачи. Immutable
                    # старый отчёт остаётся в каталоге после v2/v3; parent relations
                    # и статус задачи меняют степень подтверждения результата.
                    parent = catalog.sources.get(source.parent_source_id)
                    if parent is not None:
                        registry.evidence.internal(catalog.project_id, parent)
                chunks = value.get("matching_chunks")
                excerpt = (
                    next(
                        (
                            chunk["text"]
                            for chunk in chunks
                            if isinstance(chunk, dict)
                            and isinstance(chunk.get("text"), str)
                            and chunk["text"]
                        ),
                        "",
                    )
                    if isinstance(chunks, list)
                    else ""
                )
                if not excerpt and isinstance(value.get("text"), str):
                    excerpt = value["text"]
                record = citation(source, excerpt=excerpt, score=value.get("score"))
                fragment_data = value.get("reference")
                if isinstance(fragment_data, dict):
                    try:
                        fragment = DocumentFragmentSchema.model_validate(fragment_data)
                    except ValueError:
                        fragment = None
                    if fragment:
                        expected = document_fragment(source, start=fragment.start, end=fragment.end)
                        if expected == fragment:
                            record.reference = fragment
                            record.excerpt = fragment.quote[:SOURCE_EXCERPT_LIMIT]
                            value["fragment_handle"] = registry.register_fragment(
                                source.source_id, fragment
                            )
                value["source_handle"] = registry.register(record)
                changes = record.model_dump(exclude={"source_id", "reference"})
                if record.reference:
                    changes["reference"] = record.reference
                registry.update(source.source_id, **changes)
            for key, item in list(value.items()):
                if key not in {"properties", "dialog_history"}:
                    ProjectAgentService._register_views(item, catalog, registry)
        elif isinstance(value, list):
            for item in value:
                ProjectAgentService._register_views(item, catalog, registry)

    @staticmethod
    def _personal_task_source(value) -> KnowledgeSourceSchema | None:
        """Карточка строится из серверного DTO; финальный выбор перечитывает задачу с ACL."""
        if not isinstance(value, dict) or not isinstance(value.get("task"), dict):
            return None
        try:
            task = TaskSchema.model_validate(value["task"])
        except ValueError:
            return None
        if value.get("source_id") != f"task:{task.id}":
            return None
        return KnowledgeSourceSchema(
            source_id=value["source_id"],
            entity_type="task",
            entity_id=task.id,
            task_id=task.id,
            title=f"{task.key} · {task.title}",
            excerpt=(task.description_md or "")[:SOURCE_EXCERPT_LIMIT] or None,
            card=KnowledgeSourceCardSchema(
                key=task.key,
                status=task.status.value if task.status else None,
                priority=task.priority.value,
                assignee=task.assignee,
                due_date=task.due_date.isoformat() if task.due_date else None,
                summary=(task.description_md or "")[:240] or None,
            ),
        )

    @staticmethod
    def _register_personal_views(
        value,
        registry,
        *,
        project_document_scopes: dict[str, int] | None = None,
    ) -> None:
        """Project DTO допускает только результат wrapper, не вложенные пользовательские данные."""
        if isinstance(value, dict):
            if value.get("source_id") == f"task:{value.get('id')}" and "task" not in value:
                try:
                    registry.evidence.task(TaskSchema.model_validate(value), projection="summary")
                except ValueError:
                    pass
            task_source = ProjectAgentService._personal_task_source(value)
            if task_source is not None:
                registry.evidence.task(TaskSchema.model_validate(value["task"]))
                value["source_handle"] = registry.register(task_source)
            data = value.get("document", value)
            if value.get("source_id") and isinstance(data, dict) and data.get("ownership"):
                try:
                    document = DocumentCatalogItemSchema.model_validate(data)
                    kind, identifier = decode_document_ref(document.ref)
                except ValueError:
                    document = None
                if (
                    document
                    and (
                        document.ownership.project_id is None or project_document_scopes is not None
                    )
                    and value["source_id"] == f"{kind}:{identifier}"
                ):
                    if (
                        project_document_scopes is not None
                        and document.ownership.project_id is not None
                    ):
                        project_document_scopes[document.ref] = document.ownership.project_id
                    registry.evidence.document(document)
                    record = KnowledgeSourceSchema(
                        source_id=value["source_id"],
                        entity_type=kind,
                        entity_id=identifier,
                        title=document.title,
                        task_id=document.ownership.owner_task_id,
                        card=KnowledgeSourceCardSchema(summary=document.caption),
                    )
                    try:
                        fragment = DocumentFragmentSchema.model_validate(value.get("reference"))
                    except ValueError:
                        fragment = None
                    text = value.get("text")
                    if fragment and isinstance(text, str):
                        start = fragment.start - value.get("offset", 0)
                        if (
                            fragment.document_ref == document.ref
                            and fragment.content_hash == value.get("content_hash")
                            and start >= 0
                            and text[start : start + len(fragment.quote)] == fragment.quote
                        ):
                            record.reference = fragment
                            record.excerpt = fragment.quote[:SOURCE_EXCERPT_LIMIT]
                            value["fragment_handle"] = registry.register_fragment(
                                record.source_id, fragment
                            )
                    value["source_handle"] = registry.register(record)
                    changes = record.model_dump(exclude={"source_id", "reference"})
                    if record.reference:
                        changes["reference"] = record.reference
                    registry.update(record.source_id, **changes)
            for key, item in list(value.items()):
                if key not in {"properties", "dialog_history"}:
                    ProjectAgentService._register_personal_views(
                        item,
                        registry,
                        project_document_scopes=project_document_scopes if key == "items" else None,
                    )
        elif isinstance(value, list):
            for item in value:
                ProjectAgentService._register_personal_views(
                    item,
                    registry,
                    project_document_scopes=project_document_scopes,
                )

    @staticmethod
    def _elapsed_ms(started_at: float) -> float:
        """Возвращает длительность фазы по monotonic clock в миллисекундах."""
        return round((perf_counter() - started_at) * 1000, 3)

    @staticmethod
    async def _require_project(db: ProjectAgentScope, *, project_id: int) -> Project:
        """Возвращает проект области анализа или поднимает доменную ошибку."""
        project = await db.projects.get_by_id(project_id=project_id)
        if project is None:
            raise ProjectNotFoundError(project_id=project_id)
        return project

    async def get_status(self, project_id: int) -> KnowledgeStatusSchema:
        """Сверяет каждый источник и chunk, а не только наличие collection."""
        try:
            async with self.scope() as db:
                await self._require_project(db, project_id=project_id)
                counts = await db.jobs.get_status_counts(project_id)
                last_error = await db.jobs.get_last_error(project_id)
                catalog = build_catalog(project_id, await db.sources.get_project_rows(project_id))
        except KnowledgeIndexJobsRepositoryError as error:
            raise KnowledgeServiceError(str(error)) from error
        manifest = {}
        provider_error = None
        points_count = None
        if self.config.knowledge_enabled:
            try:
                manifest = await self.qdrant_client.manifest(project_id)
                points_count = len(manifest)
            except ClientError as error:
                provider_error = error.error_details
        rows, obsolete = coverage(
            catalog,
            manifest,
            target_chars=self.config.chunk_target_chars,
            overlap_chars=self.config.chunk_overlap_chars,
            embedding_signature=digest(
                [self.embedding_client.model, self.qdrant_client.vector_dim]
            ),
        )
        issues = file_issues(catalog)
        pending = counts.get(KnowledgeIndexStatus.PENDING, 0)
        processing = counts.get(KnowledgeIndexStatus.PROCESSING, 0)
        complete = (
            bool(catalog.sources)
            and not any(row["missing"] or row["stale"] for row in rows)
            and not obsolete
            and not issues
        )
        return KnowledgeStatusSchema(
            enabled=self.config.knowledge_enabled,
            ready=self.config.knowledge_enabled
            and provider_error is None
            and complete
            and pending == 0
            and processing == 0,
            points_count=points_count,
            pending_jobs=pending,
            processing_jobs=processing,
            failed_jobs=counts.get(KnowledgeIndexStatus.FAILED, 0),
            last_error=provider_error or last_error,
            coverage=rows,
            file_issues=issues,
            obsolete_points=obsolete,
        )

    async def reindex(self, project_id: int) -> None:
        """Ставит ручную полную пересборку в постоянную очередь."""
        if not self.config.knowledge_enabled:
            raise KnowledgeDisabledError("KNOWLEDGE_ENABLED=false")
        try:
            # Постановка задания и её фиксация — один факт: владелец
            # транзакции здесь, а не внутри репозитория очереди.
            async with self.scope() as db:
                await db.knowledge_events.reindex_project(project_id=project_id)
                await db.unit_of_work.commit()
        except (KnowledgeEventsServiceError, UnitOfWorkRepositoryError) as error:
            raise KnowledgeServiceError(str(error)) from error

    def _write_gate(
        self,
        *,
        question: str,
        history: list[KnowledgeChatMessageSchema],
        memory: str,
        phases: dict[str, float | None],
        verbatim: str | None = None,
    ) -> WriteGate:
        """Запускает проверку перед первым изменяющим инструментом вопроса."""
        started = perf_counter()

        async def decide() -> bool:
            try:
                return await self._classify_write_intent(
                    question=question, history=history, memory=memory, verbatim=verbatim
                )
            except Exception:
                # Сбой проверки — не повод отказать в ответе: агент ответит на
                # чтение, а изменение не выполнится (как и прежде при сбое).
                logger.warning("⚠️ Проверка поручения на запись не удалась.", exc_info=True)
                return False
            finally:
                phases["write_intent"] = self._elapsed_ms(started)

        return WriteGate(decide())

    async def _classify_write_intent(
        self,
        *,
        question: str,
        history: list[KnowledgeChatMessageSchema],
        memory: str = "",
        verbatim: str | None = None,
    ) -> bool:
        """Спрашивает модель, поручил ли участник изменить данные в текущем вопросе."""
        recent = {
            "question": question,
            "dialog_history": [message.model_dump() for message in history[-10:]],
            "dialog_memory": memory,
        }
        AgentContextBudget(
            system_prompt=PROJECT_AGENT_WRITE_INTENT_PROMPT,
            max_tokens=self.config.context_tokens,
            history_tokens=self.config.history_tokens,
        ).prepare(recent)
        payload: dict[str, Any] = {"question": question}
        if verbatim is not None:
            payload["participant_verbatim"] = verbatim
        content = json.dumps(
            {
                **payload,
                "dialog_memory": recent["dialog_memory"],
                "history": [
                    {"role": message["role"], "content": message["content"][:TEXT_FRAGMENT_LIMIT]}
                    for message in recent["dialog_history"]
                ],
            },
            ensure_ascii=False,
        )
        result = await self.llm_client.get_structured_response(
            system_prompt=PROJECT_AGENT_WRITE_INTENT_PROMPT,
            content=content,
            schema=WriteIntent,
            max_completion_tokens=700,
        )
        return result.write_intent
