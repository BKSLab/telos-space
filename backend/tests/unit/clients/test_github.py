"""Контракт транспорта GitHub: pinned snapshots, пределы, неполнота и безопасные ошибки."""

import asyncio
import base64
import hashlib
import time
import traceback
from copy import deepcopy

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from src.clients.github import GITHUB_API_VERSION, GitHubClient, GitRequestBudget
from src.exceptions.git_sources import (
    GitHubAuthenticationError,
    GitHubBudgetExceededError,
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
from src.schemas.git_sources import GitPath, GitRepositorySnapshot

OWNER, REPO = "BKSLab", "telos-space"
COMMIT_SHA, TREE_SHA, HEAD_SHA = "a" * 40, "b" * 40, "d" * 40
MARKER = "synthetic-sensitive-upstream-marker"


def repository_payload():
    return {
        "id": 12345,
        "owner": {"login": OWNER},
        "name": REPO,
        "default_branch": "main",
        "private": True,
        "temp_clone_token": MARKER,
        "clone_url": "https://untrusted.test/repository.git",
    }


def blob_payload(content=b"# TELOS\n\x00\xff\n"):
    sha = hashlib.sha1(
        f"blob {len(content)}\0".encode("ascii") + content, usedforsecurity=False
    ).hexdigest()
    return {
        "sha": sha,
        "size": len(content),
        "encoding": "base64",
        "content": base64.b64encode(content).decode("ascii") + "\n",
        "url": "http://127.0.0.1/internal",
        "download_url": "https://untrusted.test/content",
    }


def tree_entry(path="docs/README.md", **overrides):
    return {
        "path": path,
        "type": "blob",
        "mode": "100644",
        "sha": "c" * 40,
        "size": 12,
        **overrides,
    }


def comparison_payload():
    return {
        "base_commit": {"sha": COMMIT_SHA},
        "merge_base_commit": {"sha": COMMIT_SHA},
        "status": "ahead",
        "ahead_by": 1,
        "behind_by": 0,
        "total_commits": 1,
        "commits": [{"sha": HEAD_SHA}],
        "files": [
            {
                "filename": "src/app.py",
                "status": "modified",
                "sha": "c" * 40,
                "patch": "@@ -1 +1 @@\n-a\n+b",
            }
        ],
    }


def build_client(handler, **limits):
    return GitHubClient(
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://untrusted.test/",
            headers={"Authorization": f"Bearer {MARKER}"},
            follow_redirects=True,
        ),
        **limits,
    )


async def test_reads_authenticated_account_and_paginates_repository_catalog():
    def reply(request):
        assert request.url.host == "api.github.com"
        assert request.headers["Authorization"] == f"Bearer {MARKER}"
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat", "email": MARKER})
        assert request.url.path == "/user/repos"
        assert request.url.params["per_page"] == "100"
        if request.url.params["page"] == "1":
            return httpx.Response(
                200,
                json=[repository_payload()],
                headers={"Link": '<https://untrusted.test/?page=2>; rel="next"'},
            )
        assert request.url.params["page"] == "2"
        return httpx.Response(200, json=[])

    client = build_client(reply)
    async with client.http_client:
        account = await client.get_authenticated_account()
        first_page = await client.list_authenticated_repositories()
        last_page = await client.list_authenticated_repositories(page=first_page.next_page)

    assert account.model_dump() == {"id": 42, "login": "octocat"}
    assert first_page.repositories[0].owner == OWNER
    assert MARKER not in first_page.model_dump_json()
    assert first_page.next_page == 2
    assert last_page.repositories == ()
    assert last_page.next_page is None


class CountingStream(httpx.AsyncByteStream):
    """Фиксирует реальное прекращение чтения, а не только обрезку готового body."""

    def __init__(self, chunks, *, delay=0):
        self.chunks = chunks
        self.delay = delay
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


