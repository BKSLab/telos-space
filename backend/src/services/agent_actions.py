"""Исполнение проектных инструментов с журналом, правами и общим commit."""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ValidationError

from src.agent.actions import ProjectAction
from src.agent.tools import AgentToolContext, describe_validation_error
from src.agent.wakeup import wake_agent_worker
from src.exceptions.access import AccessServiceError
from src.exceptions.agent_tools import (
    AgentToolAccessError,
    AgentToolArgumentsError,
    AgentToolConflictError,
    AgentToolDecidedError,
    AgentToolExpiredError,
    AgentToolOperationError,
    AgentToolsServiceError,
)
from src.exceptions.base import RepositoryError, ServiceError
from src.exceptions.project_stickers import ProjectStickerNotFoundError
from src.schemas.agent_tools import (
    AgentPendingActionSchema,
    AgentPendingProjectSchema,
    AgentToolRunSchema,
)
from src.services.agent_action_helpers import checked_document, checked_task
from src.services.agent_tool_scope import AgentProjectToolScope, AgentProjectToolScopeFactory
from src.services.tasks import user_display_name
from src.utils.request_actor import (
    RequestActor,
    current_request_actor,
    reset_request_actor,
    set_request_actor,
)

logger = logging.getLogger(__name__)

#: Сколько предложенное действие ждёт решения человека по умолчанию.
DEFAULT_CONFIRM_TTL = timedelta(hours=24)


