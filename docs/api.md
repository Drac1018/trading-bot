# API

## Health

- `GET /health`

서비스와 데이터베이스 초기화 상태를 확인합니다.

## Settings

- `GET /api/settings`
- `PUT /api/settings`
- `POST /api/settings/pause`
- `POST /api/settings/resume`
- `POST /api/settings/live/arm`
- `POST /api/settings/live/disarm`

### `GET /api/settings`

- `operational_status`
  - overview / account / settings가 공통으로 재사용할 표준 운영 상태 payload
  - `rollout_mode`: `paper | shadow | live_dry_run | limited_live | full_live`
  - `exchange_submit_allowed`
  - `limited_live_max_notional`
  - `live_execution_ready`, `trading_paused`, `approval_armed`, `approval_expires_at`
  - `operating_state`, `guard_mode_reason_*`, `blocked_reasons`, `latest_blocked_reasons`
  - `control_status_summary`: `exchange_can_trade`, `rollout_mode`, `exchange_submit_allowed`, `limited_live_max_notional`, `app_live_armed`, `approval_window_open`, `approval_state`, `approval_detail`, `paused`, `degraded`, `risk_allowed`, `blocked_reasons_current_cycle`
  - `auto_resume_status`, `auto_resume_last_blockers`
  - `account_sync_summary`, `sync_freshness_summary`, `market_freshness_summary`
    - `equity`, `wallet_balance`, `available_balance`
    - `realized_pnl`, `fee_total`, `funding_total`, `net_pnl`
  - `can_enter_new_position`

운영 설정 화면에서 즉시 확인해야 하는 핵심 상태를 반환합니다.

- `rollout_mode`
- `exchange_submit_allowed`
- `limited_live_max_notional`
- `live_execution_ready`
- `trading_paused`
- `guard_mode_reason_category`
- `guard_mode_reason_code`
- `guard_mode_reason_message`
- `pause_reason_code`
- `pause_origin`
- `auto_resume_status`
- `auto_resume_last_blockers`
- `latest_blocked_reasons`
- `operating_state`
- `pnl_summary`
  - `equity`, `wallet_balance`, `available_balance`, `cash_balance`
  - `realized_pnl`, `fee_total`, `funding_total`, `net_pnl`
  - `unrealized_pnl`, `daily_pnl`, `cumulative_pnl`
  - `basis=live_account_snapshot_preferred`
    - wallet / available / equity는 Binance 실계좌 snapshot 우선
    - fee는 `Execution` fill ledger 합산
    - funding은 별도 funding ledger 합산
- `account_sync_summary`
- `sync_freshness_summary`
- `exposure_summary`
- `execution_policy_summary`
- `market_context_summary`
- `adaptive_signal_summary`
- `position_management_summary`
- `exchange_sync_interval_seconds`
- `market_refresh_interval_minutes`
- `position_management_interval_seconds`
- `break_even_enabled`
- `move_stop_to_be_rr`
- `partial_take_profit_enabled`
- `partial_tp_rr`
- `partial_tp_size_pct`
- `time_stop_enabled`
- `time_stop_minutes`
- `time_stop_profit_floor`
- `symbol_cadence_overrides`
- `symbol_effective_cadences`

`symbol_cadence_overrides` row:

- `symbol`
- `enabled`
- `timeframe_override`
- `market_refresh_interval_minutes_override`
- `position_management_interval_seconds_override`
- `decision_cycle_interval_minutes_override`
- `ai_call_interval_minutes_override`

`symbol_effective_cadences` row:

- `symbol`
- `enabled`
- `uses_global_defaults`
- `timeframe`
- `market_refresh_interval_minutes`
- `position_management_interval_seconds`
- `decision_cycle_interval_minutes`
- `ai_call_interval_minutes`
- `estimated_monthly_ai_calls`
- `last_market_refresh_at`
- `last_position_management_at`
- `last_decision_at`
- `last_ai_decision_at`
- `next_market_refresh_due_at`
- `next_position_management_due_at`
- `next_decision_due_at`
- `next_ai_call_due_at`

staged rollout semantics:

- `paper`
  - 기존 paper 경로만 사용합니다.