async def test_gets_metadata_resolves_exact_ref_and_reads_immutable_objects():
    """Произвольные upstream URLs, symlink и submodule не создают новых requests."""
    blob = blob_payload()
    requests = []

    def reply(request):
        requests.append(request)
        assert request.url.scheme == "https" and request.url.host == "api.github.com"
        assert request.method == "GET" and request.content == b""
        assert request.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
        assert request.headers["Accept-Encoding"] == "identity"
        assert request.headers["Authorization"] == f"Bearer {MARKER}"
        path = request.url.path
        if path == f"/repos/{OWNER}/{REPO}":
            return httpx.Response(200, json=repository_payload())
        if path == f"/repos/{OWNER}/{REPO}/commits/heads/feature/ветка":
            assert b"heads%2Ffeature%2F" in request.url.raw_path
            return httpx.Response(
                200, json={"sha": COMMIT_SHA, "commit": {"tree": {"sha": TREE_SHA}}}
            )
        if path == f"/repos/{OWNER}/{REPO}/git/trees/{TREE_SHA}":
            assert dict(request.url.params) == {"recursive": "1"}
            return httpx.Response(
                200,
                json={
                    "sha": TREE_SHA,
                    "truncated": False,
                    "tree": [
                        tree_entry("документы/чтение #1?.md", sha=blob["sha"], size=blob["size"]),
                        tree_entry("link", mode="120000"),
                        tree_entry("vendor/module", mode="160000", type="commit", size=None),
                    ],
                },
            )
        if path == f"/repos/{OWNER}/{REPO}/git/blobs/{blob['sha']}":
            return httpx.Response(200, json=blob)
        pytest.fail("Клиент запросил адрес вне ожидаемого immutable чтения.")

    client = build_client(reply)
    async with client.http_client:
        repository = await client.get_repository(OWNER, REPO)
        commit = await client.resolve_ref(OWNER, REPO, "heads/feature/ветка")
        tree = await client.get_tree(OWNER, REPO, commit.tree_sha)
        content = await client.read_blob(OWNER, REPO, tree.entries[0].sha)
        assert not client.http_client.is_closed
    assert repository.id == 12345 and repository.private
    assert MARKER not in repr(repository.model_dump())
    assert commit.sha == COMMIT_SHA and tree.sha == TREE_SHA and not tree.truncated
    assert tree.entries[0].path == "документы/чтение #1?.md"
    assert [entry.is_regular_file for entry in tree.entries] == [True, False, False]
    assert content.content == b"# TELOS\n\x00\xff\n" and content.size == 11
    assert len(requests) == 4
    assert "content=" not in repr(content)
    assert client.worst_case_seconds == pytest.approx(21.2)


async def test_tree_preserves_upstream_and_local_incompleteness_and_omits_false_recursive():
    calls = []
    responses = [
        {"sha": TREE_SHA, "truncated": False, "tree": [tree_entry(str(i)) for i in range(3)]},
        {"sha": TREE_SHA, "truncated": True, "tree": []},
    ]

    def reply(request):
        calls.append(request)
        return httpx.Response(200, json=responses.pop(0))

    client = build_client(reply, max_tree_entries=2)
    async with client.http_client:
        local = await client.get_tree(OWNER, REPO, TREE_SHA)
        upstream = await client.get_tree(OWNER, REPO, TREE_SHA, recursive=False)
    assert [entry.path for entry in local.entries] == ["0", "1"] and local.truncated
    assert upstream.entries == () and upstream.truncated
    assert "recursive" not in calls[1].url.params


async def test_compare_keeps_both_sha_rename_origin_and_binary_patch_absence():
    data = comparison_payload()
    data.update(status="diverged", behind_by=2)
    data["merge_base_commit"]["sha"] = TREE_SHA
    data["files"] = [
        {
            "filename": "docs/new.md",
            "previous_filename": "docs/old.md",
            "status": "renamed",
            "sha": "c" * 40,
            "patch": "длинный patch",
        },
        {"filename": "image.png", "status": "added", "sha": TREE_SHA},
    ]
    requests = []

    def reply(request):
        requests.append(request)
        assert request.url.path.endswith(f"/compare/{COMMIT_SHA}...{HEAD_SHA}")
        assert dict(request.url.params) == {"page": "1", "per_page": "100"}
        return httpx.Response(200, json=data)

    client = build_client(reply, max_patch_chars=4)
    async with client.http_client:
        result = await client.compare_commits(OWNER, REPO, COMMIT_SHA.upper(), HEAD_SHA.upper())
    assert result.base_sha == COMMIT_SHA and result.head_sha == HEAD_SHA
    assert result.status == "diverged" and result.merge_base_sha == TREE_SHA
    assert result.files[0].previous_filename == "docs/old.md"
    assert result.files[0].patch == "длин" and result.files[0].patch_truncated
    assert result.files[1].patch is None and not result.files[1].patch_truncated
    assert result.files_complete and result.commits_complete and not result.incomplete_reasons
    assert len(requests) == 1


