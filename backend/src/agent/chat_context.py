"""Сокращение текста переписки с сохранением сообщений и их структурных связей."""

from copy import deepcopy


def is_chat_message(value) -> bool:
    """DTO истории определяется серверным видом, идентичностью и содержимым."""
    return (isinstance(value, dict) and value.get("kind") in {"project_chat", "conversation"}
            and isinstance(value.get("id"), int) and isinstance(value.get("chat_id"), int)
            and "content" in value and "attachments" in value)


def shrink_chat_text(value) -> bool:
    """Сокращает один самый большой фрагмент; ID, автор, даты и вложения остаются."""
    candidates = []

    def visit(item, path=()):
        if isinstance(item, dict):
            if is_chat_message(item):
                text = item["content"]
                if isinstance(text, str) and text and "current_message" not in path:
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
    keep = len(previous) // 2 if len(previous) > 120 else 0
    parent[key] = previous[:keep]
    if key == "content":
        parent.setdefault("content_total_chars", len(previous))
        parent["text_truncated"] = True
        parent["next_content_offset"] = parent.get("content_offset", 0) + keep
    else:
        parent["excerpt_truncated"] = True
        parent["next_offset"] = keep
    return True


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
            messages.append({key: item[key] for key in (
                "id", "chat_id", "kind", "message_reference_handle", "entity_references",
            ) if key in item})
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
        **{key: child for key, child in value.items() if key in {
            "has_more", "next_before_message_id", "next_after_message_id", "applied_filters",
        }},
        "context_compacted": True,
        "notice": "Прежнее чтение сокращено до ID и ссылок. Цитаты сохранены сервером; текст, автора и дату перечитай инструментом истории.",
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
            result = {key: deepcopy(child) for key, child in item.items() if key not in {
                "project_id", "project_key", "content_hash",
            }}
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
    result["notice"] = "Повторные поля проекта и hash убраны из чтения; сервер хранит полный снимок цитаты по message_reference_handle."
    return result


def shrink_chat_page(value: dict) -> bool:
    """Сокращает model-only страницу, сохраняя непрерывное дочитывание в обе стороны."""
    candidates = []

    def visit(item):
        if isinstance(item, dict):
            rows = item.get("items")
            if isinstance(rows, list) and len(rows) > 1 and all(is_chat_message(row) for row in rows) and isinstance(item.get("applied_filters"), dict):
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
    page["notice"] = "Страница сокращена для контекста модели. Продолжи по next_* с теми же applied_filters; непрочитанные сообщения не пропущены."
    return True