class AgentActionsService:
    """Один контракт действий для внутренних инструментов и MCP-адаптера."""

    def __init__(
        self,
        *,
        scope: AgentProjectToolScopeFactory,
        actions: Sequence[ProjectAction],
        confirm_ttl: timedelta = DEFAULT_CONFIRM_TTL,
    ) -> None:
        self.scope = scope
        self.confirm_ttl = confirm_ttl
        self.actions = {action.name: action for action in actions}
        if len(self.actions) != len(actions):
            raise ValueError("Имена проектных действий должны быть уникальны.")

    async def execute(
        self, *, context: AgentToolContext, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Исполняет разрешённый инструмент или сохраняет запрос подтверждения.

        Args:
            context: Проект, участник и ключ исполнения от доверенного транспорта.
            name: Имя зарегистрированной операции.
            arguments: Аргументы, которые пройдут строгую схему операции.
        Returns:
            Данные чтения либо сохранённое действие с состоянием и результатом.
        Raises:
            AgentToolsServiceError: При отказе доступа, конфликте или ошибке хранения.
        """
        definition = self._definition(name)
        if definition.mutating and (not context.can_write or not context.allows(name)):
            raise AgentToolAccessError("Изменение недоступно в этом запросе.")
        try:
            parameters = definition.parameters.model_validate(arguments)
        except ValidationError as error:
            raise AgentToolArgumentsError(
                describe_validation_error(error, definition.parameters)
            ) from error
        payload = parameters.model_dump(mode="json", exclude_unset=True)
        request_id = self._request_id(context, name, payload) if definition.mutating else None
        logger.info(
            "🚀 Инструмент %s, проект=%s, участник=%s.", name, context.project_id, context.user_id
        )
        if definition.needs_project and context.project_id is None:
            raise AgentToolConflictError(
                "Действию нужен проект: укажи его ключ через run_project_action."
            )
        if not definition.needs_project:
            # Личное действие не живёт в проекте, даже если страница указывала на проект.
            context = replace(context, project_id=None)
        async with self._scope() as db:
            actor = await self._authorize(db, context, definition)
            if not definition.mutating:
                return await self._invoke(definition, db, actor, parameters)
            await self._require_execution(db, actor)
            await db.runs.lock_request(actor.project_id, actor.user_id, request_id)
            previous = await db.runs.get_request(actor.project_id, actor.user_id, request_id)
            if previous is not None:
                if previous.tool_name != name or previous.arguments != payload:
                    raise AgentToolConflictError(
                        "Ключ вызова уже использован с другими аргументами."
                    )
                if previous.status in {"completed", "pending", "rejected"}:
                    return self._result(previous)
            if definition.requires_confirmation:
                result = {
                    "preview": await self._preview(db, actor, parameters),
                    "message": "Требуется решение участника по сохранённым параметрам действия.",
                }
                state = "pending"
            else:
                result = await self._invoke(definition, db, actor, parameters)
                state = "completed"
            if previous is None:
                row = await db.runs.save(
                    dict(
                        id=uuid4(),
                        project_id=actor.project_id,
                        user_id=actor.user_id,
                        message_id=actor.message_id,
                        chat_turn_id=actor.chat_turn_id,
                        request_id=request_id,
                        tool_name=name,
                        title=definition.title,
                        arguments=payload,
                        status=state,
                        result=result,
                    )
                )
            else:
                row = await db.runs.update(previous.id, dict(status=state, result=result))
            response = self._result(row)
            await db.unit_of_work.commit()
            logger.info("✅ Инструмент %s: %s, действие=%s.", name, state, row.id)
            return response

    @staticmethod
    async def _invoke(definition, db, actor, parameters):
        """Рабочая очередь подписывает эффект проверенным инициатором операции."""
        current = current_request_actor()
        token = set_request_actor(RequestActor(
            user_id=actor.user_id, name=actor.display_name,
            via_api_key_label=(current.via_api_key_label if current and current.user_id == actor.user_id else None),
        ))
        try:
            return await definition.handler(db, actor, parameters)
        finally:
            reset_request_actor(token)

    async def decide(
        self,
        *,
        context: AgentToolContext,
        action_id: UUID,
        decision: str,
        conversation_id: int | None = None,
        repeat_is_error: bool = False,
        allow_external: bool = False,
    ) -> AgentToolRunSchema:
        """Решение инициатора по действию личного виджета Ариса или MCP-вызова.

        Args:
            context: Пользователь из аутентифицированного транспорта; проект
                для MCP, для виджета берётся из самого действия.
            action_id: ID подготовленного действия.
            decision: approve или reject; аргументы действия здесь не принимаются.
            conversation_id: Диалог виджета из HTTP-маршрута; None для MCP-действия.
            repeat_is_error: Повтор уже принятого решения — ошибка, а не прежний результат.
        Returns:
            Идемпотентный результат решения; ответ виджета продолжится из очереди.
        Raises:
            AgentToolsServiceError: При конфликте состояния, отказе доступа или ошибке записи.
        """
        self._check_decision(context, decision)
        async with self._scope() as db:
            # Сначала блокируем диалог и реплику, затем действие: такой же порядок
            # использует worker, поэтому решение не образует цикл блокировок.
            if conversation_id is not None:
                conversation = await db.conversations.get_owned(
                    conversation_id, context.user_id, channel=None, lock=True
                )
                if conversation is None or (conversation.channel != "web" and not allow_external) or (context.project_id is not None and conversation.project_id != context.project_id and not allow_external):
                    raise AgentToolAccessError("Диалог недоступен.")
                latest = await db.messages.get_page(conversation_id, before_id=None, limit=1)
                if not latest:
                    raise AgentToolAccessError("Диалог не содержит ответов.")
                message = await db.messages.get_for_update(latest[0].id)
                # Проект действия виджета берётся из самой строки, а не из маршрута.
                row = await db.runs.get_for_decision(action_id, context.user_id)
            else:
                message = None
                row = await db.runs.get_owned(action_id, context.project_id, context.user_id)
            if row is None or row.chat_turn_id is not None:
                raise AgentToolAccessError("Действие недоступно в этом контексте.")
            if (message is None) != (row.message_id is None):
                raise AgentToolAccessError("Действие недоступно в этом контексте.")
            if message is not None and row.message_id != message.id:
                raise AgentToolConflictError(
                    "Диалог уже продолжился. Запросите действие в текущем обсуждении."
                )
            # Решённое действие отвечает прежним результатом (или 409 по ссылке)
            # раньше проверки ответа: после решения worker уже продолжает ответ.
            if (
                row.status == "pending"
                and message is not None
                and message.status not in {"completed", "failed"}
            ):
                raise AgentToolConflictError(
                    "Дождитесь завершения ответа и обновите состояние действия."
                )
            response = await self._apply_decision(
                db,
                replace(context, project_id=row.project_id),
                row,
                decision,
                repeat_is_error=repeat_is_error,
            )
            resumed = False
            if message is not None:
                remaining = await db.runs.get_for_messages([message.id])
                if not any(item.status == "pending" for item in remaining):
                    await db.messages.resume(message.id)
                    resumed = True
            await db.unit_of_work.commit()
        if resumed:
            wake_agent_worker()
        return response

    async def decide_in_chat(
        self, *, context: AgentToolContext, action_id: UUID, decision: str
    ) -> AgentToolRunSchema:
        """Решение по действию, которое Арис предложил в общем чате проекта.

        Решить может автор просьбы или любой участник, у которого есть права на
        само действие (запись в проект; для owner_only — владелец). Действие
        выполняется от имени решающего и с его правами; параметры неизменны.

        Args:
            context: Проект маршрута и решающий участник.
            action_id: ID действия из карточки сообщения Ариса.
            decision: approve или reject.
        Returns:
            Сохранённый результат; карточка обновится у всей команды событием чата.
        Raises:
            AgentToolsServiceError: При конфликте состояния, отказе доступа или ошибке записи.
        """
        self._check_decision(context, decision)
        async with self._scope() as db:
            await db.access.ensure_project_access(
                project_id=context.project_id, user_id=context.user_id
            )
            probe = await db.runs.get_in_chat(action_id, context.project_id, lock=False)
            if probe is None:
                raise AgentToolAccessError("Действие недоступно в этом чате.")
            # Порядок блокировок как у воркера: ход, затем действие.
            turn = await db.turns.get(probe.chat_turn_id, lock=True)
            row = await db.runs.get_in_chat(action_id, context.project_id)
            if turn is None or row is None or turn.project_id != context.project_id:
                raise AgentToolAccessError("Действие недоступно в этом чате.")
            if turn.status not in {"completed", "failed"}:
                raise AgentToolConflictError("Дождитесь, пока Арис допишет ответ.")
            if decision == "reject" and context.user_id == row.user_id:
                # Автор просьбы может отказаться всегда, даже потеряв право записи.
                actor = await self._active_user(db, context)
                response = await self._apply_decision(db, actor, row, decision, authorize=False)
            else:
                response = await self._apply_decision(db, context, row, decision)
            await db.turns.emit(context.project_id, "message.updated", turn.reply_message_id)
            await db.unit_of_work.commit()
            return response

    async def _apply_decision(
        self,
        db: AgentProjectToolScope,
        context: AgentToolContext,
        row,
        decision: str,
        *,
        authorize: bool = True,
        repeat_is_error: bool = False,
    ) -> AgentToolRunSchema:
        """Выполняет или отклоняет сохранённое действие; повтор решения идемпотентен.

        ``repeat_is_error`` — решение по ссылке: повтор уже принятого решения
        отвечает 409, а не прежним результатом.
        """
        if row.status in {"completed", "rejected"}:
            if repeat_is_error:
                raise AgentToolDecidedError("Повторное решение по действию.")
            if (row.status == "completed") != (decision == "approve"):
                raise AgentToolConflictError("По действию уже принято другое решение.")
            return AgentToolRunSchema.model_validate(row)
        if row.status != "pending":
            raise AgentToolConflictError(
                "Дождитесь завершения ответа и обновите состояние действия."
            )
        # Отклонить можно и просроченное: это лишь закрывает предложение.
        if decision == "approve" and self._expired(row):
            raise AgentToolExpiredError("Срок подтверждения истёк.")
        definition = self._definition(row.tool_name)
        actor = await self._authorize(db, context, definition) if authorize else context
        if decision == "approve":
            try:
                parameters = definition.parameters.model_validate(row.arguments)
            except ValidationError:
                updated = await db.runs.update(row.id, {
                    "status": "rejected",
                    "result": {"participant_decision": "reject", "message": "Схема действия обновилась. Сформируйте предложение заново."},
                })
                return AgentToolRunSchema.model_validate(updated)
            if await self._preview(db, actor, parameters) != row.result.get("preview", {}):
                raise AgentToolConflictError(
                    "Объект изменился после предложения. Отклоните действие и запросите актуальное."
                )
            result = await self._invoke(definition, db, actor, parameters)
            state = "completed"
        else:
            result = {"message": "Участник отклонил действие. Изменения не применены."}
            state = "rejected"
        result = {
            **result,
            "participant_decision": decision,
            "decided_by": {"user_id": actor.user_id, "name": actor.display_name},
        }
        updated = await db.runs.update(row.id, {"status": state, "result": result})
        return AgentToolRunSchema.model_validate(updated)

    @staticmethod
    def _check_decision(context: AgentToolContext, decision: str) -> None:
        if not context.can_write or decision not in {"approve", "reject"}:
            raise AgentToolAccessError("Транспорт не разрешает решение по действию.")

    @staticmethod
    async def _active_user(db: AgentProjectToolScope, context: AgentToolContext):
        user = await db.users.get_by_id(context.user_id)
        if user is None or not user.is_active:
            raise AgentToolAccessError("Пользователь больше не активен.")
        return replace(context, username=user.username, display_name=user_display_name(user))

    async def get_pending(self, *, user_id: int, action_id: UUID) -> AgentPendingActionSchema:
        """Показывает предложенное действие его инициатору для страницы подтверждения.

        Args:
            user_id: Сотрудник из входа через локальная сессия.
            action_id: ID действия из ссылки ``?confirm=``.
        Returns:
            Что будет сделано, в каком проекте, состояние и срок подтверждения.
        Raises:
            AgentToolAccessError: Действия нет, оно чужое или проект больше недоступен.
        """
        async with self._scope() as db:
            row = await db.runs.get_for_user(action_id, user_id)
            # Страница подтверждения — только для проектных действий: личные
            # действия виджета и действия чата решаются там, где предложены.
            if row is None or row.project_id is None or row.chat_turn_id is not None:
                raise AgentToolAccessError("Действие не найдено.")
            await db.access.ensure_project_access(project_id=row.project_id, user_id=user_id)
            project = await db.projects.get_project(row.project_id)
            return self._pending_view(row, project)

    async def decide_pending(
        self, *, user_id: int, action_id: UUID, decision: str
    ) -> AgentPendingActionSchema:
        """Принимает решение по ссылке: проект и диалог берутся из самого действия.

        Параметры из запроса не принимаются, повтор и просрочка — ошибки.

        Args:
            user_id: Сотрудник из входа через локальная сессия.
            action_id: ID действия из ссылки ``?confirm=``.
            decision: approve или reject.
        Returns:
            Состояние действия после решения.
        Raises:
            AgentToolsServiceError: Чужое/неизвестное (404), решённое (409), просроченное (410).
        """
        async with self._scope() as db:
            row = await db.runs.get_for_user(action_id, user_id)
            if row is None or row.project_id is None or row.chat_turn_id is not None:
                raise AgentToolAccessError("Действие не найдено.")
            conversation_id = None
            if row.message_id is not None:
                message = await db.messages.get_by_id(row.message_id)
                if message is None:
                    raise AgentToolAccessError("Действие не найдено.")
                conversation_id = message.conversation_id
            project_id = row.project_id
        await self.decide(
            context=AgentToolContext(project_id=project_id, user_id=user_id, can_write=True),
            action_id=action_id,
            decision=decision,
            conversation_id=conversation_id,
            repeat_is_error=True,
            allow_external=True,
        )
        return await self.get_pending(user_id=user_id, action_id=action_id)

    def _expired(self, row) -> bool:
        return row.created_at + self.confirm_ttl <= datetime.now(UTC)

    def _pending_view(self, row, project) -> AgentPendingActionSchema:
        status = row.status
        if status == "pending" and self._expired(row):
            status = "expired"
        result = {} if row.status == "pending" else dict(row.result)
        return AgentPendingActionSchema(
            id=row.id,
            tool_name=row.tool_name,
            title=row.title,
            status=status,
            arguments=row.arguments,
            preview=row.result.get("preview") or {},
            result=result,
            project=AgentPendingProjectSchema(id=project.id, key=project.key, name=project.name),
            created_at=row.created_at,
            expires_at=row.created_at + self.confirm_ttl,
        )

    def _definition(self, name: str) -> ProjectAction:
        definition = self.actions.get(name)
        if definition is None:
            raise AgentToolConflictError("Инструмент отсутствует в реестре.")
        return definition

    @staticmethod
    def _request_id(context: AgentToolContext, name: str, payload: dict) -> UUID:
        if not context.can_write:
            raise AgentToolAccessError("Этому вызову не разрешено изменять проект.")
        value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if context.message_id is not None:
            return uuid5(NAMESPACE_URL, f"project-agent:{context.message_id}:{name}:{value}")
        if context.chat_turn_id is not None:
            return uuid5(NAMESPACE_URL, f"tim-chat:{context.chat_turn_id}:{name}:{value}")
        if context.request_id is None:
            raise AgentToolConflictError(
                "Для изменяющего MCP-вызова необходим стабильный request_id."
            )
        return context.request_id

    @staticmethod
    async def _authorize(
        db: AgentProjectToolScope, context: AgentToolContext, definition: ProjectAction
    ) -> AgentToolContext:
        if context.user_id is None:
            raise AgentToolAccessError("Инструменту необходим аутентифицированный участник.")
        if definition.needs_project:
            if context.project_id is None:
                raise AgentToolAccessError("Действию нужен проект.")
            grant = await db.access.ensure_project_access(
                project_id=context.project_id, user_id=context.user_id
            )
            if definition.owner_only and not grant.is_owner:
                raise AgentToolAccessError("Действие доступно только владельцу проекта.")
            if definition.mutating and not grant.can_write:
                raise AgentToolAccessError("Проект доступен только для чтения.")
        user = await db.users.get_by_id(context.user_id)
        if user is None or not user.is_active:
            raise AgentToolAccessError("Пользователь больше не активен.")
        return replace(context, username=user.username, display_name=user_display_name(user))

    @staticmethod
    async def _require_execution(db: AgentProjectToolScope, context: AgentToolContext) -> None:
        """Запись из ответа Ариса возможна только в ещё активной попытке этого ответа."""
        if context.message_id is not None:
            message = await db.messages.get_for_update(context.message_id)
            if (
                message is None
                or message.status != "processing"
                or message.run_id != context.run_id
            ):
                raise AgentToolConflictError("Попытка ответа больше не активна.")
            conversation = await db.conversations.get_owned(
                message.conversation_id, context.user_id, channel=None
            )
            if conversation is None:
                raise AgentToolAccessError("Попытка принадлежит другому пользователю.")
        elif context.chat_turn_id is not None:
            turn = await db.turns.get(context.chat_turn_id, lock=True)
            if (
                turn is None
                or turn.status != "processing"
                or turn.run_id != context.run_id
                or turn.requester_id != context.user_id
                or turn.project_id != context.project_id
            ):
                raise AgentToolConflictError("Попытка ответа больше не активна.")

    @staticmethod
    def _result(row) -> dict[str, Any]:
        return {"action": AgentToolRunSchema.model_validate(row).model_dump(mode="json")}

    @staticmethod
    async def _preview(
        db: AgentProjectToolScope, context: AgentToolContext, parameters: BaseModel
    ) -> dict:
        payload = parameters.model_dump(mode="json", exclude_unset=True)
        targets = {}
        if task_id := payload.get("task_id"):
            task = await checked_task(db, context, task_id)
            targets["task"] = {
                "id": task.id,
                "key": task.key,
                "title": task.title,
                "start_date": task.start_date.isoformat() if task.start_date else None,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "updated_at": task.updated_at.isoformat(),
            }
            if (
                "expected_updated_at" in payload
                and task.updated_at != parameters.expected_updated_at
            ):
                raise AgentToolConflictError("Задача изменилась; получите свежую версию.")
        if document_id := payload.get("document_id"):
            await checked_document(db, context, document_id)
            document = await db.documents.get_document(document_id)
            targets["document"] = {
                "id": document.id,
                "title": document.title,
                "updated_at": document.updated_at.isoformat(),
            }
        if comment_id := payload.get("comment_id"):
            from_comment = await db.comments.get_comment(comment_id)
            await checked_task(db, context, from_comment.task_id)
            targets["comment"] = from_comment.model_dump(mode="json")
        if sticker_id := payload.get("sticker_id"):
            try:
                sticker = await db.stickers.get_sticker(
                    project_id=context.project_id,
                    sticker_id=sticker_id,
                    board_id=payload.get("board_id"),
                )
            except ProjectStickerNotFoundError as error:
                raise AgentToolAccessError("Стикер недоступен.") from error
            if sticker.revision != payload["revision"]:
                raise AgentToolConflictError("Стикер изменился; получите свежую версию.")
            targets["sticker"] = {
                "id": sticker.id,
                "body": sticker.body,
                "revision": sticker.revision,
            }
        if member_id := payload.get("user_id"):
            items = await db.members.get_member_list(context.project_id)
            member = next((item for item in items if item.user.id == member_id), None)
            if member is None:
                raise AgentToolAccessError("Участник недоступен.")
            targets["member"] = member.model_dump(mode="json")
        if stage_id := payload.get("stage_id"):
            stage = await db.stages.get_stage_in_project(context.project_id, stage_id)
            targets["stage"] = {"id": stage.id, "name": stage.name}
        if node_id := payload.get("node_id"):
            nodes = (await db.wbs.get_structure(context.project_id)).nodes
            node = next((item for item in nodes if item.id == node_id), None)
            if node is None:
                raise AgentToolAccessError("Раздел ИСР недоступен.")
            targets["node"] = node.model_dump(mode="json")
        for field, label, fetch in (
            ("milestone_id", "milestone", db.milestones.list_milestones),
            ("dependency_id", "dependency", db.dependencies.list_dependencies),
        ):
            if entity_id := payload.get(field):
                entity = next(
                    (item for item in await fetch(context.project_id) if item.id == entity_id), None
                )
                if entity is None:
                    raise AgentToolAccessError("Объект недоступен в этом проекте.")
                targets[label] = entity.model_dump(mode="json")
        if risk_id := payload.get("risk_id"):
            risk = await db.risks.get_risk(
                project_id=context.project_id, user_id=context.user_id, risk_id=risk_id
            )
            targets["risk"] = {
                "id": risk.id,
                "title": risk.title,
                "updated_at": risk.updated_at.isoformat(),
            }
        if attachment_id := payload.get("attachment_id"):
            attachment = next(
                (
                    item
                    for item in await db.attachments.get_attachments(payload["task_id"])
                    if item.id == attachment_id
                ),
                None,
            )
            if attachment is None:
                raise AgentToolAccessError("Файл недоступен.")
            targets["attachment"] = attachment.model_dump(mode="json")
        if "changes" in payload and isinstance(payload["changes"], list):
            targets["tasks"] = []
            for change in parameters.changes:
                task = await checked_task(db, context, change.task_id)
                if task.updated_at != change.expected_updated_at:
                    raise AgentToolConflictError(
                        "Задача календаря изменилась; получите новый preview."
                    )
                targets["tasks"].append(
                    {
                        "id": task.id,
                        "key": task.key,
                        "title": task.title,
                        "start_date": str(task.start_date) if task.start_date else None,
                        "due_date": str(task.due_date) if task.due_date else None,
                    }
                )
            targets["task_count"] = len(parameters.changes)
        return targets

    @asynccontextmanager
    async def _scope(self) -> AsyncIterator[AgentProjectToolScope]:
        try:
            async with self.scope() as db:
                yield db
        except AccessServiceError as error:
            raise AgentToolAccessError(str(error)) from error
        except RepositoryError as error:
            raise AgentToolsServiceError(str(error)) from error
        except ServiceError as error:
            if isinstance(error, AgentToolsServiceError):
                raise
            raise AgentToolOperationError(error) from error