- `shadow`
  - 시장/AI/risk/execution intent/audit까지 수행하지만 실제 Binance submit은 하지 않습니다.
- `live_dry_run`
  - 거래소 sync와 pre-trade exchange filter 검증까지 수행하지만 실제 submit은 하지 않습니다.
- `limited_live`
  - 실제 submit은 허용되지만 주문당 notional이 `limited_live_max_notional` 이하로 추가 제한됩니다.
- `full_live`
  - 기존 live submit 경로를 사용합니다.

`live_execution_ready`는 approval / credentials / pause 기준 준비 상태이고, 실제 신규 진입 가능 여부는 `exchange_submit_allowed`와 `can_enter_new_position`를 같이 봐야 합니다.

## Dashboard

- `GET /api/dashboard/overview`
- `GET /api/dashboard/operator`
- `GET /api/dashboard/profitability`
- `GET /api/market/snapshots`
- `GET /api/market/features`
- `GET /api/decisions`
- `GET /api/positions`
- `GET /api/orders`
- `GET /api/executions`
- `GET /api/executions/report`
- `GET /api/risk/checks`
- `GET /api/agents`
- `GET /api/scheduler`
- `GET /api/audit`
- `GET /api/alerts`

### `GET /api/dashboard/overview`

기존 overview 화면과 운영 요약 카드가 사용하는 기본 상태 응답입니다.

- `operational_status`
  - overview / account / settings가 공통으로 소비할 표준 운영 상태 payload
  - `trading_paused`, `live_execution_ready`, `approval_armed`, `approval_expires_at`
  - `operating_state`, `guard_mode_reason_*`, `blocked_reasons`, `latest_blocked_reasons`
  - `control_status_summary`: `exchange_can_trade`, `app_live_armed`, `approval_window_open`, `approval_state`, `approval_detail`, `paused`, `degraded`, `risk_allowed`, `blocked_reasons_current_cycle`
  - `auto_resume_status`, `account_sync_summary`, `sync_freshness_summary`, `market_freshness_summary`
    - `account_sync_summary`는 `wallet_balance`, `available_balance`, `realized_pnl`, `fee_total`, `funding_total`, `net_pnl`를 함께 포함합니다.
  - `can_enter_new_position`
- `last_market_refresh_at`
- `last_decision_at`
- `last_decision_snapshot_at`
- `last_decision_reference`
  - 마지막 AI 판단이 참조한 market/account/order freshness 기준
  - `market_snapshot_id`, `market_snapshot_at`, `account_sync_at`, `positions_sync_at`
  - `sync_freshness_summary`, `market_freshness_summary`, `freshness_blocking`, `display_gap_reason`
- `mode`
- `symbol`
- `timeframe`
- `latest_price`
- `latest_decision`
- `latest_risk`
- `active_entry_plans`
  - 현재 `armed` 상태인 신규 진입 계획 배열
  - 각 row는 `symbol`, `side`, `plan_status`, `source_decision_run_id`, `entry_mode`
  - `entry_zone_min`, `entry_zone_max`, `invalidation_price`, `max_chase_bps`
  - `idea_ttl_minutes`, `stop_loss`, `take_profit`, `risk_pct_cap`, `leverage_cap`
  - `created_at`, `expires_at`, `idempotency_key`, `metadata`
- `live_execution_ready`
- `trading_paused`
- `guard_mode_reason_*`
- `pause_reason_code`
- `pause_origin`
- `auto_resume_status`
- `auto_resume_last_blockers`
- `latest_blocked_reasons`
- `operating_state`
- `protection_recovery_status`
- `pnl_summary`
- `account_sync_summary`
- `sync_freshness_summary`
- `exposure_summary`
- `execution_policy_summary`
- `market_context_summary`
- `adaptive_signal_summary`

### `GET /api/dashboard/operator`

2026-04 멀티 심볼 개편 기준:

