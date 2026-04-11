# AI / Binance 연동 적용 정리

작성일: 2026-04-08  
범위: 실제 OpenAI 연동 경계, Binance 공개 시세/연결 테스트, 설정 UI, 주기 실행, 반응형 데이터 가독성 개선

## 이번에 적용한 핵심 내용

### 1. 실제 OpenAI 연동 경계 추가
- `backend/trading_mvp/providers.py`
- OpenAI Chat Completions 기반 JSON Schema 강제 출력 경로를 추가했습니다.
- 연결 테스트는 실제 생성 호출 대신 `GET /v1/models/{model}` 확인으로 구현해 토큰 비용을 최소화했습니다.
- Trading Decision Agent는 외부 호출 실패 시 결정론적 로직으로 자동 fallback 됩니다.

### 2. Binance 공개 시장데이터/연결 테스트 추가
- `backend/trading_mvp/services/binance.py`
- Binance USD-M Futures 공개 klines를 읽는 클라이언트를 추가했습니다.
- API 키/시크릿이 있으면 계정 정보 조회까지 연결 테스트가 가능합니다.
- 키가 없어도 공개 시세 경로로 기본 연결 테스트는 가능합니다.

### 3. 설정 저장/암호화 추가
- `backend/trading_mvp/models.py`
- `backend/trading_mvp/services/settings.py`
- `backend/trading_mvp/services/secret_store.py`
- 프런트에서 입력한 OpenAI/Binance 키는 평문 응답으로 다시 내려가지 않도록 처리했습니다.
- 저장 여부만 프런트로 반환하고, 실제 키는 암호화해 DB에 저장합니다.

### 4. 설정 API / 연결 테스트 API 추가
- `backend/trading_mvp/main.py`
- `PUT /api/settings`
- `POST /api/settings/test/openai`
- `POST /api/settings/test/binance`
- 각 테스트 결과는 감사 로그와 시스템 상태 이벤트에도 남도록 연결했습니다.

### 5. AI 주기 호출 정책 추가
- `backend/trading_mvp/services/scheduler.py`
- `backend/trading_mvp/worker_jobs.py`
- `workers/scheduler.py`
- 결정 사이클 주기와 실제 OpenAI 호출 최소 간격을 분리했습니다.
- 예시:
  - 결정 사이클은 15분마다 실행
  - 실제 OpenAI 호출은 30분 이상 간격일 때만 허용
- `historical_replay`에서는 비용 폭증을 막기 위해 외부 OpenAI 호출을 막았습니다.

### 6. 프런트 설정 화면 개편
- `frontend/components/settings-controls.tsx`
- OpenAI API 키 입력
- Binance API Key / Secret 입력
- OpenAI 연결 테스트 버튼
- Binance 연결 테스트 버튼
- AI 모델 / 온도 / 입력 캔들 수 설정
- 결정 사이클 주기 / AI 호출 최소 간격 설정
- Binance 공개 시세 사용 여부 / testnet 여부 설정
- 종이매매 / 라이브 가드 / 수동 승인 / pause-resume 제어

### 7. 데이터 가독성 / 반응형 개선
- `frontend/components/data-table.tsx`
- 기존 넓은 테이블 중심 구조를 카드형 요약 + 세부 payload 접기 구조로 변경했습니다.
- 핵심 컬럼만 먼저 보여주고 긴 JSON/부가 데이터는 `세부 payload 보기`로 접어서 보게 했습니다.
- 컬럼이 많은 행은 상위 10개 핵심 필드만 본문에 보여주고 나머지는 세부 영역으로 이동했습니다.

## 자기 피드백 후 추가로 반영한 개선

### 개선 1. CLI의 불필요한 DB 의존 제거
- `backend/trading_mvp/cli.py`
- `export-schemas` 실행 시 오케스트레이터를 먼저 만들고 있어 DB 상태에 불필요하게 의존하던 부분을 수정했습니다.
- 이제 `cycle`, `replay` 같은 실제 실행 명령에서만 오케스트레이터를 생성합니다.

### 개선 2. 워커 중복 실행 위험 완화
- `workers/scheduler.py`
- interval decision cycle이 Redis 큐 환경에서 매 루프마다 중복 enqueue될 수 있는 위험을 줄이기 위해 due 체크를 분리했습니다.

