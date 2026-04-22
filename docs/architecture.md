# Architecture

## 운영자 표현과 내부 키

- 이 문서는 구조 설명이므로 내부 구현 키를 그대로 사용합니다.
- 운영자 화면이나 운영 인계에서는 아래처럼 읽으면 됩니다.
  - `decision_cycle_interval_minutes`
  - `decision_cycle_interval_minutes_override`
  - `last_decision_at`
  - `next_decision_due_at`
    - 화면 표현: `재검토 확인 주기`
  - `ai_call_interval_minutes`
  - `ai_call_interval_minutes_override`
  - `last_ai_decision_at`
  - `next_ai_call_due_at`
    - 화면 표현: `AI 기본 검토 간격`
  - `strategy_engine`
    - 화면 표현: `전략 엔진` 또는 `진입 서사 분류`
  - `trigger_type`
    - 화면 표현: `검토 이벤트 종류`
  - `holding_profile`
    - 화면 표현: `보유 성격`
    - 값 해석: `scalp = 짧게`, `swing = 중간`, `position = 길게`
  - `entry_mode`
    - 화면 표현: `진입 방식`
  - `intent_family` / `management_action`
    - 화면 표현: `신규 진입`인지 `관리 / 보호 조치`인지 구분하는 보조 의미
- 즉, 이 문서에서 cadence, engine, trigger, holding-profile 키를 말하는 부분은 운영자 화면에서는 위 표현으로 대응해 읽으면 됩니다.

## 2026-04 Live Snapshot Availability

- `starting_equity`는 더 이상 live fallback, settings model/config, replay baseline에 사용되지 않습니다.
- 첫 Binance account sync 전에는 synthetic balance/equity를 만들지 않고 `unknown` 상태를 유지합니다.
- `pnl_summary`와 `account_sync_summary`는 둘 다 `account_snapshot_available`를 내려주며:
  - `false`면 실계좌 잔고/자산은 아직 신뢰 가능한 snapshot이 없다는 뜻입니다.
  - `true`면 최신 Binance account snapshot 기준 balance/equity를 반영하고 있다는 뜻입니다.
- operator/dashboard는 이 상태를 직접 표시하고, exposure summary는 snapshot이 없을 때 `unknown`으로 남습니다.
- replay validation equity curve는 이제 순손익 0-baseline series로 계산되며, historical drawdown은 synthetic starting balance 없이 집계됩니다.

## 2026-04 AI Context Builder

- `backend/trading_mvp/services/ai_context.py` builds one common AI input packet before provider invocation.
- The packet is deterministic and pure-function oriented so unit tests do not need a live model call.
- The builder combines:
  - composite regime summary
  - data quality / trust summary
  - previous thesis snapshot + delta
  - strategy engine / holding profile / slot / hard-stop context
- This is an input-structure change only. It does not move execution authority out of `risk.py` and `execution.py`.

## 2026-04 Historical Prior Context

- `backend/trading_mvp/services/ai_prior_context.py` attaches report-only analytics to the AI packet as soft priors.
- The prior packet is built from existing:
  - strategy-engine bucket analytics
  - capital-efficiency report buckets
  - session / time-of-day bucket history
- Read path is now conservatively memoized per SQLAlchemy session with a short-lived read-through cache keyed by `(bind, lookback, limit)` plus source-table revision state.
- The cache does not change prior semantics. If the cache is unavailable or corrupted, the builder falls back to the original full-report read path.
- Conservative sample thresholds apply before any live influence:
  - insufficient samples => `unavailable`
  - unavailable prior => no confidence boost/penalty
- The prior layer is soft only:
  - it can reduce confidence
  - it can bias AI toward `hold` / `should_abstain=true`
  - it can downgrade aggressive holding-profile recommendations back toward `scalp`
- Session/time priors are additionally calibrated by:
  - bucket recency
  - threshold-edge sample size
  - bucket concentration versus engine sample count
- Weak session/time prior alone remains light-only. It becomes more conservative only when combined with degraded/unavailable data quality.
- It does not create a new hard blocker, and it does not weaken `risk.py` or `execution.py`.

## 2026-04 Management Intent Semantics

- `long` / `short` legacy semantics are still preserved where the execution adapter needs them, but the system now also persists explicit intent classification metadata.
- The classification layer distinguishes:
  - directional entry
  - management-only actions
  - protection recovery / restore
  - exit-only actions
- This classification is used for:
  - operator/API observability
  - audit payloads
  - analytics filtering
  - historical prior hygiene
- The execution boundary is unchanged:
  - `risk.py` still makes the final deterministic allow/block decision
  - `execution.py` still consumes only approved intents
  - no new execution action type was introduced in this ticket

