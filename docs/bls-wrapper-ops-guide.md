# BLS Wrapper 운영 가이드

## 결론

현재 프로젝트에서는 BLS raw API를 앱에서 직접 다루지 말고 wrapper를 통해 운영하는 것이 가장 안전하다.

- 앱 UI에는 `series_id`를 직접 입력하지 않는다.
- 앱 UI에는 `BLS static params` 같은 테스트/고급 입력을 두지 않는다.
- `event_key -> series_id/series_ids -> normalized payload` 매핑은 wrapper 내부에서 관리한다.

## 실사용 입력값

프런트 설정 화면에서는 아래만 넣는다.

- `Event source provider`: `fred`
- `FRED API key`: 본인 키
- `BLS enrichment URL`: `http://127.0.0.1:8091/bls/releases`
- 필요 시 `BEA enrichment URL`

앱 settings 저장 시 BLS/BEA static params는 빈 값으로 정리된다.

## Wrapper 실행

wrapper 구현 위치:

- [bls_wrapper_app.py](/C:/my-trading-bot/backend/trading_mvp/bls_wrapper_app.py:1)

실행 명령:

```powershell
$env:BLS_API_KEY="your_bls_registration_key"
.venv\Scripts\python.exe -m uvicorn trading_mvp.bls_wrapper_app:app --host 127.0.0.1 --port 8091
```

확인 URL:

- [http://127.0.0.1:8091/](http://127.0.0.1:8091/)
- [http://127.0.0.1:8091/healthz](http://127.0.0.1:8091/healthz)
- [http://127.0.0.1:8091/docs](http://127.0.0.1:8091/docs)

`BLS_API_KEY`는 선택 사항이지만 registered mode 운영을 권장한다.

## 자동 반영 구조

중요한 점은 wrapper가 혼자 주기적으로 수집하지 않는다는 것이다.

- 백엔드 scheduler가 살아 있어야 한다.
- scheduler가 market/event context를 갱신할 때 FRED 일정을 다시 읽는다.
- 발표 시각이 지난 이벤트만 BLS wrapper를 자동 호출한다.
- 따라서 발표 직후 반영을 원하면 backend scheduler를 미리 켜 두어야 한다.

별도 수동 테스트 입력은 없다. 운영 반영 속도는 사실상 아래 주기에 묶인다.

- `exchange_sync_interval_seconds`
- `market_refresh_interval_minutes`
- `decision_cycle_interval_minutes`

## Wrapper 내부 권장 매핑

기본 예시 설정:

- [bls-wrapper.example.toml](/C:/my-trading-bot/infra/bls-wrapper.example.toml:1)

권장 headline 기준:

1. CPI
- headline: CPI YoY
- 보조 필드: CPI MoM
- series: `CUUR0000SA0`, `CUSR0000SA0`

2. PPI
- headline: PPI Final Demand MoM
- 보조 필드: PPI Final Demand YoY
- series: `WPSFD4`, `WPUFD4`

3. Employment Situation
- headline: nonfarm payrolls change
- 보조 필드: unemployment rate, average hourly earnings
- series: `CES0000000001`, `LNS14000000`, `CES0500000003`

## Request Contract

앱은 wrapper에 아래 query를 보낸다.

- `symbol`
- `timeframe`
- `event_name`
- `event_key`
- `event_at`

예시:

```text
GET /bls/releases?symbol=BTCUSDT&timeframe=15m&event_name=Consumer%20Price%20Index&event_key=cpi&event_at=2026-04-10T12:30:00Z
```

## Response Contract

wrapper는 이벤트 1건당 flat JSON 1개를 반환한다.

필수 필드:

- `actual`
- `reference_period`
- `event_key`
- `vendor`

권장 공통 필드:

- `prior`
- `series_id`
- `series_ids`
- `series_title`
- `headline_metric`
- `unit`

예시:

```json
{
  "actual": 3.4,
  "prior": 3.2,
  "reference_period": "2026-04",
  "event_key": "cpi",
  "vendor": "bls",
  "headline_metric": "cpi_yoy_pct",
  "unit": "percent",
  "series_ids": ["CUUR0000SA0", "CUSR0000SA0"],
  "mom_actual": 0.3,
  "mom_prior": 0.2
}
```

## 운영 규칙

- 발표 전이거나 계산 불가면 `200` + `{}` 반환
- unsupported event도 `200` + `{}` 또는 명시 필드로 처리
- upstream timeout/auth/parse 실패만 `5xx`

이유:

- 현재 앱 enrichment adapter는 실패 시 해당 enrichment를 건너뛴다.
- 빈 결과와 시스템 장애를 섞으면 운영 상태 설명이 어려워진다.

## 하지 말 것

- UI에 `series_id=...`를 직접 넣는 운영
- 앱 UI에 테스트 이벤트명/시각을 다시 붙이는 작업
- repo 내부 DB/schema를 event별 BLS mapping 저장소로 키우는 작업
- raw BLS multi-series POST 형식을 현재 adapter에 직접 우겨 넣는 작업
