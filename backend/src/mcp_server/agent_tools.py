"""Арис (проектный менеджер) как субагент внешнего клиента (#40793).

вызывающий клиент (менеджерский агент внешнего клиента) передаёт сюда всё про задачи сотрудника одной
фразой, не разбираясь в проектах и инструментах трекера: агент трекера сам
находит проект, проверяет права сотрудника и делает. Вопрос встаёт в общую
очередь ответов агента (её обрабатывает backend), вызов ждёт готового ответа
до ``AGENT_ASK_WAIT_SECONDS`` и иначе отдаёт ``request_id`` для
``tt_projects_result``.

Право ключа — ``agent:ask``. Изменения агент делает только по дословной
реплике сотрудника ``user_text`` и только в пределах остальных прав ключа.
REST-двойники — ``POST /api/v1/agent/ask`` и ``GET /api/v1/agent/requests/{id}``.
"""

import logging
from typing import Annotated
from uuid import UUID

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from src.exceptions.agent_conversations import AgentConversationsServiceError
from src.exceptions.auth import AuthServiceError
from src.mcp_server.context import tool_context
from src.mcp_server.registry import tt_tool
from src.mcp_server.write_tools import Actor
from src.schemas.agent_conversations import TimPageContextSchema
from src.schemas.agent_requests import AgentAskSchema
from src.services.agent_requests import principal_key_id, principal_key_scopes
from src.utils.api_scopes import SCOPE_AGENT_ASK

logger = logging.getLogger(__name__)

RESULT_HINT = (
    "Ответ: status (completed — готово; queued/processing — ещё готовится, спроси "
    "tt_projects_result с request_id позже; failed — ошибка в error), answer — полный "
    "текст для сотрудника со ссылками на задачи, links — задачи ответа, done — выполненные "
    "действия, pending — предложенные действия: передай сотруднику confirm_url, он сам "
    "подтвердит или отклонит. Не утверждай, что pending выполнено."
)


@tt_tool(
    name="tt_projects_ask",
    title="Спросить Ариса — проектного менеджера",
    description=(
        "Передай Арису — проектному менеджеру — любой вопрос или "
        "поручение сотрудника про его проекты, "
        "задачи, сроки, разовые задачи — одной фразой. Агент сам найдёт проект "
        "(по ключу задачи вида PROJ-12 или по смыслу), проверит права сотрудника и сделает; "
        "если неясно, о каком проекте речь, — задаст уточняющий вопрос. Менять данные он "
        "будет только по дословной реплике сотрудника в user_text; без user_text — только "
        "чтение. Опасные действия (удаление) не выполняются сразу: сотрудник подтверждает их "
        "сам по ссылке. Держи один conversation_id на одну беседу с сотрудником. Если "
        "сотрудник смотрит трекер (встройка прислала контекст страницы), передай page_context — "
        "тогда «эта задача» и «этот проект» поймутся без ключа. " + RESULT_HINT
    ),
    scope=SCOPE_AGENT_ASK,
    write=False,
)
async def projects_ask(
    context: Context,
    text: Annotated[
        str,
        Field(
            description=(
                "Вопрос или поручение своими словами: что нужно узнать или сделать. По нему "
                "агент ищет, но менять данные по нему не будет."
            ),
            min_length=1,
            max_length=8000,
        ),
    ],
    user_text: Annotated[
        str | None,
        Field(
            description=(
                "Дословная реплика сотрудника, без пересказа. Только она разрешает агенту "
                "изменения; не передавай её, если сотрудник ничего не поручал."
            ),
            max_length=8000,
        ),
    ] = None,
    conversation_id: Annotated[
        str | None,
        Field(
            description=(
                "Ключ беседы, например external:chat:<id чата>. Одна беседа — один диалог агента с "
                "памятью; без ключа каждый вопрос начинает новый диалог."
            ),
            max_length=255,
        ),
    ] = None,
    page_context: Annotated[
        TimPageContextSchema | None,
        Field(
            description=(
                "Экран трекера, открытый у сотрудника: route, project_key, task_id, board_id "
                "из последнего контекст страницы. «Эта задача» — task_id. Прав не даёт: чужое "
                "молча игнорируется; ключ задачи в тексте главнее."
            ),
        ),
    ] = None,
    actor: Actor = None,
) -> dict:
    """Задаёт вопрос агенту трекера от имени сотрудника."""
    try:
        data = AgentAskSchema(
            text=text,
            user_text=user_text,
            conversation_id=conversation_id,
            page_context=page_context,
        )
    except ValidationError as error:
        raise ToolError(
            "conversation_id: допустимы латиница, цифры и символы _ . : -, до 255 знаков, "
            "например external:chat:123."
        ) from error
    async with tool_context(context) as tools:
        principal = tools.principal
        requests = tools.services.requests
        wait_seconds = tools.settings.agent.agent_ask_wait_seconds
        try:
            request_id = await requests.ask_external(
                user_id=principal.user_id,
                api_key_id=principal_key_id(principal),
                data=data,
                key_scopes=principal_key_scopes(principal),
            )
        except (AgentConversationsServiceError, AuthServiceError) as error:
            raise ToolError(error.detail) from error
    # Ожидание — вне сессии вызова: соединение пула MCP не держится 25 секунд.
    try:
        result = await requests.wait_external_result(
            user_id=principal.user_id,
            api_key_id=principal_key_id(principal),
            request_id=request_id,
            timeout_seconds=wait_seconds,
        )
    except (AgentConversationsServiceError, AuthServiceError) as error:
        raise ToolError(error.detail) from error
    logger.info("✅ tt_projects_ask: request_id=%s, status=%s.", request_id, result.status)
    return result.model_dump(mode="json")


@tt_tool(
    name="tt_projects_result",
    title="Результат вопроса Арису",
    description=(
        "Результат вопроса, заданного через tt_projects_ask, по его request_id — когда ответ "
        "не успел за время вызова. Доступен только тому ключу и сотруднику, что задали вопрос. "
        + RESULT_HINT
    ),
    scope=SCOPE_AGENT_ASK,
    write=False,
)
async def projects_result(
    context: Context,
    request_id: Annotated[str, Field(description="request_id из ответа tt_projects_ask.")],
    actor: Actor = None,
) -> dict:
    """Отдаёт результат вопроса его автору."""
    try:
        parsed = UUID(request_id.strip())
    except ValueError as error:
        raise ToolError("request_id должен быть UUID из ответа tt_projects_ask.") from error
    async with tool_context(context) as tools:
        try:
            result = await tools.services.requests.get_external_result(
                user_id=tools.principal.user_id,
                api_key_id=principal_key_id(tools.principal),
                request_id=parsed,
            )
        except (AgentConversationsServiceError, AuthServiceError) as error:
            raise ToolError(error.detail) from error
    return result.model_dump(mode="json")
