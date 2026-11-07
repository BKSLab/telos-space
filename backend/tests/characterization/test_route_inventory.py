"""Инвентарь публичных маршрутов: URL и HTTP-методы заморожены.

Рефакторинг не имеет права добавить, удалить или переименовать маршрут.
Этот тест — единственная защита от такого изменения «по дороге»: он падает
и на исчезнувшем адресе, и на внезапно появившемся.

Классификация non-GET маршрутов используется дальше на этапе 2, где к
каждой доменной мутации подключается проверка scope записи.
"""

from fastapi.routing import APIRoute

from main import app

FROZEN_ROUTES: set[tuple[str, str]] = {
    ("GET", "/api/v1/projects/{project_id}/files/search"),
    ("GET", "/api/v1/tasks/{task_id}/files/search"),
    ("GET", "/api/v1/projects/{project_id}/files/{file_id}/preview"),
    ("GET", "/api/v1/tasks/{task_id}/attachments/{attachment_id}/preview"),
    ("GET", "/api/v1/projects/{project_id}/chat/attachments/{file_id}/preview"),
    ("GET", "/api/v1/tim/conversations/{conversation_id}/files/{file_id}/content"),
    ("GET", "/api/v1/tim/conversations/{conversation_id}/files/{file_id}/preview"),
    ("GET", "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/files/{file_id}/content"),
    ("GET", "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/files/{file_id}/preview"),
    ("PATCH", "/api/v1/projects/{project_id}/files/{file_id}"),
    ("PATCH", "/api/v1/tasks/{task_id}/attachments/{attachment_id}"),
    ("POST", "/api/v1/tasks/{task_id}/attachments/{attachment_id}/retry"),
    ("POST", "/api/v1/tasks/{task_id}/comments/with-files"),
    ("GET", "/api/v1/projects/{project_id}/files"),
    ("POST", "/api/v1/projects/{project_id}/files"),
    ("GET", "/api/v1/projects/{project_id}/files/{file_id}"),
    ("GET", "/api/v1/projects/{project_id}/files/{file_id}/content"),
    ("POST", "/api/v1/projects/{project_id}/files/{file_id}/retry"),
    ("DELETE", "/api/v1/projects/{project_id}/files/{file_id}"),
    ("DELETE", "/api/v1/boards/{board_id}"),
    ("DELETE", "/api/v1/boards/{board_id}/shapes/{shape_id}"),
    ("DELETE", "/api/v1/boards/{board_id}/stickers/{sticker_id}"),
    ("DELETE", "/api/v1/comments/{comment_id}"),
    ("DELETE", "/api/v1/document-links/{link_id}"),
    ("DELETE", "/api/v1/documents/{document_id}"),
    ("DELETE", "/api/v1/me/calendar/ics-token"),
    ("DELETE", "/api/v1/projects/{project_id}"),
    (
        "DELETE",
        "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/files/{file_id}",
    ),
    ("DELETE", "/api/v1/projects/{project_id}/board/stickers/{sticker_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/boards/{board_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/boards/{board_id}/shapes/{shape_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/boards/{board_id}/stickers/{sticker_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/chat/attachments/{file_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/chat/messages/{message_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/icon-image"),
    ("DELETE", "/api/v1/projects/{project_id}/members/{user_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/milestones/{milestone_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/risks/{risk_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/task-dependencies/{dependency_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/wbs/nodes/{node_id}"),
    ("DELETE", "/api/v1/projects/{project_id}/wbs/tasks/{task_id}/assignment"),
    ("DELETE", "/api/v1/stages/{stage_id}"),
    ("DELETE", "/api/v1/tasks/{task_id}"),
    ("DELETE", "/api/v1/tasks/{task_id}/attachments/{attachment_id}"),
    ("DELETE", "/api/v1/tim/conversations/{conversation_id}/files/{file_id}"),
    ("DELETE", "/api/v1/users/me/avatar"),
    ("DELETE", "/api/v1/users/me/tokens/{token_id}"),
    ("GET", "/api/v1/agent/pending/{action_id}"),
    ("GET", "/api/v1/agent/requests/{request_id}"),
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/boards"),
    ("GET", "/api/v1/boards/{board_id}/content"),
    ("GET", "/api/v1/boards/{board_id}/editor-metadata"),
    ("GET", "/api/v1/calendar/holidays"),
    ("GET", "/api/v1/dashboard"),
    ("GET", "/api/v1/dashboard/analytics"),
    ("GET", "/api/v1/documents/{document_id}"),
    ("GET", "/api/v1/documents/{document_id}/links"),
    ("GET", "/api/v1/ics/{token}.ics"),
    ("GET", "/api/v1/me/calendar"),
    ("GET", "/api/v1/me/calendar/ics-token"),
    ("GET", "/api/v1/me/calendar/unscheduled"),
    ("GET", "/api/v1/my/task-attention"),
    ("GET", "/api/v1/my/tasks"),
    ("GET", "/api/v1/portfolio/calendar"),
    ("GET", "/api/v1/projects"),
    ("GET", "/api/v1/projects/defaults"),
    ("GET", "/api/v1/projects/{project_id}"),
    ("GET", "/api/v1/projects/{project_id}/agent/conversations"),
    ("GET", "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/messages"),
    ("GET", "/api/v1/projects/{project_id}/board/stickers"),
    ("GET", "/api/v1/projects/{project_id}/boards"),
    ("GET", "/api/v1/projects/{project_id}/boards/{board_id}/content"),
    ("GET", "/api/v1/projects/{project_id}/boards/{board_id}/editor-metadata"),
    ("GET", "/api/v1/projects/{project_id}/boards/{board_id}/stickers"),
    ("GET", "/api/v1/projects/{project_id}/calendar"),
    ("GET", "/api/v1/projects/{project_id}/calendar/unscheduled"),
    ("GET", "/api/v1/projects/{project_id}/calendar/workload"),
    ("GET", "/api/v1/projects/{project_id}/chat"),
    ("GET", "/api/v1/projects/{project_id}/chat/attachments/{file_id}/content"),
    ("GET", "/api/v1/projects/{project_id}/chat/entities"),
    ("GET", "/api/v1/projects/{project_id}/chat/events"),
    ("GET", "/api/v1/projects/{project_id}/chat/messages"),
    ("GET", "/api/v1/projects/{project_id}/chat/search"),
    ("GET", "/api/v1/projects/{project_id}/deadline-history"),
    ("GET", "/api/v1/projects/{project_id}/documents"),
    ("GET", "/api/v1/projects/{project_id}/icon-image"),
    ("GET", "/api/v1/projects/{project_id}/knowledge/status"),
    ("GET", "/api/v1/projects/{project_id}/members"),
    ("GET", "/api/v1/projects/{project_id}/members/{user_id}/avatar"),
    ("GET", "/api/v1/projects/{project_id}/milestones"),
    ("GET", "/api/v1/projects/{project_id}/risks"),
    ("GET", "/api/v1/projects/{project_id}/risks/summary"),
    ("GET", "/api/v1/projects/{project_id}/risks/task-counts"),
    ("GET", "/api/v1/projects/{project_id}/risks/{risk_id}"),
    ("GET", "/api/v1/projects/{project_id}/stages"),
    ("GET", "/api/v1/projects/{project_id}/stats"),
    ("GET", "/api/v1/projects/{project_id}/task-dependencies"),
    ("GET", "/api/v1/projects/{project_id}/task-reports"),
    ("GET", "/api/v1/projects/{project_id}/tasks"),
    ("GET", "/api/v1/projects/{project_id}/wbs"),
    ("GET", "/api/v1/tasks/by-key/{task_key}"),
    ("GET", "/api/v1/tasks/{task_id}"),
    ("GET", "/api/v1/tasks/{task_id}/activity"),
    ("GET", "/api/v1/tasks/{task_id}/attachments"),
    ("GET", "/api/v1/tasks/{task_id}/attachments/{attachment_id}/content"),
    ("GET", "/api/v1/tasks/{task_id}/attention"),
    ("GET", "/api/v1/tasks/{task_id}/comments"),
    ("GET", "/api/v1/tasks/{task_id}/links"),
    ("GET", "/api/v1/tasks/{task_id}/move-to/preview"),
    ("GET", "/api/v1/tasks/{task_id}/report"),
    ("GET", "/api/v1/tasks/{task_id}/report/history"),
    ("GET", "/api/v1/tim/conversations"),
    ("GET", "/api/v1/tim/conversations/{conversation_id}/messages"),
    ("GET", "/api/v1/users"),
    ("GET", "/api/v1/users/me/api-keys"),
    ("GET", "/api/v1/users/me/api-keys/scopes"),
    ("GET", "/api/v1/users/me/avatar"),
    ("GET", "/api/v1/users/me/tokens"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("PATCH", "/api/v1/boards/{board_id}"),
    ("PATCH", "/api/v1/boards/{board_id}/shapes/{shape_id}"),
    ("PATCH", "/api/v1/boards/{board_id}/stickers/{sticker_id}"),
    ("PATCH", "/api/v1/boards/{board_id}/stickers/{sticker_id}/position"),
    ("PATCH", "/api/v1/comments/{comment_id}"),
    ("PATCH", "/api/v1/documents/{document_id}"),
    ("PATCH", "/api/v1/projects/{project_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/board/stickers/{sticker_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/board/stickers/{sticker_id}/position"),
    ("PATCH", "/api/v1/projects/{project_id}/boards/{board_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/boards/{board_id}/shapes/{shape_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/boards/{board_id}/stickers/{sticker_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/boards/{board_id}/stickers/{sticker_id}/position"),
    ("PATCH", "/api/v1/projects/{project_id}/chat/messages/{message_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/milestones/{milestone_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/risks/{risk_id}"),
    ("PATCH", "/api/v1/projects/{project_id}/wbs/nodes/{node_id}"),
    ("PATCH", "/api/v1/stages/{stage_id}"),
    ("PATCH", "/api/v1/tasks/{task_id}"),
    ("PATCH", "/api/v1/tasks/{task_id}/move"),
    ("PATCH", "/api/v1/tasks/{task_id}/status"),
    ("PATCH", "/api/v1/users/me"),
    ("PATCH", "/api/v1/users/me/api-keys/{key_id}"),
    ("POST", "/api/v1/agent/ask"),
    ("POST", "/api/v1/agent/pending/{action_id}/confirm"),
    ("POST", "/api/v1/agent/pending/{action_id}/reject"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/boards"),
    ("POST", "/api/v1/boards/{board_id}/batch"),
    ("POST", "/api/v1/boards/{board_id}/create-project"),
    ("POST", "/api/v1/boards/{board_id}/shapes"),
    ("POST", "/api/v1/boards/{board_id}/shapes/{shape_id}/to-sticker"),
    ("POST", "/api/v1/boards/{board_id}/stickers"),
    ("POST", "/api/v1/dashboard/analytics"),
    ("POST", "/api/v1/document-links"),
    ("POST", "/api/v1/me/calendar/ics-token"),
    ("POST", "/api/v1/projects"),
    ("POST", "/api/v1/projects/{project_id}/agent/conversations"),
    (
        "POST",
        "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/actions/{action_id}/decision",
    ),
    ("POST", "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/files"),
    ("POST", "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/messages"),
    (
        "POST",
        "/api/v1/projects/{project_id}/agent/conversations/{conversation_id}/messages/{message_id}/retry",
    ),
    ("POST", "/api/v1/projects/{project_id}/baseline"),
    ("POST", "/api/v1/projects/{project_id}/board/stickers"),
    ("POST", "/api/v1/projects/{project_id}/boards"),
    ("POST", "/api/v1/projects/{project_id}/boards/{board_id}/batch"),
    ("POST", "/api/v1/projects/{project_id}/boards/{board_id}/shapes"),
    ("POST", "/api/v1/projects/{project_id}/boards/{board_id}/shapes/{shape_id}/to-sticker"),
    ("POST", "/api/v1/projects/{project_id}/boards/{board_id}/stickers"),
    ("POST", "/api/v1/projects/{project_id}/calendar/scenarios/apply"),
    ("POST", "/api/v1/projects/{project_id}/calendar/scenarios/preview"),
    ("POST", "/api/v1/projects/{project_id}/chat/attachments"),
    ("POST", "/api/v1/projects/{project_id}/chat/entities/resolve"),
    ("POST", "/api/v1/projects/{project_id}/chat/messages"),
    ("POST", "/api/v1/projects/{project_id}/documents"),
    ("POST", "/api/v1/projects/{project_id}/icon-image"),
    ("POST", "/api/v1/projects/{project_id}/knowledge/ask"),
    ("POST", "/api/v1/projects/{project_id}/knowledge/reindex"),
    ("POST", "/api/v1/projects/{project_id}/members"),
    ("POST", "/api/v1/projects/{project_id}/milestones"),
    ("POST", "/api/v1/projects/{project_id}/risks"),
    ("POST", "/api/v1/projects/{project_id}/risks/field-suggestion"),
    ("POST", "/api/v1/projects/{project_id}/risks/suggestions"),
    ("POST", "/api/v1/projects/{project_id}/stages"),
    ("POST", "/api/v1/projects/{project_id}/task-dependencies"),
    ("POST", "/api/v1/projects/{project_id}/tasks"),
    ("POST", "/api/v1/projects/{project_id}/tasks/checklist-suggestion"),
    ("POST", "/api/v1/projects/{project_id}/tasks/rephrase"),
    ("POST", "/api/v1/projects/{project_id}/tim/actions/{action_id}/decision"),
    ("POST", "/api/v1/projects/{project_id}/wbs/nodes"),
    ("POST", "/api/v1/projects/{project_id}/wbs/nodes/{node_id}/move"),
    ("POST", "/api/v1/projects/{project_id}/wbs/suggestion"),
    ("POST", "/api/v1/projects/{project_id}/wbs/suggestion/apply"),
    ("POST", "/api/v1/projects/{project_id}/wbs/tasks/{task_id}/assign"),
    ("POST", "/api/v1/projects/{project_id}/wbs/tasks/{task_id}/placement"),
    ("POST", "/api/v1/tasks"),
    ("POST", "/api/v1/tasks/{task_id}/attachments"),
    ("POST", "/api/v1/tasks/{task_id}/baseline"),
    ("POST", "/api/v1/tasks/{task_id}/comments"),
    ("POST", "/api/v1/tasks/{task_id}/documents/import"),
    ("POST", "/api/v1/tasks/{task_id}/move-to"),
    ("POST", "/api/v1/tasks/{task_id}/report"),
    ("POST", "/api/v1/tasks/{task_id}/report/from-comment"),
    ("POST", "/api/v1/tasks/{task_id}/view"),
    ("POST", "/api/v1/tim/conversations"),
    ("POST", "/api/v1/tim/conversations/{conversation_id}/actions/{action_id}/decision"),
    ("POST", "/api/v1/tim/conversations/{conversation_id}/files"),
    ("POST", "/api/v1/tim/conversations/{conversation_id}/messages"),
    ("POST", "/api/v1/tim/conversations/{conversation_id}/messages/{message_id}/retry"),
    ("POST", "/api/v1/users/me/api-keys"),
    ("POST", "/api/v1/users/me/avatar"),
    ("POST", "/api/v1/users/me/password"),
    ("POST", "/api/v1/users/me/tokens"),
    ("PUT", "/api/v1/boards/{board_id}/editor-metadata"),
    ("PUT", "/api/v1/projects/{project_id}/boards/{board_id}/editor-metadata"),
    ("PUT", "/api/v1/projects/{project_id}/chat/messages/{message_id}/reaction"),
    ("PUT", "/api/v1/projects/{project_id}/chat/read"),
}

# Маршруты входа: доступны без аутентификации по определению.
PUBLIC_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/register"),
}

# Управление токенами закрыто для самих токенов: только сессия интерфейса.
SESSION_ONLY_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/v1/users/me/api-keys"),
    ("PATCH", "/api/v1/users/me/api-keys/{key_id}"),
    ("POST", "/api/v1/me/calendar/ics-token"),
    ("DELETE", "/api/v1/me/calendar/ics-token"),
    ("POST", "/api/v1/tasks/{task_id}/view"),
    ("POST", "/api/v1/agent/pending/{action_id}/confirm"),
    ("POST", "/api/v1/agent/pending/{action_id}/reject"),
    ("POST", "/api/v1/users/me/tokens"),
    ("DELETE", "/api/v1/users/me/tokens/{token_id}"),
}

