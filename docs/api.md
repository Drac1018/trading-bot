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

운영 설정 화면에서 즉시 확인해야 하는 핵심 상태를 반환합니다.

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

- `mode`
- `symbol`
- `timeframe`
- `latest_price`
- `latest_decision`
- `latest_risk`
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
- `default_symbol`, `default_timeframe`, `tracked_symbols`, `tracked_symbol_count`
- `can_enter_new_position`, `live_execution_ready`, `approval_armed`, `trading_paused`
- `operating_state`, `guard_mode_reason_message`, `pause_reason_code`
- `auto_resume_status`, `latest_blocked_reasons`, `auto_resume_last_blockers`
- `sync_freshness_summary`, `protected_positions`, `unprotected_positions`, `open_positions`
- `daily_pnl`, `cumulative_pnl`, `account_sync_summary`, `exposure_summary`
- `symbols`는 tracked symbol별 최신 snapshot 배열입니다.
- 각 symbol row는 `symbol`, `timeframe`, `latest_price`, `market_snapshot_time`, `ai_decision`, `risk_guard`, `execution`, `open_position`, `protection_status`, `blocked_reasons`, `live_execution_ready`, `stale_flags`, `last_updated_at`, `audit_events`를 포함합니다.
- 전역 최신 1건 `ai_decision / risk_guard / execution` 필드는 더 이상 대표값으로 내려주지 않습니다.

운영자 메인 화면 전용 snapshot입니다. 같은 흐름의 정보를 한 응답으로 묶어 보여줍니다.

- `control`
  - 지금 신규 진입 가능한지 판단하는 제어 상태
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
  - `approved_risk_pct`
  - `approved_leverage`
  - `operating_state`
- `execution`
  - 최신 판단과 연결된 주문/체결 결과
  - `order_status`
  - `execution_status`
  - `requested_quantity`
  - `filled_quantity`
  - `average_fill_price`
  - `execution_quality`
- `execution_windows`
  - 최근 실행 품질 요약
  - `average_realized_slippage_pct`
  - `partial_fill_orders`
  - `repriced_orders`
  - `aggressive_fallback_orders`
- `audit_events`
  - 최근 감사 이벤트 목록

### Decision / Risk trigger note

- `TradeDecision` payload는 신규 진입 아이디어에 대해 optional `entry_mode`, `invalidation_price`, `max_chase_bps`, `idea_ttl_minutes`를 포함할 수 있습니다.
- `risk_guard`는 신규 `long / short`에 한해 결정론적 entry trigger를 다시 검사합니다.
- 신규 차단 사유는 `ENTRY_TRIGGER_NOT_MET`, `CHASE_LIMIT_EXCEEDED`, `INVALID_INVALIDATION_PRICE`를 `reason_codes`로 남깁니다.
- `reduce / exit / protection / emergency` 계열은 이 trigger 때문에 막지 않습니다.

#### Entry trigger and auto-resize

- `TradeDecision` payload에는 신규 진입 아이디어를 제한하기 위한 optional 필드 `entry_mode`, `invalidation_price`, `max_chase_bps`, `idea_ttl_minutes`가 포함될 수 있습니다.
- `risk_guard`는 신규 `long / short`에 대해 결정론적 entry trigger를 다시 검사합니다.
- 신규 진입 차단 사유는 `ENTRY_TRIGGER_NOT_MET`, `CHASE_LIMIT_EXCEEDED`, `INVALID_INVALIDATION_PRICE`를 `reason_codes`로 남깁니다.
- 익스포저 초과가 유일한 문제이면 `risk_guard`는 신규 진입을 전면 차단하지 않고 `approved_projected_notional`과 `approved_quantity`로 자동 축소 승인할 수 있습니다.
- 이 경우 payload에는 아래 필드가 추가됩니다.
  - `raw_projected_notional`
  - `approved_projected_notional`
  - `approved_quantity`
  - `auto_resized_entry`
  - `size_adjustment_ratio`
  - `exposure_headroom_snapshot`
  - `auto_resize_reason`
- 자동 축소 승인 정보 코드는 `ENTRY_AUTO_RESIZED`, `ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT`, `ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT`, `ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT`, `ENTRY_CLAMPED_TO_SAME_TIER_LIMIT`입니다.
- `reduce / exit / protection / emergency` 계열은 trigger와 auto-resize 정책 때문에 막히지 않습니다.

### `GET /api/dashboard/profitability`

수익성 해석 전용 응답입니다.

- `windows`
  - `24h`, `7d`, `30d`
  - 각 window에 `summary`, `rationale_winners`, `rationale_losers`, `top_regimes`, `top_symbols`, `top_timeframes`, `top_hold_conditions`
- `execution_windows`
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
  - each scope includes `last_sync_at`, `freshness_seconds`, `stale_after_seconds`, `stale`, `incomplete`, `last_failure_reason`

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

Scheduler workflow names:

- `exchange_sync_cycle`
- `market_refresh_cycle`
- `position_management_cycle`
- `interval_decision_cycle`
- `scheduled_review`

## Backlog

- `GET /api/backlog`
- `GET /api/backlog/{backlog_id}`
- `POST /api/backlog/requests`
- `POST /api/backlog/applied`

`/api/backlog` 응답은 backlog 항목, 연결된 사용자 요청, 적용 이력, 시그널 성과 리포트, 구조화된 경쟁사 메모만 포함한다.
기존 backlog 전용 AI 보조 기능이던 `codex-draft`, `auto-apply` 관련 endpoint와 응답 필드는 제거되었다.

## CLI

- `python -m trading_mvp.cli seed`
- `python -m trading_mvp.cli cycle`
- `python -m trading_mvp.cli replay --cycles 5 --start-index 140`
- `python -m trading_mvp.cli review --window 24h`
- `python -m trading_mvp.cli replay-compare --cycles 12 --start-index 90 --timeframe 15m --symbols BTCUSDT`
- `python -m trading_mvp.cli export-schemas`
