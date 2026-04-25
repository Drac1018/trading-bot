# SQLite -> PostgreSQL One-Shot Migration Checklist

관련 작업 기록은 `docs/sqlite-to-postgresql-migration-work-log.md` 에 누적한다.

## 목적

이 문서는 현재 저장소에서 SQLite 데이터를 PostgreSQL로 1회 이전할 때 필요한 범위와 운영 체크리스트를 정리한다.
목표는 schema 생성이 아니라 아래 4가지를 안전하게 맞추는 것이다.

- 데이터 이전 정합성
- 실거래 안전성
- 감사 가능성
- rollback 가능성

## 현재 상태

- 운영 기본 DB는 PostgreSQL이다.
- `backend/trading_mvp/migrate.py` 는 schema-only migration 경로다.
- 실제 데이터 복사는 `backend/trading_mvp/sqlite_to_postgresql_copy.py` 스크립트로 분리한다.
- 기본은 dry-run 이고, `--apply` 또는 PowerShell `-Apply` 를 줬을 때만 실제 row copy 를 수행한다.
- source SQLite 는 기본적으로 `settings.trading_paused = true`, `settings.live_execution_armed = false` 여야 한다.
- target PostgreSQL 은 기본적으로 비어 있어야 한다.
- source/target 컬럼 mismatch 는 기본 차단이다.

## 실행 경로

- Python copy CLI: `python -m trading_mvp.sqlite_to_postgresql_copy`
- 운영 래퍼: `scripts/run_sqlite_to_postgresql_copy.ps1`

기본 안전 동작:

- source 는 `sqlite://` 만 허용
- target 은 `postgresql://` 또는 `postgresql+psycopg://` 만 허용
- 일반 서비스 실행에서 SQLite를 쓰는 경우 `DATABASE_URL=sqlite://...` 와 `TRADING_MVP_ALLOW_SQLITE=1` 을 환경변수 또는 `.env` 에 함께 명시해야 한다.
- `alembic_version` 는 복사 대상에서 제외
- 공통 테이블만 복사 계획에 포함
- 주요 운영 테이블은 우선순위 순서로 정렬
- apply 후 row count 불일치가 나면 즉시 실패
- PostgreSQL sequence 는 copy 후 재설정
- PowerShell 래퍼는 source settings 상태와 테이블별 row count 를 먼저 출력하고, target PostgreSQL 연결 확인 후에만 copy 를 진행
- PowerShell 래퍼는 `-Apply` 또는 `-SnapshotSource` 일 때만 SQLite source snapshot 을 만든다.
- `--allow-unpaused-source`, `--allow-nonempty-target`, `--allow-column-mismatch` 와 대응 PowerShell override 플래그는 dry-run 검토용이다.
- 실제 copy 모드인 `--apply` / `-Apply` 는 paused source, empty target, aligned columns 상태에서만 허용한다.

## 실행 예시

source 상태와 row count 만 확인:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_sqlite_to_postgresql_copy.ps1 `
  -SourceOnlyPreflight
```

dry-run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_sqlite_to_postgresql_copy.ps1 `
  -TargetDatabaseUrl postgresql+psycopg://user:pass@localhost:5432/trading_mvp
```

schema 준비 + 실제 복사:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_sqlite_to_postgresql_copy.ps1 `
  -TargetDatabaseUrl postgresql+psycopg://user:pass@localhost:5432/trading_mvp `
  -PrepareTargetSchema `
  -Apply
```

예외 플래그는 운영자 수동 검토 후 dry-run 에서만 사용한다. apply 전에 source pause, target 초기화, schema 정합성을 먼저 맞춘다.

## 사전 체크리스트

- 신규 진입이 차단된 상태인지 확인
- `trading_paused = true` 확인
- `live_execution_armed = false` 확인
- source SQLite 파일 백업 완료
- target PostgreSQL 인스턴스 준비 완료
- target 에 `alembic upgrade head` 완료
- `APP_SECRET_SEED` 유지 여부 확인
- 대용량 테이블 예상 row count 기록

## 적용 절차

1. source SQLite 백업과 checksum 확보
2. target PostgreSQL schema 생성
3. dry-run 으로 table plan, row count, mismatch 확인
4. source_only / target_only / column mismatch 수동 검토
5. pause 상태 재확인
6. `--apply` 또는 `-Apply` 로 실제 copy 수행
7. table별 source/target row count 재검증
8. sequence reset 확인
9. 앱 smoke 검증

## 최소 smoke 검증

- `/health`
- `/api/settings`
- `/api/dashboard/operator`
- `/api/orders`
- `/api/executions`
- `/api/risk/checks`
- `/api/audit`

추가 운영 확인:

- account / positions / open orders / protection 상태 fresh 여부
- stale / incomplete / degraded 잔존 여부
- protective order 누락 여부
- dashboard 와 audit log 만으로 현재 상태 설명 가능 여부

## rollback 기준

- source SQLite 백업 파일 유지
- cutover 후 smoke 실패 시 즉시 pause 유지
- 서비스 `DATABASE_URL` 을 이전 SQLite 백업 기준으로 복귀 가능해야 함
- rollback 시도 시각과 실패 사유를 audit 로 남길 것

## 남은 리스크

- 아직 실제 PostgreSQL 대상 end-to-end copy 검증은 하지 않았다.
- large table copy 시간과 배치 크기 튜닝은 운영 데이터 기준 검증이 필요하다.
- column mismatch 를 수동 override 할 때는 누락 컬럼 의미를 먼저 검토해야 한다.
- 이 스크립트는 one-shot 도구이며, 운영 cutover runbook 과 자동 rollback 절차는 별도 판단이 필요하다.
