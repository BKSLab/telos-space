"""Измеряемый retrieval на синтетическом корпусе PostgreSQL, без живого Git/LLM."""

import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import text

from evals.knowledge_retrieval.runner import RetrievalExample, calculate_metrics
from src.repositories.git_sources import GitSourcesRepository
from src.services.git_structure import ANALYZER_VERSION
from tests.integration.repositories.test_git_sources import A, connect, environment


async def test_large_index_exact_retrieval_and_connection_pagination(engine):
    async with environment(engine) as env:
        empty = await env.service.list_connections(project_id=env.project, principal=env.principal, verified=True)
        assert empty.items == []
        sources = [await connect(env, ("app", "api", "docs")[index % 3]) for index in range(21)]
        seen, offset = [], 0
        while True:
            page = await env.service.list_connections(project_id=env.project, principal=env.principal, verified=True, offset=offset, limit=20)
            seen.extend(item.id for item in page.items)
            if page.next_offset is None:
                break
            offset = page.next_offset
        assert seen == [source.id for source in sources]
        for source in sources[:3]:
            async with env.service._open(project_id=env.project, connection_id=source.id, principal=env.principal) as (row, _):
                snapshot = await env.service._snapshot(row)
            async with env.sessions() as db:
                # Bulk fixture builds a real searchable index; worker lifecycle is
                # tested separately, this test measures SQL retrieval and paging.
                count = 1102 if source.id == sources[0].id else 1
                await db.execute(text("""
                    INSERT INTO git_files(snapshot_id,connection_id,path,blob_sha,size,state,text,text_hash)
                    SELECT :snapshot,:connection,'src/file' || n || '.py',repeat('d',40),24,'ready',
                           'def login(): return 1',repeat('e',64) FROM generate_series(1,:count) n
                """), {"snapshot": snapshot["id"], "connection": source.id, "count": count})
                await db.execute(text("INSERT INTO git_analyses(snapshot_id,version,state) VALUES (:snapshot,:version,'published')"), {"snapshot": snapshot["id"], "version": ANALYZER_VERSION})
                await db.execute(text("""
                    INSERT INTO git_symbols(analysis_id,snapshot_id,path,key_hash,name,qualified_name,search_name,kind,line_start,line_end,"offset",end_offset)
                    SELECT a.id,f.snapshot_id,f.path,repeat('f',64),'login','login','login','function',1,1,0,21
                    FROM git_files f JOIN git_analyses a ON a.snapshot_id=f.snapshot_id WHERE f.snapshot_id=:snapshot
                """), {"snapshot": snapshot["id"]})
                await db.execute(text("""
                    INSERT INTO git_fragments(file_id,ordinal,"offset",line_start,line_end,text,search_vector)
                    SELECT id,0,0,1,1,text,to_tsvector('simple',text) FROM git_files WHERE snapshot_id=:snapshot
                """), {"snapshot": snapshot["id"]})
                await db.execute(text("UPDATE git_connections SET indexed_sha=:sha WHERE id=:connection"), {"sha": A, "connection": source.id})
                await db.commit()
        predictions, baseline, durations, examples = {}, {}, [], []
        async with env.sessions() as db:
            repo = GitSourcesRepository(db)
            for number, source in enumerate(sources[:3]):
                question = "src/file1102.py" if number == 0 else "login"
                example = RetrievalExample(str(number), question, frozenset({f"{source.id}:{A}:src/file{1102 if number == 0 else 1}.py:1:1"}))
                examples.append(example)
                start = time.perf_counter()
                result = await repo.search(project_id=env.project, connection_ids=(source.id,), query=question, limit=10, sha=A)
                durations.append((time.perf_counter() - start) * 1000)
                predictions[str(number)] = [f"{item['connection_id']}:{item['commit_sha']}:{item['path']}:{item['line_start']}:{item['line_start'] + item['excerpt'].rstrip(chr(10)).count(chr(10))}" for item in result]
                old = (await db.execute(text("""
                    SELECT f.path FROM git_fragments r JOIN git_files f ON f.id=r.file_id
                    WHERE f.connection_id=:connection AND r.search_vector @@ plainto_tsquery('simple',:query)
                    ORDER BY f.path LIMIT 10
                """), {"connection": source.id, "query": question})).scalars().all()
                baseline[str(number)] = [f"{source.id}:{A}:{path}:1:1" for path in old]
        metrics = calculate_metrics(examples, predictions)
        assert metrics.recall_at_k["10"] == 1 and metrics.mrr == 1
        artifact = {"scope": "realPG SQL; synthetic fixture, controlled provider; not live Git/LLM",
                    "files": 1104, "connections": 21, "examples": 3,
                    "fts_baseline": asdict(calculate_metrics(examples, baseline)),
                    "recall_at_10": metrics.recall_at_k["10"], "mrr": metrics.mrr, "query_ms": durations}
        Path(tempfile.gettempdir(), "telos41-retrieval-scale.json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")
