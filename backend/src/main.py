import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pprint import pformat

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.middleware.api_key_usage import ApiKeyUsageMiddleware
from src.api.middleware.request_actor import RequestActorMiddleware
from src.api.v1.endpoints.agent_conversations import router as agent_conversations_router
from src.api.v1.endpoints.agent_pending import router as agent_pending_router
from src.api.v1.endpoints.agent_requests import router as agent_requests_router
from src.api.v1.endpoints.analytics import router as analytics_router
from src.api.v1.endpoints.api_tokens import router as api_tokens_router
from src.api.v1.endpoints.auth import router as auth_router
from src.api.v1.endpoints.board_editor import router as board_editor_router
from src.api.v1.endpoints.board_shapes import router as board_shapes_router
from src.api.v1.endpoints.calendar import router as calendar_router
from src.api.v1.endpoints.calendar_feed import router as calendar_feed_router
from src.api.v1.endpoints.calendar_scenarios import router as calendar_scenarios_router
from src.api.v1.endpoints.calendar_workload import router as calendar_workload_router
from src.api.v1.endpoints.dashboard import router as dashboard_router
from src.api.v1.endpoints.document_links import router as document_links_router
from src.api.v1.endpoints.documents import router as documents_router
from src.api.v1.endpoints.free_boards import router as free_boards_router
from src.api.v1.endpoints.knowledge import router as knowledge_router
from src.api.v1.endpoints.milestones import router as milestones_router
from src.api.v1.endpoints.my_calendar import router as my_calendar_router
from src.api.v1.endpoints.personal_api_keys import router as personal_api_keys_router
from src.api.v1.endpoints.project_boards import router as project_boards_router
from src.api.v1.endpoints.project_chat_ws import router as project_chat_ws_router
from src.api.v1.endpoints.project_chats import download_router as chat_download_router
from src.api.v1.endpoints.project_chats import router as project_chats_router
from src.api.v1.endpoints.project_files import router as project_files_router
from src.api.v1.endpoints.project_icons import router as project_icons_router
from src.api.v1.endpoints.project_members import router as project_members_router
from src.api.v1.endpoints.project_risks import router as project_risks_router
from src.api.v1.endpoints.project_stages import router as project_stages_router
from src.api.v1.endpoints.project_stickers import router as project_stickers_router
from src.api.v1.endpoints.projects import router as projects_router
from src.api.v1.endpoints.risk_formulation import router as risk_formulation_router
from src.api.v1.endpoints.risk_suggestions import router as risk_suggestions_router
from src.api.v1.endpoints.task_activity import router as task_activity_router
from src.api.v1.endpoints.task_attachments import router as task_attachments_router
from src.api.v1.endpoints.task_attention import router as task_attention_router
from src.api.v1.endpoints.task_attention_ws import router as task_attention_ws_router
from src.api.v1.endpoints.task_checklists import router as task_checklists_router
from src.api.v1.endpoints.task_comments import router as task_comments_router
from src.api.v1.endpoints.task_dependencies import router as task_dependencies_router
from src.api.v1.endpoints.task_documents import router as task_documents_router
from src.api.v1.endpoints.task_moves import router as task_moves_router
from src.api.v1.endpoints.task_reports import router as task_reports_router
from src.api.v1.endpoints.tasks import router as tasks_router
from src.api.v1.endpoints.tim import chat_router as tim_chat_router
from src.api.v1.endpoints.tim import router as tim_router
from src.api.v1.endpoints.users import router as users_router
from src.api.v1.endpoints.wbs_nodes import router as wbs_nodes_router
from src.core.app_state import (
    RUNTIME_STATE_KEY,
    SESSION_FACTORY_STATE_KEY,
    SETTINGS_STATE_KEY,
)
from src.core.config_logger import configure_logging
from src.core.settings import get_settings
from src.db.session import async_session_factory, engine
from src.dependencies.scopes import build_api_key_usage_recorder
from src.dependencies.services import build_agent_worker
from src.dependencies.task_attention import build_task_attention_runtime
from src.exceptions.clients import VectorStoreClientError
from src.knowledge.composition import build_knowledge_worker
from src.knowledge.runtime import (
    build_knowledge_runtime,
    close_knowledge_runtime,
    create_http_client,
    create_qdrant_client,
)
from src.mcp_server.server import build_mcp_app, mcp_server
from src.realtime.runtime import CHAT_RUNTIME_KEY, build_chat_runtime
from src.utils.check_db import check_db_connection

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[dict[str, object]]:
    """Создаёт тяжёлые ресурсы при старте и освобождает их при остановке.

    Приложение — единственный владелец сетевых клиентов: они создаются
    здесь, отдаются graph-у зависимостей через состояние запроса и здесь же
    закрываются. Смонтированное MCP-приложение видит то же состояние,
    поэтому у обоих транспортов один набор клиентов, а не два.

    Args:
        app: Экземпляр FastAPI, жизненным циклом которого управляет функция.

    Yields:
        Состояние запроса с ресурсами приложения.
    """
    logger.info("🚀 Запуск приложения %s.", settings.app.app_name)
    async with async_session_factory() as db_session:
        await check_db_connection(db_session=db_session)

    http_client = create_http_client(settings)
    qdrant_client = create_qdrant_client(settings)
    runtime = build_knowledge_runtime(
        settings=settings,
        http_client=http_client,
        qdrant_client=qdrant_client,
    )
    worker_stop = asyncio.Event()
    worker_task: asyncio.Task | None = None
    if settings.knowledge.knowledge_enabled:
        try:
            await runtime.qdrant_client.backfill_payload_indexes()
        except VectorStoreClientError:
            runtime.payload_indexes_backfill_pending = True
            logger.warning(
                "⚠️ Qdrant недоступен при старте; backfill payload-индексов отложен.",
                exc_info=True,
            )
        worker = build_knowledge_worker(
            session_factory=async_session_factory,
            settings=settings,
            runtime=runtime,
        )
        worker_task = asyncio.create_task(
            worker.run(worker_stop),
            name="project-knowledge-indexer",
        )
        logger.info("✅ Фоновый индексатор базы знаний запущен.")
    agent_worker = build_agent_worker(
        session_factory=async_session_factory, settings=settings, runtime=runtime
    )
    agent_task = asyncio.create_task(agent_worker.run(worker_stop), name="project-agent-worker")
    chat_runtime = build_chat_runtime(settings=settings, session_factory=async_session_factory)
    attention_runtime = build_task_attention_runtime(
        settings=settings, session_factory=async_session_factory
    )
    try:
        await chat_runtime.start()
        await attention_runtime.start()
        # Сессионный менеджер MCP обязан работать всё время жизни приложения:
        # смонтированное ASGI-приложение своего lifespan не получает.
        async with mcp_server.session_manager.run():
            logger.info("✅ MCP-сервер доступен на %s.", settings.app.mcp_path)
            logger.info("✅ Приложение успешно запущено.")
            yield {
                RUNTIME_STATE_KEY: runtime,
                SETTINGS_STATE_KEY: settings,
                SESSION_FACTORY_STATE_KEY: async_session_factory,
                CHAT_RUNTIME_KEY: chat_runtime,
                "task_attention_runtime": attention_runtime,
            }
    finally:
        await attention_runtime.close()
        await chat_runtime.close()
        worker_stop.set()
        agent_task.cancel()
        with suppress(asyncio.CancelledError):
            await agent_task
        if worker_task is not None:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
        await close_knowledge_runtime(runtime)
        await engine.dispose()
        logger.info("✅ Ресурсы приложения освобождены.")


