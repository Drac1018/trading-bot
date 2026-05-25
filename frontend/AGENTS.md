# AGENTS.md

`frontend/`는 일반 공개 서비스가 아니라 운영자 전용 제품 표면이다.

## 세부 원칙

* 백엔드의 실제 상태를 그대로 보여주고, 프런트에서 임의 상태를 만들지 말 것
* pause, live ready, approval armed, blocked reason은 서로 구분되게 표시할 것
* 한국어 운영 문구를 유지하고, 모바일에서도 핵심 정보가 가려지지 않게 반응형으로 구성할 것
* 목록/로그/백로그는 가능하면 최신 활동 기준으로 읽기 쉽게 정렬할 것
* 상세 정책 설명은 문서 링크나 요약으로 연결하고, 화면에 장문 정책을 반복하지 말 것
* 공개 인터넷에 `http://...:3000`을 직접 노출하는 구성을 제품화 완료로 보지 말 것
* 외부 접근이 필요하면 HTTPS reverse proxy, operator auth, CSRF, rate limit, IP allowlist/VPN 조건을 먼저 확인할 것
* 브라우저에서 백엔드 `:8000`으로 직접 호출하지 말고 상대 `/api/...` 경로와 Next 서버 프록시를 사용할 것
* `OPERATOR_API_KEY`, role-specific operator key, CSRF secret은 클라이언트 번들/상태/로그에 노출하지 말 것
* state-changing 요청은 same-origin/CSRF 조건과 백엔드 `X-Operator-Intent`/role 경계를 유지할 것

## 권장 검증

변경 범위에 맞춰 아래를 우선 실행:

* `& 'C:\Program Files\nodejs\corepack.cmd' pnpm -C C:\my-trading-bot\frontend lint`
* `& 'C:\Program Files\nodejs\corepack.cmd' pnpm -C C:\my-trading-bot\frontend build`
* `C:\Program Files\nodejs\corepack.cmd`가 없으면 `Test-Path 'C:\my-trading-bot\.tools\node-v22.21.1-win-x64\corepack.cmd'`를 확인한 뒤 해당 로컬 Corepack을 fallback으로 사용한다. 현재 `.tools\node-v24.14.1-win-x64\corepack.cmd`는 없다.
* 인증/프록시/라우팅 변경 시 `pnpm -C frontend test:smoke` 또는 현재 Playwright smoke 경로 확인
