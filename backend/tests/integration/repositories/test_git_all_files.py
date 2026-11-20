"""Tracked content is authoritative; actual non-text objects remain in the catalog."""

from unittest.mock import patch

import pytest
from sqlalchemy import select, update

from src.clients.github import GitHubClient
from src.db.models.git_sources import GitConnection, GitFile, GitSnapshot
from src.exceptions.git_sources import (
    GitHubLimitError,
    GitSourceConflictError,
    GitSourceIncompleteError,
)
from tests.integration.repositories.test_git_sources import A, blob, connect, converge, environment
from tests.integration.repositories.test_git_sync_triggers import ready, row


async def test_all_tracked_names_secret_like_text_large_json_and_nontext_metadata(engine):
    async with environment(engine) as env:
        text_files = {
            ".env": b"API_KEY = 'synthetic-long-value'\nallfilesmarker",
            "private.pem": b"-----BEGIN PRIVATE KEY-----\nsynthetic\nallfilesmarker",
            "credentials.json": b'{"synthetic": "allfilesmarker"}',
            "node_modules/lib.js": b"export const value = 'allfilesmarker';",
            "dist/app.min.js": b"var v='allfilesmarker';",
            "package-lock.json": b'{"name":"allfilesmarker"}',
            "assets/photo.png": b"allfilesmarker",  # File extension never determines text eligibility.
            "unknown.custom-format": b"allfilesmarker",
            "frontend/nginx.conf": b"# allfilesmarker\nserver {}",
            ".gitattributes": b"# allfilesmarker\n* text=auto",
            "script.py.mako": b"# allfilesmarker\n${revision}",
            "utf16.txt": "allfilesmarker 🧭".encode("utf-16"),
            "large.generated.json": b'{"data":"'
            + b"x" * (2 * 1024 * 1024)
            + b'","tag":"allfilesmarker"}',
            "link": (b"../outside", "120000"),
        }
        metadata_files = {
            "image.bin": b"binary\0image",
            "encoding.bin": b"\xff",
            "large.asset": b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\n",
            "submodule": (b"external repository commit", "160000"),
        }
        env.remote.add_version(A, {**text_files, **metadata_files})
        source = await connect(env)
        await ready(env, source.id)
        connection = await env.service.get_connection(
            project_id=env.project, connection_id=source.id, principal=env.principal
        )
        assert connection.state == "ready"
        assert connection.coverage.total_files == connection.coverage.cataloged_files == 18
        assert connection.coverage.indexed_files == 14
        assert connection.coverage.metadata_files == 4
        assert connection.coverage.failed_files == 0
        assert connection.coverage.metadata_counts == {
            "binary_file": 1,
            "unsupported_encoding": 1,
            "lfs_pointer": 1,
            "submodule": 1,
        }
        assert "include_paths" not in connection.model_dump()
        assert "excluded_files" not in connection.coverage.model_dump()
        page = await env.service.get_tree(
            project_id=env.project, connection_id=source.id, principal=env.principal, limit=5
        )
        assert page.total_files == 18 and page.next_offset == 5
        rest = await env.service.get_tree(
            project_id=env.project, connection_id=source.id, principal=env.principal, offset=5
        )
        entries = {item.path: item for item in page.items + rest.items}
        assert set(entries) == set(text_files) | set(metadata_files)
        assert all(entries[path].index_state == "metadata" for path in metadata_files)
        assert all(entries[path].index_state == "ready" for path in text_files)
        read = await env.service.read_file(
            project_id=env.project, connection_id=source.id, principal=env.member, path=".env"
        )
        assert read.text == text_files[".env"].decode()
        search = await env.service.search(
            project_id=env.project, principal=env.member, query="allfilesmarker", limit=30
        )
        assert {item.path for item in search.items} == set(text_files) - {"link"}
        assert not search.partial
        assert not any("outside" in path for path in env.remote.requests)


async def test_transport_limit_keeps_object_visible_and_retry_resumes(engine):
    async with environment(engine) as env:
        env.remote.add_version(
            A, {"large.txt": b"transport-retry", "after.txt": b"processed anyway"}
        )
        source = await connect(env)
        original = GitHubClient.read_blob

        async def limited(client, owner, repo, sha):
            if sha == blob(b"transport-retry"):
                raise GitHubLimitError()
            return await original(client, owner, repo, sha)

        async def cataloged():
            return (await row(env, source.id))["coverage"].get("cataloged_files") == 2

        with patch.object(GitHubClient, "read_blob", limited):
            await converge(env, cataloged)
        current = await row(env, source.id)
        assert current["coverage"]["failed_files"] == 1 and current["state"] == "partial"
        assert current["indexed_sha"] is None
        tree = await env.service.get_tree(
            project_id=env.project, connection_id=source.id, principal=env.principal
        )
        entries = {entry.path: entry for entry in tree.items}
        assert entries["large.txt"].index_state == "error"
        assert entries["large.txt"].index_reason == "transport_limit"
        assert entries["after.txt"].index_state == "ready"
        with pytest.raises(GitSourceIncompleteError):
            await env.service.read_file(
                project_id=env.project,
                connection_id=source.id,
                principal=env.principal,
                path="large.txt",
            )
        await env.service.sync_connection(
            project_id=env.project, connection_id=source.id, principal=env.principal
        )
        await ready(env, source.id)
        assert (await row(env, source.id))["coverage"]["failed_files"] == 0


async def test_legacy_filtered_snapshot_is_retained_but_not_reused_as_current(engine):
    async with environment(engine) as env:
        env.remote.add_version(A, {
            "README.md": b"Original project guidance\n",
            "retained.txt": b"unchanged full original text",
        })
        source = await connect(env)
        await ready(env, source.id)
        proof = await env.service.read_file(
            project_id=env.project,
            connection_id=source.id,
            principal=env.principal,
            path="README.md",
        )
        async with env.sessions() as db:
            await db.execute(
                update(GitConnection)
                .where(GitConnection.id == source.id)
                .values(scope_hash="f" * 64)
            )
            await db.execute(
                update(GitSnapshot)
                .where(GitSnapshot.connection_id == source.id)
                .values(scope_hash="f" * 64)
            )
            await db.execute(
                update(GitFile)
                .where(GitFile.id == proof.file_id)
                .values(state="excluded", reason="secret_content", text=None)
            )
            await db.commit()
        attempts_before = list(env.remote.requests)
        read = await env.service.read_file(
            project_id=env.project,
            connection_id=source.id,
            principal=env.principal,
            path="README.md",
        )
        retained = await env.service.read_file(
            project_id=env.project, connection_id=source.id, principal=env.principal,
            path="retained.txt",
        )
        new_requests = env.remote.requests[len(attempts_before):]
        assert any(path.endswith("/" + blob(b"Original project guidance\n")) for path in new_requests)
        assert not any(path.endswith("/" + blob(b"unchanged full original text")) for path in new_requests)
        assert retained.text == "unchanged full original text"
        assert read.text == "Original project guidance\n" and read.file_id != proof.file_id
        with pytest.raises(GitSourceConflictError):
            await env.service.validate_sources(
                project_id=env.project, principal=env.principal, sources=[proof]
            )
        async with env.sessions() as db:
            assert (
                await db.scalar(select(GitFile.state).where(GitFile.id == proof.file_id))
            ) == "excluded"
