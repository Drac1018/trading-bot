from __future__ import annotations

from fastapi.testclient import TestClient

from trading_mvp.main import app


def test_removed_legacy_api_routes_are_not_registered() -> None:
    route_paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/backlog" not in route_paths
    assert "/api/backlog/{backlog_id}" not in route_paths
    assert "/api/backlog/requests" not in route_paths
    assert "/api/backlog/applied" not in route_paths


def test_non_live_execution_surfaces_are_hard_disabled() -> None:
    with TestClient(app) as client:
        for path in ("/api/system/seed", "/api/replay/run", "/api/replay/validation"):
            response = client.post(path)
            assert response.status_code == 410
            payload = response.json()["detail"]
            assert payload["code"] == "NON_LIVE_SURFACE_DISABLED"
            assert "hard-disabled" in payload["message"]