- `control`에는 계좌/시스템 전역 상태만 남습니다.
- `control.operational_status`
  - overview / account / settings와 같은 표준 운영 상태 payload
  - `rollout_mode`, `exchange_submit_allowed`, `limited_live_max_notional`
  - `trading_paused`, `live_execution_ready`, `approval_armed`, `guard_mode_reason_*`, `blocked_reasons`
  - `control_status_summary`: `exchange_can_trade`, `rollout_mode`, `exchange_submit_allowed`, `limited_live_max_notional`, `app_live_armed`, `approval_window_open`, `approval_state`, `approval_detail`, `paused`, `degraded`, `risk_allowed`, `blocked_reasons_current_cycle`
  - `auto_resume_status`, `account_sync_summary`, `sync_freshness_summary`, `market_freshness_summary`
    - `account_sync_summary`에는 `wallet_balance`, `available_balance`, `realized_pnl`, `fee_total`, `funding_total`, `net_pnl`가 additive로 포함됩니다.
  - `can_enter_new_position`
- `control.last_market_refresh_at`
- `control.last_decision_at`
- `control.last_decision_snapshot_at`
- `control.last_decision_reference`
  - 마지막 AI 판단이 실제로 사용한 snapshot/freshness 기준과 현재 표시 중인 상태의 gap 설명
- `default_symbol`, `default_timeframe`, `tracked_symbols`, `tracked_symbol_count`
- `can_enter_new_position`, `rollout_mode`, `exchange_submit_allowed`, `limited_live_max_notional`, `live_execution_ready`, `approval_armed`, `trading_paused`
- `operating_state`, `guard_mode_reason_message`, `pause_reason_code`
- `auto_resume_status`, `latest_blocked_reasons`, `auto_resume_last_blockers`
- `sync_freshness_summary`, `protected_positions`, `unprotected_positions`, `open_positions`
- `pnl_summary`, `daily_pnl`, `cumulative_pnl`, `account_sync_summary`, `exposure_summary`
- symbol snapshot에는 additive `pending_entry_plan` 필드가 포함될 수 있습니다.
  - 현재 symbol에 `armed` plan이 있으면 `symbol`, `side`, `plan_status`, `entry_mode`, `entry_zone_min`, `entry_zone_max`, `expires_at`, `idempotency_key`, `metadata`가 내려갑니다.
- `symbols`는 tracked symbol별 최신 snapshot 배열입니다.
- 각 symbol row는 `symbol`, `timeframe`, `latest_price`, `market_snapshot_time`, `ai_decision`, `risk_guard`, `execution`, `open_position`, `protection_status`, `blocked_reasons`, `live_execution_ready`, `stale_flags`, `last_updated_at`, `audit_events`를 포함합니다.
  - `execution.recent_fills`: 최근 fill ladder 요약. `execution_id`, `external_trade_id`, `fill_price`, `fill_quantity`, `fee_paid`, `commission_asset`, `realized_pnl`, `created_at`
  - `protection_status`: 기본 protected/missing 상태 외에 `recovery_status`, `auto_recovery_active`, `failure_count`, `last_error`, `last_transition_at`, `trigger_source`, `lifecycle_state`, `verification_status`, `last_event_type`, `last_event_message`, `last_event_at`
  - `audit_events`: operator dashboard에서는 raw payload 전체 대신 approval / protection / execution 설명에 필요한 compact payload만 유지합니다.
- 전역 최신 1건 `ai_decision / risk_guard / execution` 필드는 더 이상 대표값으로 내려주지 않습니다.

운영자 메인 화면 전용 snapshot입니다. 같은 흐름의 정보를 한 응답으로 묶어 보여줍니다.

- `control`
  - 지금 신규 진입 가능한지 판단하는 제어 상태
  - `control_status_summary`
    - `operational_status.control_status_summary`의 same-value passthrough
  - `can_enter_new_position`
  - `live_execution_ready`
  - `approval_armed`
  - `trading_paused`
  - `operating_state`
  - `guard_mode_reason_message`
  - `pause_reason_code`
  - `auto_resume_status`
  - `latest_blocked_reasons`
  - `auto_resume_last_blockers`
  - `sync_freshness_summary`
  - `protected_positions`
  - `unprotected_positions`
  - `scheduler_status`
  - `scheduler_window`