async def test_compare_exposes_api_local_and_pagination_limits_without_following_link():
    navigation_cases = {"first", "last", "prev", "navigation"}
    for case in (
        "api",
        "local",
        "pagination",
        "commit_count",
        "empty_page",
        "first",
        "last",
        "prev",
        "navigation",
        "multiple_next",
    ):
        data = comparison_payload()
        headers = {}
        limits = {}
        if case == "api":
            data["files"] = [
                {"filename": f"file{i}.md", "status": "modified", "sha": TREE_SHA}
                for i in range(300)
            ]
            expected = ("api_file_limit",)
        elif case == "local":
            data["files"].append({"filename": "extra.md", "status": "removed", "sha": TREE_SHA})
            limits["max_compare_files"] = 1
            expected = ("file_limit",)
        elif case in navigation_cases:
            rels = ("first", "last", "prev") if case == "navigation" else (case,)
            headers["Link"] = ", ".join(
                f'<https://untrusted.test/{rel}>; rel="{rel}"' for rel in rels
            )
            expected = ()
        else:
            data["total_commits"] = 2
            data["commits"][0]["sha"] = TREE_SHA
            if case in {"pagination", "empty_page", "multiple_next"}:
                headers["Link"] = '<http://127.0.0.1/next>; rel="next"'
                expected = ("pagination", "commit_limit")
                if case == "multiple_next":
                    headers["Link"] = (
                        '<https://untrusted.test/first>; rel="first", <http://127.0.0.1/next>; rel="prev next", <https://untrusted.test/last>; rel="last"'
                    )
                if case == "empty_page":
                    data["commits"] = []
            else:
                expected = ("commit_limit",)
        requests = []

        def reply(request, *, requests=requests, data=data, headers=headers):
            requests.append(request)
            return httpx.Response(200, json=data, headers=headers)

        client = build_client(reply, **limits)
        async with client.http_client:
            result = await client.compare_commits(OWNER, REPO, COMMIT_SHA, HEAD_SHA)
        assert result.files_complete is (case in navigation_cases)
        assert result.incomplete_reasons == expected
        assert result.commits_complete is (case in {"api", "local"} | navigation_cases)
        assert len(requests) == 1
        if case == "local":
            assert len(result.files) == 1


async def test_empty_behind_and_identical_comparisons_verify_head_through_merge_base():
    for status in ("behind", "identical"):
        head = HEAD_SHA if status == "behind" else COMMIT_SHA
        data = {
            **comparison_payload(),
            "status": status,
            "ahead_by": 0,
            "behind_by": 2 if status == "behind" else 0,
            "total_commits": 0,
            "commits": [],
            "files": [],
            "merge_base_commit": {"sha": head},
        }
        client = build_client(lambda request, data=data: httpx.Response(200, json=data))
        async with client.http_client:
            result = await client.compare_commits(OWNER, REPO, COMMIT_SHA, head)
        assert result.head_sha == result.merge_base_sha == head
        assert result.files == () and result.files_complete and result.commits_complete
        assert not result.incomplete_reasons


async def test_invalid_input_never_reaches_transport():
    requests = []
    client = build_client(lambda request: requests.append(request) or httpx.Response(200, json={}))
    calls = [
        lambda: client.get_repository("https://untrusted.test", REPO),
        lambda: client.get_repository(OWNER, "../repo"),
        lambda: client.get_repository(OWNER, ".."),
        lambda: client.get_repository(OWNER, "repo?token=x"),
        lambda: client.resolve_ref(OWNER, REPO, "heads/../main"),
        lambda: client.resolve_ref(OWNER, REPO, "main?x=1"),
        lambda: client.resolve_ref(OWNER, REPO, "heads/hidden.lock"),
        lambda: client.resolve_ref(OWNER, REPO, "https://untrusted.test/ref"),
        lambda: client.resolve_ref(OWNER, REPO, "refs//heads/main"),
        lambda: client.get_tree(OWNER, REPO, "main"),
        lambda: client.get_tree(OWNER, REPO, TREE_SHA, recursive="false"),
        lambda: client.read_blob(OWNER, REPO, "a" * 39),
        lambda: client.compare_commits(OWNER, REPO, "main", HEAD_SHA),
    ]
    async with client.http_client:
        for call in calls:
            with pytest.raises(GitHubRequestError) as caught:
                await call()
            assert caught.value.__context__ is None
    assert requests == []


