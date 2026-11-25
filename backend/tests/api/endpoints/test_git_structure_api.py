"""Валидация новых адресных Git-чтений до сервисного исполнения."""

import httpx

from src.exceptions.git_sources import GitSourceNotAvailableError, GitSourceScopeRequiredError
from src.schemas.git_sources import GitGraph, GitImpact, GitSnapshotPage, GitSymbolPage
from tests.api.endpoints.test_git_sources_api import transport


async def test_structure_routes_validate_bounds_forward_scope_and_hide_private_proofs(current_principal):
    app, service = transport(current_principal)
    common = dict(connection_id=3, commit_sha="a" * 40, analyzer_version=None, coverage={})
    service.list_snapshots.return_value = GitSnapshotPage(items=[], next_offset=None)
    service.list_symbols.return_value = GitSymbolPage(**common, items=[], next_offset=None)
    service.get_graph.return_value = GitGraph(**common, nodes=[], edges=[], truncated=False)
    service.get_impact.return_value = GitImpact(**common, nodes=[], edges=[], truncated=False, task_links=[])
    routes = {
        "snapshots": (service.list_snapshots, ""),
        "symbols": (service.list_symbols, ""),
        "graph": (service.get_graph, ""),
        "impact": (service.get_impact, "symbol_id=1"),
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for route, (method, query) in routes.items():
            url = f"/api/v1/projects/7/git/connections/3/{route}?{query}"
            response = await client.get(url)
            assert response.status_code == 200
            assert "sources" not in response.json() or response.json()["sources"] == []
            args = method.await_args.kwargs
            assert args["principal"] == current_principal and args["project_id"] == 7 and args["connection_id"] == 3
            count = method.await_count
            for bad in ("limit=0", "limit=101"):
                assert (await client.get(url + "&" + bad)).status_code == 422
            assert method.await_count == count
            for error, status in ((GitSourceNotAvailableError(), 404), (GitSourceScopeRequiredError(), 403)):
                method.side_effect = error
                assert (await client.get(url)).status_code == status
            method.side_effect = None
        root = "/api/v1/projects/7/git/connections/3/"
        for query in ("depth=0", "depth=5", "direction=sideways", "relation=runtime", "root_id=0"):
            assert (await client.get(root + "graph?" + query)).status_code == 422
        assert (await client.get(root + "impact")).status_code == 422
        assert (await client.get(root + "symbols?symbol_id=0")).status_code == 422
