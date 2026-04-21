# 운영 UI / 서비스 자동기동 / AI 호출량 업데이트

작성일: 2026-04-08

## 현재 읽는 법

- 이 문서는 2026-04-08 시점의 적용 내역을 기록한 운영 메모입니다.
- 본문에서는 당시 구현 키와 계산식을 그대로 보존합니다.
- 현재 운영자 UI에서는 아래처럼 읽습니다.
  - `decision_cycle_interval_minutes` = `재검토 확인 주기`
  - `ai_call_interval_minutes` = `AI 기본 검토 간격`
- 따라서 본문에서 `의사결정 주기`, `AI 호출 간격`, `decision_cycle_interval_minutes`, `ai_call_interval_minutes`를 언급하는 부분은 현재 화면 기준으로는 `재검토 확인 주기`와 `AI 기본 검토 간격`에 대응됩니다.
- 주의:
  - 아래 3번 섹션의 월간 호출량 계산은 2026-04-08 당시 설계 스냅샷입니다.
  - 현재 live-core 범위는 이벤트 기반 review dispatch + `1h` review 기준으로 운영되며, 본문 수치를 현재 운영 기준의 실시간 source-of-truth로 해석하면 안 됩니다.

## 이번에 적용한 내용

### 1. 가상거래 / 실거래 로그 분리
- 프런트 내비게이션에 `가상거래 로그`, `실거래 로그` 페이지를 추가했다.
- 페이지 구성은 아래와 같다.
  - `paper-logs`: `/api/orders?mode=paper`, `/api/executions?mode=paper`
  - `live-logs`: `/api/orders?mode=live`, `/api/executions?mode=live`
- 기존 `전체 주문 / 체결` 페이지는 통합 로그 용도로 유지했다.

관련 파일:
- `frontend/components/nav.tsx`
- `frontend/lib/page-config.ts`
- `backend/trading_mvp/main.py`
- `backend/trading_mvp/services/dashboard.py`

### 2. 설정 페이지 한글화 및 운영자 중심 UI 개선
- 설정 화면을 한국어 운영자 기준으로 다시 정리했다.
- OpenAI / Binance 연결 상태 배지, 일시중지 / 재개 버튼, 리스크 한도, 스케줄 주기, 키 입력, 연결 테스트 버튼을 한 화면에서 확인할 수 있게 유지했다.
- 반응형에서 테이블이 읽기 어려웠던 문제를 완화하기 위해:
  - 카드형 레이아웃 유지
  - 상세 payload는 접기 형태로 분리
  - 로그 페이지를 별도 분리해 한 화면에 너무 많은 표가 몰리지 않게 조정

관련 파일:
- `frontend/components/settings-controls.tsx`
- `frontend/components/data-table.tsx`
- `frontend/lib/ui-copy.ts`
- `frontend/app/page.tsx`
- `frontend/app/layout.tsx`

### 3. 월간 AI 호출량 계산 추가
- 설정 API 응답에 실제 예상 호출량과 `활성화 시 예상 호출량`을 모두 포함하도록 확장했다.
- 현재 저장 설정 기준:
  - `estimated_monthly_ai_calls`: 현재 실제 설정 상태 기준 예상 호출량
  - `projected_monthly_ai_calls_if_enabled`: 현재 주기 설정 그대로 OpenAI를 켰을 때의 예상 호출량
- 현재 운영자 UI 표현으로 읽으면:
  - `decision_cycle_interval_minutes` = `재검토 확인 주기`
  - `ai_call_interval_minutes` = `AI 기본 검토 간격`

현재 기본값 기준 계산:
- 현재 실제값: `0회/월`
  - 이유: `ai_enabled = false`
- OpenAI 활성화 시 예상값: `1710회/월`
  - 거래 의사결정 AI: `1440회`
  - 통합 기획 AI(4h): `180회`
  - UI/UX AI(12h): `60회`
  - 제품 개선 AI(24h): `30회`

