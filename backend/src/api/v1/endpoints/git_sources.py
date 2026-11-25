"""Git HTTP использует тот же сервис и ACL, что MCP/Арис; credentials только из owner UI."""

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from src.dependencies.git_sources import GitSourcePrincipalDep, GitSourceServiceDep
from src.exceptions.auth import AuthServiceError
from src.exceptions.git_sources import GitSourceRateLimitedError, GitSourceServiceError
from src.schemas.git_sources import (
    GitComparison,
    GitConnection,
    GitConnectionCreate,
    GitConnectionPage,
    GitConnectionUpdate,
    GitEventAccepted,
    GitFileRead,
    GitGraph,
    GitHubProfile,
    GitHubProfileUpdate,
    GitImpact,
    GitProjectMap,
    GitQueued,
    GitRepositoryPage,
    GitSearchResult,
    GitSnapshotPage,
    GitSymbolPage,
    GitTaskCandidates,
    GitTaskLink,
    GitTaskLinkCreate,
    GitTaskLinks,
    GitTreePage,
    GitWebhookSecretUpdate,
)


class GitRoute(APIRoute):
    """Валидация никогда не отражает secret input обратно в HTTP detail."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError as error:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {
                                "loc": [item["loc"][0]]
                                if item["loc"]
                                and item["loc"][0] in {"body", "path", "query", "header"}
                                else [],
                                "type": item["type"],
                                "msg": "Некорректные параметры Git-запроса.",
                            }
                            for item in error.errors()
                        ]
                    },
                )
            except GitSourceRateLimitedError as error:
                raise HTTPException(
                    status_code=error.status_code,
                    detail=error.detail,
                    headers={"Retry-After": str(error.retry_after)},
                ) from None
            except (GitSourceServiceError, AuthServiceError) as error:
                raise HTTPException(status_code=error.status_code, detail=error.detail) from None

        return handler


router = APIRouter(prefix="/projects/{project_id}/git", tags=["git-sources"], route_class=GitRoute)
ProjectId = Annotated[int, Path(gt=0, description="ID доступного проекта.")]
ConnectionId = Annotated[int, Path(gt=0, description="ID подключения внутри этого проекта.")]
TaskId = Annotated[int, Path(gt=0, description="ID задачи этого проекта.")]
LinkId = Annotated[int, Path(gt=0, description="ID связи этой задачи.")]
RESPONSES = {
    401: {"description": "Требуется действующая локальная аутентификация."},
    403: {"description": "Недостаточно текущих scopes/прав владельца."},
    404: {"description": "Объект отсутствует или недоступен."},
    409: {"description": "Изменились версия, generation или связь."},
    422: {"description": "Недопустимые параметры без отражения secret input."},
    500: {"description": "Ошибка обработки Git."},
    503: {"description": "Источник/credentials не готовы либо исчерпан ограниченный бюджет."},
}


@router.get(
    "/connections",
    operation_id="listGitConnections",
    response_model=GitConnectionPage,
    responses=RESPONSES,
    summary="Получить подключения Git проекта",
    description="Метаданные 0..N независимых sources без credentials; нужны projects:read и knowledge:read.",
    response_description="Страница подключений и их фактический охват.",
)
async def list_git_connections(
    project_id: ProjectId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    verified: bool = False,
) -> GitConnectionPage:
    """Возвращает безопасные метаданные доступных привязок проекта."""
    return await service.list_connections(
        project_id=project_id, principal=principal, limit=limit, offset=offset, **({"verified": True} if verified else {})
    )


@router.post(
    "/connections",
    operation_id="createGitConnection",
    response_model=GitConnection,
    status_code=201,
    responses=RESPONSES,
    summary="Подключить GitHub repository",
    description="Текущий владелец явно выбирает ref/область; private sharing требует согласия, токен берётся из профиля владельца.",
    response_description="Проверенная привязка; индекс готовится в durable очереди.",
)
async def create_git_connection(
    project_id: ProjectId,
    data: GitConnectionCreate,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> GitConnection:
    """Проверяет upstream identity и сохраняет ограниченную project binding."""
    return await service.create_connection(project_id=project_id, principal=principal, data=data)


@router.get(
    "/connections/{connection_id}",
    operation_id="getGitConnection",
    response_model=GitConnection,
    responses=RESPONSES,
    summary="Получить Git-подключение",
    description="Read ACL одинаков во всех каналах; secret/ciphertext отсутствуют.",
    response_description="Состояние выбранного подключения.",
)
async def get_git_connection(
    project_id: ProjectId,
    connection_id: ConnectionId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> GitConnection:
    """Читает metadata своего project binding."""
    return await service.get_connection(
        project_id=project_id, connection_id=connection_id, principal=principal
    )


@router.patch(
    "/connections/{connection_id}",
    operation_id="updateGitConnection",
    response_model=GitConnection,
    responses=RESPONSES,
    summary="Изменить Git-подключение",
    description="Owner-only PATCH с expected_revision; смена ref/отзыв доступа инвалидируют старое поколение.",
    response_description="Новая версия настройки и фактическое состояние очереди.",
)
async def update_git_connection(
    project_id: ProjectId,
    connection_id: ConnectionId,
    data: GitConnectionUpdate,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> GitConnection:
    """Меняет лишь выбранное подключение без соседних sources/tasks."""
    return await service.update_connection(
        project_id=project_id, connection_id=connection_id, principal=principal, data=data
    )


@router.delete(
    "/connections/{connection_id}",
    operation_id="deleteGitConnection",
    status_code=204,
    responses=RESPONSES,
    summary="Отключить Git-подключение",
    description="Владелец отзывает credential binding; история файлов/связей остаётся, выдача закрывается немедленно.",
    response_description="Подключение отключено.",
)
async def delete_git_connection(
    project_id: ProjectId,
    connection_id: ConnectionId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> Response:
    """Отключает источник без удаления проекта и других подключений."""
    await service.delete_connection(
        project_id=project_id, connection_id=connection_id, principal=principal
    )
    return Response(status_code=204)


@router.put(
    "/connections/{connection_id}/webhook-secret",
    operation_id="setGitWebhookSecret",
    response_model=GitConnection,
    responses=RESPONSES,
    summary="Заменить секрет Git webhook",
    description="Только UI session текущего владельца; SecretStr входы шифруются отдельным ключом и не возвращаются.",
    response_description="Метаданные с новой версией настройки webhook; токен чтения GitHub не меняется.",
)
async def set_git_webhook_secret(
    project_id: ProjectId,
    connection_id: ConnectionId,
    data: GitWebhookSecretUpdate,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> GitConnection:
    """Ротирует или отзывает webhook HMAC secret."""
    return await service.set_webhook_secret(
        project_id=project_id, connection_id=connection_id, principal=principal, data=data
    )


@router.post(
    "/connections/{connection_id}/sync",
    operation_id="syncGitConnection",
    response_model=GitConnection,
    status_code=202,
    responses=RESPONSES,
    summary="Поставить сверку Git в очередь",
    description="Owner-only bounded reconciliation; queued не означает завершённую индексацию.",
    response_description="Metadata поставленного на сверку подключения.",
)
async def sync_git_connection(
    project_id: ProjectId,
    connection_id: ConnectionId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> GitConnection:
    """Ставит независимую сверку выбранного ref."""
    return await service.sync_connection(
        project_id=project_id, connection_id=connection_id, principal=principal
    )


@router.get(
    "/connections/{connection_id}/tree",
    operation_id="getGitTree",
    response_model=GitTreePage,
    responses=RESPONSES,
    summary="Прочитать дерево Git snapshot",
    description="Все объекты recorded repository/SHA после текущего ACL и live Contents proof; статусы текста, метаданных и ошибок.",
    response_description="Постраничный полный каталог с явным truncated/stale и состоянием обработки объектов.",
)
async def get_git_tree(
    project_id: ProjectId,
    connection_id: ConnectionId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
    sha: Annotated[str | None, Query(max_length=40)] = None,
    prefix: Annotated[str, Query(max_length=4096)] = "",
    children: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> GitTreePage:
    """Читает ограниченное дерево указанной версии своего репозитория."""
    return await service.get_tree(
        project_id=project_id,
        connection_id=connection_id,
        principal=principal,
        sha=sha,
        prefix=prefix,
        **({"children": True} if children else {}),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/connections/{connection_id}/file",
    operation_id="readGitFile",
    response_model=GitFileRead,
    responses=RESPONSES,
    summary="Прочитать точный Git-файл",
    description="Unicode page immutable commit/path/blob; без исполнения, symlink/LFS/download URL и обхода текущего доступа.",
    response_description="Подтверждённый фрагмент и server proof его identity.",
)
async def read_git_file(
    project_id: ProjectId,
    connection_id: ConnectionId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
    path: Annotated[str, Query(min_length=1, max_length=4096)],
    sha: Annotated[str | None, Query(max_length=40)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    max_chars: Annotated[int, Query(ge=1, le=8000)] = 8000,
    line_start: Annotated[int | None, Query(ge=1)] = None,
) -> GitFileRead:
    """Читает страницу проверенного исходника, никогда не серверный filesystem path."""
    return await service.read_file(
        project_id=project_id,
        connection_id=connection_id,
        principal=principal,
        path=path,
        sha=sha,
        offset=offset,
        max_chars=max_chars,
        **({"line_start": line_start} if line_start is not None else {}),
    )


@router.get(
    "/connections/{connection_id}/compare",
    operation_id="compareGitCommits",
    response_model=GitComparison,
    responses=RESPONSES,
    summary="Сравнить известные Git версии",
    description="Два recorded SHA одной project connection; API cap/pagination не выдаются за полный diff.",
    response_description="Подтверждённые base/head SHA и явная полнота сравнения.",
)
async def compare_git_commits(
    project_id: ProjectId,
    connection_id: ConnectionId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
    base_sha: Annotated[str, Query(min_length=40, max_length=40)],
    head_sha: Annotated[str, Query(min_length=40, max_length=40)],
) -> GitComparison:
    """Сравнивает только known snapshots того же binding."""
    return await service.compare(
        project_id=project_id,
        connection_id=connection_id,
        principal=principal,
        base_sha=base_sha,
        head_sha=head_sha,
    )


@router.get(
    "/search",
    operation_id="searchGitSources",
    response_model=GitSearchResult,
    responses=RESPONSES,
    summary="Найти Git исходники проекта",
    description="FTS кандидаты допускаются только после live proof и current project scopes; чужие/отключённые источники закрыты.",
    response_description="Bounded точные источники с repo/SHA/path и partial/stale.",
)
async def search_git_sources(
    project_id: ProjectId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
    q: Annotated[str, Query(min_length=1, max_length=2000)],
    connection_id: Annotated[int | None, Query(gt=0)] = None,
    sha: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=30)] = 10,
) -> GitSearchResult:
    """Ищет по одному или доступным репозиториям текущего проекта."""
    return await service.search(
        project_id=project_id,
        principal=principal,
        query=q,
        connection_ids=(connection_id,) if connection_id is not None else (),
        **({"sha": sha} if sha is not None else {}),
        limit=limit,
    )


@router.get(
    "/connections/{connection_id}/snapshots",
    operation_id="listGitSnapshots",
    response_model=GitSnapshotPage,
    responses=RESPONSES,
    summary="Доступные версии репозитория",
    description="Снимки разрешённого подключения после текущей проверки GitHub.",
    response_description="Страница записанных версий с состоянием анализа.",
)
async def list_git_snapshots(
    project_id: ProjectId,
    connection_id: ConnectionId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> GitSnapshotPage:
    """Возвращает страницу снимков выбранного подключения."""
    return await service.list_snapshots(
        project_id=project_id,
        connection_id=connection_id,
        principal=principal,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/connections/{connection_id}/symbols",
    operation_id="listGitSymbols",
    response_model=GitSymbolPage,
    responses=RESPONSES,
    summary="Найти объявления Git",
    description="Чтение опубликованного структурного индекса закреплённого снимка.",
    response_description="Страница символов с координатами и охватом.",
)
async def list_git_symbols(
    project_id: ProjectId,
    connection_id: ConnectionId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
    sha: Annotated[str | None, Query(max_length=40)] = None,
    path: Annotated[str | None, Query(max_length=4096)] = None,
    query: Annotated[str | None, Query(max_length=512)] = None,
    symbol_id: Annotated[int | None, Query(gt=0)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> GitSymbolPage:
    """Возвращает объявления внутри точного SHA."""
    return await service.list_symbols(
        project_id=project_id,
        connection_id=connection_id,
        principal=principal,
        sha=sha,
        path=path,
        query=query,
        symbol_id=symbol_id,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/connections/{connection_id}/graph",
    operation_id="getGitGraph",
    response_model=GitGraph,
    responses=RESPONSES,
    summary="Прочитать граф структуры Git",
    description="Ограниченный граф одного снимка с доказательствами.",
    response_description="Узлы, связи, координаты и граница охвата.",
)
async def get_git_graph(
    project_id: ProjectId,
    connection_id: ConnectionId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
    sha: Annotated[str | None, Query(max_length=40)] = None,
    root_id: Annotated[int | None, Query(gt=0)] = None,
    direction: Literal["in", "out", "both"] = "both",
    relation: Literal["all", "dependency", "containment"] = "all",
    depth: Annotated[int, Query(ge=1, le=4)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> GitGraph:
    """Читает типизированные связи указанной версии."""
    return await service.get_graph(
        project_id=project_id,
        connection_id=connection_id,
        principal=principal,
        sha=sha,
        root_id=root_id,
        direction=direction,
        relation=relation,
        depth=depth,
        limit=limit,
    )


@router.get(
    "/connections/{connection_id}/impact",
    operation_id="getCodeImpact",
    response_model=GitImpact,
    responses=RESPONSES,
    summary="Оценить потенциальное влияние",
    description="Входящие подтверждённые зависимости и задачи с доказанными изменениями путей.",
    response_description="Ограниченный граф зависимости и проверенные связи задач.",
)
async def get_git_impact(
    project_id: ProjectId,
    connection_id: ConnectionId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
    symbol_id: Annotated[int, Query(gt=0)],
    sha: Annotated[str | None, Query(max_length=40)] = None,
    depth: Annotated[int, Query(ge=1, le=4)] = 2,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> GitImpact:
    """Показывает потенциальное влияние в пределах одного поколения."""
    return await service.get_impact(
        project_id=project_id,
        connection_id=connection_id,
        principal=principal,
        sha=sha,
        symbol_id=symbol_id,
        depth=depth,
        limit=limit,
    )


@router.get(
    "/map",
    operation_id="getGitMap",
    response_model=GitProjectMap,
    responses=RESPONSES,
    summary="Получить bounded карту Git проекта",
    description="До100 компонентов и500 декларативных связей с отдельными repository→SHA; выводы по имени явно candidate.",
    response_description="Manifest, bounded symbols/imports и source proofs без выдуманного общего SHA.",
)
async def get_git_map(
    project_id: ProjectId, service: GitSourceServiceDep, principal: GitSourcePrincipalDep
) -> GitProjectMap:
    """Читает статическую карту после проверок всех её доступных источников."""
    return await service.get_map(project_id=project_id, principal=principal)


@router.get(
    "/tasks/{task_id}/links",
    operation_id="listTaskGitLinks",
    response_model=GitTaskLinks,
    responses=RESPONSES,
    summary="Получить Git связи задачи",
    description="Tasks:read плюс оба source read scopes; текущий task.project_id должен совпадать с connection.project_id.",
    response_description="Независимые PR/commit/CI/deploy факты конкретных repository/SHA/environment.",
)
async def list_task_git_links(
    project_id: ProjectId,
    task_id: TaskId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> GitTaskLinks:
    """Читает доступные N:N связи без предположений о merge/проверках/проде."""
    return await service.get_task_links(project_id=project_id, principal=principal, task_id=task_id)


@router.post(
    "/tasks/{task_id}/links",
    operation_id="linkTaskGitChange",
    response_model=GitTaskLink,
    status_code=201,
    responses=RESPONSES,
    summary="Связать задачу с PR или commit",
    description="Явное tasks:write действие; repository identity проверена общим Git сервисом, автосвязей по тексту нет.",
    response_description="Единственная сохранённая связь; evidence пока может быть partial.",
)
async def link_task_git_change(
    project_id: ProjectId,
    task_id: TaskId,
    data: GitTaskLinkCreate,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> GitTaskLink:
    """Добавляет явную N:N привязку, не меняя стадию задачи."""
    return await service.link_task(
        project_id=project_id, principal=principal, task_id=task_id, data=data
    )


@router.delete(
    "/tasks/{task_id}/links/{link_id}",
    operation_id="unlinkTaskGitChange",
    status_code=204,
    responses=RESPONSES,
    summary="Снять Git связь задачи",
    description="Tasks:write снимает одну relationship; исходник, GitChange и ссылки других задач сохраняются.",
    response_description="Связь снята.",
)
async def unlink_task_git_change(
    project_id: ProjectId,
    task_id: TaskId,
    link_id: LinkId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> Response:
    """Удаляет одну проверенную связь текущего проекта."""
    await service.unlink_task(
        project_id=project_id, principal=principal, task_id=task_id, link_id=link_id
    )
    return Response(status_code=204)


@router.post(
    "/tasks/{task_id}/links/{link_id}/refresh",
    operation_id="refreshTaskGitEvidence",
    response_model=GitQueued,
    status_code=202,
    responses=RESPONSES,
    summary="Проверить Git/CI/deploy факты задачи",
    description="Ставит phased GET checkpoint в durable очередь; ответ queued не является подтверждением проверки или выкладки.",
    response_description="Фактическое обновление поставлено в очередь.",
)
async def refresh_task_git_evidence(
    project_id: ProjectId,
    task_id: TaskId,
    link_id: LinkId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> GitQueued:
    """Запрашивает ограниченное обновление реальных provider фактов."""
    return await service.refresh_link(
        project_id=project_id, principal=principal, task_id=task_id, link_id=link_id
    )


@router.get(
    "/tasks/{task_id}/candidates",
    operation_id="listTaskGitCandidates",
    response_model=GitTaskCandidates,
    responses=RESPONSES,
    summary="Найти предложения Git связей",
    description="Точный server task key ищется в bounded PR/commit/branch названиях; несколько task keys означают ambiguous, связи не создаются.",
    response_description="Только suggested candidates с подтверждённой repository identity.",
)
async def list_task_git_candidates(
    project_id: ProjectId,
    task_id: TaskId,
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
) -> GitTaskCandidates:
    """Предлагает совпадения для ручного решения пользователя."""
    return await service.get_task_candidates(
        project_id=project_id, principal=principal, task_id=task_id
    )


@router.post(
    "/connections/{connection_id}/events",
    operation_id="acceptGitEvent",
    response_model=GitEventAccepted,
    status_code=202,
    responses=RESPONSES,
    summary="Принять подписанное GitHub событие",
    description="Отдельная HMAC аутентификация, repository binding и delivery deduplication; SHA события не публикуется без authoritative GET сверки.",
    response_description="Квитанция принятия либо дубликата без исходного payload.",
)
async def accept_git_event(
    project_id: ProjectId,
    connection_id: ConnectionId,
    request: Request,
    service: GitSourceServiceDep,
    signature: Annotated[str, Header(alias="X-Hub-Signature-256", max_length=80)],
    delivery_id: Annotated[str, Header(alias="X-GitHub-Delivery", max_length=128)],
    event_type: Annotated[str, Header(alias="X-GitHub-Event", max_length=40)],
) -> GitEventAccepted:
    """Ограниченно читает body до HMAC, не логируя его и не сохраняя private content."""
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 256 * 1024:
            raise HTTPException(status_code=413, detail="Слишком большое Git событие.")
        body.extend(chunk)
    return await service.ingest_event(
        project_id=project_id,
        connection_id=connection_id,
        body=bytes(body),
        signature=signature,
        delivery_id=delivery_id,
        event_type=event_type,
    )


profile_router = APIRouter(prefix="/users/me/github", tags=["github-profile"], route_class=GitRoute)


@profile_router.get("", operation_id="getGitHubProfile", summary="Получить подключение GitHub")
async def get_github_profile(
    service: GitSourceServiceDep, principal: GitSourcePrincipalDep
) -> GitHubProfile:
    return await service.github_profile(principal)


@profile_router.put("", operation_id="setGitHubProfile", summary="Подключить аккаунт GitHub")
async def set_github_profile(
    data: GitHubProfileUpdate, service: GitSourceServiceDep, principal: GitSourcePrincipalDep
) -> GitHubProfile:
    return await service.set_github_profile(principal, data.token)


@profile_router.post(
    "/check",
    operation_id="checkGitHubProfile",
    response_model=GitHubProfile,
    summary="Проверить токен GitHub",
    description=(
        "Проверяет аккаунт переданного токена без сохранения и изменения подключений. "
        "Требует действующую локальную сессию. Доступ к отдельным репозиториям не проверяет."
    ),
    response_description="Аккаунт проверенного токена без credentials.",
    responses={
        401: {"description": "Сессия отсутствует или больше не действует."},
        403: {"description": "Требуется локальная сессия активного пользователя."},
        422: {"description": "Недопустимый токен или параметры без отражения secret input."},
        429: {"description": "Лимит GitHub; время ожидания в Retry-After."},
        500: {"description": "Не удалось проверить токен."},
        503: {"description": "GitHub временно недоступен."},
    },
)
async def check_github_profile(
    data: GitHubProfileUpdate, service: GitSourceServiceDep, principal: GitSourcePrincipalDep
) -> GitHubProfile:
    """Возвращает аккаунт кандидата, не изменяя сохранённый профиль."""
    return await service.check_github_profile(principal, data.token)


@profile_router.delete(
    "", operation_id="deleteGitHubProfile", summary="Отключить аккаунт GitHub", status_code=204
)
async def delete_github_profile(
    service: GitSourceServiceDep, principal: GitSourcePrincipalDep
) -> Response:
    await service.set_github_profile(principal, None)
    return Response(status_code=204)


@profile_router.get(
    "/repositories", operation_id="listGitHubProfileRepositories", summary="Получить репозитории GitHub"
)
async def list_github_profile_repositories(
    service: GitSourceServiceDep,
    principal: GitSourcePrincipalDep,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
) -> GitRepositoryPage:
    return await service.list_github_repositories(principal, page)