- `market_signal`
  - 최근 24h / 7d / 30d 성과 요약
  - `performance_windows`
  - `hold_blocked_summary`
  - `adaptive_signal_summary`
  - `market_context_summary`
- `ai_decision`
  - 최신 AI 제안
  - `decision`
  - `confidence`
  - `rationale_codes`
  - `explanation_short`
  - `provider_name`
  - `trigger_event`
  - `decision_run_id`
- `risk_guard`
  - 최신 결정론적 승인 결과
  - `allowed`
  - `decision`
  - `reason_codes`
    - blocker-only alias입니다. `blocked_reason_codes`와 같은 의미입니다.
  - `blocked_reason_codes`
    - 실제 신규 진입을 막은 사유만 담습니다.
  - `adjustment_reason_codes`
    - 자동 축소 승인처럼 허용 상태에서 함께 남겨야 하는 조정/승인 사유를 담습니다.
  - `approved_risk_pct`
  - `approved_leverage`
  - `operating_state`
  - `debug_payload`
    - `requested_notional`, `requested_quantity`
    - `resized_notional`, `resized_quantity`
    - `requested_exchange_notional`, `requested_exchange_quantity`
    - `requested_exchange_reason_code`, `resized_exchange_reason_code`
    - `current_symbol_notional`, `current_directional_notional`
    - `projected_symbol_notional`, `projected_directional_notional`
    - `open_order_reserved_notional`
    - `headroom`
    - `exchange_minimums`
    - `entry_trigger`
    - `sync_timestamps`
- `execution`
  - 최신 판단과 연결된 주문/체결 결과
  - `order_status`
  - `execution_status`
  - `requested_quantity`
  - `filled_quantity`
  - `average_fill_price`
  - `execution_quality`
  - `recent_fills`
    - 최근 fill ladder 요약
    - `execution_id`, `external_trade_id`, `fill_price`, `fill_quantity`, `fee_paid`, `commission_asset`, `realized_pnl`, `created_at`
- `execution_windows`
  - 최근 실행 품질 요약
  - `average_realized_slippage_pct`
  - `partial_fill_orders`
  - `repriced_orders`
  - `aggressive_fallback_orders`
- `audit_events`
  - 최근 감사 이벤트 목록
  - operator dashboard audit rows는 compact payload를 유지합니다.
    - approval timeline: `approval_state`, `approval_window_open`, `approval_expires_at`, `approval_detail.*`
    - protection timeline: `recovery_status`, `missing_components`, `last_error`, `protection_lifecycle.*`, `verification_detail.*`
    - execution timeline: `order_status`, `submission_state`, `requested_quantity`, `filled_quantity`, `fill_price`, `average_fill_price`, `reason_codes`

### Decision / Risk trigger note

- `TradeDecision` payload는 신규 진입 아이디어에 대해 optional `entry_mode`, `invalidation_price`, `max_chase_bps`, `idea_ttl_minutes`를 포함할 수 있습니다.
- `risk_guard`는 신규 `long / short`에 한해 결정론적 entry trigger를 다시 검사합니다.
- 신규 차단 사유는 `ENTRY_TRIGGER_NOT_MET`, `CHASE_LIMIT_EXCEEDED`, `INVALID_INVALIDATION_PRICE`를 `reason_codes`와 `blocked_reason_codes`로 남깁니다.
- `reduce / exit / protection / emergency` 계열은 이 trigger 때문에 막지 않습니다.

### Pending entry plan lifecycle

