"""MCP-контракт реестра рисков через общие application services."""

import logging
from datetime import date
from typing import Annotated

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from src.exceptions.base import ApplicationError
from src.mcp_server.context import ToolContext, resolve_project, resolve_task, tool_context
from src.mcp_server.registry import telos_tool
from src.mcp_server.write_tools import Actor
from src.schemas.enums import RiskRating, RiskResponseStrategy, RiskSource, RiskStatus
from src.schemas.project_risks import (
    ProjectRiskCreateSchema,
    ProjectRiskFilters,
    ProjectRiskSchema,
    ProjectRiskUpdateSchema,
)
from src.utils.api_scopes import SCOPE_PROJECTS_READ, SCOPE_TASKS_WRITE

logger = logging.getLogger(__name__)
ProjectKey = Annotated[
    str, Field(min_length=1, max_length=32, description="Ключ проекта, например PROJ.")
]
RiskKey = Annotated[
    str,
    Field(
        pattern=r"^RISK-[1-9][0-9]{0,9}$", description="Стабильный ключ риска, например RISK-12."
    ),
]
Confirmation = Annotated[
    bool,
    Field(
        description="true только после явного подтверждения пользователем этих полей; AI не регистрирует риски самостоятельно."
    ),
]


def _risk_id(key: str) -> int:
    """Разбирает публичный ключ без возможности переполнить SQL INTEGER."""
    if not key.startswith("RISK-") or not key[5:].isdigit() or not 0 < int(key[5:]) <= 2147483647:
        raise ToolError("Ключ риска должен иметь вид RISK-12.")
    return int(key[5:])


async def _links(
    tools: ToolContext, project_id: int, owner: str | None, task_key: str | None
) -> dict:
    """Переводит логин и ключ задачи в проверенные ссылки текущего проекта."""
    result = {}
    if owner is not None:
        result["owner_user_id"] = (
            await tools.services.members.resolve_member_user_id(
                project_id=project_id, username=owner.strip().lower()
            )
            if owner.strip()
            else None
        )
    if task_key is not None:
        if task_key.strip():
            task = await resolve_task(tools, task_key.strip())
            if task.project_id != project_id:
                raise ToolError("Связанная задача должна принадлежать проекту риска.")
            result["task_id"] = task.task_id
        else:
            result["task_id"] = None
    return result


async def _items(
    tools: ToolContext, project_id: int, risks: list[ProjectRiskSchema], *, detail: bool
) -> list[dict]:
    """Формирует читаемые ссылки пакетно, без N+1-запросов и внутренних ID."""
    task_ids = {risk.task_id for risk in risks if risk.task_id is not None}
    keys = (
        await tools.services.query.get_task_keys(project_id=project_id, task_ids=task_ids)
        if task_ids
        else {}
    )
    members = (
        await tools.services.members.get_member_list(project_id)
        if any(risk.owner_user_id is not None for risk in risks)
        else []
    )
    owners = {member.user.id: member.user.username for member in members}
    result = []
    for risk in risks:
        item = risk.model_dump(
            mode="json", exclude={"id", "project_id", "task_id", "owner_user_id"}
        )
        item.update(task_key=keys.get(risk.task_id), owner=owners.get(risk.owner_user_id))
        limit = 4000 if detail else 300
        item["truncated_fields"] = []
        for name in ("description", "mitigation_plan", "response_plan"):
            if len(item[name]) > limit:
                item[name] = item[name][:limit] + "…"
                item["truncated_fields"].append(name)
        result.append(item)
    return result


