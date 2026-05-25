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
* 작업 시작과 종료 시 `git status --short` 기준으로 dirty 상태를 확인하고, 이번 작업으로 생긴 변경과 기존 변경을 구분한다.
* Codex가 만든 임시 로그, 캐시, 테스트 산출물, 임시 포트 프로세스처럼 출처와 영향이 명확한 생성물은 작업 종료 전에 정리한다.
* 분류 결과에서 안전한 생성물 정리 대상으로 표시했고 7일 이상 지난 파일은 다음 작업 종료 시 자동 제거할 수 있다.
* 7일 기준은 분류 보고나 작업 메모에 남긴 시점을 우선하며, 기록이 없으면 자동 제거하지 않는다.
* 7일 경과 파일도 삭제 전 검증이 필요하다. tracked 파일이 아니고, 설정/소스/마이그레이션/문서/DB/서비스 정의/비밀 파일이 아니며, 현재 프로세스나 런타임 설정에서 참조되지 않는다는 근거가 있어야 한다.
* 자동 삭제 검증은 최소 `git ls-files --error-unmatch -- <path>` 실패 확인, 안전 생성물 경로 또는 ignore 대상 확인, 수정 시각 확인, 필요 시 `rg -F <파일명>` 참조 확인을 포함한다.
* 삭제 가능성이 100%로 확인되지 않은 후보는 자동 삭제하지 말고 보존 목록에 남긴다.
* tracked 소스, 설정, 마이그레이션, 문서 diff는 사용자가 명시적으로 요청하지 않는 한 되돌리거나 삭제하지 않는다.
* 정리할지 애매한 dirty 파일은 보존하고, 파일 경로와 판단 사유를 보고한다.
* 최소 변경을 기본값으로 두고, 하위 `backend/AGENTS.md`, `backend/trading_mvp/services/AGENTS.md`, `frontend/AGENTS.md`의 세부 지침을 각 영역에서 함께 따른다.

## 운영 상태 검증

* 운영 상태, 실거래 안전, 상태 불일치, stale runtime, 제품화 보안, 성능/비용 진단은 `AGENTS.md`만 보지 말고 실제 런타임 근거와 함께 판단한다.
* 위 진단에서는 `.env`와 런타임 DB 대상, `/health`, `/api/settings`, `/api/settings/ai-usage`, `/api/dashboard/operator`, `/api/dashboard/profitability`, `/api/analytics/cost-breakdown`, 그리고 필요한 DB 테이블을 확인한 뒤 작성한다.
* `.env`를 확인할 때는 값 자체를 출력하지 말고 키 존재 여부, 사용 중인 런타임 대상, redacted 요약만 보고한다.
* 순수 문서/카피/정적 코드 변경은 관련 소스와 테스트 범위만 확인하고, 불필요하게 DB/API 조회로 범위를 확대하지 않는다.
* no-entry나 상태 불일치 진단은 `scheduler_runs -> agent_runs/decisions -> risk_checks -> pending_entry_plans -> positions -> orders/executions -> audit_events` 흐름을 우선 추적한다.
* 프런트 표시보다 백엔드 플래그, DB 행, 스케줄러/워커 타임스탬프, 거래소 응답을 우선한다.

## 제품화 / 외부 운영 안전

* 공개 인터넷에 `http://...:3000` 또는 백엔드 `:8000`을 직접 노출하지 않는다.
* 외부 운영 접근은 HTTPS reverse proxy, operator auth, CSRF, rate limit, IP allowlist/VPN 조건을 확인한 뒤에만 허용한다.
* 브라우저 API 호출은 상대 `/api/...` 경로를 사용하고, Next 서버 프록시가 서버 측 `OPERATOR_API_KEY` 계열 값을 붙이게 한다.
* state-changing API는 Next 프록시의 same-origin/CSRF 검사와 백엔드 `X-Operator-Intent`/role 검사를 모두 유지한다.
* full_live/public 운영 전에는 `scripts/run_productization_checks.ps1` 또는 그에 준하는 readiness/security 검증을 우선한다.

## 검증과 포트 정리

* 좁은 백엔드 변경은 `.\.venv\Scripts\python.exe -m compileall -q backend\trading_mvp`, `.\.venv\Scripts\python.exe -m ruff check backend tests workers`, 관련 `pytest`를 우선 실행한다.
* 넓은 백엔드/제품화 변경은 `.\.venv\Scripts\python.exe -m pytest`와 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_productization_checks.ps1`를 우선 검토한다.
* 프런트 변경은 현재 검증된 Corepack 경로로 `& 'C:\Program Files\nodejs\corepack.cmd' pnpm -C C:\my-trading-bot\frontend lint`와 `& 'C:\Program Files\nodejs\corepack.cmd' pnpm -C C:\my-trading-bot\frontend build`를 우선 실행한다.
* 위 경로가 없으면 `Test-Path 'C:\my-trading-bot\.tools\node-v22.21.1-win-x64\corepack.cmd'`를 확인한 뒤 해당 로컬 Corepack을 fallback으로 사용한다. 현재 `.tools\node-v24.14.1-win-x64\corepack.cmd`는 없으므로 검증 명령으로 쓰지 않는다.
* 모든 코드/문서 변경 후에는 범위에 맞춰 `git diff --check`를 확인한다.
* 추가 작업이나 검증을 마친 뒤 `127.0.0.1:8001` 또는 `127.0.0.1:3001`에 리스닝 프로세스가 남아 있으면, `8000/3000` 기준 운영과 충돌하지 않도록 해당 포트의 프로세스를 조회하고 자동 종료한다.
* 사용자가 명시적으로 유지하라고 한 포트 프로세스는 종료하지 않는다.