### 개선 3. 테스트 자동 수집 충돌 제거
- `backend/trading_mvp/services/connectivity.py`
- 서비스 함수명이 `test_*`로 시작해 pytest가 테스트로 오인하던 부분을 `check_*`로 변경했습니다.

### 개선 4. Binance 타입 안정성 보강
- `backend/trading_mvp/services/binance.py`
- 응답 타입을 명시적으로 검사/캐스팅하도록 바꿔 mypy 오류를 제거했습니다.

## 검증 결과

### 백엔드
- `pytest -q` 통과
- 결과: `11 passed`
- `ruff check backend tests` 통과
- `mypy backend/trading_mvp` 통과

### 프런트
- `pnpm lint` 통과
- `pnpm build` 통과

### 런타임 / CLI
- `python -m trading_mvp.migrate` 통과
- `python -m trading_mvp.cli export-schemas` 통과
- `python -m trading_mvp.cli cycle` 통과
- `python -m trading_mvp.cli review --window 24h` 통과

### API 직접 확인
- `GET /api/settings` 200
- `PUT /api/settings` 200
- `POST /api/settings/test/openai` 200
- `POST /api/settings/test/binance` 200

## 현재 동작 방식

### OpenAI
- 키가 없으면 deterministic mock provider 사용
- 키가 있고 `ai_enabled=true`이며 `ai_provider=openai`면 실제 OpenAI 사용
- trading decision은 비용 절감을 위해 최소 호출 간격을 지킴
- 연결 테스트는 토큰을 거의 쓰지 않는 모델 조회 방식

### Binance
- `binance_market_data_enabled=true`면 실시간 market snapshot에 Binance 공개 klines를 우선 사용
- 실패하면 seed 데이터로 fallback
- API 키와 시크릿이 있으면 연결 테스트에서 계정 정보까지 확인

## 사용자가 다음에 할 일

1. 설정 페이지에서 OpenAI 키 입력
2. OpenAI 연결 테스트 실행
3. Binance API Key / Secret 입력
4. Binance 연결 테스트 실행
5. `binance_market_data_enabled` 활성화
6. 필요 시 `binance_testnet_enabled` 활성화
7. `ai_enabled` 활성화
8. 결정 사이클 / AI 호출 간격 조정
9. 저장 후 수동 사이클 또는 워커 실행으로 동작 확인

## 주의 사항

- 기본값은 여전히 종이매매입니다.
- 외부 AI 호출이 실패해도 결정론적 fallback이 우선합니다.
- `historical_replay`는 비용 보호를 위해 외부 OpenAI를 호출하지 않습니다.
- Binance 공개 시세 연결이 실패하면 seed 시세로 fallback 됩니다.
- 라이브 실주문 어댑터는 여전히 기본 비활성/가드 상태입니다.

## 주요 수정 파일

### 백엔드
- `backend/trading_mvp/config.py`
- `backend/trading_mvp/models.py`
- `backend/trading_mvp/providers.py`
- `backend/trading_mvp/main.py`
- `backend/trading_mvp/cli.py`
- `backend/trading_mvp/schemas.py`
- `backend/trading_mvp/services/settings.py`
- `backend/trading_mvp/services/secret_store.py`
- `backend/trading_mvp/services/binance.py`
- `backend/trading_mvp/services/connectivity.py`
- `backend/trading_mvp/services/market_data.py`
- `backend/trading_mvp/services/agents.py`
- `backend/trading_mvp/services/orchestrator.py`
- `backend/trading_mvp/services/scheduler.py`
- `backend/trading_mvp/worker_jobs.py`
- `workers/scheduler.py`
- `alembic/versions/b94b86b18709_add_ai_and_binance_settings.py`

### 프런트
- `frontend/components/settings-controls.tsx`
- `frontend/components/data-table.tsx`
- `frontend/app/dashboard/[slug]/page.tsx`
- `frontend/lib/ui-copy.ts`

### 테스트 / 설정
- `tests/test_settings_and_connectivity.py`
- `.env.example`
- `pyproject.toml`