계산 기준:
- 30일 기준 총 분: `43200분`
- 거래 의사결정 호출 간격:
  - 당시 내부 계산 키 기준
  - `max(decision_cycle_interval_minutes, ai_call_interval_minutes)`
  - 현재값: `max(15, 30) = 30분`
  - `43200 / 30 = 1440회`

관련 파일:
- `backend/trading_mvp/services/settings.py`
- `backend/trading_mvp/schemas.py`
- `frontend/components/settings-controls.tsx`

### 4. Windows 서비스 자동 시작 구성
- 백엔드, 프런트, 워커, 스케줄러를 Windows 서비스로 등록하고 자동 시작으로 설정했다.
- 현재 서비스 이름:
  - `TradingMvpBackend`
  - `TradingMvpFrontend`
  - `TradingMvpWorker`
  - `TradingMvpScheduler`
- 프런트 서비스는 서비스 환경에서 `pnpm exec` 대신 `node.exe`로 Next CLI를 직접 호출하도록 조정해, 서비스 환경에서 `next start`가 누락되는 문제를 줄였다.

관련 파일:
- `scripts/install_windows_services.ps1`
- `scripts/run_frontend_service.ps1`

## 현재 동작 확인 결과

### 백엔드
- `pytest -q`: 통과 (`11 passed`)
- `ruff check backend tests`: 통과
- `mypy backend/trading_mvp`: 통과
- `GET /health`: `200`
- `GET /api/settings`: `200`

### 프런트 / 서비스
- Windows 서비스 상태:
  - `TradingMvpBackend`: Running / Automatic
  - `TradingMvpFrontend`: Running / Automatic
  - `TradingMvpWorker`: Running / Automatic
  - `TradingMvpScheduler`: Running / Automatic
- HTTP 확인:
  - `http://127.0.0.1:3000` -> `200`
  - `http://127.0.0.1:3000/dashboard/settings` -> `200`
  - `http://127.0.0.1:3000/dashboard/paper-logs` -> `200`
  - `http://127.0.0.1:3000/dashboard/live-logs` -> `200`

## 자기 점검 후 반영한 개선

### 1. 서비스용 프런트 시작 경로 단순화
- 서비스 환경에서 `corepack/pnpm exec`가 빌드 후 `next start`로 안정적으로 이어지지 않는 문제가 있었다.
- 이를 줄이기 위해 서비스 전용 스크립트는 Next CLI를 `node.exe`로 직접 실행하도록 바꿨다.

### 2. 설정 응답 의미 보강
- `AI 비활성` 상태에서는 실제 호출량이 0으로 보이기 때문에 비용 추정이 부족했다.
- 그래서 `현재 실제값`과 `활성화 시 예상값`을 분리해 표시하도록 조정했다.

### 3. 로그 화면 분리
- 운영자 입장에서 종이매매와 실거래 로그가 섞이면 판단이 느려진다.
- 그래서 통합 화면은 유지하되, 빠르게 보는 용도의 전용 페이지를 추가했다.

## 알려둘 점

### 1. 프런트 의존성 디렉터리 권한
- 현재 이 Windows 환경에서는 서비스가 생성하거나 재설치한 `frontend/node_modules`에 대해
  인터랙티브 셸에서 `pnpm lint`, `pnpm build`가 ACL 문제로 재실행되지 않을 수 있다.
- 대신 이번 검증에서는:
  - 서비스 빌드 성공 로그
  - 실제 HTTP `200`
  - 설정 / 로그 페이지 응답
  로 런타임 정상 동작을 확인했다.

### 2. 실거래 로그 페이지
- 구조는 준비되어 있지만 현재 기본 모드가 종이매매이므로 실거래 로그는 비어 있을 수 있다.

## 운영자가 바로 볼 위치
- 설정 페이지: `http://127.0.0.1:3000/dashboard/settings`
- 가상거래 로그: `http://127.0.0.1:3000/dashboard/paper-logs`
- 실거래 로그: `http://127.0.0.1:3000/dashboard/live-logs`
- 백엔드 상태: `http://127.0.0.1:8000/health`
