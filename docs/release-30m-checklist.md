# 발표 30분 전 운영 체크리스트

## 운영자 표현과 내부 키

- 이 문서는 발표 전 준비 체크 문서라 내부 설정 키와 화면 표현이 같이 섞여 있습니다.
- 운영자 화면에서는 아래처럼 읽으면 됩니다.
  - `event_source_provider`
    - 화면 표현: `Event source provider`
  - `event_source_bls_enrichment_url`
    - 화면 표현: `BLS enrichment URL`
  - `event_source_bea_enrichment_url`
    - 화면 표현: `BEA enrichment URL`
  - `exchange_sync_interval_seconds`
    - 화면 표현: `거래소 동기화 주기`
  - `market_refresh_interval_minutes`
    - 화면 표현: `시장 갱신 주기`
  - `decision_cycle_interval_minutes`
    - 화면 표현: `재검토 확인 주기`
- 즉, 이 문서에서 settings/cadence 키를 말하는 부분은 운영자 화면에서는 위 표현으로 대응해 읽으면 됩니다.

## 목적

경제지표 발표 후 BLS actual 값이 최대한 빨리 반영되도록, 발표 30분 전부터 어떤 프로세스를 어떤 순서로 켜야 하는지 정리한 런북이다.

발표 직전 최소 확인판은 [release-5m-final-check.md](/C:/my-trading-bot/docs/release-5m-final-check.md:1)를 본다.
발표 직후 장애 대응은 [release-2m-incident-response.md](/C:/my-trading-bot/docs/release-2m-incident-response.md:1)를 본다.
운영자가 한 장만 볼 때는 [release-operator-runbook.md](/C:/my-trading-bot/docs/release-operator-runbook.md:1)를 본다.

## 결론

BLS 반영에 필요한 필수 프로세스는 아래 2개다.

1. BLS wrapper
2. backend API

frontend는 설정 확인용이라 권장되지만 필수는 아니다.

중요:

- BLS 자동 반영은 `backend` 안의 background scheduler가 담당한다.
- 별도 `scripts/run_scheduler.ps1`는 BLS actual 반영의 필수 조건이 아니다.
- 발표 직후 반영을 원하면 `backend`가 발표 전에 이미 떠 있어야 한다.

## T-30분

### 1. BLS wrapper 먼저 실행

```powershell
cd C:\my-trading-bot
$env:BLS_API_KEY="본인_BLS_키"
powershell -ExecutionPolicy Bypass -File scripts\run_bls_wrapper.ps1
```

확인:

- [http://127.0.0.1:8091/healthz](http://127.0.0.1:8091/healthz)
- [http://127.0.0.1:8091/docs](http://127.0.0.1:8091/docs)

정상 기준:

- `healthz`가 `200 OK`
- `/` 또는 `/docs`가 열린다

### 2. backend 실행

```powershell
cd C:\my-trading-bot
powershell -ExecutionPolicy Bypass -File scripts\run_backend.ps1
```

확인:

- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

정상 기준:

- `200 OK`
- backend 콘솔 에러 없음

### 3. frontend 실행

```powershell
cd C:\my-trading-bot
powershell -ExecutionPolicy Bypass -File scripts\run_frontend.ps1
```

설정 화면:

- [http://127.0.0.1:3000/dashboard/settings?view=integration](http://127.0.0.1:3000/dashboard/settings?view=integration)

### 4. settings 저장 1회 수행

발표 전에 아래 값이 맞는지 보고 반드시 한 번 저장한다.

- `Event source provider = fred` (`event_source_provider`)
- `FRED API key = 본인 키`
- `BLS enrichment URL = http://127.0.0.1:8091/bls/releases` (`event_source_bls_enrichment_url`)
- 필요 시 `BEA enrichment URL` (`event_source_bea_enrichment_url`)

이 저장 1회의 의미:

- 현재 UI에 없는 legacy `static params`를 비운다
- 현재 운영용 URL을 DB에 확정한다

### 5. cadence 확인

발표 직후 반영을 원하면 아래처럼 둔다.

- `exchange_sync_interval_seconds = 30~60` (`거래소 동기화 주기`)
- `market_refresh_interval_minutes = 1` (`시장 갱신 주기`)
- `decision_cycle_interval_minutes = 1` (`재검토 확인 주기`)

의미:

- 이 구조는 push가 아니라 polling 기반이다
- 반영 속도는 위 cadence에 묶인다

## T-10분

### 6. 최종 상태 점검

아래 4개만 보면 된다.

1. wrapper 정상
- [http://127.0.0.1:8091/healthz](http://127.0.0.1:8091/healthz)

2. backend 정상
- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

3. settings 저장 확인
- [http://127.0.0.1:3000/dashboard/settings?view=integration](http://127.0.0.1:3000/dashboard/settings?view=integration)

4. event source가 FRED 기준으로 보이는지 확인
- settings 화면의 외부 이벤트 소스 카드

## 발표 시각

### 7. 수동 입력은 하지 않는다

발표 시각이 지나면 backend가 다음 event-context 갱신 시점에 자동으로:

1. FRED 일정 확인
2. 발표 시각 경과 여부 판단
3. BLS wrapper 호출
4. enrichment payload 반영

즉 발표 시각에 따로 `event_at`, `series_id`, 테스트 버튼 같은 것을 만질 필요가 없다.

## 발표 후 1~2분

### 8. 아직 반영이 안 보이면 확인할 것

순서대로 본다.

1. backend가 아직 살아 있는지
- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

2. wrapper가 살아 있는지
- [http://127.0.0.1:8091/healthz](http://127.0.0.1:8091/healthz)

3. settings를 이번 UI 기준으로 한 번 저장했는지
- legacy static params가 남아 있으면 다음 저장 전까지 과거 값이 유지될 수 있다

4. FRED API key가 저장돼 있는지
- settings 화면의 `저장된 FRED 키 있음` 표시 확인

5. cadence가 너무 느리지 않은지
- `market_refresh_interval_minutes` (`시장 갱신 주기`)
- `decision_cycle_interval_minutes` (`재검토 확인 주기`)

## 즉시 반영이 꼭 필요할 때

### 9. 선택적 수동 사이클

정말 즉시 확인이 필요하면 전체 운영 사이클을 한 번 수동 실행할 수 있다.

```powershell
Invoke-WebRequest -Method POST http://127.0.0.1:8000/api/cycles/run -UseBasicParsing
```

주의:

- 이건 단순 조회가 아니라 전체 cycle 실행이다
- 실거래 설정 상태라면 의사결정 경로까지 같이 돈다
- 따라서 운영 의미를 알고 있을 때만 사용한다

## 하지 말 것

- `series_id`를 UI에 다시 넣는 것
- `BLS static params`를 수동으로 되살리는 것
- 발표 직전에만 backend를 켜는 것
- BLS 자동 반영을 위해 별도 `run_scheduler.ps1`가 꼭 필요하다고 생각하는 것

## 한 줄 요약

발표 30분 전에는 `wrapper -> backend -> frontend -> settings 저장 1회` 순서로 켜고, backend를 계속 살려 둬야 발표 후 BLS actual 값이 자동 반영된다.
