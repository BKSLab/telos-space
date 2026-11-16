"""Проверки синтетических исходных сценариев, не приёмка функций продукта."""

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "aris_context_contract.json"
SHA = re.compile(r"[0-9a-f]{40}\Z")


def validate_fixture(payload: dict[str, Any]) -> list[str]:
    """Сверяет ожидания с материалами и авторитетными переходами самой fixture.

    index_attempt/deliver_event описывают вход будущего адаптера и не исполняют
    индексацию. Проверяется только непротиворечивость заданного ground truth.
    """
    errors: list[str] = []
    coverage: set[str] = set()
    projects, repositories, materials = (
        payload["projects"],
        payload["repositories"],
        payload["sources"],
    )

    def require(condition: bool, rule: str, location: str) -> None:
        if not condition:
            errors.append(f"{location}: {rule}")

    def revision(source: dict, number: int) -> dict:
        return next((v for v in source["versions"] if v["revision"] == number), {})

    def facts(source: dict, number: int | None = None) -> dict:
        return revision(source, source["current_revision"] if number is None else number).get(
            "facts", {}
        )

    for repository_id, repository in repositories.items():
        project = projects.get(repository["project_id"], {})
        require(
            repository_id in project.get("repository_ids", []), "repository binding", repository_id
        )
        require(repository["head_sha"] in repository["commits"], "known head", repository_id)
        for commit, parents in repository["commits"].items():
            require(bool(SHA.fullmatch(commit)), "full commit SHA", repository_id)
            require(
                all(parent in repository["commits"] for parent in parents),
                "known parents",
                repository_id,
            )
    for ref, source in materials.items():
        location = f"material {ref}"
        numbers = [v["revision"] for v in source["versions"]]
        require(len(set(numbers)) == len(numbers), "unique revisions", location)
        require(source["current_revision"] in numbers, "known current revision", location)
        require(
            set(source["acl"]["readers"]) <= set(payload["actors"]), "known ACL actors", location
        )
        require(source["acl"]["revision"] > 0, "ACL revision", location)
        require(source["visibility"] in {"project", "private", "draft"}, "visibility", location)
        require(
            source["project_id"] is None or source["project_id"] in projects,
            "known project",
            location,
        )
        if source["visibility"] != "project":
            require(source["owner_id"] in payload["actors"], "private owner", location)
        for item in source["versions"]:
            require(
                bool(item["text"].strip()) and bool(item["facts"]), "input text and facts", location
            )
            if "repository_id" in source:
                repository = repositories.get(source["repository_id"], {})
                require(
                    repository.get("project_id") == source["project_id"],
                    "source repository project",
                    location,
                )
                require(
                    item["facts"].get("commit_sha") in repository.get("commits", {}),
                    "source repository SHA",
                    location,
                )
            if source["kind"] == "code":
                require(
                    all(item["facts"].get(field) for field in ("path", "symbol", "body")),
                    "code identity",
                    location,
                )
                require(item["facts"].get("body", "") in item["text"], "code excerpt", location)
    for project_id, project in projects.items():
        require(set(project["member_ids"]) <= set(payload["actors"]), "known members", project_id)
        require(
            set(project["repository_ids"])
            == {r for r, value in repositories.items() if value["project_id"] == project_id},
            "complete repository bindings",
            project_id,
        )
        if project["passport_ref"]:
            passport = materials.get(project["passport_ref"], {})
            require(
                passport.get("kind") == "passport" and passport.get("project_id") == project_id,
                "passport ownership",
                project_id,
            )
            require(
                facts(passport).get("task_count") == len(project["task_refs"]),
                "live task count",
                project_id,
            )
        for task in project["task_refs"]:
            require(
                materials.get(task, {}).get("kind") == "task"
                and materials[task]["project_id"] == project_id,
                "task ownership",
                project_id,
            )
    for job_id, job in payload["jobs"].items():
        require(
            bool(revision(materials.get(job["source_ref"], {"versions": []}), job["revision"])),
            "job source revision",
            job_id,
        )

    for case in payload["cases"]:
        location = case["id"]
        request = case["request"]
        require(
            request["actor_id"] in payload["actors"] and bool(request["question"].strip()),
            "request actor and question",
            location,
        )
        require(
            request["channel"] in {"shared", "private_project", "global"},
            "request channel",
            location,
        )
        require(
            all(ref in materials for ref in case["materials"]), "known input references", location
        )
        if any(ref not in materials for ref in case["materials"]):
            continue
        sources = {ref: deepcopy(materials[ref]) for ref in case["materials"]}
        repos = deepcopy(repositories)
        pending: dict[str, list[dict]] = {}
        rolled_back: set[str] = set()
        known_heads = {
            (key, value["generation"]): value["head_sha"] for key, value in repos.items()
        }
        deliveries: list[tuple[str, int, str]] = []
        delivered_ids: dict[str, tuple] = {}
        visited_commits: dict[str, list[str]] = {}
        selected = (
            request["selected_project_ids"]
            if request["channel"] == "global"
            else [request["project_id"]]
        )
        require(
            all(
                project in projects and request["actor_id"] in projects[project]["member_ids"]
                for project in selected
            ),
            "request project membership",
            location,
        )
        for project in selected:
            if project in projects:
                coverage.add(f"repositories:{len(projects[project]['repository_ids'])}")

        for item in case["events"]:
            kind, ref = item["kind"], item.get("source_ref")
            source = sources.get(ref)
            require(item["phase"] in {"before_read", "before_publish"}, "event phase", location)
            if ref:
                require(source is not None, "event input reference", location)
                if source is None:
                    continue
            if "revision" in item:
                require(bool(revision(source, item["revision"])), "event source revision", location)
            if "job_id" in item:
                job = payload["jobs"].get(item["job_id"])
                require(
                    job is not None
                    and (
                        not ref or (job["source_ref"], job["revision"]) == (ref, item["revision"])
                    ),
                    "event job binding",
                    location,
                )
            if kind == "set_revision":
                require(
                    item["revision"] > source["current_revision"], "monotonic revision", location
                )
                if source["kind"] == "deployment":
                    history = visited_commits.setdefault(ref, [facts(source)["commit_sha"]])
                    commit = facts(source, item["revision"]).get("commit_sha")
                    if commit in history and commit != history[-1]:
                        coverage.add("deployment rollback")
                    history.append(commit)
                source["current_revision"] = item["revision"]
            elif kind == "revoke_access":
                require(
                    item["actor_id"] in source["acl"]["readers"]
                    and item["acl_revision"] > source["acl"]["revision"],
                    "ACL transition",
                    location,
                )
                source["acl"]["readers"] = [
                    actor for actor in source["acl"]["readers"] if actor != item["actor_id"]
                ]
                source["acl"]["revision"] = item["acl_revision"]
            elif kind == "ref_update":
                repository = repos[item["repository_id"]]
                require(
                    item["previous_sha"] == repository["head_sha"]
                    and item["generation"] == repository["generation"] + 1,
                    "causal ref generation",
                    location,
                )
                parents, seen = [item["commit_sha"]], set()
                while parents:
                    commit = parents.pop()
                    if commit not in seen:
                        seen.add(commit)
                        parents.extend(repository["commits"].get(commit, []))
                require(item["commit_sha"] in repository["commits"], "known new head", location)
                require(
                    item["force"] or item["previous_sha"] in seen,
                    "non-ancestor requires force",
                    location,
                )
                if item["force"] and item["previous_sha"] not in seen:
                    coverage.add("force push")
                repository.update(head_sha=item["commit_sha"], generation=item["generation"])
                known_heads[item["repository_id"], item["generation"]] = item["commit_sha"]
            elif kind == "deliver_event":
                value = (item["repository_id"], item["generation"], item["commit_sha"])
                require(known_heads.get(value[:2]) == value[2], "delivered ref identity", location)
                require(
                    delivered_ids.get(item["event_id"], value) == value,
                    "duplicate event identity",
                    location,
                )
                delivered_ids[item["event_id"]] = value
                deliveries.append(value)
            elif kind == "transaction_write":
                pending.setdefault(item["transaction_id"], []).append(item)
            elif kind == "transaction_rollback":
                require(
                    bool(pending.pop(item["transaction_id"], [])),
                    "rollback has domain write",
                    location,
                )
                rolled_back.add(item["transaction_id"])
                coverage.add("transaction rollback")
            else:
                require(
                    kind in {"checkpoint", "crash", "restart", "index_attempt", "index_failure"},
                    "known event kind",
                    location,
                )
        require(not pending, "no uncommitted scenario transaction", location)
        if (
            deliveries
            and len(set(deliveries)) < len(deliveries)
            and any(a[0] == b[0] and a[1] > b[1] for a, b in zip(deliveries, deliveries[1:], strict=False))
        ):
            coverage.add("duplicate and reordered events")

        def denial(
            ref: str,
            number: int,
            *,
            sources=sources,
            request=request,
            selected=selected,
            repos=repos,
        ) -> str | None:
            source = sources[ref]
            if number != source["current_revision"]:
                return "not_current_revision"
            if source["deleted"]:
                return "deleted"
            if source["visibility"] == "draft":
                return "draft"
            if request["actor_id"] not in source["acl"]["readers"]:
                return "acl"
            if source["visibility"] == "private" and source["owner_id"] != request["actor_id"]:
                return "owner"
            project = source["project_id"]
            if (project is None and request["channel"] != "global") or (
                project is not None
                and (
                    project not in selected
                    or request["actor_id"] not in projects[project]["member_ids"]
                )
            ):
                return "scope"
            if (
                source["visibility"] == "private"
                and source["conversation_id"]
                and (
                    source["conversation_id"] != request["conversation_id"]
                    or request["channel"] == "shared"
                )
            ):
                return "private_conversation"
            if (
                source["kind"] == "code"
                and facts(source).get("commit_sha") != repos[source["repository_id"]]["head_sha"]
            ):
                return "head_mismatch"
            return None

        accepted: dict[tuple[str, str], list[Any]] = {}
        for assertion in case["expected"]["facts"]:
            ref, number, field = assertion["source_ref"], assertion["revision"], assertion["field"]
            require(ref in sources, "expected input reference", location)
            if ref not in sources:
                continue
            require(
                denial(ref, number) is None, "expected evidence accessible and current", location
            )
            require(
                field in facts(sources[ref], number)
                and facts(sources[ref], number).get(field) == assertion["value"],
                "expected fact matches material",
                location,
            )
            accepted.setdefault((sources[ref]["subject"], field), []).append(assertion["value"])
        for assertion in case["expected"]["unknown"]:
            values, visible = [], []
            for ref, source in sources.items():
                value = facts(source).get(assertion["field"])
                if source["subject"] == assertion["subject"] and value not in (None, "", []):
                    values.append(value)
                    if denial(ref, source["current_revision"]) is None:
                        visible.append(value)
            reason = assertion["reason"]
            matches = {
                "missing": not values,
                "inaccessible": bool(values) and not visible,
                "conflicting": len({json.dumps(value, sort_keys=True) for value in visible}) > 1,
            }
            require(matches.get(reason, False), "unknown reason follows materials", location)
            if reason == "conflicting" and matches[reason]:
                coverage.add("contradictory current sources")
        for assertion in case["forbidden"]["sources"]:
            ref, number = assertion["source_ref"], assertion["revision"]
            require(
                ref in sources and bool(revision(sources[ref], number)),
                "forbidden input revision",
                location,
            )
            if ref in sources:
                require(
                    denial(ref, number) == assertion["reason"],
                    "forbidden reason follows scope/version/ACL",
                    location,
                )
                if denial(ref, number) == assertion["reason"]:
                    coverage.add(assertion["reason"])
        for claim in case["forbidden"]["claims"]:
            key = (claim["subject"], claim["field"])
            unknown = any(
                (item["subject"], item["field"]) == key for item in case["expected"]["unknown"]
            )
            require(
                unknown or (key in accepted and claim["value"] not in accepted[key]),
                "forbidden claim has contrary evidence or explicit unknown",
                location,
            )
        for expected in case["expected"]["repository_heads"]:
            repository = repos.get(expected["repository_id"], {})
            require(
                (repository.get("ref"), repository.get("head_sha"), repository.get("generation"))
                == (expected["ref"], expected["commit_sha"], expected["generation"]),
                "expected current ref/SHA/generation",
                location,
            )
        for expected in case["expected"]["manifest"]:
            source = sources.get(expected["source_ref"])
            require(
                source is not None
                and source["current_revision"] == expected["revision"]
                and denial(expected["source_ref"], expected["revision"]) is None,
                "manifest targets current accessible source",
                location,
            )
        require(
            set(case["expected"]["absent_outbox_transactions"]) <= rolled_back,
            "outbox absence follows rollback",
            location,
        )

        allowed = {
            ref: source
            for ref, source in sources.items()
            if denial(ref, source["current_revision"]) is None
        }
        if case["expected"]["facts"] and case["forbidden"]["sources"]:
            coverage.add(f"isolation:{request['channel']}")
        for ref, source in allowed.items():
            value = facts(source)
            if source["kind"] == "passport" and value["task_count"] == 0:
                coverage.add(
                    "empty passport" if not value["goal"] else "filled passport without tasks"
                )
            if source["kind"] == "task":
                links = value["pull_request_refs"]
                require(
                    all(
                        link in sources
                        and sources[link]["kind"] == "pull_request"
                        and facts(sources[link]).get("task_ref") == ref
                        for link in links
                    ),
                    "reciprocal task PR links",
                    location,
                )
                linked = [sources[link] for link in links if link in sources]
                if (
                    len({item.get("repository_id") for item in linked}) >= 2
                    and {facts(item).get("state") for item in linked} >= {"open", "merged"}
                    and value["status"] != "completed"
                ):
                    coverage.add("one task two repository PRs")
        code_sources = [source for source in allowed.values() if source["kind"] == "code"]
        if any(
            a["repository_id"] != b["repository_id"]
            and facts(a)["commit_sha"] != facts(b)["commit_sha"]
            and (facts(a)["path"], facts(a)["symbol"]) == (facts(b)["path"], facts(b)["symbol"])
            for a in code_sources
            for b in code_sources
        ):
            coverage.add("same path symbol different repository SHA")
        deploys = [source for source in allowed.values() if source["kind"] == "deployment"]
        environments = [
            (source["repository_id"], facts(source).get("environment")) for source in deploys
        ]
        require(
            len(set(environments)) == len(environments),
            "unique active repository environment",
            location,
        )
        if (
            {environment for _, environment in environments} >= {"staging", "production"}
            and len({facts(source)["commit_sha"] for source in deploys}) > 1
            and any(
                source["kind"] == "ci"
                and all(
                    facts(source)["commit_sha"] != facts(deploy)["commit_sha"] for deploy in deploys
                )
                for source in allowed.values()
            )
        ):
            coverage.add("CI SHA and deployment environments differ")
        for item in case["events"]:
            if (
                item["kind"] == "index_attempt"
                and sources[item["source_ref"]]["current_revision"] != item["revision"]
            ):
                require(
                    any(
                        value["source_ref"] == item["source_ref"]
                        and value["revision"] == sources[item["source_ref"]]["current_revision"]
                        for value in case["expected"]["manifest"]
                    ),
                    "race expects current manifest",
                    location,
                )
                coverage.add("late old index write")
            if item["kind"] == "restart":
                trace = [value for value in case["events"] if value.get("job_id") == item["job_id"]]
                kinds = [value["kind"] for value in trace]
                require(
                    kinds == ["checkpoint", "crash", "restart", "index_attempt"]
                    and trace[0].get("stage") == "extracted"
                    and trace[0].get("worker_id") == trace[1].get("worker_id") != item["worker_id"],
                    "restart resumes same checkpointed job",
                    location,
                )
                coverage.add("checkpoint crash restart")
            if item["kind"] == "index_failure":
                coverage.add("index failure before retry")

    required = {
        "empty passport",
        "filled passport without tasks",
        "contradictory current sources",
        "not_current_revision",
        "deleted",
        "acl",
        "draft",
        "private_conversation",
        "owner",
        "scope",
        "isolation:shared",
        "isolation:private_project",
        "isolation:global",
        "repositories:0",
        "repositories:1",
        "repositories:3",
        "one task two repository PRs",
        "same path symbol different repository SHA",
        "CI SHA and deployment environments differ",
        "late old index write",
        "checkpoint crash restart",
        "duplicate and reordered events",
        "force push",
        "deployment rollback",
        "transaction rollback",
        "index failure before retry",
    }
    require(
        required <= coverage, f"missing semantic coverage: {sorted(required - coverage)}", "fixture"
    )
    return errors


