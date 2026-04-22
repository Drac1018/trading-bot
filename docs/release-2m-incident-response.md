# 발표 직후 2분 장애 대응표

## 운영자 표현과 내부 키

- 이 문서는 발표 직후 장애 대응 문서라 내부 설정 키와 화면 표현이 같이 섞여 있습니다.
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

## 목적

발표 직후 1~2분 안에 BLS actual 값이 안 붙을 때, 가장 짧은 순서로 무엇을 확인할지 정리한 대응표다.

운영자가 한 장만 볼 때는 [release-operator-runbook.md](/C:/my-trading-bot/docs/release-operator-runbook.md:1)를 본다.

## 0. 먼저 결론

지금 환경 기준으로 가장 먼저 볼 것은 아래 3개다.

1. wrapper 살아 있는지
2. backend 살아 있는지
3. settings가 이번 UI 기준으로 저장됐는지

## 1. 30초 안에 볼 것

### wrapper 확인

- [http://127.0.0.1:8091/healthz](http://127.0.0.1:8091/healthz)

정상:

- `200 OK`

비정상:

- wrapper 재시작

```powershell
cd C:\my-trading-bot
powershell -ExecutionPolicy Bypass -File scripts\run_bls_wrapper.ps1
```

### backend 확인

- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

정상:

- `200 OK`

비정상:

- backend 재시작

```powershell
cd C:\my-trading-bot
powershell -ExecutionPolicy Bypass -File scripts\run_backend.ps1
```

## 2. 60초 안에 볼 것

### settings 확인

- [http://127.0.0.1:3000/dashboard/settings?view=integration](http://127.0.0.1:3000/dashboard/settings?view=integration)

확인 항목:

- `Event source provider = fred` (`event_source_provider`)
- `저장된 FRED 키 있음`
- `BLS enrichment URL = http://127.0.0.1:8091/bls/releases` (`event_source_bls_enrichment_url`)

현재 확인 결과:

- legacy `BLS/BEA static params`는 이미 DB에서 `{}` 상태다
- 즉 지금은 static params 잔존이 장애 원인이 아니다

## 3. 그래도 안 붙으면

### cadence 확인

아래가 너무 크면 반영이 늦을 수 있다.

- `exchange_sync_interval_seconds` (`거래소 동기화 주기`)
- `market_refresh_interval_minutes` (`시장 갱신 주기`)
- `decision_cycle_interval_minutes` (`재검토 확인 주기`)

권장:

- `30~60초`
- `1분`
- `1분`

## 4. 정말 급하면

전체 cycle을 한 번 수동 실행할 수 있다.

```powershell
Invoke-WebRequest -Method POST http://127.0.0.1:8000/api/cycles/run -UseBasicParsing
```

주의:

- 단순 조회가 아니라 전체 cycle 실행이다
- 실거래 경로까지 함께 돌 수 있다

## 5. 장애 원인 분기

### wrapper down

- 증상: `8091/healthz` 실패
- 조치: wrapper 재시작

### backend down

- 증상: `8000/health` 실패
- 조치: backend 재시작

### settings mismatch

- 증상: URL/FRED 설정이 비었거나 잘못됨
- 조치: settings 저장

### cadence delay

- 증상: 서비스는 정상이지만 반영이 늦음
- 조치: cadence를 1분 수준으로 낮추고 다음 발표 전부터 유지

## 한 줄 요약

발표 직후 2분 안에 BLS 값이 안 붙으면 `8091 health -> 8000 health -> settings -> 수동 cycle` 순서로 본다.
