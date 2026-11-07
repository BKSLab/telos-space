"""READ-токен против всех классов доменных мутаций.

Route-graph тест доказывает, что зависимость подключена. Этот — что она
действительно останавливает запрос: токен на чтение получает 403 и не
доходит до доменных репозиториев и файлового I/O.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from main import app
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.file_comments import (
    get_file_comments_service,
    get_task_file_mutations_service,
)
from src.dependencies.scopes import get_project_files_service
from src.dependencies.services import (
    get_project_agent_service,
    get_users_service,
    get_wbs_suggestion_service,
)
from src.schemas.knowledge import KnowledgeAnswerSchema
from src.schemas.wbs_suggestion import WbsSuggestionSchema
from src.services.auth import AuthService
from src.services.file_comments import FileCommentsService
from src.services.file_metadata import TaskFileMutationsService
from src.services.project_agent import ProjectAgentService
from src.services.project_files import ProjectFilesService
from src.services.users import UsersService
from src.services.wbs_suggestion import WbsSuggestionService
from src.storage.task_attachments import TaskAttachmentStorage
from tests.api.bearer_doubles import (
    FakeTokensRepository,
    FakeUsersRepository,
    auth_header,
    install_bearer_auth,
    user_schema,
)

# По одному представителю каждого класса доменных мутаций.
MUTATION_SAMPLES = [
    ("PATCH", "/api/v1/users/me", {"json": {"first_name": "Новое"}}),
    ("POST", "/api/v1/users/me/password", {"json": {"current_password": "x", "new_password": "y"}}),
    ("DELETE", "/api/v1/users/me/avatar", {}),
    ("POST", "/api/v1/dashboard/analytics", {"json": {}}),
    ("POST", "/api/v1/projects", {"json": {"key": "NEW", "name": "Новый"}}),
    ("PATCH", "/api/v1/projects/1", {"json": {"name": "Другое"}}),
    ("DELETE", "/api/v1/projects/1", {}),
    ("POST", "/api/v1/projects/1/members", {"json": {"username": "someone"}}),
    ("DELETE", "/api/v1/projects/1/members/2", {}),
    ("POST", "/api/v1/projects/1/stages", {"json": {"name": "Стадия"}}),
    ("PATCH", "/api/v1/stages/1", {"json": {"name": "Другая"}}),
    ("DELETE", "/api/v1/stages/1", {}),
    ("POST", "/api/v1/projects/1/tasks", {"json": {"title": "Задача"}}),
    ("PATCH", "/api/v1/tasks/1", {"json": {"title": "Другая"}}),
    ("DELETE", "/api/v1/tasks/1", {}),
    ("PATCH", "/api/v1/tasks/1/move", {"json": {"stage_id": 1}}),
    ("POST", "/api/v1/tasks/1/baseline", {"json": {}}),
    ("POST", "/api/v1/tasks/1/comments", {"json": {"body_md": "Текст"}}),
    ("DELETE", "/api/v1/comments/1", {}),
    ("POST", "/api/v1/projects/1/wbs/nodes", {"json": {"title": "Раздел"}}),
    ("PATCH", "/api/v1/projects/1/wbs/nodes/1", {"json": {"title": "Другой"}}),
    ("DELETE", "/api/v1/projects/1/wbs/nodes/1", {}),
    ("POST", "/api/v1/projects/1/wbs/suggestion/apply", {"json": {"nodes": [], "placements": []}}),
    ("POST", "/api/v1/projects/1/documents", {"json": {"title": "Документ", "content_md": "x"}}),
    ("PATCH", "/api/v1/documents/1", {"json": {"title": "Другой"}}),
    ("DELETE", "/api/v1/documents/1", {}),
    ("POST", "/api/v1/document-links", {"json": {"document_id": 1, "task_id": 1}}),
    ("DELETE", "/api/v1/document-links/1", {}),
    (
        "POST",
        "/api/v1/projects/1/milestones",
        {"json": {"title": "Веха", "due_date": "2026-10-01"}},
    ),
    ("PATCH", "/api/v1/projects/1/milestones/1", {"json": {"title": "Другая"}}),
    ("DELETE", "/api/v1/projects/1/milestones/1", {}),
    (
        "POST",
        "/api/v1/projects/1/task-dependencies",
        {"json": {"predecessor_id": 1, "successor_id": 2}},
    ),
    ("DELETE", "/api/v1/projects/1/task-dependencies/1", {}),
    ("POST", "/api/v1/projects/1/board/stickers", {"json": {"body": "Текст"}}),
    ("PATCH", "/api/v1/projects/1/board/stickers/1", {"json": {"body": "Другой"}}),
    ("DELETE", "/api/v1/projects/1/board/stickers/1?revision=1", {}),
    ("POST", "/api/v1/projects/1/calendar/scenarios/apply", {"json": {"changes": []}}),
    ("POST", "/api/v1/projects/1/knowledge/reindex", {}),
    ("POST", "/api/v1/tasks/1/attachments", {"files": {"file": ("a.txt", b"x", "text/plain")}}),
    ("PATCH", "/api/v1/tasks/1/attachments/1", {"json": {"caption": "Новая подпись"}}),
    ("POST", "/api/v1/tasks/1/attachments/1/retry", {}),
    (
        "POST",
        "/api/v1/tasks/1/comments/with-files",
        {
            "data": {
                "request_id": "00000000-0000-4000-8000-000000000001",
                "body_md": "Текст",
                "captions": '["Подпись"]',
                "attachment_ids": "[1]",
            },
            "files": {"files": ("a.txt", b"x", "text/plain")},
        },
    ),
    ("POST", "/api/v1/projects/1/files", {"files": {"file": ("a.txt", b"x", "text/plain")}}),
    ("PATCH", "/api/v1/projects/1/files/1", {"json": {"caption": "Новая подпись"}}),
    ("POST", "/api/v1/projects/1/files/1/retry", {}),
    ("DELETE", "/api/v1/projects/1/files/1", {}),
    ("DELETE", "/api/v1/tasks/1/attachments/1", {}),
    (
        "POST",
        "/api/v1/tasks/1/documents/import",
        {"files": {"file": ("a.txt", b"x", "text/plain")}},
    ),
]

# Логически read-only POST: токену на чтение они доступны.
READ_ONLY_POST_SAMPLES = [
    ("/api/v1/projects/1/calendar/scenarios/preview", {"json": {"changes": []}}),
    ("/api/v1/projects/1/wbs/suggestion", {}),
    ("/api/v1/projects/1/knowledge/ask", {"json": {"question": "Что нового?", "history": []}}),
]


@pytest.fixture
def bearer_token(anonymous_app) -> Callable[[ApiTokenScope], AsyncMock]:
    """Ставит в граф аутентификацию по токену с выбранными правами.

    Обычные доменные сервисы подменяются. Файловые сценарии с короткой
    областью используют настоящий AuthService и имеют доступ только к
    его репозиториям в памяти: обход guard выявит обращение к отсутствующей
    доменной зависимости, вместо незаметного вызова настоящей БД.
    """

    def install(scope: ApiTokenScope) -> AsyncMock:
        install_bearer_auth(scope)
        auth = AuthService(
            users_repository=FakeUsersRepository(),
            tokens_repository=FakeTokensRepository(scope),
            invite_code="synthetic-invite",
        )

        @asynccontextmanager
        async def file_scope() -> AsyncIterator[SimpleNamespace]:
            yield SimpleNamespace(auth=auth)

        storage = AsyncMock(spec=TaskAttachmentStorage)
        task_files = TaskFileMutationsService(scope=file_scope, storage=storage)
        comments = FileCommentsService(scope=file_scope, storage=storage, knowledge_enabled=False)
        project_files = ProjectFilesService(
            scope=file_scope,
            storage=storage,
            max_file_size=10 * 1024 * 1024,
            knowledge_enabled=False,
        )

        users_service = AsyncMock(spec=UsersService)
        users_service.get_user.return_value = user_schema()
        users_service.update_profile.return_value = user_schema()

        suggestion_service = AsyncMock(spec=WbsSuggestionService)
        suggestion_service.suggest.return_value = WbsSuggestionSchema()

        agent_service = AsyncMock(spec=ProjectAgentService)
        agent_service.ask.return_value = KnowledgeAnswerSchema(answer="Ответ", sources=[])

        app.dependency_overrides.update(
            {
                get_users_service: lambda: users_service,
                get_wbs_suggestion_service: lambda: suggestion_service,
                get_project_agent_service: lambda: agent_service,
                get_task_file_mutations_service: lambda: task_files,
                get_file_comments_service: lambda: comments,
                get_project_files_service: lambda: project_files,
            }
        )
        return storage

    return install


@pytest.fixture
def anonymous_app():
    """Снимает автоподмену сессии: проверяется настоящая цепочка прав."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