@telos_tool(
    name="telos_list_project_risks",
    scope=SCOPE_PROJECTS_READ,
    title="Реестр рисков проекта",
    description="Читает зарегистрированные риски с фильтрами и пагинацией; это отдельные записи, не календарные сигналы.",
)
async def list_project_risks(
    context: Context,
    project_key: ProjectKey,
    status: Annotated[RiskStatus | None, Field(description="Состояние риска.")] = None,
    probability: Annotated[RiskRating | None, Field(description="Вероятность.")] = None,
    impact: Annotated[RiskRating | None, Field(description="Влияние.")] = None,
    risk_level: Annotated[RiskRating | None, Field(description="Серверный уровень.")] = None,
    owner: Annotated[str | None, Field(description="Точный логин владельца.")] = None,
    task_key: Annotated[str | None, Field(description="Ключ связанной задачи.")] = None,
    search: Annotated[
        str | None, Field(max_length=255, description="Поиск по ключу, названию и описанию.")
    ] = None,
    active_only: Annotated[bool, Field(description="Исключить CLOSED.")] = False,
    page: Annotated[int, Field(ge=1, description="Номер страницы.")] = 1,
    page_size: Annotated[int, Field(ge=1, le=100, description="Размер страницы до 100.")] = 25,
) -> dict:
    """Возвращает страницу рисков доступного проекта."""
    logger.info("🚀 MCP: реестр рисков проекта key=%s.", project_key)
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            links = await _links(tools, project_id, owner, task_key)
            filters = ProjectRiskFilters(
                status=status,
                probability=probability,
                impact=impact,
                risk_level=risk_level,
                search=search,
                active_only=active_only,
                **links,
            )
            result = await tools.services.risks.list_risks(
                project_id=project_id,
                user_id=tools.principal.user_id,
                filters=filters,
                page=page,
                page_size=page_size,
            )
            items = await _items(tools, project_id, result.items, detail=False)
        except (ApplicationError, ValidationError) as error:
            raise ToolError(getattr(error, "detail", "Некорректные фильтры рисков.")) from error
    logger.info("✅ MCP: получено рисков %s.", len(items))
    return {
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "items": items,
    }


@telos_tool(
    name="telos_get_project_risk",
    scope=SCOPE_PROJECTS_READ,
    title="Карточка риска",
    description="Возвращает оценки, состояние, владельца, контроль, связь с задачей, стратегию и оба плана риска. Длинные тексты отмечены truncated_fields.",
)
async def get_project_risk(context: Context, project_key: ProjectKey, risk_key: RiskKey) -> dict:
    """Возвращает риск только внутри доступного проекта."""
    logger.info("🚀 MCP: чтение риска key=%s.", risk_key)
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            risk = await tools.services.risks.get_risk(
                project_id=project_id, risk_id=_risk_id(risk_key), user_id=tools.principal.user_id
            )
            result = (await _items(tools, project_id, [risk], detail=True))[0]
        except ApplicationError as error:
            raise ToolError(error.detail) from error
    logger.info("✅ MCP: риск прочитан key=%s.", risk_key)
    return result


@telos_tool(
    name="telos_create_project_risk",
    scope=SCOPE_TASKS_WRITE,
    title="Зарегистрировать риск",
    description="Создаёт риск только после явного подтверждения пользователем предложенных полей. risk_level вычисляет сервер.",
)
async def create_project_risk(
    context: Context,
    project_key: ProjectKey,
    confirmed_by_user: Confirmation,
    title: Annotated[str, Field(min_length=1, max_length=255, description="Название события.")],
    description: Annotated[
        str, Field(min_length=1, max_length=20000, description="Причины и последствия в Markdown.")
    ],
    probability: Annotated[RiskRating, Field(description="Вероятность.")],
    impact: Annotated[RiskRating, Field(description="Влияние.")],
    response_strategy: Annotated[
        RiskResponseStrategy, Field(description="Стратегия реагирования.")
    ],
    mitigation_plan: Annotated[str, Field(max_length=20000, description="Превентивный план.")] = "",
    response_plan: Annotated[
        str, Field(max_length=20000, description="План при наступлении.")
    ] = "",
    status: Annotated[RiskStatus, Field(description="Начальное состояние.")] = RiskStatus.OPEN,
    source: Annotated[
        RiskSource,
        Field(description="MANUAL либо AI_SUGGESTED для принятого человеком AI-предложения."),
    ] = RiskSource.MANUAL,
    owner: Annotated[str | None, Field(description="Логин участника проекта.")] = None,
    task_key: Annotated[str | None, Field(description="Задача этого проекта.")] = None,
    review_date: Annotated[
        date | None, Field(description="Следующий контроль, не дедлайн.")
    ] = None,
    actor: Actor = None,
) -> dict:
    """Создаёт подтверждённый риск через общий сервис и transactional outbox."""
    if confirmed_by_user is not True:
        raise ToolError(
            "Сначала получите подтверждение пользователя на регистрацию этих полей риска."
        )
    logger.info("🚀 MCP: регистрация риска проекта key=%s.", project_key)
    async with tool_context(context, require_write=True) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            links = await _links(tools, project_id, owner, task_key)
            data = ProjectRiskCreateSchema(
                title=title,
                description=description,
                probability=probability,
                impact=impact,
                response_strategy=response_strategy,
                mitigation_plan=mitigation_plan,
                response_plan=response_plan,
                status=status,
                source=source,
                review_date=review_date,
                **links,
            )
            risk = await tools.services.risks.create_risk(
                project_id=project_id, user_id=tools.principal.user_id, data=data
            )
        except (ApplicationError, ValidationError) as error:
            raise ToolError(
                getattr(error, "detail", "Проверьте обязательные поля и длину текста риска.")
            ) from error
    logger.info("✅ MCP: создан риск key=%s.", risk.key)
    return {"key": risk.key, "risk_level": risk.risk_level.value, "created": True}


