"""Структурная публикация на настоящем PostgreSQL с синтетическим GitHub."""

from unittest.mock import patch

import pytest
from sqlalchemy import select, update

from src.clients.github import GitHubClient
from src.db.models.git_sources import GitSnapshot, GitSyncJob
from src.db.models.git_structure import GitAnalysis
from src.db.models.projects import Project
from src.exceptions.git_sources import GitSourceNotAvailableError
from src.schemas.git_sources import GitChangeProof, GitTaskLinkCreate, GitTree, GitTreeEntry
from src.services.git_structure import ANALYZER_VERSION
from tests.integration.repositories.test_git_sources import A, B, connect, converge, environment
from tests.integration.repositories.test_git_sync_triggers import ready


async def test_snapshot_publication_resolution_search_range_and_repo_isolation(engine):
    async with environment(engine) as env:
        env.remote.add_version(
            A,
            {
                "pkg/base.py": 'class Base:\r\n    "ж"\r\n'.encode(),
                "pkg/api.py": b"from .base import Base as B\nclass Service(B): pass\n",
            },
        )
        connections = [await connect(env, repo) for repo in ("app", "api", "docs")]
        for conn in connections:
            await ready(env, conn.id)
        args = dict(project_id=env.project, principal=env.member, connection_id=connections[0].id)
        symbols = await env.service.list_symbols(**args, sha=A, query="Base")
        base = next(i for i in symbols.items if i.name == "Base")
        assert symbols.analyzer_version == ANALYZER_VERSION
        assert symbols.coverage.analyzed_files == 2
        assert symbols.sources[0].entity_type == "git_connection"
        assert len(symbols.sources) >= 2
        graph = await env.service.get_impact(**args, sha=A, symbol_id=base.id)
        assert any(
            e.kind == "imports" and e.target_id == base.id and e.confidence == "resolved"
            for e in graph.edges
        )
        assert not graph.task_links
        linked = await env.service.link_task(
            project_id=env.project,
            principal=env.principal,
            task_id=env.tasks[0],
            data=GitTaskLinkCreate(connection_id=connections[0].id, kind="commit", commit_sha=A),
        )
        original = GitHubClient.get_commit_details

        async def with_paths(client, *args):
            result = await original(client, *args)
            return result.model_copy(
                update={"changed_paths": ("pkg/api.py", "unrelated.txt"), "files_partial": False}
            )

        with patch.object(GitHubClient, "get_commit_details", with_paths):
            impact = await env.service.get_impact(**args, sha=A, symbol_id=base.id)
        assert [(link.task_id, link.change_id, link.path) for link in impact.task_links] == [
            (env.tasks[0], linked.change_id, "pkg/api.py")
        ]
        assert any(isinstance(proof, GitChangeProof) for proof in impact.sources)
        other = {**args, "connection_id": connections[1].id}
        with pytest.raises(GitSourceNotAvailableError):
            await env.service.get_graph(**other, root_id=base.id)
        result = await env.service.search(
            project_id=env.project,
            principal=env.member,
            sha=A,
            connection_ids=(connections[0].id,),
            query="Base",
        )
        assert result.items[0].symbol_id == base.id
        result = await env.service.search(
            project_id=env.project, principal=env.member, query="pkg/base.py", limit=3
        )
        assert len({hit.connection_id for hit in result.items}) == 3
        directory = await env.service.get_tree(**args, children=True, sha=A)
        assert [(entry.path, entry.type) for entry in directory.items] == [("pkg", "tree")]
        leaves = await env.service.get_tree(**args, children=True, sha=A, prefix="pkg", limit=1)
        assert leaves.total_files == 2 and leaves.next_offset == 1
        source = await env.service.read_file(**args, sha=A, path="pkg/base.py", line_start=2)
        assert source.text == '    "ж"\r\n' and source.line_start == source.line_end == 2

        env.remote.sha = B
        env.remote.add_version(B, {"pkg/renamed.py": b"class Renamed: pass\n"})

        async def new_version():
            page = await env.service.list_snapshots(**args)
            return any(s.commit_sha == B and s.analysis_state == "published" for s in page.items)

        await converge(env, new_version)
        assert (await env.service.list_symbols(**args, sha=A, symbol_id=base.id)).items[
            0
        ].path == "pkg/base.py"
        with pytest.raises(GitSourceNotAvailableError):
            await env.service.list_symbols(**args, sha=B, symbol_id=base.id)
        env.remote.denied.add("app")
        with pytest.raises(GitSourceNotAvailableError):
            await env.service.list_symbols(**args, sha=A)
        assert (await env.service.list_symbols(**other, sha=A, query="Base")).items
        visible = await env.service.list_connections(
            project_id=env.project, principal=env.member, verified=True
        )
        assert {c.id for c in visible.items} == {
            c.id for c in connections[1:]
        } and visible.total == 2
        manifest = await env.service.get_map(project_id=env.project, principal=env.member)
        assert all(c.id != connections[0].id for c in manifest.manifest)


