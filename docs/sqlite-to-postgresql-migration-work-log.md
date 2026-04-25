# SQLite -> PostgreSQL Migration Work Log

이 문서는 SQLite -> PostgreSQL one-shot migration 준비 과정에서 확인한 사실과 남은 블로커를 누적 기록한다.

## 2026-04-24

### 작업 내용

- 현재 HEAD 기준으로 PostgreSQL 전환 미완료 상태를 다시 확인했다.
- `DATABASE_URL` 미설정 시 직실행 경로가 SQLite로 조용히 fallback 되지 않도록 runtime guard 를 추가했다.
- Alembic 기본 URL 을 PostgreSQL 기준으로 정렬했다.
- backend / worker / scheduler PowerShell 실행 스크립트에 `DATABASE_URL` fail-fast 가드를 추가했다.
- SQLite -> PostgreSQL one-shot copy 스크립트와 운영 래퍼를 추가했다.
- source-only preflight 실행 경로를 추가했다.
- source-only preflight 에 source table별 row count 출력과 총 row count 기록을 추가했다.
- apply 모드에서 unpaused source, nonempty target, column mismatch override 를 사용할 수 없도록 차단했다.
- PostgreSQL sequence reset 은 serial sequence 가 존재하는 단일 PK 에만 적용하도록 보강했다.
- target empty 검사는 복사 대상 공통 테이블뿐 아니라 target-only 사용자 테이블도 포함하도록 보강했다.
- `.env` 에 명시한 `TRADING_MVP_ALLOW_SQLITE=1` 도 환경변수와 동일한 SQLite dev/test opt-in 으로 인정하도록 맞췄다.
- `.env.example` 의 SQLite 로컬/dev 예시는 `DATABASE_URL=sqlite://...` 와 `TRADING_MVP_ALLOW_SQLITE=1` 을 함께 주석으로 보여주도록 정리했다.
- 테스트 bootstrap 은 로컬 `.env` 의 `LIVE_TRADING_ENV_ENABLED=false` 에 흔들리지 않도록 테스트 기본값을 명시했다.

### 추가된 실행 경로

- Python copy CLI: `python -m trading_mvp.sqlite_to_postgresql_copy`
- PowerShell 운영 래퍼: `powershell -ExecutionPolicy Bypass -File scripts/run_sqlite_to_postgresql_copy.ps1`
- source-only preflight: `powershell -ExecutionPolicy Bypass -File scripts/run_sqlite_to_postgresql_copy.ps1 -SourceOnlyPreflight`

### 비테스트 `trading_mvp.database` import 영향 경로

- `backend/trading_mvp/main.py`: backend app startup 경로. `DATABASE_URL` fail-fast 적용 대상이다.
- `backend/trading_mvp/cli.py`: `python -m trading_mvp.cli` 직접 실행 경로. `DATABASE_URL` fail-fast 적용 대상이다.
- `backend/trading_mvp/worker_jobs.py`: worker job DB session 경로. `DATABASE_URL` fail-fast 적용 대상이다.
- `workers/scheduler.py`: scheduler loop DB session 경로. `DATABASE_URL` fail-fast 적용 대상이다.
- `backend/trading_mvp/models.py`: 모델 import 만으로도 `database.Base` 를 통과한다. ad hoc 분석 코드가 모델만 import 해도 `DATABASE_URL` 이 필요하다.
- `backend/trading_mvp/services/replay_validation.py`: replay 검증은 내부 in-memory SQLite 를 쓰지만 `Base`/models import 때문에 모듈 import 시 `DATABASE_URL` guard 영향권에 있다.

### 현재 확인된 source 상태

- source DB: `data/trading_mvp.db`
- 현재 확인값: `settings.trading_paused = False`, `settings.live_execution_armed = True`
- source-only preflight row count: 사용자 테이블 `19`, 총 row `120113`
- 판단: 이 상태에서는 one-shot migration apply 를 진행하면 안 된다.

### 현재 확인된 target 상태

- 확인 URL: `postgresql+psycopg://trading:trading@127.0.0.1:5432/trading_mvp_rehearsal`
- 결과: TCP 연결 거부
- 판단: 로컬 리허설용 PostgreSQL target 이 준비되어야 한다.

### 검증 기록