@telos_tool(
    name="telos_update_project_risk",
    scope=SCOPE_TASKS_WRITE,
    title="Изменить риск",
    description="Изменяет только переданные поля после подтверждения человеком. Пустая строка owner/task_key/review_date удаляет соответствующее значение; источник неизменяем.",
)
async def update_project_risk(
    context: Context,
    project_key: ProjectKey,
    risk_key: RiskKey,
    confirmed_by_user: Confirmation,
    title: Annotated[str | None, Field(max_length=255, description="Новое название.")] = None,
    description: Annotated[
        str | None, Field(max_length=20000, description="Новое Markdown-описание.")
    ] = None,
    probability: Annotated[RiskRating | None, Field(description="Новая вероятность.")] = None,
    impact: Annotated[RiskRating | None, Field(description="Новое влияние.")] = None,
    status: Annotated[RiskStatus | None, Field(description="Новое состояние.")] = None,
    response_strategy: Annotated[
        RiskResponseStrategy | None, Field(description="Новая стратегия.")
    ] = None,
    mitigation_plan: Annotated[
        str | None, Field(max_length=20000, description="Превентивный план; пустая строка очищает.")
    ] = None,
    response_plan: Annotated[
        str | None,
        Field(max_length=20000, description="План при наступлении; пустая строка очищает."),
    ] = None,
    owner: Annotated[
        str | None, Field(description="Логин участника; пустая строка снимает назначение.")
    ] = None,
    task_key: Annotated[
        str | None, Field(description="Ключ задачи; пустая строка удаляет связь.")
    ] = None,
    review_date: Annotated[
        str | None, Field(description="Дата ГГГГ-ММ-ДД; пустая строка очищает.")
    ] = None,
    actor: Actor = None,
) -> dict:
    """Передаёт частичное изменение общему сервису рисков."""
    if confirmed_by_user is not True:
        raise ToolError("Сначала получите подтверждение пользователя на изменение риска.")
    logger.info("🚀 MCP: изменение риска key=%s.", risk_key)
    async with tool_context(context, require_write=True) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            payload = {
                name: value
                for name, value in dict(
                    title=title,
                    description=description,
                    probability=probability,
                    impact=impact,
                    status=status,
                    response_strategy=response_strategy,
                    mitigation_plan=mitigation_plan,
                    response_plan=response_plan,
                ).items()
                if value is not None
            }
            payload.update(await _links(tools, project_id, owner, task_key))
            if review_date is not None:
                payload["review_date"] = review_date.strip() or None
            data = ProjectRiskUpdateSchema.model_validate(payload)
            risk = await tools.services.risks.update_risk(
                project_id=project_id,
                risk_id=_risk_id(risk_key),
                user_id=tools.principal.user_id,
                data=data,
            )
        except (ApplicationError, ValidationError) as error:
            raise ToolError(
                getattr(error, "detail", "Передайте корректные изменения риска и дату ГГГГ-ММ-ДД.")
            ) from error
    logger.info("✅ MCP: изменён риск key=%s.", risk.key)
    return {"key": risk.key, "risk_level": risk.risk_level.value, "updated_fields": sorted(payload)}
