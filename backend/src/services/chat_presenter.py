"""Пакетная сборка актуальных read models без SQL в сервисном слое."""

from collections import defaultdict
from urllib.parse import quote

from src.agent.aris import ARIS_NAME
from src.schemas.agent_tools import AgentToolRunSchema
from src.schemas.knowledge import decode_answer_sources
from src.schemas.project_chats import (
    ChatAgentView,
    ChatAttachmentView,
    ChatEntityView,
    ChatMessageView,
    ChatReplyView,
    ChatUser,
)
from src.services.chat_message_references import (
    hydrate_message_references,
    scoped_message_references,
)
from src.services.chat_scope import ChatScope
from src.services.context_evidence import (
    HIDDEN_CONTEXT_ANSWER,
    ContextEvidenceService,
    unverified_answer_actions,
)


def public_user(row) -> ChatUser:
    """Публичное имя и наличие фотографии без контактов и ключей хранилища."""
    return ChatUser(
        id=row["id"],
        username=row["username"],
        display_name=" ".join(filter(None, [row["last_name"], row["first_name"]]))
        or row["username"],
        has_avatar=bool(row.get("has_avatar")),
    )


def entity_view(row) -> ChatEntityView:
    """Строит локальную ссылку и подпись из текущих полей объекта."""
    kind, key, entity_id, extra = (
        row["entity_type"],
        quote(row["project_key"], safe=""),
        row["entity_id"],
        row["extra"],
    )
    base = f"/projects/{key}"
    href = {
        "TASK": f"{base}/tasks?task={entity_id}",
        "DOCUMENT": f"{base}/docs/{quote(row.get('document_ref') or '', safe='')}",
        "RISK": f"{base}/risks?risk={entity_id}",
        "MILESTONE": f"{base}/calendar?anchor={extra or ''}&milestone={entity_id}",
        "WBS_NODE": f"{base}/structure?wbs={entity_id}",
    }[kind]
    return ChatEntityView(
        entity_type=kind,
        entity_id=entity_id,
        document_ref=row.get("document_ref"),
        title=row["title"],
        status=row["status"],
        subtitle=f"{row['project_key']}-{extra}"
        if kind == "TASK"
        else extra
        if kind == "MILESTONE"
        else None,
        href=href,
    )