@pytest.mark.parametrize(
    "status, error_type, headers",
    [
        (401, GitHubAuthenticationError, {}),
        (403, GitHubForbiddenError, {}),
        (404, GitHubNotFoundError, {}),
        (409, GitHubConflictError, {}),
        (422, GitHubRequestError, {}),
        (429, GitHubRateLimitError, {}),
        (
            403,
            GitHubRateLimitError,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1900000000"},
        ),
        (403, GitHubRateLimitError, {"Retry-After": "120"}),
        (204, GitHubResponseError, {}),
        (418, GitHubResponseError, {}),
    ],
)
async def test_http_errors_are_typed_safe_and_fail_fast(status, error_type, headers, caplog):
    requests = []
    stream = CountingStream([MARKER.encode()])

    def reply(request):
        requests.append(request)
        return httpx.Response(status, stream=stream, headers=headers)

    client = build_client(reply)
    async with client.http_client:
        with pytest.raises(error_type) as caught:
            await client.get_repository(OWNER, REPO)
    assert type(caught.value) is error_type
    assert caught.value.upstream_status == status
    assert len(requests) == 1 and stream.consumed == 0 and stream.closed
    assert MARKER not in "".join(traceback.format_exception(caught.value)) + caplog.text
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    if error_type is GitHubRateLimitError:
        assert caught.value.retry_after == (120 if "Retry-After" in headers else 60)
        assert caught.value.rate_limit_reset == (
            1900000000 if "x-ratelimit-reset" in headers else None
        )


async def test_redirects_are_rejected_even_when_injected_client_enables_them():
    for status in (301, 302, 307, 308):
        requests = []

        def reply(request, *, requests=requests, status=status):
            requests.append(request)
            return httpx.Response(status, headers={"Location": f"https://untrusted.test/{MARKER}"})

        client = build_client(reply)
        async with client.http_client:
            with pytest.raises(GitHubRedirectError):
                await client.get_repository(OWNER, REPO)
        assert len(requests) == 1


async def test_transport_and_server_retries_are_bounded_and_drop_sensitive_exception(monkeypatch):
    monkeypatch.setattr("src.clients.github.backoff_seconds", lambda attempt: 0)
    for failure in ("network", "timeout", "server"):
        for recover in (False, True):
            requests = []

            def reply(request, *, requests=requests, recover=recover, failure=failure):
                requests.append(request)
                if recover and len(requests) == 2:
                    return httpx.Response(200, json=repository_payload())
                if failure == "server":
                    return httpx.Response(503, json={"message": MARKER})
                error_type = httpx.ConnectError if failure == "network" else httpx.ReadTimeout
                raise error_type(MARKER, request=request)

            client = build_client(reply)
            async with client.http_client:
                if recover:
                    assert (await client.get_repository(OWNER, REPO)).id == 12345
                else:
                    with pytest.raises(GitHubTransportError) as caught:
                        await client.get_repository(OWNER, REPO)
                    assert MARKER not in "".join(traceback.format_exception(caught.value))
                    assert caught.value.__context__ is None
            assert len(requests) == 2


async def test_streaming_enforces_byte_limit_before_reading_whole_body_and_closes_response():
    for declared in (False, True):
        stream = CountingStream([b"x" * 60] * 10)
        requests = []

        def reply(request, *, requests=requests, stream=stream, declared=declared):
            requests.append(request)
            return httpx.Response(
                200, stream=stream, headers={"Content-Length": "600"} if declared else {}
            )

        client = build_client(reply, max_response_bytes=100)
        async with client.http_client:
            with pytest.raises(GitHubLimitError):
                await client.get_repository(OWNER, REPO)
        assert stream.consumed == (0 if declared else 2)
        assert stream.closed and len(requests) == 1


