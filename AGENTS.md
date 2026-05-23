# AGENTS.md instructions for C:\my-trading-bot

## 기본 응답

* 한국어 중심으로 답변한다.
* 추상 설명보다 바로 적용 가능한 문구, 명령, 파일 경로, 검증 결과를 우선한다.
* Codex에 다시 넣을 프롬프트가 필요하면 바로 복붙 가능한 형태로 제공한다.
* 우선순위, 작업 범위, 기존 소스와의 충돌 여부, 운영 리스크를 먼저 정리한다.
* 상태 불일치, 실거래 안전성, 운영 안정성을 일반적인 UI 해석보다 우선한다.
* 불필요하게 장황하게 설명하지 말고 핵심 위주로 보고한다.
* 필요 시 영문 산출물도 바로 복붙 가능하게 제공한다.

## 작업 범위와 변경 관리

* 작업 전 관련 `AGENTS.md`, 실제 엔트리 파일, source-of-truth 모듈을 먼저 확인한다.
* dirty worktree에서는 기존 dirty 상태와 이번 작업 diff를 분리해서 보고한다.
* 이미 dirty인 파일은 요청 범위에 포함된 경우에만 만지고, 만졌다면 이유와 영향 범위를 명시한다.
* 최소 변경을 기본값으로 두고, 하위 `backend/AGENTS.md`, `backend/trading_mvp/services/AGENTS.md`, `frontend/AGENTS.md`의 세부 지침을 각 영역에서 함께 따른다.

## 운영 상태 검증

* 보고/진단은 `AGENTS.md`만 보지 말고 실제 런타임 근거와 함께 판단한다.
* 읽기 전용 운영 보고는 `.env`와 런타임 DB 대상, `/health`, `/api/settings`, `/api/settings/ai-usage`, `/api/dashboard/operator`, `/api/dashboard/profitability`, `/api/analytics/cost-breakdown`, 그리고 필요한 DB 테이블을 확인한 뒤 작성한다.
* no-entry나 상태 불일치 진단은 `risk_checks -> pending_entry_plans -> audit_events -> scheduler_runs -> orders/executions` 흐름을 우선 추적한다.
* 프런트 표시보다 백엔드 플래그, DB 행, 스케줄러/워커 타임스탬프, 거래소 응답을 우선한다.

## 검증과 포트 정리

* 코드 변경 후에는 범위에 맞춰 `pytest`, `ruff`, `py_compile` 또는 `compileall`, `git diff --check`, 프런트 변경 시 lint/build를 우선 검증한다.
* 추가 작업이나 검증을 마친 뒤 `127.0.0.1:8001` 또는 `127.0.0.1:3001`에 리스닝 프로세스가 남아 있으면, `8000/3000` 기준 운영과 충돌하지 않도록 해당 포트의 프로세스를 조회하고 자동 종료한다.
* 사용자가 명시적으로 유지하라고 한 포트 프로세스는 종료하지 않는다.
