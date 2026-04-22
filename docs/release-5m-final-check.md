# 발표 5분 전 최종 점검표

## 운영자 표현과 내부 키

- 이 문서는 발표 직전 최종 점검 문서라 내부 설정 키와 화면 표현이 같이 섞여 있습니다.
- 운영자 화면에서는 아래처럼 읽으면 됩니다.
  - `event_source_provider`
    - 화면 표현: `Event source provider`
  - `event_source_bls_enrichment_url`
    - 화면 표현: `BLS enrichment URL`
  - `exchange_sync_interval_seconds`
    - 화면 표현: `거래소 동기화 주기`
  - `market_refresh_interval_minutes`
    - 화면 표현: `시장 갱신 주기`
  - `decision_cycle_interval_minutes`
    - 화면 표현: `재검토 확인 주기`
- 즉, 이 문서에서 settings/cadence 키를 말하는 부분은 운영자 화면에서는 위 표현으로 대응해 읽으면 됩니다.

## 목표

발표 직후 BLS actual 값이 빠지지 않도록, 발표 5분 전에 최소 항목만 최종 확인하는 체크리스트다.

발표 직후 장애 대응은 [release-2m-incident-response.md](/C:/my-trading-bot/docs/release-2m-incident-response.md:1)를 본다.
운영자가 한 장만 볼 때는 [release-operator-runbook.md](/C:/my-trading-bot/docs/release-operator-runbook.md:1)를 본다.

## 1. 프로세스 2개가 살아 있는지

필수는 아래 2개다.

1. BLS wrapper
- [http://127.0.0.1:8091/healthz](http://127.0.0.1:8091/healthz)

2. backend
- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

정상 기준:

- 둘 다 `200 OK`

## 2. settings 저장을 한 번 했는지

설정 화면:

- [http://127.0.0.1:3000/dashboard/settings?view=integration](http://127.0.0.1:3000/dashboard/settings?view=integration)

최소 확인:

- `Event source provider = fred` (`event_source_provider`)
- `FRED API key` 저장됨
- `BLS enrichment URL = http://127.0.0.1:8091/bls/releases` (`event_source_bls_enrichment_url`)

중요:

- legacy static params는 이 UI 기준으로 **한 번 저장해야 실제로 비워진다**
- 아직 저장 안 했으면 지금 바로 저장한다

## 3. cadence가 너무 느리지 않은지

권장:

- `exchange_sync_interval_seconds = 30~60` (`거래소 동기화 주기`)
- `market_refresh_interval_minutes = 1` (`시장 갱신 주기`)
- `decision_cycle_interval_minutes = 1` (`재검토 확인 주기`)

의미:

- 발표 직후 반영 속도는 이 cadence에 묶인다
- backend가 살아 있어도 cadence가 느리면 반영이 늦어질 수 있다

## 4. 발표 직후 반영 조건

이 2개가 동시에 맞아야 한다.

1. backend가 이미 떠 있을 것
2. 발표 시각이 지났을 것

현재 구조는 push가 아니라 scheduler polling 기반이다.  
즉 발표 전에 backend가 꺼져 있으면 발표 직후 자동 반영되지 않는다.

## 5. 직후 1분 내 안 보이면 볼 것

순서대로 본다.

1. wrapper health
- [http://127.0.0.1:8091/healthz](http://127.0.0.1:8091/healthz)

2. backend health
- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

3. settings 저장 여부
- 이번 UI에서 저장 1회 했는지

4. FRED 키 저장 여부
- settings 화면의 `저장된 FRED 키 있음`

## 6. 정말 급하면

운영 의미를 알고 있을 때만 전체 cycle을 한 번 수동 실행할 수 있다.

```powershell
Invoke-WebRequest -Method POST http://127.0.0.1:8000/api/cycles/run -UseBasicParsing
```

주의:

- 단순 조회가 아니라 전체 cycle 실행이다
- 실거래 경로까지 함께 돌 수 있다

## 한 줄 요약

발표 5분 전에는 `wrapper 정상`, `backend 정상`, `settings 저장 1회`, `1분 cadence` 이 4가지만 확인하면 된다.
