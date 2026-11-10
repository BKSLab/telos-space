"""Ограниченная рабочая память агента без дополнительных вызовов модели."""

import json
import re
from copy import deepcopy

_HISTORY_OMISSION = "\n… [часть реплики опущена] …\n"
_HISTORY_FACT = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4}|"
    r"(?:task|project|document|risk|action|board|milestone):[\w.-]+|"
    r"[a-z][a-z0-9_]*-\d+|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|"
    r"срок\w*|дедлайн\w*|решили|договорились|решени\w*|"
    r"подтверждени\w*|ожида\w*|осталось|не выполнен\w*|"
    r"deadline|due_date|pending|completed|decision|confirmation(?:_id|_url)?)\b",
    re.IGNORECASE,
)


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def estimate_tokens(text: str) -> int:
    """Локальная консервативная оценка, не точный токенизатор провайдера.

    Считаем UTF-8 байты / 3, чтобы кириллица не получала английский бюджет
    «четыре символа на токен». Фактическое usage учитывается отдельно клиентом.
    """
    return (len(text.encode("utf-8")) + 2) // 3


def clip_text(text: str, tokens: int) -> str:
    """Обрезает по UTF-8 границе, не создавая повреждённого Unicode."""
    return text.encode("utf-8")[: max(0, tokens) * 3].decode("utf-8", errors="ignore")


def _exact_excerpts(text: str, tokens: int, *, marker: str = _HISTORY_OMISSION) -> str:
    """Точные фрагменты с обоих краёв и вокруг явных дат/ID/решений, без пересказа."""
    if estimate_tokens(text) <= tokens:
        return text
    if tokens <= estimate_tokens(marker):
        return clip_text(marker.strip(), tokens)
    edge_tokens = max(1, (tokens - estimate_tokens(marker)) // 6)
    head = clip_text(text, edge_tokens)
    tail = text.encode("utf-8")[-edge_tokens * 3 :].decode("utf-8", errors="ignore")
    spans = [(0, len(head)), (len(text) - len(tail), len(text))]

    def render(selected: list[tuple[int, int]]) -> str:
        merged: list[list[int]] = []
        for start, end in sorted(selected):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
        return marker.join(text[start:end] for start, end in merged)

    # Граница предложения даёт контекст значения, но не тащит весь отчёт одной строкой.
    matches = list(_HISTORY_FACT.finditer(text))
    for match in reversed(matches):
        if any(start <= match.start() and match.end() <= end for start, end in spans):
            continue
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 100)
        for separator in ("\n", ". ", "; "):
            boundary = text.rfind(separator, start, match.start())
            if boundary >= 0:
                start = max(start, boundary + len(separator))
            boundary = text.find(separator, match.end(), end)
            if boundary >= 0:
                end = min(end, boundary + (1 if separator == ". " else 0))
        candidate = [*spans, (start, end)]
        if estimate_tokens(render(candidate)) <= tokens:
            spans = candidate
    return render(spans)


def _history_receipts(value):
    if isinstance(value, dict):
        if (
            isinstance(value.get("status"), str)
            and value["status"] in {"pending", "completed", "rejected", "failed"}
            and ("tool_name" in value or "confirmation_id" in value)
        ):
            yield value
        for item in value.values():
            yield from _history_receipts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _history_receipts(item)