- 15분 decision cycle은 신규 `long / short` 아이디어를 즉시 주문으로 연결하지 않고, 조건부 진입이면 `PendingEntryPlan`을 `armed` 상태로 저장할 수 있습니다.
- plan은 `symbol + side` 기준 active 1개만 유지하며, 같은 실행 시도는 `symbol + side + source_decision_run_id + expires_at` 기반 `idempotency_key`로 중복을 억제합니다.
- plan에는 `entry_mode`, `entry_zone_min`, `entry_zone_max`, `invalidation_price`, `max_chase_bps`, `idea_ttl_minutes`, `stop_loss`, `take_profit`, `risk_pct_cap`, `leverage_cap`가 함께 저장됩니다.
- `ENTRY_TRIGGER_NOT_MET`, `CHASE_LIMIT_EXCEEDED`, `SLIPPAGE_THRESHOLD_EXCEEDED`처럼 현재가 기준으로만 미충족인 entry blocker만 남아 있으면 plan을 `armed`로 유지할 수 있습니다.
- 반대로 `hold`, 반대 방향 신규 계획, TTL 만료, invalidation 붕괴, stale sync, protection 불일치가 발생하면 기존 armed plan은 `canceled` 또는 `expired`로 종료됩니다.
- 1분 watcher cycle은 최신 시장/계좌/포지션/오픈오더/보호주문 snapshot을 다시 모은 뒤 plan zone 진입 여부를 보고, 1분 confirm까지 충족한 경우에만 `risk_guard`를 재실행합니다.
- watcher 실행에서도 순서는 항상 `decision(plan) -> risk_guard -> execution`이며, `risk_guard.allowed=true`가 아니면 live order를 제출하지 않습니다.
- watcher가 주문을 성공적으로 제출하거나 동일 `idempotency_key` 실행이 이미 완료된 것을 확인하면 plan은 `triggered`로 종료됩니다.

#### Entry trigger and auto-resize

- `TradeDecision` payload에는 신규 진입 아이디어를 제한하기 위한 optional 필드 `entry_mode`, `invalidation_price`, `max_chase_bps`, `idea_ttl_minutes`가 포함될 수 있습니다.
- `risk_guard`는 신규 `long / short`에 대해 결정론적 entry trigger를 다시 검사합니다.
- 신규 진입 차단 사유는 `ENTRY_TRIGGER_NOT_MET`, `CHASE_LIMIT_EXCEEDED`, `INVALID_INVALIDATION_PRICE`를 `reason_codes`와 `blocked_reason_codes`로 남깁니다.
- 익스포저 초과가 유일한 문제이고 `market/account/positions/open_orders/protective_orders` freshness, protection 검증, pause, approval, leverage/risk 하드 게이트가 모두 정상일 때만 `risk_guard`는 신규 진입을 전면 차단하지 않고 `approved_projected_notional`과 `approved_quantity`로 자동 축소 승인할 수 있습니다.
- 이 경우 payload에는 아래 필드가 추가됩니다.
  - `raw_projected_notional`
  - `approved_projected_notional`
  - `approved_quantity`
  - `auto_resized_entry`
  - `size_adjustment_ratio`
  - `exposure_headroom_snapshot`
  - `auto_resize_reason`
- auto-resize가 최종 승인되면 `allowed=true`, `reason_codes=[]`, `blocked_reason_codes=[]`이고 auto-resize 관련 코드는 `adjustment_reason_codes`에만 남습니다.
- `reason_codes`는 현재 평가 사이클 기준 blocker-only 필드입니다. pre-resize exposure blocker나 이전 cycle blocker를 누적해서 재사용하지 않습니다.
- auto-resize가 발생하면 directional / single-position / gross / same-tier 한도는 resized size 기준으로 다시 평가합니다.
- 신규 진입 size는 risk 단계에서 exchange `min_notional`, `min_qty`, `step_size`를 참고해 실행 가능한 수량으로 먼저 정규화합니다.
- `approved_projected_notional`과 `approved_quantity`는 exchange-actionable 기준 값이며 execution preflight는 이 값을 넘어서는 silent upsize를 하지 않습니다.
- exchange minimum을 만족하지 못하면 blocker reason은 계속 `ENTRY_SIZE_BELOW_MIN_NOTIONAL`만 사용하고, 세부 원인은 `debug_payload.requested_exchange_reason_code` 또는 `debug_payload.resized_exchange_reason_code`에 남깁니다.
- `debug_payload.requested_exposure_limit_codes`는 resize 전 한도 초과 사유를, `debug_payload.final_exposure_limit_codes`는 resize 후 최종 한도 초과 사유를 담습니다.
- `debug_payload.exchange_minimums`는 `filter_source`, `tick_size`, `step_size`, `min_qty`, `min_notional`, `minimum_actionable_quantity`, `minimum_actionable_notional`을 함께 내려줍니다.
- `debug_payload.entry_trigger`는 `ENTRY_TRIGGER_NOT_MET`가 발생한 경우 현재가, entry zone, breakout / pullback confirmation, invalidation, chase 판정값을 같이 남깁니다.
- 신규 진입 노출 계산에서 `reduce_only`, `close_only`, `STOP*`, `TAKE_PROFIT*`, `TRAILING_STOP*` open order는 reserved exposure에 포함하지 않습니다.
- `debug_payload.sync_timestamps`는 `account`, `positions`, `open_orders`, `protective_orders` 마지막 sync 시각을 같이 내려 stale state 확인에 사용합니다.
- 자동 축소 승인 정보 코드는 `ENTRY_AUTO_RESIZED`, `ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT`, `ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT`, `ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT`, `ENTRY_CLAMPED_TO_SAME_TIER_LIMIT`이며 모두 `adjustment_reason_codes`에 기록됩니다.
- `reduce / exit / protection / emergency` 계열은 trigger와 auto-resize 정책 때문에 막히지 않습니다.