async def test_same_sha_upgrade_retains_published_graph_and_reader_never_queues(engine):
    async with environment(engine) as env:
        env.remote.add_version(A, {"old.py": b"class Old: pass\n"})
        conn = await connect(env)
        await ready(env, conn.id)
        args = dict(project_id=env.project, principal=env.member, connection_id=conn.id)
        async with env.sessions() as db:
            snapshot = await db.scalar(
                select(GitSnapshot).where(GitSnapshot.connection_id == conn.id)
            )
            await db.execute(
                update(GitAnalysis)
                .where(GitAnalysis.snapshot_id == snapshot.id)
                .values(version="previous-parser")
            )
            before = list(
                (
                    await db.scalars(
                        select(GitSyncJob.id).where(GitSyncJob.connection_id == conn.id)
                    )
                ).all()
            )
            await db.commit()
        page = await env.service.list_snapshots(**args)
        assert page.items[0].analyzer_version == "previous-parser"
        assert "analyzer_upgrade_pending" in page.items[0].coverage.reasons
        old = await env.service.list_symbols(**args, query="Old")
        assert old.items and old.analyzer_version == "previous-parser"
        env.remote.add_version(B, {"new.py": b"x=1"})
        env.remote.sha = B
        stale_symbols = await env.service.list_symbols(**args, sha=A)
        assert stale_symbols.items and "source_stale" in stale_symbols.coverage.reasons
        graph = await env.service.get_graph(**args, sha=A, root_id=old.items[0].id)
        assert "source_stale" in graph.coverage.reasons
        snapshots = await env.service.list_snapshots(**args)
        assert "source_stale" in snapshots.items[0].coverage.reasons
        connections = await env.service.list_connections(
            project_id=env.project, principal=env.member, verified=True
        )
        assert connections.items[0].observed_sha == A
        assert "source_stale" in connections.items[0].coverage.reasons
        hits = await env.service.search(project_id=env.project, principal=env.member, sha=A,
                                        connection_ids=(conn.id,), query="Old")
        assert hits.items[0].symbol_id == old.items[0].id
        assert hits.items[0].stale and hits.partial
        async with env.sessions() as db:
            assert before == list(
                (
                    await db.scalars(
                        select(GitSyncJob.id).where(GitSyncJob.connection_id == conn.id)
                    )
                ).all()
            )
        env.remote.sha = A
        assert "source_stale" not in (await env.service.list_symbols(**args, sha=A)).coverage.reasons

        async def updated():
            result = await env.service.list_snapshots(**args)
            return result.items[0].analyzer_version == ANALYZER_VERSION

        await converge(env, updated)
        published = await env.service.list_symbols(**args, query="Old")
        assert (
            published.analyzer_version == ANALYZER_VERSION
            and published.items[0].id != old.items[0].id
        )


async def test_incomplete_direct_subtree_indexes_known_files_without_claiming_complete(engine):
    async with environment(engine) as env:
        env.remote.add_version(A, {"known.py": b"def known(): pass\n"})
        tree_sha, entries = env.remote.versions[A]

        async def truncated(client, owner, repo, sha, *, recursive=True):
            return GitTree(
                sha=tree_sha, entries=tuple(GitTreeEntry(**e) for e in entries), truncated=True
            )

        with patch.object(GitHubClient, "get_tree", truncated):
            conn = await connect(env)

            async def known_file():
                result = await env.service.get_connection(
                    project_id=env.project, connection_id=conn.id, principal=env.principal
                )
                return (
                    result.coverage.indexed_files == 1
                    and "upstream_directory_truncated" in result.coverage.reasons
                )

            await converge(env, known_file)
        args = dict(project_id=env.project, connection_id=conn.id, principal=env.member)
        tree = await env.service.get_tree(**args, sha=A, children=True)
        assert tree.truncated and [item.path for item in tree.items] == ["known.py"]
        snapshot = (await env.service.list_snapshots(**args)).items[0]
        assert not snapshot.complete and snapshot.analysis_state == "absent"


async def test_worker_resumes_truncated_tree_and_blocks_owner_transfer_during_read(engine):
    async with environment(engine) as env:
        env.remote.add_version(A, {"folder/m.py": b"def stable(): pass\n"})
        tree_sha, entries = env.remote.versions[A]
        nested_sha = "e" * 40
        calls = []

        async def trees(client, owner, repo, sha, *, recursive=True):
            calls.append((sha, recursive))
            if recursive:
                return GitTree(sha=sha, entries=(), truncated=True)
            if sha == tree_sha:
                return GitTree(
                    sha=sha,
                    truncated=False,
                    entries=(
                        GitTreeEntry(path="folder", type="tree", mode="040000", sha=nested_sha),
                    ),
                )
            assert sha == nested_sha
            return GitTree(
                sha=sha, truncated=False, entries=(GitTreeEntry(**{**entries[0], "path": "m.py"}),)
            )

        with patch.object(GitHubClient, "get_tree", trees):
            conn = await connect(env)
            await ready(env, conn.id)
        assert (tree_sha, False) in calls and (nested_sha, False) in calls
        args = dict(project_id=env.project, principal=env.member, connection_id=conn.id)
        assert (await env.service.list_symbols(**args, query="stable")).items
        resolve = GitHubClient.resolve_ref

        async def transfer(client, *args):
            result = await resolve(client, *args)
            async with env.sessions() as db:
                await db.execute(
                    update(Project)
                    .where(Project.id == env.project)
                    .values(owner_id=env.member.user_id)
                )
                await db.commit()
            return result

        with (
            patch.object(GitHubClient, "resolve_ref", transfer),
            pytest.raises(GitSourceNotAvailableError),
        ):
            await env.service.list_symbols(**args, sha=A)