def compact_dialogue_history(
    history: list[dict], *, max_tokens: int = 2000, preserve_latest_user: bool = True
) -> list[dict]:
    """Готовит ограниченную копию истории только для контекста модели.

    Сохраняет хронологию, последнее поручение целиком и точные выдержки старых
    фактов вместо сгенерированной сводки. Все pending-квитанции и последний
    результат записи сохраняются вместе с метаданными. SQL-история не меняется.

    Args:
        history: Словари сообщений с role/content, от старых к новым.
        max_tokens: Лимит estimate_tokens(encode(result)), включая JSON.
        preserve_latest_user: False допустим для уже старых порций памяти;
            тогда их последний user тоже может быть сокращён.

    Returns:
        Независимая копия выбранных сообщений с маркерами сокращения текста.
        Число удалённых целиком сообщений — len(history) - len(result);
        вызывающий код передаёт его модели отдельно.

    Raises:
        ValueError: Последнее поручение или обязательные квитанции не помещаются.
    """
    if max_tokens < estimate_tokens(encode([])):
        raise ValueError("Бюджет истории должен вмещать хотя бы пустой список.")
    messages = deepcopy(history)
    if estimate_tokens(encode(messages)) <= max_tokens:
        return messages
    latest_user = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].get("role") == "user"
        ),
        None,
    )
    receipts = [(index, list(_history_receipts(item))) for index, item in enumerate(messages)]
    protected = {
        index for index, items in receipts if any(item["status"] == "pending" for item in items)
    }
    latest_outcome = next(
        (
            index
            for index, items in reversed(receipts)
            if any(item["status"] != "pending" for item in items)
        ),
        None,
    )
    if latest_outcome is not None:
        protected.add(latest_outcome)
    if preserve_latest_user and latest_user is not None:
        protected.add(latest_user)
    selected: dict[int, dict] = {}
    per_message = min(400, max(100, max_tokens // 3))

    def ordered() -> list[dict]:
        return [selected[index] for index in sorted(selected)]

    def shortened(message: dict, limit: int) -> dict | None:
        if estimate_tokens(encode(message)) <= limit:
            return message
        text = message.get("content")
        if not isinstance(text, str):
            return None
        result = {**message, "content": ""}
        text_tokens = limit - estimate_tokens(encode(result)) - 2
        if text_tokens < 30:
            return None
        while text_tokens >= 30:
            result["content"] = _exact_excerpts(text, text_tokens)
            overflow = estimate_tokens(encode(result)) - limit
            if overflow <= 0:
                return result
            text_tokens -= max(1, overflow)
        return None

    for index in sorted(protected):
        item = messages[index]
        if not (preserve_latest_user and index == latest_user):
            item = shortened(
                item, max(per_message, estimate_tokens(encode({**item, "content": ""})) + 40)
            )
        if item is None:
            raise ValueError("Обязательные квитанции не помещаются в бюджет истории.")
        selected[index] = item
    if estimate_tokens(encode(ordered())) > max_tokens:
        raise ValueError("Последнее поручение и квитанции превышают бюджет истории.")

    def priority(index: int) -> tuple[int, int]:
        message = messages[index]
        return (
            2 * bool(_HISTORY_FACT.search(str(message.get("content", ""))))
            + (message.get("role") == "user"),
            index,
        )

    for index in sorted(set(range(len(messages))) - protected, key=priority, reverse=True):
        available = max_tokens - estimate_tokens(encode(ordered())) - 1
        item = shortened(messages[index], min(per_message, available))
        if item is not None:
            selected[index] = item
            if estimate_tokens(encode(ordered())) > max_tokens:
                del selected[index]
    return ordered()


def result_key(request: dict, result: dict) -> str:
    """Порядок аргументов и явное offset=0 не создают вторую копию страницы."""
    action = result.get("action")
    if isinstance(action, dict) and action.get("id") is not None:
        return f"action:{action['id']}"
    name = request["name"]
    if name in {"telos_read_source", "telos_get_document"} and result.get("source_id"):
        page = result.get("document", result)
        offset = page.get("offset", request.get("arguments", {}).get("offset", 0))
        return encode([name, result["source_id"], offset])
    return json.dumps(request, ensure_ascii=False, sort_keys=True, default=str)


def compact_action(action: dict) -> dict:
    """Квитанция сохраняет факт записи; большие параметры остаются в БД и UI."""
    result = {
        key: deepcopy(value)
        for key, value in action.items()
        if key not in {"arguments", "preview", "result"}
    }
    result["result"] = compact_fields(action.get("result", {}))
    if action.get("status") == "pending" and "confirmation" not in result:
        result["confirmation"] = "Параметры и решение доступны участнику в карточке действия."
    return result


def compact_fields(value):
    """Убирает тела из квитанций, оставляя ID, результаты и явную отметку сокращения."""
    if isinstance(value, dict):
        result = {}
        omitted = []
        for key, item in value.items():
            if key in {
                "content_md",
                "body_md",
                "description_md",
                "extracted_text",
                "arguments",
                "preview",
                "checklist",
            }:
                omitted.append(key)
            elif key in {"id", "status", "participant_decision", "confirmation"} or key.endswith(
                ("_id", "_ids", "_url")
            ):
                result[key] = deepcopy(item)
            else:
                result[key] = compact_fields(item)
        if omitted:
            result["omitted_fields"] = omitted
        return result
    if isinstance(value, list):
        items = [compact_fields(item) for item in value[:20]]
        return items if len(value) <= 20 else {"items": items, "omitted_count": len(value) - 20}
    if isinstance(value, str) and len(value) > 800:
        return value[:800] + "… [сокращено; подробности доступны инструментом]"
    return value


class AgentContextBudget:
    def __init__(self, *, system_prompt: str, max_tokens: int, history_tokens: int):
        self.system_tokens = estimate_tokens(system_prompt) + 32
        self.max_tokens = max_tokens
        self.history_tokens = history_tokens

    def tokens(self, content: dict) -> int:
        return self.system_tokens + estimate_tokens(encode(content))

    def prepare(self, content: dict) -> None:
        """Ограничивает историю по объёму, а не только по числу сообщений."""
        history = content.get("dialog_history", [])
        kept = compact_dialogue_history(
            history,
            max_tokens=self.history_tokens,
            # Текущее поручение хранится отдельно и не сокращается при fit.
            preserve_latest_user=not bool(content.get("question")),
        )
        content["dialog_history"] = kept
        if len(kept) < len(history):
            content["dialog_history_omitted"] = (
                content.get("dialog_history_omitted", 0) + len(history) - len(kept)
            )
        memory = content.get("dialog_memory", "")
        if estimate_tokens(memory) > self.history_tokens // 2:
            content["dialog_memory"] = _exact_excerpts(
                memory, self.history_tokens // 2, marker="\n… [память сокращена] …\n"
            )
        actions = content.get("previous_actions", [])
        latest_outcome = next(
            (
                index
                for index in range(len(actions) - 1, -1, -1)
                if actions[index].get("status") != "pending"
            ),
            None,
        )
        kept_actions = [
            item
            for index, item in enumerate(actions)
            if index >= len(actions) - 20
            or item.get("status") == "pending"
            or index == latest_outcome
        ]
        content["previous_actions"] = [compact_action(item) for item in kept_actions]
        if len(kept_actions) < len(actions):
            content["previous_actions_omitted"] = (
                content.get("previous_actions_omitted", 0) + len(actions) - len(kept_actions)
            )
        content["context_policy"] = (
            "Описания нужны для отбора. Точные условия проверяй telos_read_source/telos_get_document. "
            "omitted/compacted означают сокращение, а не отсутствие данных. "
            "Читай только нужные объекты и страницы; используй next_offset."
        )

    @staticmethod
    def add_result(content: dict, *, request: dict, result: dict) -> None:
        """Повтор той же страницы заменяет её; разные страницы сохраняются отдельно."""
        result = deepcopy(result)
        if isinstance(result.get("action"), dict):
            result["action"] = compact_action(result["action"])
            # Тексты поручения уже есть в вопросе и сохранённом журнале действий.
            request = {
                "name": request["name"],
                "arguments": compact_fields(request.get("arguments", {})),
            }
        signature = result_key(request, result)
        entries = content["read_results"]
        entries[:] = [
            item for item in entries if result_key(item["request"], item["result"]) != signature
        ]
        entries.append({"request": request, "result": result})
        # После дочитывания не передаём ещё и стартовую копию того же объекта.
        seen = set()

        def collect(value):
            if isinstance(value, dict):
                source_id = value.get("source_id")
                if isinstance(source_id, str):
                    seen.add(source_id)
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(result)
        content["retrieval_context"] = [
            item
            for item in content.get("retrieval_context", [])
            if item.get("source_id") not in seen
        ]

    def fit(self, content: dict) -> str:
        """Применяется перед КАЖДЫМ раундом, включая накопленные ответы инструментов."""
        while self.tokens(content) > self.max_tokens:
            history = content.get("dialog_history", [])
            if history:
                try:
                    kept = compact_dialogue_history(
                        history,
                        max_tokens=max(80, estimate_tokens(encode(history)) // 2),
                        preserve_latest_user=not bool(content.get("question")),
                    )
                except ValueError:
                    kept = history
                if kept != history:
                    content["dialog_history"] = kept
                    if len(kept) < len(history):
                        content["dialog_history_omitted"] = (
                            content.get("dialog_history_omitted", 0) + len(history) - len(kept)
                        )
                    continue
            if self._shrink_lists(content):
                continue
            # Отработанные большие чтения уступают место последнему результату.
            compacted = False
            for entry in content.get("read_results", [])[:-1]:
                result = entry["result"]
                if (
                    "action" not in result
                    and not result.get("context_compacted")
                    and estimate_tokens(encode(result)) > 160
                ):
                    entry["result"] = self._receipt(result)
                    compacted = True
                    break
            if compacted:
                continue
            previous_actions = content.get("previous_actions", [])
            receipts = previous_actions + [
                entry["result"]["action"]
                for entry in content.get("read_results", [])
                if "action" in entry["result"]
            ]
            latest_outcome = next(
                (action for action in reversed(receipts) if action.get("status") != "pending"), None
            )
            for action in receipts:
                if (
                    action is not latest_outcome
                    and action.get("status") != "pending"
                    and not action.get("result_compacted")
                    and estimate_tokens(encode(action.get("result", {}))) > 160
                ):
                    references = []

                    def collect_references(value, collected=references):
                        if isinstance(value, dict):
                            if isinstance(value.get("source_id"), str):
                                collected.append(
                                    {
                                        key: value[key]
                                        for key in ("source_id", "source_handle")
                                        if key in value
                                    }
                                )
                            for item in value.values():
                                collect_references(item, collected)
                        elif isinstance(value, list):
                            for item in value:
                                collect_references(item, collected)

                    collect_references(action["result"])
                    action["result"] = {
                        "sources": references[:10],
                        "notice": "Полный результат сохранён в карточке действия.",
                    }
                    action["result_compacted"] = True
                    compacted = True
                    break
            if compacted:
                continue
            disposable_action = next(
                (
                    action
                    for action in previous_actions
                    if action is not latest_outcome and action.get("status") != "pending"
                ),
                None,
            )
            if disposable_action is not None:
                previous_actions.remove(disposable_action)
                content["previous_actions_omitted"] = content.get("previous_actions_omitted", 0) + 1
                continue
            if self._shrink_text(content):
                continue
            # Схемы аргументов не обрезаем: агент может запросить их заново по имени.
            tools = content.get("available_tools", [])
            if any("description" in item for item in tools):
                for item in tools:
                    item.pop("description", None)
                content["tool_descriptions_omitted"] = True
                continue
            reads = content.get("read_results", [])
            disposable = next((entry for entry in reads if "action" not in entry["result"]), None)
            if disposable is not None:
                reads.remove(disposable)
                content["read_results_omitted"] = content.get("read_results_omitted", 0) + 1
                continue
            raise ValueError(
                "Обязательный контекст превышает бюджет. Уточните вопрос или сократите список действий."
            )
        return encode(content)

    @staticmethod
    def _receipt(result: dict) -> dict:
        keys = {
            "source_id",
            "source_handle",
            "title",
            "entity_type",
            "offset",
            "next_offset",
            "total",
            "total_chars",
            "error",
        }
        return {
            **{key: value for key, value in result.items() if key in keys},
            "context_compacted": True,
            "notice": "Текст прежнего чтения убран из контекста; при необходимости повтори нужную страницу.",
        }

    @staticmethod
    def _shrink_lists(content: dict) -> bool:
        candidates = []

        def visit(value, path=()):
            if not isinstance(value, dict):
                return
            for key, item in list(value.items()):
                if key in {
                    "available_tools",
                    "tools",
                    "properties",
                    "arguments",
                    "previous_actions",
                    "dialog_history",
                }:
                    continue
                if isinstance(item, list):
                    if key != "read_results" and len(item) > 1:
                        candidates.append((len(encode(item)), value, key, path))
                    for child in item:
                        visit(child, (*path, key))
                elif isinstance(item, dict) and key != "action":
                    visit(item, (*path, key))

        visit(content)
        if not candidates:
            return False
        _, parent, key, _ = max(candidates, key=lambda item: item[0])
        items = parent[key]
        keep = max(1, len(items) // 2)
        parent[key] = items[:keep]
        parent[f"{key}_omitted"] = parent.get(f"{key}_omitted", 0) + len(items) - keep
        if key == "items" and "next_offset" in parent:
            # Старт страницы добавляется источником, не вычисляется по общей длине.
            parent["next_offset"] = parent.get("offset", 0) + keep
        return True

    @staticmethod
    def _shrink_text(content: dict) -> bool:
        candidates = []

        def visit(value):
            if isinstance(value, dict):
                for key, item in list(value.items()):
                    if key in {
                        "question",
                        "current_participant",
                        "available_tools",
                        "tools",
                        "arguments",
                        "action",
                        "previous_actions",
                        "dialog_history",
                    }:
                        continue
                    if isinstance(item, str) and len(item) > 400:
                        candidates.append((len(item.encode("utf-8")), value, key))
                    elif isinstance(item, (list, dict)):
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(content)
        if not candidates:
            return False
        _, parent, key = max(candidates, key=lambda item: item[0])
        text = parent[key]
        if key == "dialog_memory":
            parent[key] = _exact_excerpts(
                text, max(80, estimate_tokens(text) // 2), marker="\n… [память сокращена] …\n"
            )
        else:
            parent[key] = text[: max(200, len(text) // 2)]
        parent[f"{key}_truncated"] = True
        if key in {"text", "content_md"} and "offset" in parent:
            parent["next_offset"] = parent["offset"] + len(parent[key])
        return True