# POST, которые ничего не меняют: расчёт, предпросмотр и поиск.
READ_ONLY_POST_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/v1/agent/ask"),
    ("POST", "/api/v1/projects/{project_id}/chat/entities/resolve"),
    ("POST", "/api/v1/projects/{project_id}/risks/field-suggestion"),
    ("POST", "/api/v1/projects/{project_id}/tasks/checklist-suggestion"),
    ("POST", "/api/v1/projects/{project_id}/risks/suggestions"),
    ("POST", "/api/v1/projects/{project_id}/calendar/scenarios/preview"),
    ("POST", "/api/v1/projects/{project_id}/tasks/rephrase"),
    ("POST", "/api/v1/projects/{project_id}/wbs/suggestion"),
    ("POST", "/api/v1/projects/{project_id}/knowledge/ask"),
}


def _actual_routes() -> set[tuple[str, str]]:
    """Собирает фактический инвентарь маршрутов приложения."""
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            routes.add((method, route.path))
    return routes


def test_route_inventory_is_frozen() -> None:
    """Набор публичных маршрутов не меняется рефакторингом."""
    actual = _actual_routes()

    disappeared = FROZEN_ROUTES - actual
    appeared = actual - FROZEN_ROUTES

    assert not disappeared, f"Маршруты исчезли: {sorted(disappeared)}"
    assert not appeared, f"Появились незадекларированные маршруты: {sorted(appeared)}"


