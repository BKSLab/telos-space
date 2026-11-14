"""Сокращение текста переписки с сохранением сообщений и их структурных связей."""

from copy import deepcopy
from hashlib import sha256

from src.agent.aris import strip_address


def is_chat_message(value) -> bool:
    """DTO истории определяется серверным видом, идентичностью и содержимым."""
    return (
        isinstance(value, dict)
        and value.get("kind") in {"project_chat", "conversation"}
        and isinstance(value.get("id"), int)
        and isinstance(value.get("chat_id"), int)
        and "content" in value
        and "attachments" in value
    )


def deduplicate_chat_question(value: dict) -> bool:
    """Полный SHA связывает current prefix с question; текущий вопрос остаётся целиком."""
    current = (value.get("chat_context") or {}).get("current_message")
    question = value.get("question")
    if not (
        is_chat_message(current)
        and isinstance(question, str)
        and current["content"]
        and current.get("content_offset", 0) == 0
    ):
        return False
    prefix = strip_address(current["content"])
    candidates = [question]
    if prefix and question.startswith(prefix):
        leader = current["content"][: current["content"].find(prefix)]
        candidates.append(leader + question)
    if not any(
        len(candidate) == current.get("content_total_chars")
        and sha256(candidate.encode("utf-8")).hexdigest() == current.get("content_hash")
        for candidate in candidates
    ):
        return False
    current["content"] = ""
    current["content_in_question"] = True
    current["text_truncated"] = True
    current["next_content_offset"] = 0
    return True