class ChatPresenter:
    def __init__(self, evidence: ContextEvidenceService | None = None) -> None:
        self.evidence = evidence

    async def reveal(self, messages, *, reader_id: int):
        """Вызывается после закрытия DB области истории, не из broadcast publisher."""
        guarded = [message for message in reversed(messages) if message._source_context is not None]
        if self.evidence is None:
            for message in guarded:
                message.content = HIDDEN_CONTEXT_ANSWER
                message.agent.requires_context_validation = False
            return messages
        verified = await self.evidence.present_many(
            [message._source_context[1] for message in guarded], user_id=reader_id
        )
        for message, (public, available) in zip(guarded, verified, strict=True):
            content, values, actions, references = message._source_context
            message.agent.requires_context_validation = False
            if not available:
                message.content = HIDDEN_CONTEXT_ANSWER
                continue
            message.content = content
            if message.agent is not None:
                for name, value in public.items():
                    setattr(message.agent, name, value)
                message.agent.actions, message.agent.message_references = actions, references
        return messages

    """Количество запросов зависит от страницы, а не от числа сообщений."""

    async def messages(self, db: ChatScope, rows: list) -> list[ChatMessageView]:
        """Гидратирует ссылки, авторов, ответы, вложения и реакции пакетом."""
        if not rows:
            return []
        ids = [row.id for row in rows]
        replies = await db.messages.get_many(
            rows[0].chat_id,
            list({row.reply_to_message_id for row in rows if row.reply_to_message_id}),
        )
        refs = await db.entities.get_many(ids)
        mentions = await db.mentions.get_many(ids)
        files = await db.attachments.get_many(ids)
        reactions = await db.reactions.get_many(ids)
        agent_ids = [row.id for row in rows if getattr(row, "author_kind", "user") == "agent"]
        turns = {
            row.reply_message_id: row
            for row in (await db.turns.get_by_replies(agent_ids) if agent_ids else [])
        }
        message_refs = [
            reference
            for turn in turns.values()
            for reference in scoped_message_references(
                getattr(turn, "message_references", None) or [],
                kind="project_chat",
                chat_id=rows[0].chat_id,
                project_id=rows[0].project_id,
            )
        ]
        source_ids = list({reference.message_id for reference in message_refs})
        citation_sources = (
            {
                row.id: row
                for row in await db.messages.get_many(
                    rows[0].chat_id, source_ids, populate_existing=True
                )
            }
            if source_ids
            else {}
        )
        project_key = await db.chats.get_project_key(rows[0].project_id) if message_refs else None
        citation_agent_ids = [
            row.id for row in citation_sources.values() if row.author_kind == "agent"
        ]
        citation_turns = (
            {
                turn.reply_message_id: turn
                for turn in await db.turns.get_by_replies(citation_agent_ids)
            }
            if citation_agent_ids
            else {}
        )
        unavailable_citations = frozenset(
            message_id
            for message_id in citation_agent_ids
            if message_id not in citation_turns
            or citation_turns[message_id].status != "completed"
            or citation_turns[message_id].sources
        )
        stale_reply_ids = [
            row.id
            for row in rows
            if row.revision == 1
            and row.id in turns
            and turns[row.id].status in {"completed", "failed"}
        ]
        if stale_reply_ids:
            # При READ COMMITTED ход мог завершиться после чтения страницы.
            # Обновляем и identity map: обычный SELECT оставит старую заглушку.
            fresh_replies = await db.messages.get_many(
                rows[0].chat_id, stale_reply_ids, populate_existing=True
            )
            fresh_by_id = {row.id: row for row in fresh_replies}
            rows = [fresh_by_id.get(row.id, row) for row in rows]
        actions_by_turn = defaultdict(list)
        for run in (
            await db.runs.get_for_turns([turn.id for turn in turns.values()]) if turns else []
        ):
            actions_by_turn[run.chat_turn_id].append(AgentToolRunSchema.model_validate(run))
        user_ids = (
            {
                row.author_user_id
                for row in [*rows, *replies, *citation_sources.values()]
                if row.author_user_id
            }
            | {row.user_id for row in mentions}
            | {turn.requester_id for turn in turns.values()}
        )
        users = {
            row["id"]: public_user(row) for row in await db.participants.get_users(list(user_ids))
        }
        ref_keys = list(
            {
                (
                    row.entity_type,
                    row.document_ref if row.entity_type == "DOCUMENT" else row.entity_id,
                )
                for row in refs
            }
        )
        entity_rows = (
            await db.entity_lookup.find(
                rows[0].project_id, refs=ref_keys, limit=max(len(ref_keys), 1)
            )
            if ref_keys
            else []
        )
        entities = {
            (
                row["entity_type"],
                row.get("document_ref") if row["entity_type"] == "DOCUMENT" else row["entity_id"],
            ): entity_view(row)
            for row in entity_rows
        }
        refs_by_message, mentions_by_message, files_by_message, reactions_by_message = (
            defaultdict(list),
            defaultdict(list),
            defaultdict(list),
            defaultdict(lambda: defaultdict(list)),
        )
        for row in refs:
            refs_by_message[row.message_id].append(
                entities.get(
                    (
                        row.entity_type,
                        row.document_ref if row.entity_type == "DOCUMENT" else row.entity_id,
                    )
                )
                or ChatEntityView(
                    entity_type=row.entity_type,
                    entity_id=row.entity_id,
                    document_ref=row.document_ref,
                    title="Объект больше недоступен",
                    available=False,
                )
            )
        for row in mentions:
            if row.user_id in users:
                mentions_by_message[row.message_id].append(users[row.user_id])
        for row in files:
            files_by_message[row.message_id].append(ChatAttachmentView.model_validate(row))
        for row in reactions:
            reactions_by_message[row.message_id][row.reaction].append(row.user_id)
        reply_map = {row.id: row for row in replies}
        result = []
        for row in rows:
            deleted = row.deleted_at is not None
            reply = reply_map.get(row.reply_to_message_id)
            result.append(
                ChatMessageView(
                    id=row.id,
                    chat_id=row.chat_id,
                    project_id=row.project_id,
                    seq=row.seq,
                    author_type="AGENT"
                    if getattr(row, "author_kind", "user") == "agent"
                    else "USER",
                    author=users.get(row.author_user_id),
                    client_message_id=row.client_message_id,
                    content="" if deleted else row.content,
                    revision=row.revision,
                    created_at=row.created_at,
                    edited_at=row.edited_at,
                    deleted_at=row.deleted_at,
                    reply=ChatReplyView(
                        id=reply.id,
                        seq=reply.seq,
                        author_type="AGENT"
                        if getattr(reply, "author_kind", "user") == "agent"
                        else "USER",
                        author=users.get(reply.author_user_id),
                        content="Сообщение удалено" if reply.deleted_at else reply.content[:240],
                        deleted=reply.deleted_at is not None,
                    )
                    if reply
                    else None,
                    mentions=[] if deleted else mentions_by_message[row.id],
                    entities=[] if deleted else refs_by_message[row.id],
                    attachments=[] if deleted else files_by_message[row.id],
                    reactions=[]
                    if deleted
                    else [
                        {"reaction": reaction, "user_ids": user_ids}
                        for reaction, user_ids in reactions_by_message[row.id].items()
                    ],
                    agent=self._agent(
                        turns.get(row.id),
                        users,
                        actions_by_turn,
                        hydrate_message_references(
                            getattr(turns.get(row.id), "message_references", None) or [],
                            kind="project_chat",
                            chat_id=row.chat_id,
                            project_id=row.project_id,
                            messages=citation_sources,
                            authors={key: user.display_name for key, user in users.items()},
                            project_key=project_key,
                            unavailable_message_ids=unavailable_citations,
                        )
                        if not deleted
                        else [],
                    ),
                )
            )
        for view in result:
            turn = turns.get(view.id)
            if turn is not None and turn.sources:
                view._source_context = (
                    view.content,
                    turn.sources,
                    actions_by_turn.get(turn.id, []) if turn.status == "completed" else [],
                    view.agent.message_references,
                )
                # Общий outbox не знает получателя. Он никогда не несёт приватный
                # source-derived body; history GET проверяет именно текущего reader.
                view.content = "Ответ Ариса готов. Проверяю доступ к источникам…"
                view.agent.requires_context_validation = True
                view.agent.sources, view.agent.external_sources, view.agent.assertion_references = (
                    [],
                    [],
                    [],
                )
                view.agent.actions, view.agent.message_references = [], []
            elif (
                turn is not None
                and turn.status == "completed"
                and any(
                    action.effect != "external_release"
                    for action in actions_by_turn.get(turn.id, [])
                )
            ):
                view.content, view.agent.message_references = HIDDEN_CONTEXT_ANSWER, []
            if view.reply and view.reply.author_type == "AGENT":
                view.reply.content = (
                    "Ответ Ариса: откройте оригинал для проверки актуальных источников."
                )
        return result

    @staticmethod
    def _agent(turn, users, actions_by_turn, message_references=None) -> ChatAgentView | None:
        """Карточка ответа Ариса; у людских сообщений — None."""
        if turn is None:
            return None
        return ChatAgentView(
            name=ARIS_NAME,
            status=turn.status,
            error=turn.error,
            progress=turn.progress if turn.status in {"queued", "processing"} else None,
            requester=users.get(turn.requester_id),
            request_message_id=turn.request_message_id,
            **decode_answer_sources(turn.sources or []),
            message_references=message_references or [],
            actions=unverified_answer_actions(actions_by_turn.get(turn.id, []), status=turn.status),
        )