def test_non_get_routes_are_fully_classified() -> None:
    """Каждый изменяющий маршрут отнесён ровно к одному классу доступа.

    Неклассифицированный non-GET маршрут — это маршрут, для которого никто
    не решил, нужен ли ему scope записи. Такой пробел должен ломать сборку.
    """
    non_get = {(method, path) for method, path in FROZEN_ROUTES if method != "GET"}
    explicit = PUBLIC_ROUTES | SESSION_ONLY_ROUTES | READ_ONLY_POST_ROUTES

    overlap = [
        pair
        for pair in explicit
        if sum(
            pair in group for group in (PUBLIC_ROUTES, SESSION_ONLY_ROUTES, READ_ONLY_POST_ROUTES)
        )
        > 1
    ]
    assert not overlap, f"Маршрут попал в несколько классов: {sorted(overlap)}"
    assert explicit <= non_get, (
        f"Классифицирован несуществующий маршрут: {sorted(explicit - non_get)}"
    )

    mutations = non_get - explicit
    assert len(non_get) == 134, f"Изменилось число изменяющих маршрутов: {len(non_get)}"
    assert len(mutations) == 113, f"Изменилось число доменных мутаций: {len(mutations)}"


def test_every_route_has_unique_method_and_path() -> None:
    """Один и тот же метод и путь не объявлены дважды."""
    seen: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            seen.append((method, route.path))

    duplicates = {pair for pair in seen if seen.count(pair) > 1}
    assert not duplicates, f"Дублирующиеся маршруты: {sorted(duplicates)}"
