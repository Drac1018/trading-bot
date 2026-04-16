from __future__ import annotations

from trading_mvp.main import app


def test_removed_legacy_api_routes_are_not_registered() -> None:
    route_paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/backlog" not in route_paths
    assert "/api/backlog/{backlog_id}" not in route_paths
    assert "/api/backlog/requests" not in route_paths
    assert "/api/backlog/applied" not in route_paths
