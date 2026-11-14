"""Чистая гидратация цитат из свежих сообщений уже проверенной переписки."""

from collections.abc import Iterable, Mapping
from hashlib import sha256

from src.agent.aris import ARIS_NAME
from src.schemas.chat_references import ChatMessageReferenceSchema


def scoped_message_references(
    values: Iterable, *, kind: str, chat_id: int, project_id: int | None
) -> list[ChatMessageReferenceSchema]:
    """Чужие пространства и повреждённые сохранённые ссылки не выходят в UI."""
    result = []
    for value in values:
        try:
            reference = ChatMessageReferenceSchema.model_validate(value)
        except ValueError:
            continue
        if (
            reference.kind == kind
            and reference.chat_id == chat_id
            and reference.project_id == project_id
        ):
            result.append(reference)
    return result


def hydrate_message_references(
    values: Iterable,
    *,
    kind: str,
    chat_id: int,
    project_id: int | None,
    messages: Mapping[int, object],
    authors: Mapping[int, str],
    owner_user_id: int | None = None,
    project_key: str | None = None,
    unavailable_message_ids: frozenset[int] = frozenset(),
) -> list[ChatMessageReferenceSchema]:
    """Показывает текст лишь при совпадении версии, hash и точного диапазона."""
    result = []
    for reference in scoped_message_references(
        values, kind=kind, chat_id=chat_id, project_id=project_id
    ):
        source = messages.get(reference.message_id)
        state = "unavailable"
        changes = {"project_key": project_key}
        if source is not None:
            source_scope = (
                getattr(source, "chat_id", None)
                if kind == "project_chat"
                else getattr(source, "conversation_id", None)
            )
            if source_scope != chat_id:
                continue
            content = source.content or ""
            if getattr(source, "deleted_at", None) is not None:
                state = "deleted"
            elif source.id in unavailable_message_ids or (
                kind == "conversation" and source.status != "completed"
            ):
                state = "unavailable"
            elif (
                (getattr(source, "revision", None) or 1) == reference.revision
                and sha256(content.encode("utf-8")).hexdigest() == reference.content_hash
                and 0 <= reference.start < reference.end <= len(content)
                and content[reference.start : reference.end] == reference.quote
            ):
                state = "available"
            else:
                state = "edited"
            is_agent = (
                getattr(source, "author_kind", None) == "agent"
                or getattr(source, "role", None) == "assistant"
            )
            author_id = None if is_agent else getattr(source, "author_user_id", owner_user_id)
            changes.update(
                author_id=author_id,
                author_name=ARIS_NAME if is_agent else authors.get(author_id, "Участник"),
                created_at=source.created_at,
            )
        result.append(
            reference.model_copy(
                update={
                    **changes,
                    "state": state,
                    "quote": reference.quote if state == "available" else "",
                }
            )
        )
    return result