## 2026-04 Prompt Routing And Fail-Closed

- `backend/trading_mvp/services/ai_prompt_routing.py` is the thin adapter between hybrid triggers and the model provider.
- The adapter is responsible for:
  - resolving `strategy_engine × trigger_type -> prompt_family`
  - declaring `allowed_actions` / `forbidden_actions`
  - bounding invalid provider output before it reaches `risk.py`
  - applying fail-closed on new-entry-capable reviews when the provider times out, is unavailable, or returns invalid schema
- `protection_review_event` and other survival-path contexts stay management-only. AI may advise, but provider failure does not block deterministic protection or reduction handling.
- `breakout_exception_engine` remains scalp-only. The routing layer explicitly prevents swing/position promotion from that family.
- `risk.py` and `execution.py` remain unchanged as the final deterministic approval and execution boundary.

## 2026-04 Strategy Engine Rule Surface

- Operator-facing engine heuristics are summarized in `docs/strategy-engine-rule-surface.md`.
- That document is descriptive, not normative:
  - it explains the current selection / routing surface
  - it does not redefine risk or execution semantics
- The key split remains:
  - entry engines: `trend_pullback`, `trend_continuation`, `range_mean_reversion`, `breakout_exception`
  - management engine: `protection_reduce`

## 2026-04 Hybrid AI Review Trigger Model

- `interval_decision_cycle` remains the scheduler-owned decision loop, but AI review is no longer a full-symbol interval scan.
- The cycle now has two stages:
  - deterministic pre-AI trigger planning in `orchestrator.build_interval_decision_plan()`
  - selective AI dispatch in `scheduler.run_interval_decision_cycle()`
- Current runtime source of truth for trigger semantics is `backend/trading_mvp/services/orchestrator.py`, `backend/trading_mvp/services/scheduler.py`, and `docs/api.md`.
- Entry-side AI review is created only when a deterministic candidate event exists for that symbol.
- Open-position AI review is no longer reopened by `open_position_recheck_due` or `periodic_backstop_due`; historical rows may still carry those values as stored legacy semantics.
- `market_refresh_cycle`, `exchange_sync_cycle`, and `position_management_cycle` keep their existing cadence responsibilities and do not depend on AI availability.

### Trigger reasons

Current runtime trigger reasons:

- `entry_candidate_event`
- `breakout_exception_event`
- `protection_review_event`
- `manual_review_event`

Historical reference only:

- `open_position_recheck_due`
- `periodic_backstop_due`

### Trigger payload contract

- Every current runtime AI review trigger carries:
  - `symbol`
  - `timeframe`
  - `strategy_engine`
  - `holding_profile`
  - `assigned_slot`
  - `candidate_weight`
  - `reason_codes`
  - `trigger_fingerprint`
  - `last_decision_at`
  - `triggered_at`
- Open-position review triggers also persist:
  - `fingerprint_basis`
  - `fingerprint_changed_fields`
  - `dedupe_reason`
  - `last_material_review_at`
  - `forced_review_reason`
- `trigger_fingerprint` is used for debounce/dedupe. If the same symbol repeats with the same materially equivalent trigger fingerprint, the scheduler records a deduped skip instead of reinvoking AI.
- Historical reference: older `open_position_recheck_due` rows used a dedicated bucketed fingerprint basis: `strategy_engine`, `holding_profile`, `hard_stop_active`, `stop_widening_allowed`, regime summary, `data_quality_grade`, `thesis_degrade_detected`, `position_state_bucket`, and `protection_health_summary`.
- Entry-capable AI routes consume `data_quality` as an AI-layer safety gate before `risk.py`:
  - unavailable quality fail-closes new entry reviews
  - degraded breakout reviews fail-close before provider invocation
  - degraded/unavailable long-horizon entry proposals are bounded back to `hold`
- Survival paths remain deterministic and are not blocked by these quality gates.
- The open-position fingerprint intentionally avoids raw noisy values. Material bucket changes reopen AI review; same-basis repeats are deduped.
- Review cadence observability remains explicit: `applied_review_cadence_minutes`, `review_cadence_source`, `holding_profile_cadence_hint`, `cadence_fallback_reason`, and `max_review_age_minutes` are persisted alongside trigger metadata.
- In current runtime policy, those cadence fields are observability / compatibility metadata and time passage alone no longer reopens AI review.
- 운영자 화면에서는 이 cadence observability를 `재검토 확인 주기`, `AI 기본 검토 간격`, 재검토 사유/백오프 설명으로 풀어서 읽습니다.
- Historical reference: older runtime used `forced_review_reason=OPEN_POSITION_MAX_REVIEW_AGE_EXCEEDED` to reopen unchanged open-position reviews.
- `protection_review_event` is dedupe-exempt so protection/emergency style survival paths are not delayed by unchanged fingerprints.
- Historical reference: `periodic_backstop_due` was the legacy safety backstop for stale-thesis refresh and missed-event recovery. It is not a current runtime trigger.

