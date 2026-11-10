from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass, replace
from datetime import date
from time import perf_counter
from typing import Any

from pydantic import BaseModel
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models import Model

from src.agent.aris import ArisMode
from src.agent.context import AgentContextBudget
from src.agent.progress import SEARCH_PROJECT, ProgressReporter, ProgressSink
from src.agent.runtime import (
    PROJECT_ACTION_TOOL,
    ArisAnswer,
    ArisDeps,
    ArisRunResult,
    ArisRuntime,
    WriteGate,
)
from src.agent.runtime import metrics as runtime_metrics
from src.agent.tools import AgentTool, AgentToolContext, AgentToolExecutor
from src.clients.embedding import EmbeddingClient
from src.clients.llm import LlmClient
from src.clients.qdrant import KnowledgeSearchHit, ProjectQdrantClient
from src.db.models.knowledge_index_jobs import (
    KnowledgeIndexStatus,
)
from src.db.models.projects import Project
from src.exceptions.ai import AgentAnswerError, AiProviderError
from src.exceptions.base import RepositoryError
from src.exceptions.clients import ClientError
from src.exceptions.knowledge import (
    KnowledgeDisabledError,
    KnowledgeEventsServiceError,
    KnowledgeIndexJobsRepositoryError,
    KnowledgeServiceError,
)
from src.exceptions.projects import ProjectNotFoundError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.knowledge.catalog import build_catalog, digest
from src.knowledge.context import (
    citation,
    coverage,
    file_issues,
    retrieve,
)
from src.prompts.project_agent import (
    ARIS_BASE_PROMPTS,
    PROJECT_AGENT_API_BASE_PROMPT,
    PROJECT_AGENT_WRITE_INTENT_PROMPT,
)
from src.schemas.knowledge import (
    KnowledgeAnswerSchema,
    KnowledgeChatMessageSchema,
    KnowledgeSourceSchema,
    KnowledgeStatusSchema,
)
from src.services.agent_cards import source_card
from src.services.db_scope import ProjectAgentScope, ProjectAgentScopeFactory

logger = logging.getLogger(__name__)

PROJECT_DESCRIPTION_LIMIT = 400
TEXT_FRAGMENT_LIMIT = 1200
SOURCE_EXCERPT_LIMIT = 500
#: Канал ответа внешнему клиенту.
API_CHANNEL = "api"
EMPTY_ANSWER = (
    "Не удалось завершить проверку источников. Уточните вопрос или сузьте перечень объектов."
)
INCOMPLETE_ANSWER = (
    "Проверка неполная: исчерпан лимит шагов, часть запрошенных данных осталась "
    "непрочитанной. Уточните вопрос или сузьте перечень объектов."
)


class WriteIntent(BaseModel):
    """Ответ проверки: поручил ли участник изменить данные в текущем вопросе."""

    write_intent: bool = False


def retrieval_query(question: str, history: list[KnowledgeChatMessageSchema]) -> str:
    """Запрос стартового поиска: вопрос, а в продолжении беседы — ещё и прошлый вопрос.

    Раньше запрос переписывал отдельный вызов модели (4–6 с до старта агента).
    Первый вопрос беседы самодостаточен; в продолжении («а кто по ней исполнитель?»)
    предыдущая реплика участника несёт предмет разговора. Дальше агент ищет сам
    инструментами, стартовый поиск — только подсказка.
    """
    previous = next((item.content for item in reversed(history) if item.role == "user"), "").strip()
    if not previous:
        return question
    return f"{previous[:TEXT_FRAGMENT_LIMIT]}\n{question}"


class _SourceRegistry:
    """Связывает реальные source_id с непредсказуемыми хэндлами одного запроса."""

    def __init__(self) -> None:
        self.nonce = secrets.token_hex(8)
        self._by_handle: dict[str, KnowledgeSourceSchema] = {}
        self._handle_by_source_id: dict[str, str] = {}

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


@dataclass(frozen=True, slots=True)
class ProjectAgentConfig:
    """Настройки семантического поиска, нужные Арис.

    Сервис получает только те значения, которые действительно использует,
    а не весь объект настроек приложения.
    """

    knowledge_enabled: bool
    semantic_limit: int
    score_threshold: float
    chunk_target_chars: int = 2200
    chunk_overlap_chars: int = 300
    tool_rounds: int = 4
    context_tokens: int = 12000
    retrieval_limit: int = 5
    history_tokens: int = 1200
    app_public_url: str = ""


