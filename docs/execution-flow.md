# Execution Flow

## 2026-04 AI Context Plumbing

- `run_decision_cycle()` now builds `ai_context` before calling `TradingDecisionAgent.run(...)`.
- The packet is persisted with the decision input payload so audit/debug can compare the model input against the returned decision.
- `run_decision_cycle()` also attaches `prior_context` from historical engine/capital/session analytics before the provider call.
- `prior_context` is soft-only:
  - min sample threshold 미달이면 `unavailable`
  - strong prior는 confidence를 소폭 올릴 수 있음
  - weak prior + poor quality는 `hold` / `should_abstain=true`로 더 쉽게 기울 수 있음
  - `breakout_exception`, `swing`, `position` 문맥은 quality/prior 요구가 더 엄격함
- The decision schema adds optional rationale, invalidation, abstain, regime-risk, and payoff-timing metadata, but the execution path still honors the same post-decision flow:
  - schema validation
  - `risk.py` final gate
  - `execution.py`
- Provider outputs that omit the new optional fields remain valid. The agent backfills only metadata defaults; it does not create a new execution action type.

## 2026-04 Protection / Management Semantics Split

- The decision object can still be `long` / `short` / `reduce` / `exit` for backward compatibility, but persisted output and decision metadata now also carry:
  - `intent_family`
  - `management_action`
  - `legacy_semantics_preserved`
  - `analytics_excluded_from_entry_stats`
- This means protection recovery / restore no longer has to look like a generic new entry to operator, audit, analytics, or prior builders.
- Execution meaning is unchanged:
  - if legacy `long` / `short` mapping is required for deterministic protection restore, it remains allowed internally
  - this ticket does not change `risk.py`
  - this ticket does not change `execution.py`

## 2026-04 Prompt Routing And Bounded Output

- `TradingDecisionAgent.run(...)` now resolves a route contract before provider invocation:
  - `trigger_type`
  - `strategy_engine`
  - `prompt_family`
  - `allowed_actions`
  - `forbidden_actions`
- The provider sees that contract in both natural-language instructions and a structured payload block.
- After provider output:
  - invalid action for the current trigger is bounded to a safe `hold` / `reduce` / `exit`
  - breakout-exception holding profile upgrades are bounded back to `scalp`
  - loser profile upgrades and stop widening attempts are bounded before risk review
- On new-entry-capable routes, provider timeout / unavailable / malformed / schema-invalid output is fail-closed into `hold`.
- On protection/reduce/emergency-style routes, provider failure falls back to deterministic management behavior and does not block survival handling.
- Historical priors do not block survival handling. `reduce`, `exit`, protection recovery, and other deterministic management paths ignore prior penalties for execution purposes.
- `risk.py` still receives only the normalized decision. `execution.py` still receives only intents approved by `risk.py`.
- Historical analytics / prior builders now exclude non-entry intent rows from entry stats so management/protection behavior does not contaminate entry expectancy or payoff timing.

## 2026-04 Hybrid AI Review Dispatch

- `interval_decision_cycle` now plans review triggers before any AI call.
- The deterministic planner reuses candidate ranking, derivatives veto, meta gate, and slot allocation to decide whether a symbol has a real review event.
- New-entry review is dispatched only for event-bearing candidate symbols.
- Open-position review is dispatched only when one of the following is true:
  - `open_position_recheck_due`
  - `protection_review_event`
  - `periodic_backstop_due`
- If no event exists, the scheduler writes a `decision_ai_no_event` audit row and skips the AI call entirely.
- If the same trigger fingerprint repeats, the scheduler writes `decision_ai_deduped` and skips the AI call.
- If the periodic backstop becomes due, the scheduler writes `decision_ai_backstop_due` and allows a limited review even without a fresh entry candidate event.

## 2026-04 Position Review Cadence

- `holding_profile_cadence_hint.decision_interval_minutes` is now consumed by the scheduler for open-position AI review due checks.
- Safe fallback behavior:
  - if the cadence hint is missing, non-numeric, or invalid, the scheduler falls back to the effective symbol `ai_call_interval_minutes`
  - cadence is used only for AI position review timing
  - cadence does not delay `exchange_sync_cycle`
  - cadence does not delay `market_refresh_cycle`
  - cadence does not delay deterministic protection or emergency handling
- `position_management_cycle` continues to run on the existing management cadence. The scheduler clamps any profile-derived review cadence so protection handling is never slowed below the configured management baseline.

## 2026-04 Direct Decision Path Consistency

