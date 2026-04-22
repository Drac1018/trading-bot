# Audit Legacy Trigger Retention Policy

## 목적

이 문서는 과거 audit row에 남아 있을 수 있는 legacy `trigger_reason` 값

- `open_position_recheck_due`
- `periodic_backstop_due`

를 현재 runtime 정책과 혼동하지 않도록 보존/해석 원칙을 정리한 운영 정책 초안이다.

현재 저장소의 우선순위는 실거래 코어 안정화이므로, 이 문서는 기능 확장보다 아래 원칙을 우선한다.

- forensic / audit 원문 보존
- 현재 runtime 의미와 과거 stored semantics 분리
- `risk_guard`, protection, execution control 불변
- 작은 diff 우선

## 현재 기준선

현재 runtime 기준:

- 시간 경과만으로 AI review를 다시 만들지 않는다.
- scheduler는 orchestration loop로 계속 돌지만, AI review 사유는 event / selection / protection / manual 경로만 사용한다.
- `next_ai_review_due_at`는 호환성 필드일 뿐, 현재 운영상 예정 시각 의미로 쓰지 않는다.

현재 runtime trigger reason:

- `entry_candidate_event`
- `breakout_exception_event`
- `protection_review_event`
- `manual_review_event`

과거 stored audit / metadata에는 아래 legacy 값이 남아 있을 수 있다.

- `open_position_recheck_due`
- `periodic_backstop_due`

이 값들은 현재 runtime 정책이 아니라, 과거 정책으로 생성된 historical record다.

## 보존 원칙

1. 과거 audit row의 raw field는 forensic 증적이므로 기본적으로 수정하지 않는다.
2. 현재 UI/consumer는 raw 값을 현재 runtime trigger처럼 해석하지 않고 `과거 정책 기록`으로 분리 표시한다.
3. `risk_guard`, protection, execution control, pause/approval semantics는 이 정책 논의와 분리한다.
4. 운영 중 혼선이 생기더라도 먼저 해석 계층(UI/read-model)을 정리하고, raw rewrite나 migration은 마지막 수단으로 본다.

## 옵션 비교

### 옵션 1. Raw 유지 + UI 해석만 유지

방법:

- DB row와 raw audit payload는 그대로 둔다.
- UI/helper/read-model에서만 legacy trigger를 `과거 정책 기록`으로 해석한다.

장점:

- 가장 작은 diff다.
- DB migration이 없다.
- forensic 원문 보존이 가장 단순하다.
- 실거래 경로와 execution semantics를 건드리지 않는다.

단점:

- raw row를 직접 읽는 외부 consumer는 여전히 오해할 수 있다.
- UI/helper가 `trigger_reason` 키 구조를 계속 따라가야 한다.
- BI / ad-hoc SQL 조회에서 별도 설명이 필요하다.

운영 리스크:

- 낮음
- 주의점은 schema drift 시 helper 갱신 누락이다.

감사 가능성 영향:

- 원문 보존 측면에서는 가장 좋다.
- 해석 일관성은 UI/helper 품질에 의존한다.

### 옵션 2. Legacy flag 추가

방법:

- 기존 raw field는 남기고, 별도 `legacy_trigger_semantics=true` 또는 `trigger_semantics_version=legacy_time_based_review` 같은 표시 필드를 추가한다.

장점:

- consumer가 레거시 여부를 더 쉽게 구분할 수 있다.
- SQL / export / BI에서 필터링이 쉬워진다.

단점:

- backfill 또는 read-time inference가 필요하다.
- raw field와 추가 flag가 어긋날 수 있다.
- API/schema/contracts가 늘어난다.

운영 리스크:

- 중간
- flag 생성 규칙과 backfill 검증이 필요하다.

감사 가능성 영향:

- raw + flag를 함께 남기면 좋다.
- 다만 dual source가 생겨 설명 책임이 늘어난다.

### 옵션 3. Archival table 분리

방법:

- legacy trigger를 가진 과거 row를 별도 archival table로 복사/이관한다.

장점:

- 현재 운영 테이블은 더 깔끔해진다.
- 현재 정책 row와 과거 정책 row가 물리적으로 분리된다.

단점:

- migration / runbook / query 경로가 복잡해진다.
- 시간 순서 조회, 상관관계 조회, operator 설명이 더 어려워질 수 있다.
- 실익 대비 작업 범위가 크다.