#### Dashboard risk source-of-truth

- dashboard / overview blocked reasons는 아래 순서로 blocker-only 값을 읽습니다.
  - `risk_check.payload.blocked_reason_codes`
  - `risk_check.payload.reason_codes`
  - `risk_check.reason_codes`
- operator `risk_guard` snapshot은 최신 `debug_payload`를 그대로 노출합니다.
- operator `risk_guard.reason_codes`와 `blocked_reason_codes`는 blocker-only를 내려주고, `adjustment_reason_codes`는 자동 축소 승인 같은 비차단 사유를 별도로 내려줍니다.

### `GET /api/dashboard/profitability`

수익성과 execution quality 해석 전용 응답입니다.

수익성 해석 전용 응답입니다.

- `windows`
  - `summary`에는 execution quality aggregate도 함께 포함됩니다.
    - `average_arrival_slippage_pct`
    - `average_realized_slippage_pct`
    - `average_first_fill_latency_seconds`
    - `cancel_attempts`
    - `cancel_successes`
    - `cancel_success_rate`
  - `24h`, `7d`, `30d`
  - 각 window에 `summary`, `rationale_winners`, `rationale_losers`, `top_regimes`, `top_symbols`, `top_timeframes`, `top_hold_conditions`
- `execution_windows`
  - decision quality와 execution quality를 분리한 최근 window 요약입니다.
  - `execution_quality_summary`에는 아래 숫자가 포함됩니다.
    - `degraded_orders`
    - `partial_fill_orders`
    - `repriced_orders`
    - `aggressive_fallback_orders`
    - `average_arrival_slippage_pct`
    - `average_realized_slippage_pct`
    - `average_first_fill_latency_seconds`
    - `cancel_attempts`
    - `cancel_successes`
    - `cancel_success_rate`
  - `worst_profiles`는 `policy_profile` 기준 execution risk profile입니다.
    - `average_arrival_slippage_pct`
    - `average_realized_slippage_pct`
    - `average_first_fill_latency_seconds`
    - `cancel_attempts`
    - `cancel_successes`
    - `cancel_success_rate`
- `hold_blocked_summary`
- `adaptive_signal_summary`
- `latest_decision`
- `latest_risk`

### `GET /api/audit`

감사 로그 타임라인입니다. 감사 로그 화면의 탭 분류는 각 row의 `event_category`를 기준으로 동작합니다.

지원 query:

- `event_type`
- `severity`
- `search`
- `limit`

주요 응답 필드:

- `event_type`
- `event_category`
  - `risk`
  - `execution`
  - `approval_control`
  - `protection`
  - `health_system`
  - `ai_decision`
- `entity_type`
- `entity_id`
- `severity`
- `message`
- `payload`
- `created_at`

## Live Sync

- `POST /api/live/sync`
- `sync_freshness_summary`
  - `account`
  - `positions`
  - `open_orders`
  - `protective_orders`
  - each scope includes `status`, `raw_status`, `last_sync_at`, `last_attempt_at`, `last_attempt_status`
  - each scope includes `last_failure_at`, `last_failure_reason`, `last_skip_at`, `last_skip_reason`
  - each scope includes `freshness_seconds`, `stale_after_seconds`, `stale`, `incomplete`

