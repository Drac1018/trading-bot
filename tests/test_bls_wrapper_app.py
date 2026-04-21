from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from trading_mvp import bls_wrapper_app


def test_bls_wrapper_returns_cpi_metrics(monkeypatch) -> None:
    def _fake_fetch(*, config, series_ids, start_year, end_year):
        del config, start_year, end_year
        assert series_ids == ["CUUR0000SA0", "CUSR0000SA0"]
        return {
            "status": "REQUEST_SUCCEEDED",
            "Results": {
                "series": [
                    {
                        "seriesID": "CUUR0000SA0",
                        "data": [
                            {"year": "2026", "period": "M03", "value": "320.0"},
                            {"year": "2026", "period": "M02", "value": "319.0"},
                            {"year": "2025", "period": "M03", "value": "310.0"},
                            {"year": "2025", "period": "M02", "value": "309.0"},
                        ],
                    },
                    {
                        "seriesID": "CUSR0000SA0",
                        "data": [
                            {"year": "2026", "period": "M03", "value": "300.0"},
                            {"year": "2026", "period": "M02", "value": "299.0"},
                            {"year": "2026", "period": "M01", "value": "298.0"},
                        ],
                    },
                ]
            },
        }

    monkeypatch.setattr(bls_wrapper_app, "_fetch_bls_series_payload", _fake_fetch)

    payload = bls_wrapper_app.build_release_enrichment(
        config=bls_wrapper_app.get_wrapper_config(),
        event_name="Consumer Price Index",
        event_key="cpi",
        event_at=datetime(2026, 4, 10, 12, 30, tzinfo=UTC),
    )

    assert payload["event_key"] == "cpi"
    assert payload["headline_metric"] == "cpi_yoy_pct"
    assert payload["reference_period"] == "2026-03"
    assert payload["series_id"] == "CUUR0000SA0"
    assert payload["series_ids"] == ["CUUR0000SA0", "CUSR0000SA0"]
    assert payload["actual"] == 3.225806
    assert payload["prior"] == 3.236246
    assert payload["mom_actual"] == 0.334448
    assert payload["mom_prior"] == 0.33557


def test_bls_wrapper_returns_employment_metrics(monkeypatch) -> None:
    def _fake_fetch(*, config, series_ids, start_year, end_year):
        del config, start_year, end_year
        assert series_ids == ["CES0000000001", "LNS14000000", "CES0500000003"]
        return {
            "status": "REQUEST_SUCCEEDED",
            "Results": {
                "series": [
                    {
                        "seriesID": "CES0000000001",
                        "data": [
                            {"year": "2026", "period": "M04", "value": "158400"},
                            {"year": "2026", "period": "M03", "value": "158222"},
                            {"year": "2026", "period": "M02", "value": "158070"},
                        ],
                    },
                    {
                        "seriesID": "LNS14000000",
                        "data": [
                            {"year": "2026", "period": "M04", "value": "4.2"},
                            {"year": "2026", "period": "M03", "value": "4.1"},
                        ],
                    },
                    {
                        "seriesID": "CES0500000003",
                        "data": [
                            {"year": "2026", "period": "M04", "value": "36.22"},
                            {"year": "2026", "period": "M03", "value": "36.11"},
                            {"year": "2026", "period": "M02", "value": "35.99"},
                        ],
                    },
                ]
            },
        }

    monkeypatch.setattr(bls_wrapper_app, "_fetch_bls_series_payload", _fake_fetch)

    payload = bls_wrapper_app.build_release_enrichment(
        config=bls_wrapper_app.get_wrapper_config(),
        event_name="Employment Situation",
        event_key="employment_situation",
        event_at=datetime(2026, 5, 8, 12, 30, tzinfo=UTC),
    )

    assert payload["event_key"] == "employment_situation"
    assert payload["headline_metric"] == "nonfarm_payrolls_change_k"
    assert payload["reference_period"] == "2026-04"
    assert payload["actual"] == 178.0
    assert payload["prior"] == 152.0
    assert payload["payrolls_actual_k"] == 178.0
    assert payload["payrolls_prior_k"] == 152.0
    assert payload["unemployment_rate_actual"] == 4.2
    assert payload["unemployment_rate_prior"] == 4.1
    assert payload["avg_hourly_earnings_mom_actual"] == 0.304625
    assert payload["avg_hourly_earnings_mom_prior"] == 0.333426


def test_bls_wrapper_endpoint_returns_empty_for_unsupported_event() -> None:
    with TestClient(bls_wrapper_app.app) as client:
        response = client.get(
            "/bls/releases",
            params={
                "event_name": "Retail Sales",
                "event_key": "retail_sales",
                "event_at": "2026-04-15T12:30:00Z",
            },
        )

    assert response.status_code == 200
    assert response.json() == {}


def test_bls_wrapper_root_returns_help_page() -> None:
    with TestClient(bls_wrapper_app.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Trading MVP BLS Wrapper" in response.text
    assert "/docs" in response.text


def test_bls_wrapper_release_path_returns_help_page_for_html_request() -> None:
    with TestClient(bls_wrapper_app.app) as client:
        response = client.get("/bls/releases", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert "실제 API 호출은 query를 포함해야 합니다." in response.text
    assert "event_at" in response.text


def test_bls_wrapper_release_path_keeps_422_for_json_request() -> None:
    with TestClient(bls_wrapper_app.app) as client:
        response = client.get("/bls/releases", headers={"accept": "application/json"})

    assert response.status_code == 422
    assert response.json()["detail"] == "event_at and either event_key or event_name are required."
