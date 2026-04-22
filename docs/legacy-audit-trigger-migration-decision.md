# Legacy Audit Trigger Migration / Archival Decision Memo

## 목적

이 문서는 과거 audit row에 남아 있을 수 있는 legacy trigger reason

- `open_position_recheck_due`
- `periodic_backstop_due`

에 대해, 실제 migration 또는 archival을 지금 수행할지 여부를 운영 관점에서 판단하기 위한 decision memo다.

이 문서는 구현 지시가 아니다.  
SQL migration, schema 변경, backfill 코드는 포함하지 않는다.

## 현재 전제

- 현재 runtime source of truth는 `backend/trading_mvp/services/orchestrator.py`, `backend/trading_mvp/services/scheduler.py`, `docs/api.md`다.
- 현재 runtime에서는 시간 경과만으로 AI review를 다시 만들지 않는다.
- 과거 raw audit row에는 legacy trigger reason이 남아 있을 수 있다.
- 현재 UI는 이를 `과거 정책 기록`으로 분리 해석한다.
- `risk_guard`, protection, execution control, pause/approval semantics는 이 결정과 분리한다.

관련 문서:

- [docs/api.md](/C:/my-trading-bot/docs/api.md)
- [docs/audit-legacy-trigger-policy.md](/C:/my-trading-bot/docs/audit-legacy-trigger-policy.md)

## 평가 기준

이번 판단은 아래 4개 기준으로 본다.

1. forensic 보존
2. 운영 리스크
3. query 복잡도
4. UI / consumer 영향

추가로 함께 보는 보조 기준:

- rollback 가능성
- 감사 대응성
- 운영 복잡도

## 옵션 비교

### 옵션 A. Raw 유지 + UI / read-model 해석만 유지

설명:

- DB row와 raw payload는 그대로 둔다.
- UI/helper/read-model에서만 legacy trigger를 `과거 정책 기록`으로 해석한다.

장점:

- 가장 작은 변경이다.
- forensic 원문 보존이 가장 단순하다.
- rollback이 사실상 필요 없다.
- runtime / execution semantics를 건드리지 않는다.

단점:

- raw row를 직접 읽는 consumer는 별도 해석이 필요하다.
- schema drift 시 helper 갱신이 필요하다.
- ad-hoc SQL만으로는 현재/과거 semantics 분리가 불편하다.

평가:

- forensic 보존: 매우 좋음
- 운영 리스크: 낮음
- query 복잡도: 중간
- UI / consumer 영향: UI는 낮음, 외부 consumer는 중간
- rollback 가능성: 매우 높음
- 감사 대응성: 좋음
- 운영 복잡도: 낮음

### 옵션 B. Legacy flag 추가

설명:

- raw field는 유지하고, 별도 `is_legacy_trigger_semantics` 또는 `trigger_semantics_version` 같은 flag를 추가한다.

장점:

- UI 밖 consumer도 legacy 여부를 쉽게 구분할 수 있다.
- SQL / export / BI에서 필터링이 쉬워진다.

단점:

- backfill 또는 read-time inference 규칙이 필요하다.
- raw field와 flag가 어긋날 수 있다.
- schema / API / 문서가 동시에 늘어난다.

평가:

- forensic 보존: 좋음
- 운영 리스크: 중간
- query 복잡도: 낮음
- UI / consumer 영향: 낮음
- rollback 가능성: 중간
- 감사 대응성: 좋음
- 운영 복잡도: 중간

### 옵션 C. One-time backfill with preserved raw field

설명:

- 과거 row에 normalized semantic field를 한 번 backfill한다.
- 예:
  - `raw_trigger_reason`
  - `normalized_trigger_reason`
  - `trigger_semantics_version`

장점:

- row continuity를 유지하면서 queryable semantics를 확보할 수 있다.
- UI 외 consumer도 현재/과거 의미를 쉽게 구분할 수 있다.

단점:

- inference가 틀리면 복구 비용이 커진다.
- migration 검증, sampling, rollback 계획이 필요하다.
- 현재 단계 기준으로는 투자 대비 실익이 크지 않다.

평가:

- forensic 보존: 좋음
- 운영 리스크: 중간 이상
- query 복잡도: 낮음
- UI / consumer 영향: 낮음
- rollback 가능성: 중간 이하
- 감사 대응성: 좋음
- 운영 복잡도: 높음

### 옵션 D. Archival table 분리

설명:

- legacy trigger row를 별도 archival table 또는 별도 저장 영역으로 분리한다.

장점:

- 현재 운영 테이블을 더 깔끔하게 유지할 수 있다.
- 현재 정책 row와 historical row를 물리적으로 분리할 수 있다.

단점:

- query 경로, runbook, 데이터 흐름이 복잡해진다.
- 시간 순서 추적과 cross-reference가 더 어려워질 수 있다.
- audit trail 연속성 설명 비용이 늘어난다.

평가:

- forensic 보존: 가능
- 운영 리스크: 높음
- query 복잡도: 높음
- UI / consumer 영향: 중간 이상
- rollback 가능성: 낮음
- 감사 대응성: 케이스에 따라 좋을 수 있으나 운영 설명 비용 큼
- 운영 복잡도: 매우 높음

## 현재 프로젝트 기준 권장안

권장안: **옵션 A. Raw 유지 + UI / read-model 해석만 유지**

이유:

- 현재 단계의 우선순위는 실거래 코어 안정화다.
- 이미 UI 해석은 `과거 정책 기록`으로 분리돼 있다.
- runtime semantics는 backend에서 정리됐고, 현재 남은 문제는 raw row 보존 정책이다.
- 지금 schema / migration / archival을 열면 얻는 이익보다 운영 리스크가 크다.

한 줄 판단:

> 지금은 raw를 건드리지 말고, 해석만 분리하는 것이 맞다.

## 보류안

보류안: **옵션 B. Legacy flag 추가**

보류 이유:

- 가장 현실적인 다음 단계 후보이긴 하지만, 아직은 필요성이 충분히 크지 않다.
- 외부 consumer, BI, compliance 요구가 커질 때 재검토하는 것이 맞다.

즉:

- 지금 당장 하지는 않음
- 하지만 향후 첫 번째 승격 후보는 archival보다 flag 추가다

## 지금 하지 말아야 할 것

현재 단계에서 비권장:

- legacy raw row 삭제
- legacy raw `trigger_reason` 덮어쓰기
- archival table 먼저 도입
- `risk_guard` / protection / execution control 변경과 함께 묶어서 처리
- migration과 UI cleanup을 한 번에 묶는 큰 diff

## 재검토 조건

아래 중 하나가 생기면 옵션 B 또는 C를 다시 검토한다.

- UI 밖 consumer가 늘어나 helper-only 해석 유지비가 커지는 경우
- BI / compliance / external report에서 SQL 기준의 legacy 분리가 필요한 경우
- audit schema drift가 잦아 helper 추적 비용이 커지는 경우
- 감사 대응에서 DB 레벨에서 바로 `legacy / current semantics` 구분이 필요한 경우

## 결정 메모

현재 결론:

- **실행 결정:** migration / archival 미실행
- **운영 정책:** raw 보존 + 해석 분리 유지
- **문서 source of truth:** `docs/api.md`, `docs/audit-legacy-trigger-policy.md`
- **후속 트랙:** 필요 시 별도 migration/archival decision 트랙으로만 다룰 것
