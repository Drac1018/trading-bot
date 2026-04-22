# 발표 운영 최종 런북

## 운영자 표현과 내부 키

- 이 문서는 발표 운영 체크 문서라 내부 설정 키와 화면 표현이 같이 섞여 있습니다.
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

운영자가 경제지표 발표 전후에 이 문서 한 장만 보고 BLS actual 반영 운영을 끝낼 수 있게 정리한 최종 런북이다.

## 가장 쉬운 실행

한 번에 실행하고 현재 준비 상태까지 같이 보려면 아래 명령을 쓴다.

```powershell
cd C:\my-trading-bot
powershell -ExecutionPolicy Bypass -File scripts\run_release_day.ps1
```

frontend까지 같이 띄우려면:

```powershell
cd C:\my-trading-bot
powershell -ExecutionPolicy Bypass -File scripts\run_release_day.ps1 -IncludeFrontend
```

이미 떠 있는 프로세스는 건드리지 않고 준비 상태만 다시 보려면:

```powershell
cd C:\my-trading-bot
powershell -ExecutionPolicy Bypass -File scripts\run_release_day.ps1 -CheckOnly
```

## 핵심 리스크

지금 남은 핵심 리스크는 2개다.

1. 발표 전에 `backend`가 살아 있지 않음
2. cadence가 1분 수준이 아님

`legacy static params`는 현재 DB 기준으로 이미 `{}` 상태다.

## 필수 프로세스

필수는 아래 2개다.

1. BLS wrapper
2. backend API

frontend는 설정 확인용이라 권장되지만 필수는 아니다.

중요:

- BLS 자동 반영은 `backend` 안의 background scheduler가 담당한다.
- 지원 경제지표는 발표 시각 직후 release watch가 market refresh를 추가로 돌려 actual 반영 지연을 줄인다.
- 별도 `scripts/run_scheduler.ps1`는 BLS actual 반영의 필수 조건이 아니다.
- 발표 직후 반영을 원하면 `backend`가 발표 전에 이미 떠 있어야 한다.

## T-30분

### 1. wrapper 실행

개별 실행 대신 `scripts\run_release_day.ps1`를 써도 된다.

```powershell
cd C:\my-trading-bot
$env:BLS_API_KEY="본인_BLS_키"
powershell -ExecutionPolicy Bypass -File scripts\run_bls_wrapper.ps1
```

확인:

- [http://127.0.0.1:8091/healthz](http://127.0.0.1:8091/healthz)
- [http://127.0.0.1:8091/docs](http://127.0.0.1:8091/docs)

정상 기준:

- `200 OK`

### 2. backend 실행

```powershell
cd C:\my-trading-bot
powershell -ExecutionPolicy Bypass -File scripts\run_backend.ps1
```

확인:

- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

정상 기준:

- `200 OK`

### 3. frontend 실행

```powershell
cd C:\my-trading-bot
powershell -ExecutionPolicy Bypass -File scripts\run_frontend.ps1
```

설정 화면:

- [http://127.0.0.1:3000/dashboard/settings?view=integration](http://127.0.0.1:3000/dashboard/settings?view=integration)

### 4. settings 확인 후 저장 1회

최소 확인:

- `Event source provider = fred` (`event_source_provider`)
- `저장된 FRED 키 있음`
- `BLS enrichment URL = http://127.0.0.1:8091/bls/releases` (`event_source_bls_enrichment_url`)
- 필요 시 `BEA enrichment URL` (`event_source_bea_enrichment_url`)

이 저장 1회의 의미:

- 현재 운영 URL을 DB에 확정한다
- 예전 UI에서 남았을 수 있는 legacy 설정을 현재 기준으로 정리한다

## T-5분

### 5. cadence 확인

권장:

- `exchange_sync_interval_seconds = 30~60` (`거래소 동기화 주기`)
- `market_refresh_interval_minutes = 1` (`시장 갱신 주기`)
- `decision_cycle_interval_minutes = 1` (`재검토 확인 주기`)

의미:

- 이 구조는 push가 아니라 polling 기반이다
- 발표 직후 반영 속도는 위 cadence에 묶인다

### 6. 최종 상태 점검

아래 4개만 보면 된다.

1. wrapper 정상
- [http://127.0.0.1:8091/healthz](http://127.0.0.1:8091/healthz)

2. backend 정상
- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

3. settings 저장 완료
- [http://127.0.0.1:3000/dashboard/settings?view=integration](http://127.0.0.1:3000/dashboard/settings?view=integration)

4. cadence 1분 수준

## 발표 시각

### 7. 수동 입력은 하지 않는다

발표 시각이 지나면 backend가 다음 event-context 갱신 시점에 자동으로:

1. FRED 일정 확인
2. 발표 시각 경과 여부 판단
3. BLS wrapper 호출
4. enrichment payload 반영

즉 발표 시각에 `event_at`, `series_id`, 테스트 버튼 같은 것을 만질 필요가 없다.

## 발표 직후 1~2분

### 8. 값이 안 붙으면 보는 순서

1. wrapper health
- [http://127.0.0.1:8091/healthz](http://127.0.0.1:8091/healthz)

2. backend health
- [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

3. settings 확인
- `Event source provider = fred` (`event_source_provider`)
- `저장된 FRED 키 있음`
- `BLS enrichment URL` 확인 (`event_source_bls_enrichment_url`)

4. cadence 확인
- `market_refresh_interval_minutes` (`시장 갱신 주기`)
- `decision_cycle_interval_minutes` (`재검토 확인 주기`)

## 정말 급하면

### 9. 수동 cycle 1회

```powershell
Invoke-WebRequest -Method POST http://127.0.0.1:8000/api/cycles/run -UseBasicParsing
```

주의:

- 단순 조회가 아니라 전체 cycle 실행이다
- 실거래 설정 상태라면 의사결정 경로까지 같이 돈다

## 장애 원인 분기

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

## 하지 말 것

- `series_id`를 UI에 다시 넣는 것
- `BLS static params`를 수동으로 되살리는 것
- 발표 직전에만 backend를 켜는 것
- BLS 자동 반영을 위해 별도 `run_scheduler.ps1`가 꼭 필요하다고 생각하는 것

## 한 줄 요약

발표 전에는 `wrapper -> backend -> settings 저장`, 발표 직후에는 `8091 health -> 8000 health -> settings -> 필요 시 수동 cycle` 순서로 보면 된다.