- `ruff check` 대상: `backend/trading_mvp/config.py`, `backend/trading_mvp/database.py`, `backend/trading_mvp/migrate.py`, `backend/trading_mvp/sqlite_to_postgresql_copy.py`, `workers/worker.py`, 관련 테스트 파일
- `ruff check` 결과: 통과
- `pytest` 대상: `tests/test_config.py`, `tests/test_sqlite_to_postgresql_copy.py`, `tests/test_background_loops.py`, `tests/test_main_routes.py`, `tests/test_settings_and_connectivity.py::test_health_endpoint_uses_lifespan_startup`
- `pytest` 결과: `20 passed`
- `python -m trading_mvp.sqlite_to_postgresql_copy --help`: 통과
- `scripts/run_sqlite_to_postgresql_copy.ps1 -SourceOnlyPreflight -AllowUnpausedSource`: source 상태와 테이블별 row count 출력 성공
- `scripts/run_sqlite_to_postgresql_copy.ps1 -SourceOnlyPreflight`: 현재 source 가 unpaused 상태라서 기본 경로에서 차단 확인
- `python -m trading_mvp.sqlite_to_postgresql_copy --source-url sqlite:///./data/trading_mvp.db --source-only-preflight --allow-unpaused-source`: source table `19`, total row `120113` 출력 성공
- `python -m trading_mvp.sqlite_to_postgresql_copy --source-url sqlite:///./data/trading_mvp.db --source-only-preflight`: 현재 source 가 unpaused 상태라서 기본 경로에서 차단 확인
- `scripts/run_sqlite_to_postgresql_copy.ps1 -TargetDatabaseUrl postgresql+psycopg://trading:trading@127.0.0.1:5432/trading_mvp_rehearsal -AllowUnpausedSource`: target PostgreSQL 연결 거부 확인
- `scripts/run_sqlite_to_postgresql_copy.ps1 -Apply -AllowUnpausedSource`: target 연결 전 조합 오류로 즉시 차단하도록 변경
- `scripts/run_sqlite_to_postgresql_copy.ps1 -Apply -AllowNonemptyTarget`: target 연결 전 조합 오류로 즉시 차단하도록 변경
- `scripts/run_sqlite_to_postgresql_copy.ps1 -Apply -AllowColumnMismatch`: target 연결 전 조합 오류로 즉시 차단하도록 변경
- `python -m trading_mvp.sqlite_to_postgresql_copy --apply --allow-nonempty-target`: target 연결 전 조합 오류로 즉시 차단 확인
- `scripts/run_backend.ps1`, `scripts/run_worker.ps1`, `scripts/run_scheduler.ps1`: 환경변수와 `.env` 모두에 `DATABASE_URL` 이 없으면 즉시 중단하도록 확인
- `uvicorn`/`python -m` 직접 import 경로: 환경변수와 `.env` 모두에 `DATABASE_URL` 이 없으면 즉시 중단하고, SQLite URL 은 `TRADING_MVP_ALLOW_SQLITE=1` opt-in 이 필요하도록 확인
- `git diff --check`: whitespace 오류 없음. CRLF 변환 경고만 있음.
- 전체 `pytest -q`: `498 passed`, `16 failed`
- 전체 pytest 실패 분류: AI instruction 문자열 기대값, scheduler market refresh 결과 payload 기대값, risk/headroom/min-notional 기대값 계열이다.
- 전체 pytest 판단: migration / PostgreSQL 전환 변경 파일과 직접 연결된 import error 또는 runtime guard 회귀는 확인되지 않았다.

### 다음 dry-run 명령

```powershell
powershell -ExecutionPolicy Bypass -File C:\my-trading-bot\scripts\run_sqlite_to_postgresql_copy.ps1 `
  -TargetDatabaseUrl postgresql+psycopg://trading:trading@127.0.0.1:5432/trading_mvp_rehearsal `
  -PrepareTargetSchema
```

### 다음 apply 리허설 명령

```powershell
powershell -ExecutionPolicy Bypass -File C:\my-trading-bot\scripts\run_sqlite_to_postgresql_copy.ps1 `
  -TargetDatabaseUrl postgresql+psycopg://trading:trading@127.0.0.1:5432/trading_mvp_rehearsal `
  -PrepareTargetSchema `
  -Apply
```

### 남은 블로커

- source DB 가 아직 pause/disarm 상태인지 재확인해야 한다.
- 현재 머신에서 PostgreSQL target 을 실행하거나 접속할 수 있어야 한다.
- 실제 PostgreSQL 대상 end-to-end copy 검증은 아직 수행하지 못했다.
- 전체 테스트 스위트의 기존 risk 계열 실패는 migration 변경과 별도로 분류해야 한다.
