# 운영 대시보드 페이지 기획서 v1

작성일: 2026-05-10 KST
범위: `frontend/app`의 실제 라우트, `AppChrome` 좌측 메뉴, `dashboardPages` 동적 slug, 현재 로컬 런타임 확인 결과

## 1. 확인 기준

- 운영자용 내부 대시보드다. 화면은 백엔드의 실제 상태를 그대로 보여주고, 프런트에서 임의 상태를 만들지 않는다.
- `pause`, `live ready`, `approval armed`, `blocked reason`은 서로 다른 상태로 표시한다.
- 한국어 운영 문구를 유지하고, 모바일에서도 핵심 정보가 가려지지 않게 한다.
- 목록, 로그, 백로그는 최신 활동 기준으로 빠르게 스캔되게 한다.
- 설정/제어 화면을 제외한 페이지는 읽기 전용을 기본 원칙으로 둔다.

## 2. 현재 라우트 맵

실제 앱 셸은 `frontend/components/app-chrome.tsx`의 `AppChrome`이다. `frontend/components/nav.tsx`의 `AppNav`는 현재 검색 결과상 사용처가 없다.

| 구분 | URL | 현재 메뉴 노출 | 주 역할 |
| --- | --- | --- | --- |
| 운영 개요 | `/` | 노출 | 전체 거래 상태, 안전 상태, 확인 필요 항목, 노출/보호, 비용, 감사 로그 |
| 계좌 / 잔고 | `/dashboard/account` | 노출 | Binance 원본 캐시 또는 로컬 동기화 기준 계정/잔고 확인 |
| 비용 분해 | `/dashboard/cost-breakdown` | 노출 | 기간별 손익, 수수료, 펀딩비, 슬리피지 read-only 분석 |
| 시장 상태 | `/dashboard/market` | 노출 | 가격/지표 입력, 시장 데이터 freshness, 캔들 차트 |
| AI 판단 | `/dashboard/decisions` | 노출 | 현재 AI 판단, 진입 흐름 탭, 최근 decision row |
| 포지션 | `/dashboard/positions` | 노출 | 열린 포지션, 보호 가격, 손익, 보호 주문 상태 |
| 주문 / 체결 | `/dashboard/orders` | 노출 | position_id 기준 주문/체결 묶음, 정산/동기화 상태 |
| 안전 점검 | `/dashboard/safety-checks` | 노출 | risk check 호출별 입력/판단/차단 사유/원본 JSON 감사 |
| 자동 실행 | `/dashboard/scheduler` | 노출 | 스케줄러 상태, 다음 실행, 심볼별 AI 검토/생략 상태 |
| 감사 로그 | `/dashboard/audit` | 노출 | 감사 이벤트 탐색, 탭/검색/정렬/건수 필터 |
| 설정 | `/dashboard/settings` | 노출 | 운영 설정, 연동 설정, live arm/pause 등 mutation-capable 제어 |
| 고급 디버그 | `/dashboard/agents` | 노출 | 최근 AI 실행 기록 요약 |
| 리스크 상태 | `/dashboard/risk` | 미노출 | risk check 카드와 운영 알림. 안전 점검과 역할 중복 있음 |

## 3. 로컬 런타임 확인 결과

### 3.1 백엔드

- `http://127.0.0.1:8000/health`: `{"status":"ok","mode":"service_ready","database":"ready"}`

### 3.2 현재 3000 접근 상태

- `http://127.0.0.1:3000` 라우트들은 SSR 응답은 모두 `200`이다.
- 단, `/dashboard/market`, `/dashboard/decisions`, `/dashboard/positions`, `/dashboard/orders`, `/dashboard/scheduler`, `/dashboard/audit`, `/dashboard/settings`, `/dashboard/agents`, `/dashboard/risk`는 클라이언트 동적 청크 `/_next/static/chunks/0p25a_bqe1z5z.js`가 `500`으로 실패했다.
- 결과적으로 해당 화면들은 사용자 관점에서 `화면을 불러오는 중입니다.`에 멈출 수 있다.
- 포트 `3000`에는 `next start` 프로세스가 2개 확인됐다.
  - PID `21568`: `next start --hostname 127.0.0.1 --port 3000`
  - PID `8308`: `next start --hostname 0.0.0.0 --port 3000`