거래소 주문, 포지션, 계좌, 보호 주문 상태를 동기화하고 운영 상태를 다시 계산합니다.

## Binance Account

- `GET /api/binance/account`

Binance 원본 권한과 앱 내부 실주문 readiness를 분리해서 보여줍니다.

- `exchange_can_trade`
- `app_live_execution_ready`
- `app_trading_paused`
- `app_operating_state`
- `latest_blocked_reasons`

## Reviews / Cycles

- `POST /api/cycles/run`
- `POST /api/reviews/{window}`
- `POST /api/replay/run`
- `POST /api/replay/validation`

`POST /api/reviews/{window}` currently supports only `window=1h`.
`4h / 12h / 24h` review windows are disabled in the current live-core scope.

Scheduler workflow names:

- `exchange_sync_cycle`
- `market_refresh_cycle`
- `position_management_cycle`
- `entry_plan_watcher_cycle`
- `interval_decision_cycle`

## CLI

- `python -m trading_mvp.cli seed`
- `python -m trading_mvp.cli cycle`
- `python -m trading_mvp.cli replay --cycles 5 --start-index 140`
- `python -m trading_mvp.cli review --window 1h`
- `python -m trading_mvp.cli replay-compare --cycles 12 --start-index 90 --timeframe 15m --symbols BTCUSDT --data-source-type synthetic_seed`
- `python -m trading_mvp.cli export-schemas`

## 2026-04 Live Sync / Replay / Ranking Additions

### Live sync stream metadata

`POST /api/live/sync`, `GET /api/dashboard/overview`, `GET /api/dashboard/operator`, and `GET /api/settings`
now expose additive stream/reconciliation fields.

- `user_stream_summary`
  - `status`
  - `source`
  - `listen_key`
  - `listen_key_created_at`
  - `listen_key_refreshed_at`
  - `last_keepalive_at`
  - `last_connected_at`
  - `last_disconnected_at`
  - `connection_attempted_at`
  - `last_event_at`
  - `last_event_type`
  - `last_error`
  - `reconnect_count`
  - `heartbeat_ok`
  - `stream_source`
  - `next_retry_at`
  - `backoff_seconds`
- `reconciliation_summary`
  - `status`
  - `source`
  - `last_reconciled_at`
  - `last_success_at`
  - `last_error`
  - `last_symbol`
  - `stream_fallback_active`
  - `reconcile_source`
  - `position_mode`
  - `position_mode_source`
  - `position_mode_checked_at`
  - `mode_guard_active`
  - `mode_guard_reason_code`
  - `mode_guard_message`
  - `enabled_symbols`
  - `guarded_symbols`
  - `symbol_states`
    - per-symbol `position_status`, `exchange_position_side`, `remote_position_sides`, `open_order_position_sides`, `protection_status`, `guard_active`, `guard_reason_code`
- flat summary fields on live sync:
  - `stream_health`
  - `last_stream_event_time`
  - `stream_source`
  - `reconcile_source`
  - `stream_event_count`
  - `stream_issues`
    - reconnect backoff, listen key registration 실패, disconnect 같은 user stream 이슈 payload

Interpretation:

- user stream is the first update path for order/account/position events.
- active live order state prefers user stream events first and only falls back to REST order/trade reconciliation when the stream is unavailable, stale, or unverified.
- REST polling remains the reconciliation source-of-truth for periodic account/position/open-order snapshots and for stream loss recovery.
- `stream_source=user_stream` with `reconcile_source=user_stream_primary` is the normal steady-state shape.
- `reconcile_source=rest_polling_fallback` means active order reconciliation had to fall back to REST and a matching user stream warning should exist in audit/health.
- `position_mode=one_way` is the only non-guarded live entry shape in the current backend model.
- `mode_guard_active=true` means exchange position mode is unclear or conflicts with current one-way local semantics, so `can_enter_new_position=false` and live `risk_guard` includes `EXCHANGE_POSITION_MODE_UNCLEAR` or `EXCHANGE_POSITION_MODE_MISMATCH`.