- Direct `run_decision_cycle()` calls now rebuild an effective `selection_context` / slot summary for AI context and audit metadata when the caller did not provide one.
- This keeps direct/manual decision runs aligned with the ranked scheduler path for slot, capacity, and trigger metadata, even when the underlying deterministic decision path is reviewing an open position.
- Hard exposure blocks still win over slot soft caps. Slot allocation remains a soft sizing layer and does not override hard blockers.

## Holding Profile Overlay

- 신규 진입 기본 프로필은 `holding_profile=scalp`입니다.
- `holding_profile=swing` 또는 `position`은 강한 higher timeframe 구조 정렬, breadth, lead-lag, derivatives 역풍 부재, meta gate `pass`가 동시에 맞을 때만 사용합니다.
- `interval_decision_cycle`이 만든 `holding_profile`과 `holding_profile_reason`은 pending entry plan, risk, execution, position management까지 그대로 전달됩니다.
- `breakout_confirm` 신규 진입은 기본적으로 scalp/intraday 성격으로 다루며, 장기 보유 프로필에서 예외를 넓히지 않습니다.

## Hard Stop Handling

- 최초 손절은 항상 deterministic hard stop 기준으로 생성됩니다.
- live execution은 exchange-resident protective stop을 계속 유지해야 하며, protection 없는 상태를 정상 상태로 표시하지 않습니다.
- AI는 stop width 제안, break-even 이동, trailing tighten, partial reduce 같은 보조 관리만 할 수 있습니다.
- AI는 hard stop 제거, stop widening, 무손절 유지, protection 없는 상태 허용을 할 수 없습니다.

현재 운영 루프는 하나의 interval decision cycle에 모든 책임을 몰아넣지 않고, 아래 4개 cycle로 분리됩니다.

## 운영 cycle

1. `exchange_sync_cycle`
   - 계좌, 포지션, 오픈 오더, 보호주문 상태 동기화만 수행
   - AI 호출 금지
   - 신규 진입 판단 금지
   - 전역 `exchange_sync_interval_seconds`만 사용

2. `market_refresh_cycle`
   - 심볼별 시장 스냅샷 수집
   - 필요 시 feature 계산을 위한 기반만 갱신
   - 신규 진입 판단 금지
   - 심볼별 `market_refresh_interval_minutes` effective cadence 사용

3. `position_management_cycle`
   - 열린 포지션이 있을 때만 break-even, trailing, partial take-profit, edge decay, reduce 강화 수행
   - 신규 진입 금지
   - `tighten_only` 유지
   - 심볼별 `position_management_interval_seconds` effective cadence 사용

4. `interval_decision_cycle`
   - 신규 진입/축소/청산 판단의 중심 루프
   - AI 판단, deterministic baseline, `risk_guard`, live execution 담당
   - exchange sync / position management를 매번 강제로 포함하지 않음
   - 심볼별 `decision_cycle_interval_minutes` effective cadence 사용

## 전역 기본값 + symbol override

- 전역 설정:
  - `default_timeframe`
  - `exchange_sync_interval_seconds`
  - `market_refresh_interval_minutes`
  - `position_management_interval_seconds`
  - `decision_cycle_interval_minutes`
  - `ai_call_interval_minutes`
- 심볼별 override:
  - `timeframe_override`
  - `market_refresh_interval_minutes_override`
  - `position_management_interval_seconds_override`
  - `decision_cycle_interval_minutes_override`
  - `ai_call_interval_minutes_override`
  - `enabled`

override가 비어 있으면 전역값을 그대로 상속합니다.

## 중복 신규 진입 방지

- base timeframe이 `15m`여도 decision cycle을 `5m`로 더 촘촘히 돌릴 수 있습니다.
- 단, 같은 base candle 안에서는 동일 symbol의 신규 진입 평가를 다시 만들지 않습니다.
- 현재 1차 구현은 `latest decision market_snapshot.snapshot_time == current snapshot_time`이면 same-candle 신규 진입 평가를 skip합니다.
- 열린 포지션의 보호 관리는 `position_management_cycle`에서 더 자주 실행할 수 있습니다.

## 안전 경계

- `risk_guard`는 여전히 최종 허용/차단 관문입니다.
- pause, guard mode, live approval, protection recovery, stale sync 차단 로직은 그대로 유지됩니다.
- `historical_replay`는 live execution을 절대 수행하지 않습니다.
- AI가 꺼져 있어도 exchange sync, market refresh, position management는 계속 실행할 수 있습니다.
- 보호주문 관련 stop widening은 허용되지 않으며, 관리 로직은 항상 보호 방향 우선입니다.