운영 리스크:

- 높음
- 실수 시 audit trail 연속성이 깨질 수 있다.

감사 가능성 영향:

- raw 보존은 가능하다.
- 하지만 “찾기 쉬운가” 관점에서는 오히려 나빠질 수 있다.

### 옵션 4. One-time migration with preserved raw field

방법:

- 과거 row는 그대로 유지하되, one-time migration으로 normalized semantic field를 backfill한다.
- 예:
  - `raw_trigger_reason`
  - `normalized_trigger_reason`
  - `trigger_semantics_version`
  - `is_legacy_trigger_semantics`

장점:

- row continuity를 유지하면서 queryable semantics를 확보할 수 있다.
- UI 외 consumer도 현재/과거 의미를 쉽게 구분할 수 있다.
- raw field를 남기면 forensic 보존도 유지된다.

단점:

- one-time backfill 검증 비용이 크다.
- 과거 row inference가 틀리면 되돌리기 까다롭다.
- schema/API/read-model 동시 변경이 필요하다.

운영 리스크:

- 중간 이상
- migration 검증, rollback 계획, sampling review가 필요하다.

감사 가능성 영향:

- raw + normalized를 함께 남기면 강하다.
- 다만 현재 단계에서는 과한 투자일 수 있다.

## 현재 프로젝트 기준 권장안

권장안: **옵션 1. Raw 유지 + UI 해석만 유지**

권장 이유:

- 현재 단계의 최우선은 실거래 코어 안정화다.
- 이미 decisions / overview / scheduler / audit 화면에서 legacy trigger를 `과거 정책 기록`으로 분리 해석하도록 정리돼 있다.
- runtime semantics는 이미 backend에서 정리됐고, 지금 남은 문제는 raw forensic data를 어떻게 읽을지다.
- 이 시점에 migration이나 archival 분리는 얻는 이익보다 운영 리스크가 크다.

즉, **지금은 raw를 건드리지 말고 해석만 분리**하는 것이 가장 맞다.

## 권장 운영 규칙

1. 과거 `open_position_recheck_due`, `periodic_backstop_due` row는 삭제/수정하지 않는다.
2. UI와 운영 문서에서는 이를 `과거 정책 기록`으로 명시한다.
3. 새 audit schema가 `trigger_reason / last_ai_trigger_reason` 외 다른 키를 쓰기 시작하면, read helper를 먼저 갱신한다.
4. 외부 export / BI / 운영 분석 문서에서도 legacy trigger는 현재 runtime trigger로 집계하지 않는다.
5. 신규 migration은 아래 조건이 생길 때만 검토한다.

## 옵션 재검토 트리거

아래 중 하나가 생기면 옵션 4를 우선 후보로 다시 검토한다.

- UI 밖의 consumer가 많아져 helper-only 해석이 유지비를 넘는 경우
- BI / compliance / external report에서 SQL 기준의 legacy 분리가 필요해진 경우
- schema drift가 잦아 `trigger_reason` 위치 추론 비용이 커진 경우
- 감사 대응에서 raw 값과 현재 정책 경계를 DB 레벨에서 바로 보여줘야 하는 경우

## 비권장안

현재 단계에서 아래는 권장하지 않는다.

- legacy raw row 삭제
- legacy raw row의 trigger reason 덮어쓰기
- archival table 우선 도입
- `risk_guard` / protection / execution control과 legacy trigger 정리를 한 번에 묶는 변경

## 구현/운영 참고

현재 프런트 해석 계층 참고 파일:

- `frontend/lib/decision-timeline.ts`
- `frontend/lib/audit-log.ts`
- `frontend/components/dashboard-views.tsx`
- `frontend/components/overview-dashboard.tsx`
- `frontend/components/log-explorer.tsx`

관련 API 해석 참고:

- `docs/api.md`

주의:

- `docs/architecture.md`, `docs/execution-flow.md`, `docs/strategy-engine-rule-surface.md`에는 과거 trigger 설명이 남아 있을 수 있다.
- 이 문서들은 historical context로 참고하고, 현재 운영 의미 해석의 source of truth는 runtime code path와 `docs/api.md`, 그리고 본 정책 문서로 본다.