def shrink_chat_text(value, *, min_content_chars: int = 0) -> bool:
    """Сокращает один самый большой фрагмент; ID, автор, даты и вложения остаются."""
    candidates = []

    def visit(item, path=()):
        if isinstance(item, dict):
            if is_chat_message(item):
                text = item["content"]
                if (
                    isinstance(text, str)
                    and len(text) > min_content_chars
                    and "current_message" not in path
                ):
                    priority = 1 if "quoted_message" in path or path[-1:] == ("message",) else 0
                    candidates.append((priority, -len(text.encode("utf-8")), item, "content"))
                for attachment in item.get("attachments", []):
                    for key in ("excerpt", "text"):
                        text = attachment.get(key)
                        if isinstance(text, str) and text:
                            candidates.append((0, -len(text.encode("utf-8")), attachment, key))
                return
            for key, child in item.items():
                if key not in {"reference", "message_references"}:
                    visit(child, (*path, key))
        elif isinstance(item, list):
            for child in item:
                visit(child, path)

    visit(value)
    if not candidates:
        return False
    _, _, parent, key = min(candidates, key=lambda row: row[:2])
    previous = parent[key]
    floor = min_content_chars if key == "content" else 0
    keep = max(floor, len(previous) // 2 if len(previous) > 120 else 0)
    parent[key] = previous[:keep]
    if key == "content":
        parent.setdefault("content_total_chars", len(previous))
        parent["text_truncated"] = True
        parent["next_content_offset"] = parent.get("content_offset", 0) + keep
    else:
        parent["excerpt_truncated"] = True
        parent["next_offset"] = keep
    return True


def _compact_attachment_metadata(message: dict) -> bool:
    """Вложенность уже задаёт сообщение-владельца; совпавшее время наследуется от него."""
    changed = False
    for file in message["attachments"]:
        keys = {"content_hash"}
        if file.get("message_id") == message["id"]:
            keys.add("message_id")
        if file.get("created_at") == message.get("created_at"):
            keys.add("created_at")
        if file.get("truncated") is False:
            keys.add("truncated")
        if file.get("original_chars") == file.get("total_chars"):
            keys.add("original_chars")
        for key in ("detail", "excerpt"):
            if file.get(key) is None:
                keys.add(key)
        for key in keys.intersection(file):
            file.pop(key)
            changed = True
    return changed


def compact_chat_context(value: dict) -> bool:
    """Сокращает повторные metadata model-only окна, не удаляя ни одной реплики."""
    chat = value.get("chat_context")
    if not isinstance(chat, dict) or chat.get("metadata_compacted"):
        return False
    changed = False

    def remove(item, key):
        nonlocal changed
        if key in item:
            item.pop(key)
            changed = True

    def visit(item, *, current=False):
        nonlocal changed
        if is_chat_message(item):
            for key in ("project_id", "project_key"):
                remove(item, key)
            if not current:
                remove(item, "content_hash")
                if item.get("reply_to_message_id") is None:
                    remove(item, "reply_to_message_id")
                full_length = item.get("content_total_chars", len(item["content"]))
                if item.get("citation_range") == {"start": 0, "end": full_length}:
                    remove(item, "citation_range")
                if not item.get("content_offset"):
                    remove(item, "content_offset")
                if item.get("next_content_offset") is None:
                    remove(item, "next_content_offset")
                if not item.get("text_truncated") and item.get("content_total_chars") == len(
                    item["content"]
                ):
                    remove(item, "content_total_chars")
                for reference in item.get("entity_references", []):
                    for key, child in list(reference.items()):
                        if child is None:
                            remove(reference, key)
            changed = _compact_attachment_metadata(item) or changed
        elif isinstance(item, dict):
            for key, child in item.items():
                visit(child, current=key == "current_message")
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(chat)
    if changed:
        chat["metadata_compacted"] = True
    return changed


def chat_text_tokens(value) -> int:
    """Оценка только сокращаемого текста, без стоимости обязательного каркаса."""
    texts = []

    def visit(item, path=()):
        if is_chat_message(item):
            if "current_message" not in path:
                texts.append(item["content"])
            for attachment in item.get("attachments", []):
                texts.append(attachment.get("excerpt") or attachment.get("text") or "")
        elif isinstance(item, dict):
            for key, child in item.items():
                visit(child, (*path, key))
        elif isinstance(item, list):
            for child in item:
                visit(child, path)

    visit(value)
    return sum((len(text.encode("utf-8")) + 2) // 3 for text in texts)


def chat_receipt(value: dict) -> dict | None:
    """Квитанция старого чтения сохраняет ссылки и возможность продолжить страницу."""
    messages = []

    def visit(item):
        if is_chat_message(item):
            messages.append(
                {
                    key: item[key]
                    for key in (
                        "id",
                        "chat_id",
                        "kind",
                        "message_reference_handle",
                        "entity_references",
                    )
                    if key in item and (key != "entity_references" or item[key])
                }
            )
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    if not messages:
        return None
    return {
        "messages": messages,
        **{
            key: child
            for key, child in value.items()
            if key
            in {
                "has_more",
                "next_before_message_id",
                "next_after_message_id",
            }
            and child is not None
        },
        **(
            {
                "applied_filters": {
                    key: child
                    for key, child in value["applied_filters"].items()
                    if child is not None
                }
            }
            if isinstance(value.get("applied_filters"), dict)
            else {}
        ),
        "context_compacted": True,
        "notice": "Чтение сокращено до ID/handles. Текст, автора и дату перечитай; снимки цитат сохранены сервером.",
    }


def compact_chat_result(value: dict) -> dict | None:
    """Убирает повторные служебные поля tool DTO; исходный chat_context не меняет."""
    if value.get("chat_metadata_compacted") or value.get("context_compacted"):
        return None
    found = False

    def project(item):
        nonlocal found
        if is_chat_message(item):
            found = True
            result = {
                key: deepcopy(child)
                for key, child in item.items()
                if key
                not in {
                    "project_id",
                    "project_key",
                    "content_hash",
                }
            }
            for key in ("reply_to_message_id", "next_content_offset"):
                if result.get(key) is None:
                    result.pop(key, None)
            if not result.get("entity_references"):
                result.pop("entity_references", None)
            if not result.get("content_offset"):
                result.pop("content_offset", None)
            if not result.get("text_truncated"):
                result.pop("text_truncated", None)
                if result.get("content_total_chars") == len(result["content"]):
                    result.pop("content_total_chars", None)
            if result.get("citation_range") == {
                "start": 0,
                "end": len(result["content"]),
            } and not item.get("text_truncated"):
                result.pop("citation_range", None)
            _compact_attachment_metadata(result)
            return result
        if isinstance(item, dict):
            return {key: project(child) for key, child in item.items()}
        if isinstance(item, list):
            return [project(child) for child in item]
        return item

    result = project(value)
    if not found:
        return None
    result["chat_metadata_compacted"] = True
    result["notice"] = (
        "Повторные поля проекта и hash убраны из чтения; сервер хранит полный снимок цитаты по message_reference_handle."
    )
    return result


def shrink_chat_page(value: dict) -> bool:
    """Сокращает model-only страницу, сохраняя непрерывное дочитывание в обе стороны."""
    candidates = []

    def visit(item):
        if isinstance(item, dict):
            rows = item.get("items")
            if (
                isinstance(rows, list)
                and len(rows) > 1
                and all(is_chat_message(row) for row in rows)
                and isinstance(item.get("applied_filters"), dict)
            ):
                candidates.append((len(rows), item))
            else:
                for child in item.values():
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    if not candidates:
        return False
    _, page = max(candidates, key=lambda pair: pair[0])
    rows = page["items"]
    keep = max(1, len(rows) // 2)
    after = page["applied_filters"].get("after_message_id") is not None
    page["items"] = rows[:keep] if after else rows[-keep:]
    page["items_omitted"] = page.get("items_omitted", 0) + len(rows) - keep
    page["has_more"] = True
    page["next_after_message_id"] = page["items"][-1]["id"] if after else None
    page["next_before_message_id"] = None if after else page["items"][0]["id"]
    page["notice"] = (
        "Страница сокращена для контекста модели. Продолжи по next_* с теми же applied_filters; непрочитанные сообщения не пропущены."
    )
    return True