async def test_read_token_cannot_mutate_any_class_of_mutation(
    api_client: AsyncClient,
    bearer_token,
) -> None:
    """Токен на чтение получает 403 на каждом классе доменных мутаций.

    Весь список проходится одним тестом: падение показывает сразу все
    открывшиеся маршруты, а не первый из них. Разбиение того же списка на
    отдельные тесты давало сорок с лишним идентификаторов при одном
    проверяемом правиле.
    """
    storage = bearer_token(ApiTokenScope.READ)
    offenders: list[str] = []

    for method, path, payload in MUTATION_SAMPLES:
        response = await api_client.request(method, path, headers=auth_header(), **payload)
        if response.status_code != 403:
            offenders.append(f"{method} {path} -> {response.status_code}")
        elif response.json() != {"detail": "Токен выдан только на чтение."}:
            offenders.append(f"{method} {path} -> чужая формулировка отказа: {response.json()}")

    assert not offenders, "Токену на чтение доступны мутации:\n  " + "\n  ".join(offenders)
    assert storage.mock_calls == [], "READ-токен дошёл до файлового хранилища."


async def test_read_token_passes_read_only_posts(
    api_client: AsyncClient,
    bearer_token,
) -> None:
    """Расчёт и предпросмотр остаются доступны токену на чтение.

    Проверяется именно отсутствие 403 от guard прав: дальше запрос может
    упасть по любой причине, и это уже не про права.
    """
    bearer_token(ApiTokenScope.READ)
    offenders: list[str] = []

    for path, payload in READ_ONLY_POST_SAMPLES:
        response = await api_client.post(path, headers=auth_header(), **payload)
        if response.status_code == 403:
            offenders.append(path)

    assert not offenders, "Закрыты для токена на чтение: " + ", ".join(offenders)


async def test_write_token_passes_the_scope_guard(
    api_client: AsyncClient,
    bearer_token,
) -> None:
    """Токен с правом записи проходит guard прав."""
    bearer_token(ApiTokenScope.WRITE)

    response = await api_client.patch(
        "/api/v1/users/me",
        headers=auth_header(),
        json={"first_name": "Новое"},
    )

    assert response.status_code != 403


async def test_read_token_still_reads(api_client: AsyncClient, bearer_token) -> None:
    """Токен на чтение не теряет доступ к чтению."""
    bearer_token(ApiTokenScope.READ)

    response = await api_client.get(
        "/api/v1/auth/me",
        headers=auth_header(),
    )

    assert response.status_code != 403
