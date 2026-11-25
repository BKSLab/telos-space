"""Ограниченное чтение GitHub REST API без Git checkout и внешних redirect."""

import asyncio
import base64
import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter, ValidationError

from src.clients.retry import backoff_seconds, worst_case_seconds
from src.exceptions.git_sources import (
    GitHubAuthenticationError,
    GitHubBudgetExceededError,
    GitHubClientError,
    GitHubConflictError,
    GitHubForbiddenError,
    GitHubLimitError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubRedirectError,
    GitHubRequestError,
    GitHubResponseError,
    GitHubTransportError,
)
from src.schemas.git_sources import (
    GitAccountSnapshot,
    GitBlob,
    GitCandidatePage,
    GitCandidateSnapshot,
    GitCheckPage,
    GitCheckSnapshot,
    GitCommit,
    GitCommitDetails,
    GitComparison,
    GitDeploymentPage,
    GitDeploymentSnapshot,
    GitDeploymentStatusPage,
    GitDeploymentStatusSnapshot,
    GitFileChange,
    GitOwner,
    GitPullRequestSnapshot,
    GitRef,
    GitRepositoryName,
    GitRepositoryPage,
    GitRepositorySnapshot,
    GitSha,
    GitTree,
    GitTreeEntry,
)

GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RESPONSE_BYTES = 48 * 1024 * 1024
DEFAULT_MAX_BLOB_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_PATCH_CHARS = 64_000
GITHUB_COMPARE_FILE_LIMIT = 300
GITHUB_PAGE_SIZE = 100
STREAM_CHUNK_BYTES = 64 * 1024

_OWNER = TypeAdapter(GitOwner)
_REPOSITORY = TypeAdapter(GitRepositoryName)
_REF = TypeAdapter(GitRef)
_SHA = TypeAdapter(GitSha)


def _input[T](adapter: TypeAdapter[T], value: object) -> T:
    try:
        return adapter.validate_python(value)
    except ValidationError:
        pass
    # ValidationError содержит входное значение. Не сохраняем его даже в cause.
    raise GitHubRequestError()


def _parsed[T](build: Callable[[], T]) -> T:
    try:
        return build()
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    # Upstream JSON/Pydantic error может содержать temp_clone_token или тело файла.
    raise GitHubResponseError()


def _numeric_header(value: str | None) -> int | None:
    if value and value.isascii() and value.isdecimal() and len(value) <= 10:
        return int(value)
    return None


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp without timezone.")
    return result


def _repository_snapshot(data: dict[str, Any]) -> GitRepositorySnapshot:
    return GitRepositorySnapshot(
        id=data["id"],
        owner=data["owner"]["login"],
        name=data["name"],
        default_branch=data["default_branch"],
        private=data["private"],
    )


@dataclass(slots=True)
class GitRequestBudget:
    """Server-owned ledger, shared by reads and their final source revalidation."""

    max_requests: int = 32
    requests_made: int = 0
    exhausted: bool = False

    def __post_init__(self) -> None:
        if type(self.max_requests) is not int or self.max_requests < 1 or self.requests_made != 0:
            raise GitHubRequestError()

    @property
    def remaining(self) -> int:
        return self.max_requests - self.requests_made

    def consume(self) -> None:
        """Count an actual attempt, including a retry, before any external I/O."""
        if self.requests_made >= self.max_requests:
            self.exhausted = True
            raise GitHubBudgetExceededError()
        self.requests_made += 1