async def test_wall_deadline_closes_slow_stream_without_waiting_for_httpx_inactivity_timeout():
    stream = CountingStream([b"{"], delay=1)
    client = build_client(
        lambda request: httpx.Response(200, stream=stream), timeout=0.02, attempts=1
    )
    started = time.monotonic()
    async with client.http_client:
        with pytest.raises(GitHubTransportError):
            await client.get_repository(OWNER, REPO)
    assert time.monotonic() - started < 0.75
    assert stream.closed and client.worst_case_seconds == 0.02


async def test_malformed_responses_never_become_empty_success_or_expose_body():
    responses = [
        httpx.Response(200, content=b"not json " + MARKER.encode()),
        httpx.Response(
            200,
            content=b'{"nested":'
            + b"[" * 2000
            + b"0"
            + b"]" * 2000
            + b',"marker":"'
            + MARKER.encode()
            + b'"}',
        ),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={**repository_payload(), "id": "12345"}),
        httpx.Response(200, json={**repository_payload(), "owner": {"login": MARKER + "/bad"}}),
    ]
    compressed = httpx.Response(200, json=repository_payload())
    compressed.headers["Content-Encoding"] = "gzip"
    responses.append(compressed)
    for response in responses:
        requests = []

        def reply(request, *, requests=requests, response=response):
            requests.append(request)
            return response

        client = build_client(reply)
        async with client.http_client:
            with pytest.raises(GitHubResponseError) as caught:
                await client.get_repository(OWNER, REPO)
        assert len(requests) == 1
        assert MARKER not in "".join(traceback.format_exception(caught.value))
        assert caught.value.__context__ is None


async def test_blob_size_base64_and_actual_git_digest_are_all_verified():
    original = blob_payload()
    corruptions = [
        {"size": original["size"] - 1},
        {"sha": COMMIT_SHA},
        {"content": "!invalid"},
        {"encoding": "none"},
        {"size": True},
        {"content": base64.b64encode(b"different!").decode()},
    ]
    for changes in corruptions:
        client = build_client(
            lambda request, changes=changes: httpx.Response(200, json={**original, **changes})
        )
        async with client.http_client:
            with pytest.raises(GitHubResponseError):
                await client.read_blob(OWNER, REPO, original["sha"])
    for data in ({**original, "size": 100}, {**original, "size": 1, "content": "A" * 20}):
        client = build_client(
            lambda request, data=data: httpx.Response(200, json=data), max_blob_bytes=2
        )
        async with client.http_client:
            with pytest.raises(GitHubLimitError):
                await client.read_blob(OWNER, REPO, original["sha"])
    for content in (b"", b"version https://git-lfs.github.com/spec/v1\noid sha256:123\nsize 10\n"):
        data = blob_payload(content)
        requests = []

        def reply(request, *, requests=requests, data=data):
            requests.append(request)
            return httpx.Response(200, json=data)

        client = build_client(reply)
        async with client.http_client:
            assert (await client.read_blob(OWNER, REPO, data["sha"])).content == content
        assert len(requests) == 1


async def test_untrusted_tree_paths_modes_duplicates_and_identity_are_rejected():
    for path in ("../file", "/root", "a/../file", "a//file", "a\\file", "a\x00file", "."):
        data = {"sha": TREE_SHA, "truncated": False, "tree": [tree_entry(path)]}
        client = build_client(lambda request, data=data: httpx.Response(200, json=data))
        async with client.http_client:
            with pytest.raises(GitHubResponseError):
                await client.get_tree(OWNER, REPO, TREE_SHA)
    invalid = [
        {"sha": COMMIT_SHA, "tree": []},
        {"tree": [tree_entry(), tree_entry()]},
        {"tree": [tree_entry(mode="100600")]},
        {"tree": [tree_entry(type="tree")]},
        {"tree": [tree_entry(size=None)]},
        {"truncated": "false", "tree": []},
    ]
    for changes in invalid:
        data = {"sha": TREE_SHA, "truncated": False, **changes}
        client = build_client(lambda request, data=data: httpx.Response(200, json=data))
        async with client.http_client:
            with pytest.raises(GitHubResponseError):
                await client.get_tree(OWNER, REPO, TREE_SHA)