### Live order submit unknown / reconcile flow

Live order submit no longer treats timeout or transport failure as an immediate final failure.

- submit timeout / transport failure first moves the order into `submission_state=submit_unknown`
- the system must reconcile by `client_order_id` before any safe retry
- if the exchange already accepted the order, the local row is restored and `submission_state=reconciled`
- only when `client_order_id` reconcile returns "order absent" does the system perform one bounded safe retry with the same `client_order_id`
- if submit state still cannot be confirmed, `execute_live_trade` may return `status=submission_unknown` with `reason_codes=["LIVE_ORDER_SUBMISSION_UNKNOWN"]`

Order metadata additions:

- `metadata_json.submission_tracking`
  - `submission_state`
    - `reconciled`
    - `submit_unknown`
    - `failed`
  - `submit_attempt_count`
  - `last_submit_error`
  - `client_order_id`
  - `safe_retry_used`
  - `recovered_via`
- `metadata_json.submit_request`

Audit / health expectations:

- `live_order_submission_recovered`
  - timeout or transport failure was recovered via reconcile or bounded retry
- `live_order_submission_unknown`
  - submit is still unresolved and must be reconciled before any further resend
- `emergency_exit_submission_unknown`
  - emergency exit submit is unresolved; management-only / degraded handling should remain active until reconciliation

### Replay validation data sources

`ReplayValidationRequest.data_source_type` supports:

- `synthetic_seed`
- `binance_futures_klines`

`POST /api/replay/validation` and `python -m trading_mvp.cli replay-compare` return:

- `data_source_type`
- `data_source_basis`
- `live_execution_guarantee`
- variant `summary` with:
  - `average_arrival_slippage_pct`
  - `average_realized_slippage_pct`
  - `average_first_fill_latency_seconds`
  - `cancel_attempts`
  - `cancel_successes`
  - `cancel_success_rate`
  - `average_mfe_pct`
  - `average_mae_pct`
- variant breakdowns:
  - `by_symbol`
  - `by_timeframe`
  - `by_regime`
  - `by_rationale_code`
- comparison blocks:
  - `symbol_comparison`
  - `timeframe_comparison`
  - `regime_comparison`
  - `rationale_comparison`

Replay guarantee:

- replay runs in an isolated in-memory session
- replay never submits live orders
- `live_execution_guarantee` is returned explicitly in the response payload

### Performance / profitability additions

Performance and replay summaries now expose calculated excursion metrics instead of placeholder status:

- `average_mfe_pct`
- `average_mae_pct`
- `best_mfe_pct`
- `worst_mae_pct`

Execution quality metrics are now exposed separately from signal / decision quality:

- `average_arrival_slippage_pct`
- `average_realized_slippage_pct`
- `average_first_fill_latency_seconds`
- `cancel_attempts`
- `cancel_successes`
- `cancel_success_rate`

Per-decision performance entries also include:

- `arrival_slippage_pct`
- `realized_slippage_pct`
- `first_fill_latency_seconds`
- `cancel_attempts`
- `cancel_successes`
- `cancel_success_rate`

Decision/window breakdowns also include:

- `close_outcome`
- `regimes`
- `trend_alignments`
- `close_outcomes`
- `feature_flags`
- `by_rationale_code` and `rationale_comparison` in replay validation responses

### Candidate selection / ranking summary

Overview, operator control, and settings operational payloads now expose:

- `candidate_selection_summary`
  - `generated_at`
  - `mode`
  - `max_selected`
  - `selected_symbols`
  - `skipped_symbols`
  - `rankings`

Ranking payloads include:

- `candidate`
- `score`
  - `regime_fit`
  - `expected_rr`
  - `recent_signal_performance`
  - `slippage_sensitivity`
  - `exposure_impact`
  - `confidence_consistency`
  - `correlation_penalty`
  - `total_score`
- `selection_reason`
- `max_abs_correlation`

Operational rule:

- candidate ranking only narrows which symbols enter the decision cycle
- `risk_guard` still remains the final allow/block gate before execution