class GitHubClient:
    """Read-only протокол; transport, его аутентификацией и закрытием владеет DI.

    Credential задаётся владельцем готового httpx-клиента. Этот класс не
    управляет project binding, ACL, секретами, очередью или индексом.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        attempts: int = 2,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_blob_bytes: int = DEFAULT_MAX_BLOB_BYTES,
        max_compare_files: int = GITHUB_COMPARE_FILE_LIMIT,
        max_patch_chars: int = DEFAULT_MAX_PATCH_CHARS,
        max_requests: int | None = None,
        request_budget: GitRequestBudget | None = None,
    ) -> None:
        limits = (
            max_response_bytes,
            max_blob_bytes,
            max_compare_files,
            max_patch_chars,
        )
        if (
            type(timeout) not in {int, float}
            or not math.isfinite(timeout)
            or timeout <= 0
            or type(attempts) is not int
            or attempts not in {1, 2}
            or any(type(value) is not int or value < 1 for value in limits)
            or max_compare_files > GITHUB_COMPARE_FILE_LIMIT
            or (max_requests is not None and (type(max_requests) is not int or max_requests < 1))
        ):
            raise GitHubRequestError()
        self.http_client = http_client
        self.timeout = timeout
        self.attempts = attempts
        self.max_response_bytes = max_response_bytes
        self.max_blob_bytes = max_blob_bytes
        self.max_compare_files = max_compare_files
        self.max_patch_chars = max_patch_chars
        self.max_requests = max_requests
        self.requests_made = 0
        self.request_budget = request_budget

    @property
    def worst_case_seconds(self) -> float:
        """Верхняя оценка одного метода: hard deadline попыток плюс backoff/jitter."""
        return worst_case_seconds(timeout=self.timeout, attempts=self.attempts)

    async def get_authenticated_account(self) -> GitAccountSnapshot:
        data, _ = await self._get_json("/user")
        return _parsed(lambda: GitAccountSnapshot(id=data["id"], login=data["login"]))

    async def list_authenticated_repositories(self, *, page: int = 1) -> GitRepositoryPage:
        if type(page) is not int or page < 1:
            raise GitHubRequestError()
        data, has_next = await self._get_json(
            "/user/repos", params={"page": str(page), "per_page": str(GITHUB_PAGE_SIZE)}
        )
        if not isinstance(data, list) or len(data) > GITHUB_PAGE_SIZE:
            raise GitHubResponseError()
        return _parsed(
            lambda: GitRepositoryPage(
                repositories=tuple(_repository_snapshot(item) for item in data),
                next_page=page + 1 if has_next else None,
            )
        )

    async def get_repository(self, owner: str, repo: str) -> GitRepositorySnapshot:
        """Читает identity репозитория, отбрасывая остальные upstream поля.

        Args:
            owner: Login владельца GitHub.
            repo: Имя репозитория без URL.

        Returns:
            Неизменяемая identity без технических credentials.

        Raises:
            GitHubClientError: При ошибке аргументов, доступа, сети или протокола.
        """
        path = self._repository_path(owner, repo)
        data, _ = await self._get_json(path)
        if not isinstance(data, dict):
            raise GitHubResponseError()
        result = _parsed(lambda: _repository_snapshot(data))
        if result.owner.lower() != owner.lower() or result.name.lower() != repo.lower():
            raise GitHubResponseError()
        return result

    async def resolve_ref(self, owner: str, repo: str, ref: str) -> GitCommit:
        """Одним чтением разрешает branch/tag/SHA в коммит и корневое дерево.

        Args:
            owner: Login владельца GitHub.
            repo: Имя репозитория.
            ref: Literal ref; heads/ и tags/ устраняют неоднозначность имён.

        Returns:
            SHA коммита и дерева; дальнейшее чтение использует эти SHA.

        Raises:
            GitHubClientError: При некорректном ref или недоступном снимке.
        """
        path = self._repository_path(owner, repo)
        reference = _input(_REF, ref)
        data, _ = await self._get_json(f"{path}/commits/{quote(reference, safe='')}")
        result = _parsed(lambda: GitCommit(sha=data["sha"], tree_sha=data["commit"]["tree"]["sha"]))
        if len(reference) == 40 and all(char in "0123456789abcdefABCDEF" for char in reference):
            if result.sha != reference.lower():
                raise GitHubResponseError()
        return result

    async def get_tree(
        self, owner: str, repo: str, tree_sha: str, *, recursive: bool = True
    ) -> GitTree:
        """Читает дерево по SHA, сохраняя признак неполного manifest.

        Args:
            owner: Login владельца GitHub.
            repo: Имя репозитория.
            tree_sha: Полный SHA дерева, не mutable ref.
            recursive: Рекурсивное чтение; false действительно убирает параметр API.

        Returns:
            Ограниченное дерево; ссылки и submodule остаются метаданными.

        Raises:
            GitHubClientError: При неподтверждённом SHA, лимите ответа или сбое API.
        """
        path = self._repository_path(owner, repo)
        sha = _input(_SHA, tree_sha)
        if type(recursive) is not bool:
            raise GitHubRequestError()
        data, _ = await self._get_json(
            f"{path}/git/trees/{sha}", params={"recursive": "1"} if recursive else {}
        )
        return _parsed(lambda: self._tree(data, sha))

    async def read_blob(self, owner: str, repo: str, blob_sha: str) -> GitBlob:
        """Читает точные байты blob с проверкой размера, base64 и Git SHA.

        Args:
            owner: Login владельца GitHub.
            repo: Имя репозитория.
            blob_sha: Полный SHA blob из проверенного дерева.

        Returns:
            Байты без следования symlink, LFS, submodule или download_url.

        Raises:
            GitHubClientError: При лимите blob, несоответствии SHA или сбое API.
        """
        path = self._repository_path(owner, repo)
        sha = _input(_SHA, blob_sha)
        data, _ = await self._get_json(f"{path}/git/blobs/{sha}")
        return _parsed(lambda: self._blob(data, sha))

    async def compare_commits(
        self, owner: str, repo: str, base_sha: str, head_sha: str
    ) -> GitComparison:
        """Сравнивает два pinned SHA без автоматической загрузки следующих страниц.

        Args:
            owner: Login владельца GitHub.
            repo: Имя репозитория.
            base_sha: Полный исходный SHA.
            head_sha: Полный целевой SHA.

        Returns:
            Статус, ограниченные изменения и явные причины неполноты.

        Raises:
            GitHubClientError: При mutable ref вместо SHA или недоступном сравнении.
        """
        path = self._repository_path(owner, repo)
        base, head = _input(_SHA, base_sha), _input(_SHA, head_sha)
        data, paginated = await self._get_json(
            f"{path}/compare/{base}...{head}",
            params={"page": "1", "per_page": str(GITHUB_PAGE_SIZE)},
        )
        return _parsed(lambda: self._comparison(data, base, head, paginated))

    async def get_commit_details(self, owner: str, repo: str, sha: str) -> GitCommitDetails:
        """Читает immutable commit identity и ограниченный заголовок, без авторских PII."""
        value = _input(_SHA, sha)
        data, paginated = await self._get_json(f"{self._repository_path(owner, repo)}/commits/{value}")
        result = _parsed(
            lambda: GitCommitDetails(
                sha=data["sha"],
                tree_sha=data["commit"]["tree"]["sha"],
                message=data["commit"]["message"][:4000],
                message_truncated=len(data["commit"]["message"]) > 4000,
                changed_paths=tuple(item["filename"] for item in data.get("files", [])[:self.max_compare_files]),
                files_partial=paginated or "files" not in data or len(data.get("files", [])) > self.max_compare_files,
            )
        )
        if result.sha != value:
            raise GitHubResponseError()
        return result

    async def get_pull_request(self, owner: str, repo: str, number: int) -> GitPullRequestSnapshot:
        """Читает PR; merge commit из test merge не выдаётся за состоявшийся merge."""
        if type(number) is not int or number < 1:
            raise GitHubRequestError()
        data, _ = await self._get_json(f"{self._repository_path(owner, repo)}/pulls/{number}")
        result = _parsed(
            lambda: GitPullRequestSnapshot(
                number=data["number"],
                title=data["title"][:512],
                state=data["state"],
                merged=data["merged"],
                head_sha=data["head"]["sha"],
                base_sha=data["base"]["sha"],
                merge_sha=data["merge_commit_sha"] if data["merged"] else None,
                base_repository_id=data["base"]["repo"]["id"],
                head_ref=data["head"]["ref"],
                base_ref=data["base"]["ref"],
            )
        )
        if result.number != number:
            raise GitHubResponseError()
        return result

    async def list_check_runs(self, owner: str, repo: str, sha: str) -> GitCheckPage:
        """Возвращает первую bounded страницу Checks строго выбранного SHA."""
        value = _input(_SHA, sha)
        data, paginated = await self._get_json(
            f"{self._repository_path(owner, repo)}/commits/{value}/check-runs",
            params={"per_page": "100", "page": "1", "filter": "latest"},
        )

        def build():
            items = tuple(
                GitCheckSnapshot(
                    id=item["id"],
                    name=item["name"][:512],
                    sha=item["head_sha"],
                    status=item["status"],
                    conclusion=item["conclusion"],
                    updated_at=_timestamp(item.get("completed_at") or item.get("started_at")),
                    provider_type="check_run",
                )
                for item in data["check_runs"]
            )
            if (
                len(items) > 100
                or any(item.sha != value for item in items)
                or type(data["total_count"]) is not int
                or data["total_count"] < len(items)
            ):
                raise ValueError("Invalid check identity.")
            return GitCheckPage(
                items=items,
                total=data["total_count"],
                complete=not paginated and len(items) == data["total_count"],
            )

        return _parsed(build)

    async def get_commit_statuses(self, owner: str, repo: str, sha: str) -> GitCheckPage:
        """Читает legacy CI statuses того же SHA, не приравнивая отсутствие CI к success."""
        value = _input(_SHA, sha)
        data, paginated = await self._get_json(
            f"{self._repository_path(owner, repo)}/commits/{value}/status",
            params={"per_page": "100", "page": "1"},
        )

        def build():
            if _SHA.validate_python(data["sha"]) != value:
                raise ValueError("Invalid status identity.")
            items = tuple(
                GitCheckSnapshot(
                    id=item["id"],
                    name=item["context"][:512],
                    sha=value,
                    status=item["state"],
                    conclusion=None,
                    updated_at=_timestamp(item.get("updated_at")),
                    provider_type="commit_status",
                )
                for item in data["statuses"]
            )
            if (
                len(items) > 100
                or type(data["total_count"]) is not int
                or data["total_count"] < len(items)
            ):
                raise ValueError("Invalid status count.")
            return GitCheckPage(
                items=items,
                total=data["total_count"],
                complete=not paginated and len(items) == data["total_count"],
            )

        return _parsed(build)

    async def list_deployments(
        self, owner: str, repo: str, *, limit: int = 10
    ) -> GitDeploymentPage:
        """Читает bounded deployments всех SHA, чтобы увидеть rollback/замену среды."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise GitHubRequestError()
        data, paginated = await self._get_json(
            f"{self._repository_path(owner, repo)}/deployments",
            params={"per_page": str(limit), "page": "1"},
        )

        def build():
            if not isinstance(data, list) or len(data) > limit:
                raise ValueError("Invalid deployments.")
            items = tuple(
                GitDeploymentSnapshot(
                    id=item["id"],
                    sha=item["sha"],
                    environment=item["environment"],
                    created_at=_timestamp(item["created_at"]),
                )
                for item in data
            )
            return GitDeploymentPage(items=items, complete=not paginated)

        return _parsed(build)

    async def list_deployment_statuses(
        self, owner: str, repo: str, deployment_id: int
    ) -> GitDeploymentStatusPage:
        """Текущий status deployment берётся из фактической первой страницы, без log_url."""
        if type(deployment_id) is not int or deployment_id < 1:
            raise GitHubRequestError()
        data, paginated = await self._get_json(
            f"{self._repository_path(owner, repo)}/deployments/{deployment_id}/statuses",
            params={"per_page": "100", "page": "1"},
        )

        def build():
            if not isinstance(data, list) or len(data) > 100:
                raise ValueError("Invalid deployment statuses.")
            items = tuple(
                GitDeploymentStatusSnapshot(
                    id=item["id"],
                    state=item["state"],
                    environment=item.get("environment"),
                    created_at=_timestamp(item["created_at"]),
                )
                for item in data
            )
            return GitDeploymentStatusPage(items=items, complete=not paginated)

        return _parsed(build)

    async def list_change_candidates(
        self, owner: str, repo: str, *, sha: str, kind: str
    ) -> GitCandidatePage:
        """Только bounded suggested candidates; сами branch/PR/commit не создают task links."""
        path = self._repository_path(owner, repo)
        value = _input(_SHA, sha)
        if kind not in {"commit", "pull_request"}:
            raise GitHubRequestError()
        endpoint = f"{path}/commits" if kind == "commit" else f"{path}/pulls"
        params = {
            "per_page": "30",
            "page": "1",
            **(
                {"sha": value}
                if kind == "commit"
                else {"state": "all", "sort": "updated", "direction": "desc"}
            ),
        }
        data, paginated = await self._get_json(endpoint, params=params)

        def build():
            if not isinstance(data, list) or len(data) > 30:
                raise ValueError("Invalid candidates.")
            items = tuple(
                GitCandidateSnapshot(
                    kind=kind,
                    sha=item["sha"] if kind == "commit" else item["head"]["sha"],
                    title=(item["commit"]["message"] if kind == "commit" else item["title"])[:2000],
                    pull_number=item["number"] if kind == "pull_request" else None,
                    branch=item["head"]["ref"] if kind == "pull_request" else None,
                )
                for item in data
            )
            return GitCandidatePage(items=items, complete=not paginated)

        return _parsed(build)

    @staticmethod
    def _repository_path(owner: str, repo: str) -> str:
        return f"/repos/{_input(_OWNER, owner)}/{_input(_REPOSITORY, repo)}"

    async def _get_json(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> tuple[dict[str, Any] | list[Any], bool]:
        for attempt in range(1, self.attempts + 1):
            if self.max_requests is not None and self.requests_made >= self.max_requests:
                raise GitHubBudgetExceededError()
            if self.request_budget is not None:
                self.request_budget.consume()
            self.requests_made += 1
            failure: GitHubClientError | None = None
            try:
                # httpx timeout ограничивает отдельные IO-фазы, поэтому нужен и wall deadline.
                async with asyncio.timeout(self.timeout):
                    async with self.http_client.stream(
                        "GET",
                        f"{GITHUB_API_URL}{path}",
                        params=params,
                        headers={
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": GITHUB_API_VERSION,
                            "Accept-Encoding": "identity",
                        },
                        timeout=self.timeout,
                        follow_redirects=False,
                    ) as response:
                        if response.status_code != 200:
                            failure = self._status_error(response)
                        else:
                            content = await self._read_response(response)
                            data = json.loads(content.decode("utf-8"))
                            if not isinstance(data, (dict, list)):
                                raise GitHubResponseError()
                            has_next_page = any(
                                "next" in link.get("rel", "").lower().split()
                                for link in response.links.values()
                            )
                            return data, has_next_page
            except (httpx.HTTPError, TimeoutError):
                failure = GitHubTransportError()
            except (ValueError, RecursionError):
                failure = GitHubResponseError()
            if isinstance(failure, GitHubTransportError) and attempt < self.attempts:
                await asyncio.sleep(backoff_seconds(attempt))
            else:
                # Не цепляем исключение httpx с потенциально чувствительным request.
                raise failure or GitHubResponseError()
        raise GitHubTransportError()

    async def _read_response(self, response: httpx.Response) -> bytes:
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise GitHubResponseError()
        declared_size = _numeric_header(response.headers.get("content-length"))
        if declared_size is not None and declared_size > self.max_response_bytes:
            raise GitHubLimitError()
        content = bytearray()
        async for chunk in response.aiter_bytes(
            chunk_size=min(STREAM_CHUNK_BYTES, self.max_response_bytes + 1)
        ):
            if len(content) + len(chunk) > self.max_response_bytes:
                raise GitHubLimitError()
            content.extend(chunk)
        return bytes(content)

    @staticmethod
    def _status_error(response: httpx.Response) -> GitHubClientError:
        status = response.status_code
        retry_after = _numeric_header(response.headers.get("retry-after"))
        reset = _numeric_header(response.headers.get("x-ratelimit-reset"))
        if status == 429 or (
            status == 403
            and (response.headers.get("x-ratelimit-remaining") == "0" or retry_after is not None)
        ):
            return GitHubRateLimitError(
                upstream_status=status,
                retry_after=retry_after if retry_after is not None else 60,
                rate_limit_reset=reset,
            )
        error_type = {
            401: GitHubAuthenticationError,
            403: GitHubForbiddenError,
            404: GitHubNotFoundError,
            409: GitHubConflictError,
            400: GitHubRequestError,
            422: GitHubRequestError,
        }.get(status, GitHubResponseError)
        if 300 <= status < 400:
            error_type = GitHubRedirectError
        elif 500 <= status < 600:
            error_type = GitHubTransportError
        return error_type(upstream_status=status)

    def _tree(self, data: dict[str, Any], sha: str) -> GitTree:
        raw = data["tree"]
        if not isinstance(raw, list) or type(data["truncated"]) is not bool:
            raise ValueError("Invalid tree.")
        entries = tuple(
            GitTreeEntry(
                path=entry["path"],
                sha=entry["sha"],
                type=entry["type"],
                mode=entry["mode"],
                size=entry.get("size"),
            )
            for entry in raw
        )
        result = GitTree(
            sha=data["sha"],
            entries=entries,
            truncated=data["truncated"],
        )
        if result.sha != sha or len({entry.path for entry in entries}) != len(entries):
            raise ValueError("Invalid tree identity.")
        return result

    def _blob(self, data: dict[str, Any], sha: str) -> GitBlob:
        size = data["size"]
        if (
            type(size) is not int
            or size < 0
            or data["encoding"] != "base64"
            or not isinstance(data["content"], str)
        ):
            raise ValueError("Invalid blob.")
        if size > self.max_blob_bytes:
            raise GitHubLimitError()
        encoded = data["content"].replace("\n", "").replace("\r", "")
        if len(encoded) > 4 * ((self.max_blob_bytes + 2) // 3):
            raise GitHubLimitError()
        content = base64.b64decode(encoded, validate=True)
        result = GitBlob(sha=data["sha"], size=size, content=content)
        digest = hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content, usedforsecurity=False
        ).hexdigest()
        if result.sha != sha or len(content) != size or digest != sha:
            raise ValueError("Invalid blob identity.")
        return result

    def _comparison(
        self, data: dict[str, Any], base: str, head: str, paginated: bool
    ) -> GitComparison:
        raw, commits, total = data["files"], data["commits"], data["total_commits"]
        if (
            not isinstance(raw, list)
            or not isinstance(commits, list)
            or type(total) is not int
            or total < len(commits)
        ):
            raise ValueError("Invalid comparison.")
        if _SHA.validate_python(data["base_commit"]["sha"]) != base:
            raise ValueError("Invalid comparison base.")
        commit_shas = tuple(_SHA.validate_python(commit["sha"]) for commit in commits)
        merge_base = _SHA.validate_python(data["merge_base_commit"]["sha"])
        commits_complete = not paginated and len(commits) == total
        if data["status"] == "identical" and base != head:
            raise ValueError("Invalid identical comparison.")
        # GitHub возвращает BASE..HEAD в хронологии; только полная страница
        # подтверждает HEAD последним коммитом. Частичная страница этого не обещает.
        if commits_complete:
            if commit_shas:
                if commit_shas[-1] != head:
                    raise ValueError("Invalid comparison head.")
            elif data["status"] == "identical":
                if merge_base != head or data["ahead_by"] != 0 or data["behind_by"] != 0:
                    raise ValueError("Invalid empty identical comparison.")
            elif data["status"] == "behind":
                # При HEAD-предке BASE диапазон пуст; сам merge base подтверждает HEAD.
                if (
                    base == head
                    or merge_base != head
                    or data["ahead_by"] != 0
                    or data["behind_by"] < 1
                ):
                    raise ValueError("Unverified empty behind comparison.")
            else:
                raise ValueError("Unverified empty comparison.")
        changes = []
        for change in raw[: self.max_compare_files]:
            patch = change.get("patch")
            if patch is not None and not isinstance(patch, str):
                raise ValueError("Invalid patch.")
            changes.append(
                GitFileChange(
                    filename=change["filename"],
                    previous_filename=change.get("previous_filename"),
                    additions=change.get("additions"), deletions=change.get("deletions"),
                    status=change["status"],
                    sha=change["sha"],
                    patch=patch[: self.max_patch_chars] if patch is not None else None,
                    patch_truncated=patch is not None and len(patch) > self.max_patch_chars,
                )
            )
        if len({change.filename for change in changes}) != len(changes):
            raise ValueError("Duplicate comparison path.")
        reasons = []
        if len(raw) >= GITHUB_COMPARE_FILE_LIMIT:
            reasons.append("api_file_limit")
        if len(raw) > self.max_compare_files:
            reasons.append("file_limit")
        if paginated:
            reasons.append("pagination")
        if len(commits) < total:
            reasons.append("commit_limit")
        return GitComparison(
            base_sha=base,
            head_sha=head,
            merge_base_sha=merge_base,
            status=data["status"],
            ahead_by=data["ahead_by"],
            behind_by=data["behind_by"],
            total_commits=total,
            files=tuple(changes),
            files_complete=not reasons,
            commits_complete=commits_complete,
            incomplete_reasons=tuple(reasons),
        )