def test_context_acceptance_materials_and_expectations_are_consistent() -> None:
    errors = validate_fixture(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert not errors, "\n".join(errors)


def test_context_fixture_rejects_semantic_mutations() -> None:
    """Нарушения evidence/ACL/идентичности и причинности не проходят валидацию."""
    original = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def scenario(data: dict, name: str) -> dict:
        return next(case for case in data["cases"] if case["id"] == name)

    def set_expected(data: dict, name: str, field: str, value: Any) -> None:
        assertion = next(
            item for item in scenario(data, name)["expected"]["facts"] if item["field"] == field
        )
        assertion["value"] = value

    mutations = [
        (
            "invented passport",
            lambda d: set_expected(d, "passport-filled-zero-tasks", "goal", "Выдуманная цель"),
        ),
        (
            "false unknown",
            lambda d: d["sources"]["project:p0"]["versions"][0]["facts"].update(
                goal="Известная цель"
            ),
        ),
        (
            "fake conflict",
            lambda d: d["sources"]["document:conflict-b"]["versions"][0]["facts"].update(
                release_date="2026-11-20"
            ),
        ),
        (
            "revoked evidence granted",
            lambda d: scenario(d, "sources-old-deleted-revoked")["events"].clear(),
        ),
        (
            "private evidence in shared",
            lambda d: scenario(d, "shared-isolation")["expected"]["facts"].append(
                deepcopy(scenario(d, "private-project-exact-dialog")["expected"]["facts"][1])
            ),
        ),
        (
            "draft becomes shared",
            lambda d: d["sources"]["message:draft"].update(visibility="project"),
        ),
        ("wrong repository project", lambda d: d["repositories"]["r32"].update(project_id="p1")),
        (
            "cross repository SHA",
            lambda d: d["sources"]["code:r32"]["versions"][0]["facts"].update(commit_sha="a" * 40),
        ),
        (
            "lost second PR",
            lambda d: d["sources"]["task:t1"]["versions"][0]["facts"]["pull_request_refs"].pop(),
        ),
        (
            "staging mistaken for production",
            lambda d: d["sources"]["deploy:r1:staging"]["versions"][0]["facts"].update(
                environment="production"
            ),
        ),
        (
            "CI SHA mistaken for deployment",
            lambda d: next(
                item
                for item in scenario(d, "ci-deploy-sha-environment")["expected"]["facts"]
                if item["source_ref"] == "deploy:r1:production" and item["field"] == "commit_sha"
            ).update(value="c" * 40),
        ),
        (
            "old race manifest",
            lambda d: scenario(d, "late-old-index-write")["expected"]["manifest"][0].update(
                revision=1
            ),
        ),
        (
            "restart without checkpoint",
            lambda d: scenario(d, "crash-checkpoint-restart")["events"].pop(0),
        ),
        (
            "reordered event becomes head",
            lambda d: scenario(d, "reorder-duplicate-force-push")["expected"][
                "repository_heads"
            ][0].update(commit_sha="b" * 40, generation=2),
        ),
        (
            "non ancestor without force",
            lambda d: scenario(d, "reorder-duplicate-force-push")["events"][2].update(
                force=False
            ),
        ),
        (
            "rollback still reports new deploy",
            lambda d: set_expected(d, "deploy-and-transaction-rollback", "commit_sha", "b" * 40),
        ),
        (
            "uncommitted outbox",
            lambda d: scenario(d, "deploy-and-transaction-rollback")["events"].pop(3),
        ),
    ]
    undetected = []
    for name, mutate in mutations:
        changed = deepcopy(original)
        mutate(changed)
        if not validate_fixture(changed):
            undetected.append(name)
    assert not undetected, f"Undetected invalid scenarios: {undetected}"