### 3.3 현재 소스 기준 dev 서버 3005

`http://127.0.0.1:3005`에서는 13개 라우트가 모두 `200`으로 렌더링됐고, Playwright 기준 실패 네트워크 응답/콘솔 오류는 확인되지 않았다.

초기 HTML 크기:

| URL | 초기 응답 |
| --- | ---: |
| `/` | 113.4 KB |
| `/dashboard/account` | 21.7 KB |
| `/dashboard/cost-breakdown` | 111.0 KB |
| `/dashboard/market` | 1301.2 KB |
| `/dashboard/decisions` | 167.7 KB |
| `/dashboard/positions` | 40.5 KB |
| `/dashboard/orders` | 536.7 KB |
| `/dashboard/safety-checks` | 5852.5 KB |
| `/dashboard/scheduler` | 86.6 KB |
| `/dashboard/audit` | 334.0 KB |
| `/dashboard/settings` | 121.0 KB |
| `/dashboard/agents` | 88.0 KB |
| `/dashboard/risk` | 226.9 KB |

## 4. 페이지별 기획

### 4.1 운영 개요 `/`

목표: 운영자가 첫 화면에서 “지금 새 주문이 나가도 되는가 / 확인해야 할 것이 있는가”를 판단한다.

현재 구성:

- 현재 거래 상태
- 기준 시각, 자동 실행, 상태 동기화
- 안전 상태
- 확인 필요 항목
- 노출 및 보호 요약
- 수익성 비용 분해
- 최근 감사 로그

개선 방향:

- `확인 필요`는 단순 관망/조건 미충족을 제외하고 운영 개입이 필요한 항목만 유지한다.
- 비용 카드에서 `/dashboard/cost-breakdown`으로 이어지는 링크를 더 명확히 한다.
- 최근 감사 로그에서 위험 이벤트는 `/dashboard/audit?tab=risk` 같은 탭 링크로 연결한다.
- 운영 상태 문구는 backend `control_status_summary`, `sync_freshness_summary`, `protection_reason_codes`를 그대로 출처로 삼는다.

우선순위: P1

### 4.2 계좌 / 잔고 `/dashboard/account`

목표: 최종 진입 가능 여부가 아니라 계정 원본 상태와 잔고/포지션/미체결 주문 출처를 확인한다.

현재 구성:

- Binance 원본 캐시 또는 최근 로컬 동기화 fallback
- 캐시 상태/경과 시간/갱신 소요
- 잔고 카드
- 계정 요약, 보유 자산, 포지션, 미체결 주문 표

리스크:

- 화면 진입만으로 `refreshAccountCache()` 자동 호출이 실행된다. 읽기 전용 화면으로 보이지만 실제로는 `/api/binance/account/refresh` POST를 발생시킬 수 있다.

개선 방향:

- 자동 갱신 요청을 유지할지 정책 결정이 필요하다. 운영 안정성 기준으로는 “조회 화면 진입 = POST 없음”이 더 안전하다.
- 원본 캐시가 지연된 경우 개요 화면의 신규 진입 상태와 명확히 분리해 표시한다.
- 미체결 주문은 계속 Binance 원본 캐시 기준만 표시하고, 로컬 주문 로그를 섞지 않는다.

우선순위: P0 또는 P1. 자동 POST 제거/명시화는 운영 정책 결정 후 진행한다.

### 4.3 비용 분해 `/dashboard/cost-breakdown`

목표: 거래 전략 판단이 아니라 총손익이 수수료, 펀딩비, 불리한 체결에 얼마나 소모되는지 read-only로 확인한다.

현재 구성:

- 월간/연간/오늘 기간 선택
- 손익/비용 요약
- 비용/동기화 경고
- 데이터 품질 배지
- 기간별 상세 테이블

개선 방향:

- 현재 화면 목적은 명확하다. 개요 화면의 오늘 카드와 이 상세 화면의 역할을 계속 분리한다.
- 그래프가 필요하면 “손익 vs 총비용 vs 수수료/펀딩/슬리피지” 추이를 별도 섹션으로 추가하되, API contract는 `summary`, `buckets`, `data_quality`, `warnings`를 유지한다.
- 데이터 미확정 상태에서는 숫자를 확정값처럼 강조하지 않는다.

우선순위: P2

### 4.4 시장 상태 `/dashboard/market`

목표: AI 판단/리스크/실행을 섞지 않고 가격 입력과 지표 입력의 freshness를 확인한다.

현재 구성:

- 심볼 탭과 전체 보기
- 시장 데이터 소스/상태/실시간 캔들 스트림
- 캔들 차트, 봉 기준, 표시 봉 수
- 심볼별 최신 입력 상태
- 시장 스냅샷/특성 입력 테이블

리스크:

- dev 기준 초기 응답이 약 `1.3 MB`로 크다.
- 시장 차트/테이블이 같은 페이지에 집중돼 있어 SSR payload와 hydration 비용이 커질 수 있다.

개선 방향:

- 기본 첫 화면은 freshness와 대표 차트 중심으로 유지하고, 원본 snapshot/feature 표는 접기 또는 별도 상세 섹션으로 지연 로드한다.
- `ALL` 비교와 단일 심볼 차트의 정보 구조를 분리한다.
- fallback source, Redis/cache 상태, Binance REST 보조 경로는 운영자가 바로 이해할 수 있는 badge/detail 구조로 유지한다.

우선순위: P1

### 4.5 AI 판단 `/dashboard/decisions`

목표: 현재 AI 판단과 이번 판단 주기 실행 상태를 같은 심볼 기준으로 확인한다.

현재 구성:

- 현재 결론
- 이번 판단 주기 주문 여부
- 포지션 상태
- 심볼 탭
- 마지막 AI 스냅샷, 이번 주기 AI 상태, 최근 AI 호출 시각
- 진입 흐름 탭: `summary`, `plan`, `execution`
- 최근 평가 기록

개선 방향:

- “마지막 AI 스냅샷”과 “이번 판단 주기 상태”를 계속 분리한다. stale snapshot을 현재 결론처럼 보이게 만들면 안 된다.
- `flow=plan`, `flow=execution` 탭은 URL 공유 가능 상태를 유지한다.
- 리스크 승인/실행 실패 원인은 `/dashboard/risk`, `/dashboard/orders`로 이어지는 링크를 추가한다.

우선순위: P1

### 4.6 포지션 `/dashboard/positions`

목표: 현재 열린 포지션이 보호되고 있는지, 손익과 보호 가격이 운영 가능한 상태인지 확인한다.

현재 구성:

- 열린 포지션 카드
- 상태/mode/protection badge
- 진입가/현재가, 수량/평가금액, 손절가, 목표가
- 보상/위험, 미실현/실현 손익, 보호 주문, 누락 보호 항목

개선 방향:

- position row에서 `/dashboard/orders?position_id=...` 또는 해당 position group으로 바로 이동하는 링크를 제공한다.
- 보호 주문 누락, hedge/one-way mismatch, 정산 미확정은 별도 강조 상태로 둔다.
- 열린 포지션 없음 상태는 정상 상태와 데이터 로드 실패를 구분한다.

우선순위: P1

### 4.7 주문 / 체결 `/dashboard/orders`

목표: flat order log가 아니라 position_id 기준으로 주문, 보호 주문, execution을 묶어서 정산/동기화 상태를 확인한다.

현재 구성:

- 포지션 단위 실행 이력
- 요약/주문/체결 탭
- 묶음 수, 주문 수, 체결 수
- position별 상태 badge
- 정산/체결 동기화 표시

리스크:

- 초기 응답이 약 `536.7 KB`다.
- 기본 `limit=80` 주문/체결을 모두 가져와 position group을 구성한다.

개선 방향:

- URL query로 `tab`, `symbol`, `position_id` 필터를 명시적으로 지원한다.
- 기본은 최근 position group 요약만, 주문/체결 상세는 선택 group 지연 로드로 전환한다.
- close execution 누락, local-only fee, exchange-confirmed PnL은 계속 명시적으로 분리한다.