### Safety boundary

- `agents.py` still stops at trade intent generation.
- `schemas.py` validates the trigger payload and the resulting decision shape.
- `risk.py` remains the final deterministic allow/block gate.
- `execution.py` still executes only approved intents.
- Survival paths such as `reduce`, `exit`, `reduce_only`, `protection recovery`, and `emergency_exit` remain callable without AI and must not wait for time-based AI review reopening.

## Holding Profile Split

- `agents.py`는 거래 의도와 `holding_profile` (`scalp | swing | position`)만 생성합니다.
- `orchestrator.py`는 holding profile, rationale, cadence hint를 candidate / decision / pending entry plan / risk context로 전달합니다.
- `risk.py`는 holding profile별 hard/soft gate를 적용하는 최종 허용/차단 관문으로 유지됩니다.
- `execution.py`는 승인된 intent만 실행하고, deterministic hard stop + exchange-resident protective stop metadata를 position management seed에 넘깁니다.
- `position_management.py`는 holding profile별 관리 강도만 다르게 적용하며, stop widening은 허용하지 않습니다.
- 운영자 표현으로는 `holding_profile`을 `보유 성격`으로 읽으면 됩니다.

## Stop Ownership

- 최초 stop은 AI 전권이 아니라 deterministic hard stop이 source-of-truth입니다.
- AI는 stop width recommendation, break-even, trailing tighten, staged reduce만 보조합니다.
- hard stop 제거, widening, protection 없는 상태 정상화는 architecture 경계 밖이며 허용하지 않습니다.

현재 저장소는 멀티 에이전트 자동매매 플랫폼 전체를 크게 확장하기보다, **실거래에 안전한 Binance Futures 코어**를 우선 안정화하는 구조로 정리되어 있습니다.

## 현재 핵심 경계

### 1. 시장 / 계좌 / 포지션 수집

- 위치:
  - `backend/trading_mvp/services/orchestrator.py`
  - `backend/trading_mvp/services/execution.py`
  - `backend/trading_mvp/services/runtime_state.py`
- 역할:
  - 시장 스냅샷 수집
  - 계좌 / 포지션 / 주문 / 보호주문 동기화
  - freshness / stale 상태 기록

### 2. AI 판단

- 위치: `backend/trading_mvp/services/agents.py`
- 역할:
  - Trading Decision AI
  - Chief Review AI
- 원칙:
  - AI는 구조화된 출력만 생성
  - 실행 권한은 없음
  - 기획 / 개선 성격의 보조 워크플로우는 현재 범위 밖

### 3. risk_guard

- 위치: `backend/trading_mvp/services/risk.py`
- 역할:
  - 결정론적 최종 허용 / 차단
  - 하드 리스크 기준 적용
  - 노출도 계산 및 기록

### 4. 주문 실행

- 위치: `backend/trading_mvp/services/execution.py`
- 역할:
  - Binance 실주문
  - 보호 주문 생성
  - 주문 / 체결 / 포지션 동기화
  - 실패 시 alert / audit / pause 연동

### 5. 상태 관리 / 운영 제어

- 위치: `backend/trading_mvp/services/settings.py`
- 관련 보조: `pause_control.py`
- 역할:
  - pause / resume
  - live arm / disarm
  - approval window
  - AI / 시장데이터 / 심볼 / 주기 설정

### 6. 감사 / 추적

- 위치: `backend/trading_mvp/services/audit.py`
- 역할:
  - audit event
  - alert
  - health event

### 7. 운영 UI

- 위치: `frontend/`
- 성격:
  - 홍보 사이트가 아니라 운영자용 대시보드
  - 실제 백엔드 상태를 그대로 보여주는 것이 우선

## 현재 설계 원칙

- AI 판단과 실주문은 분리
- pause / manual control / audit trail 유지
- 시장/계좌 상태를 신뢰할 수 없으면 신규 진입 차단
- 리스크 가드는 런타임 하드 정책을 절대 우회하지 않음
- 대시보드와 감사 로그만으로 현재 운영 상태를 설명할 수 있어야 함
- 테스트와 문서는 현재 실거래 코어 기준으로 유지

## 향후 확장

향후 멀티 에이전트 플랫폼으로 더 확장하더라도, 아래 경계는 유지하는 것이 전제입니다.

- AI 판단
- risk_guard
- execution
- settings / control
- audit / health