class ProjectAgentService:
    """RAG-агент: semantic retrieval плюс обязательный актуальный SQL-срез."""

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
        self._runtime: ArisRuntime | None = None

    @property
    def runtime(self) -> ArisRuntime:
        """Агент PydanticAI над текущим реестром инструментов (строится один раз)."""
        if self._runtime is None or self._runtime.executor is not self.tools:
            self._runtime = ArisRuntime(model=self.agent_model, executor=self.tools)
        return self._runtime

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
        mode: ArisMode = ArisMode.WIDGET_PROJECT,
        extra_context: dict[str, Any] | None = None,
        channel: str = "web",
        verbatim: str | None = None,
        conversation_id: str | None = None,
        on_progress: ProgressSink | None = None,
    ) -> KnowledgeAnswerSchema:
        """Ищет по всем источникам и позволяет модели дочитать текст и связи.

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
        tool_context = replace(tool_context, mode=mode, can_write=write_possible)
        progress = ProgressReporter(on_progress)
        deps: ArisDeps | None = None
        try:
            query = retrieval_query(question, history)
            registry = _SourceRegistry()
            content = {
                "mode": mode.value,
                "current_date": date.today().isoformat(),
                "question": question,
                **(extra_context or {}),
                "dialog_history": [message.model_dump() for message in history],
                "dialog_memory": memory,
                "current_participant": actor,
                "project_reference": {"id": project.id, "key": project.key},
                "writes_allowed": tool_context.can_write,
                "previous_actions": action_history or [],
                "uploaded_files": uploaded_files or [],
            }
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
            state: dict[str, Any] = {
                "catalog": None,
                "stale": False,
                "semantic_loaded": False,
                "fts_hits": [],
            }
            context_lock = asyncio.Lock()

            async def load_context(group_id: str) -> None:
                """Читает данные только после выбора рабочей области моделью."""
                async with context_lock:
                    progress.report(SEARCH_PROJECT)
                    if state["catalog"] is None:
                        current_project, catalog, fts_hits, stages = await self._load_project_base(
                            project_id, query, phases
                        )
                        state["catalog"], state["fts_hits"] = catalog, fts_hits
                        content.update(
                            retrieval_query=query,
                            current_postgres_state=self._build_postgres_context(
                                project=current_project, stages=stages, registry=registry
                            ),
                            catalog={
                                "counts": catalog.counts,
                                "file_issues": file_issues(catalog)[:5],
                                "file_issues_total": len(file_issues(catalog)),
                                "semantic_available": False,
                            },
                        )
                        content["retrieval_context"] = retrieve(
                            catalog,
                            fts_hits=fts_hits,
                            semantic_hits=[],
                            query=query,
                            target_chars=self.config.chunk_target_chars,
                            overlap_chars=self.config.chunk_overlap_chars,
                            limit=self.config.retrieval_limit,
                        )
                    # Точные операции с задачами/досками читают SQL-инструментами.
                    # Embeddings нужны только для выбранной области поиска по знаниям.
                    if (
                        group_id in {"sources", "documents", "attachments", "reports"}
                        and not state["semantic_loaded"]
                    ):
                        hits, available = await self._semantic_search(project_id, query, phases)
                        state["semantic_loaded"] = True
                        content["catalog"]["semantic_available"] = available
                        content["retrieval_context"] = retrieve(
                            state["catalog"],
                            fts_hits=state["fts_hits"],
                            semantic_hits=hits,
                            query=query,
                            target_chars=self.config.chunk_target_chars,
                            overlap_chars=self.config.chunk_overlap_chars,
                            limit=self.config.retrieval_limit,
                        )
                    self._register_views(content, state["catalog"], registry)

            deps = ArisDeps(
                execution=tool_context,
                executor=self.tools,
                system_prompt=system_prompt,
                content=content,
                budget=budget,
                tool_rounds=self.config.tool_rounds,
                defer_workflows=True,
                load_context=load_context,
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

            async def register(tool: AgentTool, result: dict[str, Any]) -> None:
                mutating = tool.mutating or tool.name == PROJECT_ACTION_TOOL
                # Новая сущность должна получить проверенный handle уже в результате
                # создавшего её инструмента, до следующего шага модели.
                if state["stale"] or mutating:
                    async with self.scope() as db:
                        await self._require_project(db, project_id=project_id)
                        state["catalog"] = build_catalog(
                            project_id, await db.sources.get_project_rows(project_id)
                        )
                    state["stale"] = False
                self._register_views(result, state["catalog"], registry)
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
                if state["stale"]:
                    async with self.scope() as db:
                        await self._require_project(db, project_id=project_id)
                        state["catalog"] = build_catalog(
                            project_id, await db.sources.get_project_rows(project_id)
                        )
                catalog = state["catalog"]
                output = run.output or ArisAnswer()
                selected = []
                seen_sources = set()
                for handle in [*output.source_ids, *(member_handles if run.output else [])]:
                    source = registry.resolve(handle)
                    if (
                        source
                        and catalog is not None
                        and source.source_id in catalog.sources
                        and source.source_id not in seen_sources
                    ):
                        current_source = catalog.sources[source.source_id]
                        selected.append(
                            source.model_copy(
                                update={
                                    "title": current_source.title,
                                    "card": source_card(current_source, catalog),
                                }
                            )
                        )
                        seen_sources.add(source.source_id)
                answer = output.answer.strip()
                if run.output is None:
                    answer = INCOMPLETE_ANSWER
                elif not answer and not selected and completed_actions:
                    answer = (
                        "Действие выполнено. Результат — в карточке действия."
                        if len(completed_actions) == 1
                        else f"Выполнено действий: {len(completed_actions)}. "
                        "Результаты — в карточках действий."
                    )
                elif not answer and not selected:
                    answer = EMPTY_ANSWER
                return KnowledgeAnswerSchema(answer=answer, sources=selected)
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
            Ответ без карточек источников: объекты названы ключами в тексте.
        Raises:
            AgentAnswerError: При ошибке чтения данных.
            AiProviderError: Если модель не смогла подготовить ответ.
        """
        started = perf_counter()
        question = question.strip()
        phases: dict[str, float | None] = {"write_intent": None}
        mode = ArisMode.WIDGET_PERSONAL
        tool_context = replace(execution, project_id=None, mode=mode)
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
        }
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
            answer = run.output.answer.strip() or (
                "Не удалось завершить проверку данных. Уточните вопрос."
            )
            return KnowledgeAnswerSchema(answer=answer, sources=[])
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

    def task_link_template(self) -> str | None:
        """Адрес карточки задачи в интерфейсе с местом для ключа; None без APP_PUBLIC_URL."""
        base = self.config.app_public_url.rstrip("/")
        return f"{base}/?task={{key}}" if base else None

    @staticmethod
    def _register_views(value, catalog, registry) -> None:
        if isinstance(value, dict):
            # Результаты доменных инструментов тоже содержат серверный source_id,
            # хотя их компактный ответ может не дублировать entity_type.
            if value.get("source_id") in catalog.sources:
                source = catalog.sources[value["source_id"]]
                excerpt = (value.get("matching_chunks") or [{}])[0].get("text") or value.get(
                    "text", ""
                )
                record = citation(source, excerpt=excerpt, score=value.get("score"))
                value["source_handle"] = registry.register(record)
                registry.update(source.source_id, **record.model_dump(exclude={"source_id"}))
            for key, item in list(value.items()):
                if key not in {"properties", "dialog_history"}:
                    ProjectAgentService._register_views(item, catalog, registry)
        elif isinstance(value, list):
            for item in value:
                ProjectAgentService._register_views(item, catalog, registry)

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

    async def _semantic_search(
        self, project_id: int, query: str, phases: dict[str, float | None]
    ) -> tuple[list[KnowledgeSearchHit], bool]:
        """Векторный поиск по проекту; недоступный индекс — не ошибка ответа."""
        if not self.config.knowledge_enabled:
            return [], False
        phase = perf_counter()
        try:
            vector = await self.embedding_client.get_embedding(query)
            phases["embedding"] = self._elapsed_ms(phase)
            phase = perf_counter()
            hits = await self.qdrant_client.search(
                project_id=project_id,
                vector=vector,
                limit=self.config.semantic_limit,
                score_threshold=self.config.score_threshold,
                entity_type=None,
            )
            phases["qdrant"] = self._elapsed_ms(phase)
            return hits, True
        except ClientError:
            phases["qdrant" if phases["embedding"] is not None else "embedding"] = self._elapsed_ms(
                phase
            )
            logger.warning(
                "Семантический поиск проекта %s недоступен; читаю PostgreSQL.",
                project_id,
                exc_info=True,
            )
            return [], False

    async def _load_project_base(
        self, project_id: int, query: str, phases: dict[str, float | None]
    ) -> tuple[Project, Any, list[Any], list[Any]]:
        """Проект, каталог источников, полнотекстовый поиск и стадии — одной короткой сессией."""
        phase = perf_counter()
        try:
            async with self.scope() as db:
                project = await self._require_project(db, project_id=project_id)
                catalog = build_catalog(project_id, await db.sources.get_project_rows(project_id))
                fts_hits = await db.sources.search(project_id, query, entity_type=None, limit=30)
                stages = await db.stages.get_by_project(project_id)
        except RepositoryError as error:
            raise AgentAnswerError(str(error)) from error
        phases["ranked_fts"] = self._elapsed_ms(phase)
        return project, catalog, fts_hits, stages

    def _build_postgres_context(
        self,
        *,
        project: Project,
        stages: list[Any],
        registry: _SourceRegistry,
    ) -> dict[str, Any]:
        """Паспорт проекта и его стадии — актуальная база ответа из PostgreSQL.

        Остальное (сводки, просрочки, календарь, риски) агент читает сам
        инструментами — раньше это подкладывал планировщик до старта агента.
        """
        project_source = KnowledgeSourceSchema(
            source_id=f"project:{project.id}",
            entity_type="project",
            entity_id=project.id,
            title=f"{project.key} · {project.name}"[:512],
            excerpt=(project.description_md or "")[:SOURCE_EXCERPT_LIMIT] or None,
        )
        return {
            "project": {
                "source_handle": registry.register(project_source),
                "key": project.key,
                "name": project.name[:512],
                "status": getattr(project.status, "value", str(project.status)),
                "start_date": str(project.start_date) if project.start_date else None,
                "due_date": str(project.due_date) if project.due_date else None,
                "description": (project.description_md or "")[:PROJECT_DESCRIPTION_LIMIT],
            },
            "stages": [
                {"name": stage.name[:255], "is_done_stage": stage.is_done_stage} for stage in stages
            ],
        }