async def test_comparison_cannot_hide_unknown_changes_or_wrong_base_or_head():
    for case in (
        "base",
        "head",
        "identical",
        "empty_ahead",
        "behind_unverified",
        "missing",
        "status",
        "rename",
        "path",
        "duplicate",
        "counts",
    ):
        data = deepcopy(comparison_payload())
        if case == "base":
            data["base_commit"]["sha"] = TREE_SHA
        elif case == "head":
            data["commits"][0]["sha"] = TREE_SHA
        elif case == "identical":
            data["status"] = "identical"
        elif case in {"empty_ahead", "behind_unverified"}:
            data.update(total_commits=0, commits=[], files=[])
            if case == "behind_unverified":
                data.update(status="behind", ahead_by=0, behind_by=1)
        elif case == "missing":
            del data["files"]
        elif case == "status":
            data["files"][0]["status"] = "unknown"
        elif case == "rename":
            data["files"][0]["status"] = "renamed"
        elif case == "path":
            data["files"][0]["filename"] = "../hidden"
        elif case == "duplicate":
            data["files"].append(deepcopy(data["files"][0]))
        else:
            data["total_commits"] = 0
        client = build_client(lambda request, data=data: httpx.Response(200, json=data))
        async with client.http_client:
            with pytest.raises(GitHubResponseError):
                await client.compare_commits(OWNER, REPO, COMMIT_SHA, HEAD_SHA)


async def test_immutable_snapshots_and_constructor_limits_have_explicit_contract():
    repository = GitRepositorySnapshot(
        id=1, owner=OWNER, name=REPO, default_branch=None, private=False
    )
    with pytest.raises(ValidationError):
        repository.id = 2
    with pytest.raises(ValidationError):
        GitRepositorySnapshot(**repository.model_dump(), credential=MARKER)
    assert TypeAdapter(GitPath).validate_python("folder/e\u0301 #1?.md") == "folder/e\u0301 #1?.md"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as transport:
        for limits in (
            {"timeout": 0},
            {"timeout": float("inf")},
            {"timeout": None},
            {"attempts": 3},
            {"attempts": True},
            {"max_response_bytes": 0},
            {"max_blob_bytes": -1},
            {"max_tree_entries": 0},
            {"max_compare_files": 301},
            {"max_patch_chars": 0},
        ):
            with pytest.raises(GitHubRequestError):
                GitHubClient(http_client=transport, **limits)


async def test_pr_uses_actual_merge_only_and_rejects_unverified_merged_identity():
    payload = {
        "number": 7,
        "title": "A change",
        "state": "open",
        "merged": False,
        "merge_commit_sha": HEAD_SHA,
        "head": {"sha": COMMIT_SHA, "ref": "feature"},
        "base": {"sha": TREE_SHA, "ref": "main", "repo": {"id": 12345}},
    }
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    client = build_client(reply)
    async with client.http_client:
        opened = await client.get_pull_request(OWNER, REPO, 7)
        assert opened.merge_sha is None and opened.head_sha == COMMIT_SHA
        payload.update(merged=True, state="closed")
        merged = await client.get_pull_request(OWNER, REPO, 7)
        assert merged.merge_sha == HEAD_SHA
        payload["merge_commit_sha"] = None
        with pytest.raises(GitHubResponseError):
            await client.get_pull_request(OWNER, REPO, 7)
        payload.update(number=8, merged=False)
        with pytest.raises(GitHubResponseError):
            await client.get_pull_request(OWNER, REPO, 7)
    assert all(
        request.method == "GET"
        and request.url.host == "api.github.com"
        and request.url.path.endswith("/pulls/7")
        for request in requests
    )


