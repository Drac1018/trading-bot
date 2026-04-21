from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse


@dataclass(frozen=True, slots=True)
class WrapperServiceConfig:
    listen_host: str
    listen_port: int
    route_path: str
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class WrapperBLSConfig:
    base_url: str
    registration_key_env: str
    catalog: bool


@dataclass(frozen=True, slots=True)
class WrapperSeriesConfig:
    series_id: str
    series_title: str | None
    transform: str
    used_for: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WrapperEventConfig:
    key: str
    event_names: tuple[str, ...]
    headline_metric: str | None
    actual_unit: str | None
    reference_period_mode: str
    release_lag_months: int
    series: tuple[WrapperSeriesConfig, ...]


@dataclass(frozen=True, slots=True)
class WrapperConfig:
    service: WrapperServiceConfig
    bls: WrapperBLSConfig
    events: tuple[WrapperEventConfig, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_config_path() -> Path:
    return _repo_root() / "infra" / "bls-wrapper.example.toml"


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_event_key(value: object) -> str:
    text = _normalize_text(value).lower()
    return text.replace("-", "_").replace(" ", "_")


def _as_string_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    normalized: list[str] = []
    for item in values:
        text = _normalize_text(item)
        if text and text not in normalized:
            normalized.append(text)
    return tuple(normalized)


def _parse_series_config(raw_series: Mapping[str, object]) -> tuple[WrapperSeriesConfig, ...]:
    series: list[WrapperSeriesConfig] = []
    for raw_item in raw_series.values():
        if not isinstance(raw_item, Mapping):
            continue
        series_id = _normalize_text(raw_item.get("series_id"))
        transform = _normalize_event_key(raw_item.get("transform"))
        used_for = _as_string_tuple(raw_item.get("used_for"))
        if not series_id or not transform or not used_for:
            continue
        series.append(
            WrapperSeriesConfig(
                series_id=series_id,
                series_title=_normalize_text(raw_item.get("series_title")) or None,
                transform=transform,
                used_for=used_for,
            )
        )
    return tuple(series)


def _parse_event_config(raw_events: Mapping[str, object]) -> tuple[WrapperEventConfig, ...]:
    events: list[WrapperEventConfig] = []
    for raw_key, raw_item in raw_events.items():
        if not isinstance(raw_item, Mapping):
            continue
        series = _parse_series_config(raw_item.get("series") if isinstance(raw_item.get("series"), Mapping) else {})
        if not series:
            continue
        key = _normalize_event_key(raw_key)
        if not key:
            continue
        events.append(
            WrapperEventConfig(
                key=key,
                event_names=_as_string_tuple(raw_item.get("event_names")),
                headline_metric=_normalize_text(raw_item.get("headline_metric")) or None,
                actual_unit=_normalize_text(raw_item.get("actual_unit")) or None,
                reference_period_mode=_normalize_event_key(raw_item.get("reference_period_mode")) or "monthly",
                release_lag_months=max(int(raw_item.get("release_lag_months") or 1), 0),
                series=series,
            )
        )
    return tuple(events)


def _load_config(path: Path) -> WrapperConfig:
    with path.open("rb") as file:
        raw = tomllib.load(file)
    service_raw = raw.get("service") if isinstance(raw.get("service"), Mapping) else {}
    bls_raw = raw.get("bls") if isinstance(raw.get("bls"), Mapping) else {}
    events_raw = raw.get("events") if isinstance(raw.get("events"), Mapping) else {}
    return WrapperConfig(
        service=WrapperServiceConfig(
            listen_host=_normalize_text(service_raw.get("listen_host")) or "0.0.0.0",
            listen_port=int(service_raw.get("listen_port") or 8091),
            route_path=_normalize_text(service_raw.get("route_path")) or "/bls/releases",
            timeout_seconds=max(float(service_raw.get("timeout_seconds") or 10.0), 1.0),
        ),
        bls=WrapperBLSConfig(
            base_url=_normalize_text(bls_raw.get("base_url")) or "https://api.bls.gov/publicAPI/v2/timeseries/data/",
            registration_key_env=_normalize_text(bls_raw.get("registration_key_env")) or "BLS_API_KEY",
            catalog=bool(bls_raw.get("catalog", False)),
        ),
        events=_parse_event_config(events_raw),
    )


@lru_cache(maxsize=1)
def get_wrapper_config() -> WrapperConfig:
    config_path = Path(
        os.getenv("TRADING_BLS_WRAPPER_CONFIG") or _default_config_path()
    )
    return _load_config(config_path)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _month_start(value: datetime) -> datetime:
    normalized = _ensure_utc(value)
    return datetime(normalized.year, normalized.month, 1, tzinfo=UTC)


def _shift_month(value: datetime, months: int) -> datetime:
    year = value.year + ((value.month - 1 + months) // 12)
    month = ((value.month - 1 + months) % 12) + 1
    return datetime(year, month, 1, tzinfo=UTC)


def _period_key(value: datetime) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _required_periods(transform: str, target_month: datetime) -> tuple[datetime, ...]:
    if transform == "latest_value":
        return (target_month, _shift_month(target_month, -1))
    if transform in {"mom_pct", "mom_abs_diff"}:
        return (
            target_month,
            _shift_month(target_month, -1),
            _shift_month(target_month, -2),
        )
    if transform == "yoy_pct":
        return (
            target_month,
            _shift_month(target_month, -1),
            _shift_month(target_month, -12),
            _shift_month(target_month, -13),
        )
    raise ValueError(f"Unsupported transform: {transform}")


def _coerce_numeric(value: object) -> float | None:
    text = _normalize_text(value).replace(",", "")
    if not text or text in {"--", "NA", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _extract_series_rows(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    status = _normalize_text(payload.get("status")).upper()
    if status and status != "REQUEST_SUCCEEDED":
        raise HTTPException(status_code=502, detail="BLS upstream returned an error status.")
    results = payload.get("Results")
    series_items: list[Mapping[str, object]] = []
    if isinstance(results, Mapping):
        raw_series = results.get("series")
        if isinstance(raw_series, Sequence) and not isinstance(raw_series, (str, bytes)):
            series_items.extend(item for item in raw_series if isinstance(item, Mapping))
    elif isinstance(results, Sequence) and not isinstance(results, (str, bytes)):
        for item in results:
            if not isinstance(item, Mapping):
                continue
            raw_series = item.get("series")
            if isinstance(raw_series, Sequence) and not isinstance(raw_series, (str, bytes)):
                series_items.extend(series for series in raw_series if isinstance(series, Mapping))
    normalized: dict[str, dict[str, object]] = {}
    for item in series_items:
        series_id = _normalize_text(item.get("seriesID"))
        if not series_id:
            continue
        normalized[series_id] = dict(item)
    return normalized


def _series_observations(series_row: Mapping[str, object]) -> dict[str, float]:
    raw_data = series_row.get("data")
    if not isinstance(raw_data, Sequence) or isinstance(raw_data, (str, bytes)):
        return {}
    observations: dict[str, float] = {}
    for item in raw_data:
        if not isinstance(item, Mapping):
            continue
        year_text = _normalize_text(item.get("year"))
        period_text = _normalize_text(item.get("period")).upper()
        if not year_text or not period_text.startswith("M") or len(period_text) != 3:
            continue
        try:
            month = int(period_text[1:])
        except ValueError:
            continue
        if month < 1 or month > 12:
            continue
        numeric_value = _coerce_numeric(item.get("value"))
        if numeric_value is None:
            continue
        observations[f"{year_text}-{month:02d}"] = numeric_value
    return observations


def _safe_pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0.0}:
        return None
    return ((current / previous) - 1.0) * 100.0


def _compute_metric_pair(
    *,
    transform: str,
    observations: Mapping[str, float],
    target_month: datetime,
) -> tuple[float | None, float | None]:
    target = observations.get(_period_key(target_month))
    prev_1 = observations.get(_period_key(_shift_month(target_month, -1)))
    if transform == "latest_value":
        return target, prev_1
    if transform == "mom_abs_diff":
        prev_2 = observations.get(_period_key(_shift_month(target_month, -2)))
        actual = None if target is None or prev_1 is None else target - prev_1
        prior = None if prev_1 is None or prev_2 is None else prev_1 - prev_2
        return actual, prior
    if transform == "mom_pct":
        prev_2 = observations.get(_period_key(_shift_month(target_month, -2)))
        return _safe_pct_change(target, prev_1), _safe_pct_change(prev_1, prev_2)
    if transform == "yoy_pct":
        prev_12 = observations.get(_period_key(_shift_month(target_month, -12)))
        prev_13 = observations.get(_period_key(_shift_month(target_month, -13)))
        return _safe_pct_change(target, prev_12), _safe_pct_change(prev_1, prev_13)
    raise ValueError(f"Unsupported transform: {transform}")


def _event_lookup_keys(event_name: str | None, event_key: str | None) -> tuple[str, ...]:
    keys: list[str] = []
    normalized_key = _normalize_event_key(event_key)
    if normalized_key:
        keys.append(normalized_key)
    normalized_name = _normalize_text(event_name).lower()
    if normalized_name and normalized_name not in keys:
        keys.append(normalized_name)
    return tuple(keys)


def _resolve_event_config(
    *,
    config: WrapperConfig,
    event_name: str | None,
    event_key: str | None,
) -> WrapperEventConfig | None:
    lookup_keys = _event_lookup_keys(event_name, event_key)
    for item in config.events:
        if item.key in lookup_keys:
            return item
        if any(name.lower() in lookup_keys for name in item.event_names):
            return item
    return None


def _prefers_html(request: Request) -> bool:
    accept = _normalize_text(request.headers.get("accept")).lower()
    if "text/html" in accept:
        return True
    if "application/json" in accept:
        return False
    return "*/*" in accept or not accept


def _metric_source_name(field_name: str) -> str:
    normalized = _normalize_event_key(field_name)
    if normalized == "actual" or "_actual" in normalized:
        return "actual"
    return "prior"


def _wrapper_help_html(*, request: Request, config: WrapperConfig) -> str:
    base_url = str(request.base_url).rstrip("/")
    route_path = config.service.route_path
    example_url = f"{base_url}{route_path}?event_key=cpi&event_at=2026-04-10T12:30:00Z"
    configured_events = "".join(f"<li><code>{item.key}</code></li>" for item in config.events)
    return f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Trading MVP BLS Wrapper</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f5efe2;
        --panel: #fffaf0;
        --border: #d8b46d;
        --text: #1f2937;
        --muted: #6b7280;
        --accent: #8a5a00;
      }}
      body {{
        margin: 0;
        padding: 24px;
        background: linear-gradient(180deg, #f9f3e7 0%, #f1e7d2 100%);
        color: var(--text);
        font: 15px/1.6 "Segoe UI", sans-serif;
      }}
      .wrap {{
        max-width: 860px;
        margin: 0 auto;
      }}
      .card {{
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 20px;
        padding: 24px;
        box-shadow: 0 12px 40px rgba(95, 63, 15, 0.08);
      }}
      h1, h2 {{
        margin: 0 0 12px;
      }}
      p {{
        margin: 0 0 12px;
      }}
      .muted {{
        color: var(--muted);
      }}
      code {{
        background: #f4ead4;
        border-radius: 8px;
        padding: 2px 6px;
        color: var(--accent);
      }}
      a {{
        color: var(--accent);
      }}
      ul {{
        margin: 8px 0 16px;
        padding-left: 20px;
      }}
      .links {{
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
        margin: 16px 0 20px;
      }}
      .links a {{
        text-decoration: none;
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 10px 14px;
        background: #fff;
      }}
      pre {{
        overflow-x: auto;
        background: #2f2418;
        color: #f8e8c7;
        border-radius: 14px;
        padding: 16px;
        margin: 12px 0 0;
      }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="card">
        <h1>Trading MVP BLS Wrapper</h1>
        <p class="muted">이 URL은 브라우저 설명 페이지입니다. 실제 API 호출은 query를 포함해야 합니다.</p>
        <div class="links">
          <a href="{base_url}/healthz">healthz</a>
          <a href="{base_url}/docs">Swagger docs</a>
          <a href="{example_url}">CPI example</a>
        </div>
        <h2>호출 규칙</h2>
        <p><code>{route_path}</code>는 BLS release enrichment API입니다.</p>
        <ul>
          <li><code>event_at</code>는 필수입니다.</li>
          <li><code>event_key</code> 또는 <code>event_name</code> 중 하나는 필요합니다.</li>
          <li>지원 이벤트는 아래 목록만 기본 제공됩니다.</li>
        </ul>
        <ul>{configured_events}</ul>
        <h2>예시</h2>
        <pre>GET {example_url}</pre>
      </div>
    </div>
  </body>
</html>"""


def _fetch_bls_series_payload(
    *,
    config: WrapperConfig,
    series_ids: Sequence[str],
    start_year: int,
    end_year: int,
) -> Mapping[str, object]:
    request_payload: dict[str, Any] = {
        "seriesid": list(series_ids),
        "startyear": str(start_year),
        "endyear": str(end_year),
    }
    registration_key = _normalize_text(os.getenv(config.bls.registration_key_env))
    if registration_key:
        request_payload["registrationkey"] = registration_key
        if config.bls.catalog:
            request_payload["catalog"] = True
    with httpx.Client(timeout=config.service.timeout_seconds) as client:
        response = client.post(
            config.bls.base_url,
            json=request_payload,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, Mapping):
        raise HTTPException(status_code=502, detail="BLS upstream returned a non-object payload.")
    return payload


def build_release_enrichment(
    *,
    config: WrapperConfig,
    event_name: str | None,
    event_key: str | None,
    event_at: datetime,
) -> dict[str, object]:
    event_config = _resolve_event_config(config=config, event_name=event_name, event_key=event_key)
    if event_config is None:
        return {}
    if event_config.reference_period_mode != "monthly":
        raise HTTPException(status_code=500, detail="Unsupported reference_period_mode in wrapper config.")
    target_month = _shift_month(_month_start(event_at), -event_config.release_lag_months)
    required_periods: list[datetime] = []
    for series in event_config.series:
        required_periods.extend(_required_periods(series.transform, target_month))
    start_year = min(item.year for item in required_periods)
    end_year = max(item.year for item in required_periods)
    payload = _fetch_bls_series_payload(
        config=config,
        series_ids=[item.series_id for item in event_config.series],
        start_year=start_year,
        end_year=end_year,
    )
    series_rows = _extract_series_rows(payload)
    metric_fields: dict[str, object] = {}
    primary_series_id: str | None = None
    primary_series_title: str | None = None
    for series in event_config.series:
        observations = _series_observations(series_rows.get(series.series_id, {}))
        actual, prior = _compute_metric_pair(
            transform=series.transform,
            observations=observations,
            target_month=target_month,
        )
        resolved_values = {"actual": actual, "prior": prior}
        for field_name in series.used_for:
            source_name = _metric_source_name(field_name)
            value = resolved_values[source_name]
            if value is not None:
                metric_fields[field_name] = round(value, 6)
                if field_name == "actual":
                    primary_series_id = series.series_id
                    primary_series_title = series.series_title
    if not metric_fields:
        return {}
    enrichment: dict[str, object] = {
        **metric_fields,
        "reference_period": _period_key(target_month),
        "event_key": event_config.key,
        "vendor": "bls",
        "series_ids": [item.series_id for item in event_config.series],
    }
    if event_config.headline_metric:
        enrichment["headline_metric"] = event_config.headline_metric
    if event_config.actual_unit:
        enrichment["unit"] = event_config.actual_unit
    if primary_series_id is not None:
        enrichment["series_id"] = primary_series_id
    if primary_series_title is not None:
        enrichment["series_title"] = primary_series_title
    return enrichment


wrapper_config = get_wrapper_config()
app = FastAPI(title="Trading MVP BLS Wrapper", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, object]:
    config = get_wrapper_config()
    return {
        "ok": True,
        "route_path": config.service.route_path,
        "configured_events": [item.key for item in config.events],
    }


@app.get("/", response_class=HTMLResponse)
def root(request: Request) -> HTMLResponse:
    config = get_wrapper_config()
    return HTMLResponse(_wrapper_help_html(request=request, config=config))


@app.get(wrapper_config.service.route_path, response_model=None)
def bls_release_enrichment(
    *,
    request: Request,
    symbol: str = Query(default="BTCUSDT"),
    timeframe: str = Query(default="15m"),
    event_name: str | None = Query(default=None),
    event_key: str | None = Query(default=None),
    event_at: datetime | None = Query(default=None),
) -> dict[str, object] | HTMLResponse:
    config = get_wrapper_config()
    if event_at is None or not (_normalize_text(event_name) or _normalize_text(event_key)):
        if _prefers_html(request):
            return HTMLResponse(_wrapper_help_html(request=request, config=config))
        raise HTTPException(
            status_code=422,
            detail="event_at and either event_key or event_name are required.",
        )
    del symbol, timeframe
    try:
        return build_release_enrichment(
            config=config,
            event_name=event_name,
            event_key=event_key,
            event_at=_ensure_utc(event_at),
        )
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"BLS upstream request failed: {exc}") from exc