우선순위: P1

### 4.8 안전 점검 `/dashboard/safety-checks`

목표: risk_guard/safety check 호출별 입력, 판단 결과, 차단 사유, 원본 JSON을 read-only 감사 화면으로 확인한다.

현재 구성:

- risk check 50건
- audit event 200건 병합
- 호출별 입력/판단/차단 사유/원본 JSON

리스크:

- 초기 응답이 약 `5.85 MB`로 가장 크다.
- 원본 JSON을 50건 upfront 렌더링하면 페이지 열람, hydration, 브라우저 메모리 모두에 부담이 크다.

개선 방향:

- 기본 목록은 요약 카드 20건 이하로 제한한다.
- 원본 JSON은 선택한 risk check의 detail drawer 또는 별도 상세 route에서 요청한다.
- audit event 매칭도 목록 응답에 필요한 ID/상태만 SSR하고, 원본 payload는 필요 시 fetch한다.
- “안전 점검”과 `/dashboard/risk`의 역할을 재정의한다. 안전 점검은 감사/detail, 리스크 상태는 현재 운영 판단 요약으로 유지하는 편이 좋다.

우선순위: P0

### 4.9 자동 실행 `/dashboard/scheduler`

목표: AI 판단 자체보다 언제 검토가 예정됐고, 왜 건너뛰었고, 중복 처리됐는지 확인한다.

현재 구성:

- 현재 scheduler 상태
- 실행 윈도우
- 다음 실행 예정
- 운영 상태
- 심볼별 AI 검토/생략 상태
- scheduler run table

개선 방향:

- scheduler row와 decision/risk row를 같은 시간축으로 추적할 수 있는 링크를 추가한다.
- “다음 실행 예정”은 stale runtime인지 정상 cadence인지 구분한다.
- AI skip reason은 원본 코드와 운영 문구를 함께 보여주되, 내부 코드는 보조로 둔다.

우선순위: P1

### 4.10 감사 로그 `/dashboard/audit`

목표: 운영 이벤트를 카테고리/심각도/검색/정렬 기준으로 탐색한다.

현재 구성:

- 탭: 전체, 리스크, 실행, 승인/운영제어, 보호주문, 헬스/시스템, AI/의사결정
- 검색
- 심각도 필터
- 최신순/오래된순/심각도 우선
- 30/50/100건
- 20초 auto refresh

리스크:

- 초기 응답이 약 `334.0 KB`다.
- 검색/필터는 클라이언트 중심이고, row payload가 커지면 비용이 증가한다.

개선 방향:

- `tab`, `severity`, `q`, `limit`, `sort`를 API query로 넘겨 서버 필터링을 우선한다.
- audit detail은 선택 row 확장 시 원본 payload를 가져온다.
- 과거 정책 기록/억제 상태 안내 문구는 유지하되, 상세 정책 장문은 별도 문서 링크로 넘긴다.

우선순위: P2

### 4.11 설정 `/dashboard/settings`

목표: 운영 설정과 연동 설정을 제어하되, mutation-capable 화면이라는 사실을 명확히 드러낸다.

현재 구성:

- 운영 설정 / 연동 설정 탭
- OpenAI/Binance/FRED/Event source credential 상태
- live arm/disarm, pause/resume, live sync, resume attempt
- 추적 심볼, cadence override, no-trade window, event operator view
- 설정 저장과 테스트 액션

개선 방향:

- 화면 상단에 “이 화면은 상태를 변경할 수 있음”을 계속 명확히 둔다.
- 저장 전/저장 후 상태 차이를 표시한다.
- live arm 차단 사유는 backend `control_status_summary.live_arm_disable_reason` 기준으로 표시한다.
- 설정 변경 액션과 단순 연결 테스트 액션을 시각적으로 분리한다.

우선순위: P1

### 4.12 고급 디버그 `/dashboard/agents`

목표: 최근 AI 실행 기록을 debug 목적에서 요약 확인한다.

현재 구성:

- 최근 AI 실행 요약
- agent run table

개선 방향:

- decision/risk/scheduler와 연결되는 ID 링크를 제공한다.
- 원본 payload는 기본 table에 모두 노출하지 않고 필요 시 펼친다.
- 일반 운영자 메뉴와 구분되는 debug 영역으로 유지한다.

우선순위: P2

### 4.13 리스크 상태 `/dashboard/risk`

목표: AI 추천보다 우선하는 리스크 가드 결과와 운영 알림을 현재 운영 판단 기준으로 보여준다.

현재 구성:

- 리스크 점검 설명
- 최근 리스크 점검 카드
- 운영 알림 table

문제:

- `dashboardPages`에는 존재하지만 `AppChrome` 메뉴에는 없다.
- `/dashboard/safety-checks`와 역할이 겹친다.

개선 방향:

- 선택지 A: 현재 운영 리스크 요약 화면으로 유지하고 좌측 메뉴에 노출한다.
- 선택지 B: 안전 점검 화면으로 통합하고 `/dashboard/risk`는 redirect/deprecate한다.
- 추천은 선택지 A다. 이유는 safety-checks가 호출별 감사/detail이고, risk는 운영자가 “왜 주문이 안 나갔는가”를 읽는 요약 화면으로 분리할 수 있기 때문이다.

우선순위: P1

## 5. 공통 정보 구조

권장 상위 내비게이션 그룹:

1. 운영 판단
   - `/`
   - `/dashboard/decisions`
   - `/dashboard/risk`
   - `/dashboard/scheduler`
2. 거래 상태
   - `/dashboard/account`
   - `/dashboard/positions`
   - `/dashboard/orders`
   - `/dashboard/market`
3. 분석 / 감사
   - `/dashboard/cost-breakdown`
   - `/dashboard/safety-checks`
   - `/dashboard/audit`
4. 설정 / 디버그
   - `/dashboard/settings`
   - `/dashboard/agents`

공통 페이지 규칙:

- 상단 1개 문장: 이 화면의 판단 범위와 아닌 범위를 명시한다.
- 첫 카드: 운영자가 지금 해야 할 판단 하나를 먼저 보여준다.
- raw/internal code는 보조 라벨 또는 detail에 둔다.
- backend source, sync status, stale reason, fallback reason은 숨기지 않는다.
- 표는 기본 요약, 원본 payload는 선택 후 펼침으로 둔다.

## 6. 우선순위

### P0. 먼저 막아야 할 것

1. `3000` production 접근에서 동적 청크 500으로 주요 페이지가 loading에 멈추는 문제를 해결한다.
2. `3000`의 중복 `next start` listener 상태를 운영 배포/서비스 기준으로 정리한다. 단, 실행 전 live safety gate를 확인해야 한다.
3. `/dashboard/safety-checks` 초기 payload를 줄인다. 목표: 초기 HTML 800 KB 이하, 원본 JSON은 상세 요청으로 이동.
4. `/dashboard/account` 첫 진입 자동 POST 정책을 결정한다. 읽기 전용 원칙을 적용하면 자동 갱신을 버튼 기반으로 바꾼다.

### P1. 정보 구조와 운영 UX

1. `/dashboard/risk`를 메뉴에 노출할지, safety-checks에 통합할지 결정한다.
2. `AppChrome` 기준 메뉴만 유지하고, 미사용 `AppNav`는 제거 또는 명확히 legacy 처리한다.
3. 시장/주문/감사 대형 페이지는 요약 우선, 상세 지연 로드로 나눈다.
4. position, order, execution, decision, risk, audit 간 ID 기반 이동 링크를 추가한다.
5. 설정 화면은 mutation-capable 경계를 더 강하게 표시한다.

### P2. 분석성과 사용성

1. 비용 분해에 기간별 시각화 섹션을 추가한다.
2. 감사 로그 서버 필터링을 도입한다.
3. agent debug table에서 payload 상세를 lazy detail로 분리한다.
4. 모바일에서 표/카드의 핵심 status badge와 시간/ID가 가려지지 않는지 회귀 확인한다.

## 7. 구현용 Codex 프롬프트

### 프롬프트 1: 3000 런타임/배포 상태 진단