app = FastAPI(
    title=settings.app.app_name,
    version=settings.app.app_version,
    lifespan=lifespan,
)

app.add_middleware(RequestActorMiddleware)
app.add_middleware(
    ApiKeyUsageMiddleware, recorder=build_api_key_usage_recorder(async_session_factory)
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.app.cors_origins,
    # Сессия живёт в cookie, поэтому браузеру нужно разрешение слать её кросс-origin.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Преобразует ошибки Pydantic/FastAPI в единый ответ 422.

    Args:
        request: Исходный HTTP-запрос.
        exc: Ошибка валидации.

    Returns:
        JSON-ответ с деталями некорректных полей.
    """
    errors = exc.errors()
    logger.warning(
        "⚠️ Ошибка валидации %s %s. Детали:\n%s",
        request.method,
        request.url.path,
        pformat([{"loc": error.get("loc"), "type": error.get("type")} for error in errors]),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(errors)},
    )


for api_router in (
    project_boards_router,
    free_boards_router,
    board_shapes_router,
    board_editor_router,
    task_reports_router,
    task_moves_router,
    task_attention_router,
    task_attention_ws_router,
    project_icons_router,
    personal_api_keys_router,
    agent_requests_router,
    agent_pending_router,
    tim_router,
    tim_chat_router,
    project_risks_router,
    risk_suggestions_router,
    risk_formulation_router,
    task_checklists_router,
    auth_router,
    users_router,
    api_tokens_router,
    dashboard_router,
    analytics_router,
    calendar_router,
    my_calendar_router,
    calendar_workload_router,
    calendar_feed_router,
    calendar_scenarios_router,
    milestones_router,
    projects_router,
    project_stickers_router,
    project_members_router,
    project_stages_router,
    tasks_router,
    wbs_nodes_router,
    documents_router,
    project_files_router,
    document_links_router,
    task_comments_router,
    task_dependencies_router,
    task_activity_router,
    task_attachments_router,
    task_documents_router,
    knowledge_router,
    agent_conversations_router,
    project_chats_router,
    chat_download_router,
    project_chat_ws_router,
):
    app.include_router(api_router, prefix=settings.app.api_v1_prefix)


# MCP монтируется отдельным ASGI-приложением: его транспорт держит долгие
# соединения и не вписывается в контур обычных JSON-эндпоинтов.
mcp_app = build_mcp_app(settings=settings)
app.mount(settings.app.mcp_path, mcp_app)


@app.get("/health/live", include_in_schema=False)
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
async def health_ready() -> JSONResponse:
    from sqlalchemy import text

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})