async def test_checks_statuses_and_commit_details_prove_requested_sha_and_pagination():
    check = {
        "id": 1,
        "name": "Tests",
        "head_sha": COMMIT_SHA,
        "status": "completed",
        "conclusion": "failure",
        "completed_at": "2026-11-16T10:00:00Z",
    }
    payload = {"total_count": 2, "check_runs": [check]}
    client = build_client(lambda request: httpx.Response(200, json=payload))
    async with client.http_client:
        page = await client.list_check_runs(OWNER, REPO, COMMIT_SHA)
        assert (
            not page.complete
            and page.items[0].sha == COMMIT_SHA
            and page.items[0].conclusion == "failure"
        )
        check["head_sha"] = HEAD_SHA
        with pytest.raises(GitHubResponseError):
            await client.list_check_runs(OWNER, REPO, COMMIT_SHA)
    payload = {"sha": HEAD_SHA, "total_count": 0, "statuses": []}
    client = build_client(lambda request: httpx.Response(200, json=payload))
    async with client.http_client:
        with pytest.raises(GitHubResponseError):
            await client.get_commit_statuses(OWNER, REPO, COMMIT_SHA)
        payload["sha"] = COMMIT_SHA
        assert (await client.get_commit_statuses(OWNER, REPO, COMMIT_SHA)).complete
    payload = {
        "sha": HEAD_SHA,
        "commit": {"tree": {"sha": TREE_SHA}, "message": "Title\n" + "x" * 5000},
    }
    client = build_client(lambda request: httpx.Response(200, json=payload))
    async with client.http_client:
        with pytest.raises(GitHubResponseError):
            await client.get_commit_details(OWNER, REPO, COMMIT_SHA)
        payload["sha"] = COMMIT_SHA
        commit = await client.get_commit_details(OWNER, REPO, COMMIT_SHA)
        assert len(commit.message) == 4000 and commit.message_truncated


async def test_deployment_pages_expose_real_sha_and_status_without_following_provider_urls():
    requests = []
    payload = [
        {
            "id": 9,
            "sha": HEAD_SHA,
            "environment": "production",
            "created_at": "2026-11-16T10:00:00Z",
            "statuses_url": "https://untrusted.test/secret",
        }
    ]

    def reply(request):
        requests.append(request)
        return httpx.Response(
            200, json=payload, headers={"Link": '<https://untrusted.test/next>; rel="next"'}
        )

    client = build_client(reply)
    async with client.http_client:
        page = await client.list_deployments(OWNER, REPO)
        assert page.items[0].sha == HEAD_SHA and not page.complete
        payload = [
            {
                "id": 10,
                "state": "inactive",
                "environment": "production",
                "created_at": "2026-11-16T11:00:00Z",
                "log_url": "http://127.0.0.1/private",
            }
        ]
        statuses = await client.list_deployment_statuses(OWNER, REPO, 9)
        assert statuses.items[0].state == "inactive" and not statuses.complete
        assert "log_url" not in statuses.model_dump_json()
    assert len(requests) == 2 and all(
        request.url.host == "api.github.com" and request.method == "GET" for request in requests
    )
    assert all(request.url.params["page"] == "1" for request in requests)


async def test_request_budget_counts_actual_attempts_and_never_crosses_batch_limit():
    requests = []

    def reply(request):
        requests.append(request)
        return (
            httpx.Response(503)
            if len(requests) == 1
            else httpx.Response(200, json=repository_payload())
        )

    client = build_client(reply, max_requests=2)
    async with client.http_client:
        await client.get_repository(OWNER, REPO)
        with pytest.raises(GitHubBudgetExceededError):
            await client.get_repository(OWNER, REPO)
    assert client.requests_made == len(requests) == 2


async def test_shared_budget_survives_distinct_clients_and_counts_retries():
    ledger = GitRequestBudget(max_requests=3)
    requests = []

    def reply(request):
        requests.append(request)
        return (
            httpx.Response(503)
            if len(requests) == 1
            else httpx.Response(200, json=repository_payload())
        )

    for _ in range(2):
        client = build_client(reply, request_budget=ledger)
        async with client.http_client:
            await client.get_repository(OWNER, REPO)
    assert ledger.requests_made == 3 and ledger.remaining == 0
    client = build_client(reply, request_budget=ledger)
    async with client.http_client:
        with pytest.raises(GitHubBudgetExceededError):
            await client.get_repository(OWNER, REPO)
    assert ledger.exhausted and len(requests) == 3 and client.requests_made == 0
