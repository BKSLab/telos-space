"""Сокращение текста переписки с сохранением сообщений и их структурных связей."""



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
                "id", "chat_id", "kind", "author_id", "author_name", "created_at", "revision",
                "content_hash", "message_reference_handle", "reply_to_message_id", "entity_references",
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
        "notice": "Текст прочитанных сообщений сокращён. Их ссылки сохранены; детали перечитай инструментом истории.",
    }