```text
C:\my-trading-bot에서 읽기 전용으로 현재 frontend runtime 상태를 진단해줘.

범위:
- AGENTS.md와 frontend/AGENTS.md 먼저 확인
- 127.0.0.1:8000/health 확인
- 3000/3005 포트 listener, PID, command line 확인
- 127.0.0.1:3000의 /, /dashboard/market, /dashboard/decisions, /dashboard/orders, /dashboard/settings를 Playwright 또는 fetch로 확인
- 동적 chunk 500, loading stuck 여부 확인

금지:
- 프로세스 kill/restart 금지
- 코드 수정 금지
- 설정/제어 버튼 클릭 금지

산출물:
- 3000이 현재 배포로 신뢰 가능한지 결론
- stale build, duplicate listener, backend 8000 의존성 중 원인 후보 분리
- 안전하게 정리하려면 필요한 gate 목록
```

### 프롬프트 2: safety-checks payload 축소

```text
C:\my-trading-bot에서 /dashboard/safety-checks 초기 렌더링 payload를 줄여줘.

목표:
- 초기 HTML을 800KB 이하로 낮추기
- 목록은 최근 20건 요약만 SSR
- 원본 JSON/payload는 선택한 risk_check 상세에서만 lazy fetch 또는 details route로 조회
- 주문, pause/live, approval 상태 변경 없음

확인:
- AGENTS.md, frontend/AGENTS.md 확인
- backend endpoint와 frontend SafetyCheckList 데이터 흐름 확인
- 기존 risk check/audit event 매칭 의미 보존
- pnpm -C frontend lint
- pnpm -C frontend build
- 관련 frontend helper test가 있으면 실행
- git diff --check

주의:
- raw reason code는 숨기지 말고 상세로 이동
- 운영자 첫 화면에는 판단 결과, 차단 사유, 생성 시각, audit event 연결 여부만 노출
```

### 프롬프트 3: 내비게이션/페이지 역할 정리

```text
C:\my-trading-bot frontend 내비게이션과 페이지 역할을 정리해줘.

목표:
- 실제 사용 중인 AppChrome 기준으로 메뉴를 정리
- 미사용 AppNav 사용처를 확인하고 제거 또는 legacy 처리
- /dashboard/risk를 메뉴에 노출할지 safety-checks와 통합할지 코드 근거로 제안 후 최소 변경
- 메뉴 라벨은 plain Korean 운영 문구 유지

검증:
- 모든 메뉴 URL이 200으로 열리는지 확인
- Playwright로 주요 페이지 heading과 nav active 상태 확인
- pnpm -C frontend lint
- pnpm -C frontend build
```

### 프롬프트 4: 주문/포지션/리스크 연결성 개선

```text
C:\my-trading-bot에서 운영자가 position_id 기준으로 포지션-주문-체결-리스크-감사 흐름을 따라갈 수 있게 연결성을 개선해줘.

범위:
- /dashboard/positions에서 관련 주문/체결 묶음으로 이동
- /dashboard/orders에서 position_id, symbol query 필터 지원
- /dashboard/risk에서 decision_run_id, risk_check_id, audit event 연결 표시
- /dashboard/audit에서 관련 entity 링크 제공

금지:
- risk.py, execution.py, scheduler.py 정책 변경 금지
- 주문 실행/취소/동기화 액션 추가 금지

검증:
- frontend lint/build
- 관련 helper tests
- Playwright로 positions -> orders -> audit 이동 확인
```

## 8. 검수 기준

- `127.0.0.1:3000` 기준으로 좌측 메뉴의 모든 URL이 loading stuck 없이 렌더링된다.
- dynamic chunk 500, hydration error, console error가 없다.
- 설정 화면을 제외하고 첫 페이지 진입만으로 POST/PUT/DELETE가 발생하지 않는다. 예외가 필요하면 화면에 명시한다.
- safety-checks 초기 HTML은 800 KB 이하로 줄인다.
- market 초기 HTML은 500 KB 이하를 목표로 한다.
- orders/audit는 기본 목록을 요약 중심으로 유지하고 원본 payload는 lazy detail로 분리한다.
- 모든 operator-facing 문구는 한국어 우선이며, raw/internal code는 보조로 둔다.
