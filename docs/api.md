# API

## 2026-04 AI Context Packet

- Trading decision input payloads can now include `ai_context`.
- `AgentRun.input_payload.ai_context` and `AgentRun.metadata_json.ai_context` use the same structure.

### `ai_context`

- `ai_context_version`
- `symbol`
- `timeframe`
- `trigger_type`
- `composite_regime`
- `data_quality`
- `previous_thesis`
- `prior_context`
- `strategy_engine`
- `strategy_engine_context`
- `holding_profile`
- `holding_profile_reason`
- `assigned_slot`
- `candidate_weight`
- `capacity_reason`
- `blocked_reason_codes`
- `hard_stop_active`
- `stop_widening_allowed`
- `initial_stop_type`
- `selection_context_summary`
- `prompt_family_hint`

### `composite_regime`

- `structure_regime`: `trend | range | squeeze | expansion | transition`
- `direction_regime`: `bullish | bearish | neutral`
- `volatility_regime`: `calm | normal | fast | shock`
- `participation_regime`: `strong | mixed | weak`
- `derivatives_regime`: `tailwind | neutral | headwind | unavailable`
- `execution_regime`: `clean | normal | stress | unavailable`
- `persistence_bars`
- `persistence_class`: `early | established | extended`
- `transition_risk`: `low | medium | high`
- `regime_reason_codes`

### `data_quality`

- `data_quality_grade`: `complete | partial | degraded | unavailable`
- `missing_context_flags`
- `stale_context_flags`
- `derivatives_available`
- `orderbook_available`
- `spread_quality_available`
- `account_state_trustworthy`
- `market_state_trustworthy`

### `previous_thesis`

- `previous_decision`
- `previous_strategy_engine`
- `previous_holding_profile`
- `previous_rationale_codes`
- `previous_no_trade_reason_codes`
- `previous_invalidation_reason_codes`
- `previous_regime_packet_summary`
- `previous_data_quality_grade`
- `last_ai_invoked_at`
- `delta_changed_fields`
- `delta_reason_codes_added`
- `delta_reason_codes_removed`
- `thesis_degrade_detected`
- `regime_transition_detected`
- `data_quality_changed`

### `prior_context`

- `engine_prior_available`
- `engine_prior_sample_count`
- `engine_sample_threshold_satisfied`
- `engine_prior_classification`: `strong | neutral | weak | unavailable`
- `engine_expectancy_hint`
- `engine_net_pnl_after_fees_hint`
- `engine_avg_signed_slippage_bps_hint`
- `engine_time_to_profit_hint_minutes`
- `engine_drawdown_impact_hint`
- `capital_efficiency_available`
- `capital_efficiency_sample_count`
- `capital_efficiency_sample_threshold_satisfied`
- `capital_efficiency_classification`: `efficient | neutral | inefficient | unavailable`
- `pnl_per_exposure_hour_hint`
- `net_pnl_after_fees_per_hour_hint`
- `time_to_0_25r_hint_minutes`
- `time_to_0_5r_hint_minutes`
- `time_to_fail_hint_minutes`
- `capital_slot_occupancy_efficiency_hint`
- `session_prior_available`
- `session_prior_sample_count`
- `session_sample_threshold_satisfied`
- `session_prior_classification`: `strong | neutral | weak | unavailable`
- `time_of_day_prior_available`
- `time_of_day_prior_sample_count`
- `time_of_day_sample_threshold_satisfied`
- `time_of_day_prior_classification`: `strong | neutral | weak | unavailable`
- `prior_reason_codes`
- `prior_penalty_level`: `none | light | medium | strong`
- `expected_payoff_efficiency_hint_summary`

### Prior live behavior

- prior는 soft-only 입니다. `risk.py` hard gate와 `execution.py` 실행 경계는 바뀌지 않습니다.
- sample threshold 미달이면 해당 prior는 `unavailable`로 남고 live confidence/abstain에 영향 주지 않습니다.
- `data_quality.degraded | unavailable`는 neutral이 아니라 uncertainty로 남습니다.
- `breakout_exception_engine`, `swing`, `position` 문맥은 weak prior 또는 poor quality에서 더 쉽게 `hold` / `should_abstain=true`로 기웁니다.
- `reduce`, `exit`, `protection_review_event` 같은 survival path는 prior 때문에 막히지 않습니다.

## 2026-04 TradeDecision Optional Metadata Additions

- Existing action enum stays unchanged: `hold | long | short | reduce | exit`
- The response schema now also accepts:
  - `confidence_band`
  - `recommended_holding_profile`
  - `primary_reason_codes`
  - `no_trade_reason_codes`
  - `abstain_reason_codes`
  - `invalidation_reason_codes`
  - `expected_time_to_0_25r_minutes`
  - `expected_time_to_0_5r_minutes`
  - `expected_mae_r`
  - `regime_transition_risk`
  - `data_quality_penalty_applied`
  - `should_abstain`
  - `bounded_output_applied`
  - `fallback_reason_codes`
  - `fail_closed_applied`
  - `provider_status`
  - `engine_prior_classification`
  - `capital_efficiency_classification`
  - `session_prior_classification`
  - `time_of_day_prior_classification`
  - `prior_penalty_level`
  - `prior_reason_codes`
  - `sample_threshold_satisfied`
  - `confidence_adjustment_applied`
  - `abstain_due_to_prior_and_quality`
  - `expected_payoff_efficiency_hint_summary`
  - `prompt_family_hint`
  - `ai_context_version`
- These fields are backward-compatible. Existing provider/mock payloads that only return the legacy minimum shape still validate.

## 2026-04 Prompt Routing / Bounded Output / Fail-Closed

- `TradingDecisionAgent` resolves prompt family from `strategy_engine × trigger_type`.
- `ai_context.prior_context` is consumed as soft prior only:
  - strong prior can lift confidence modestly
  - weak prior and poor quality can lower confidence or set `should_abstain=true`
  - inefficient capital efficiency can downgrade aggressive holding-profile proposals back toward `scalp`
- These adjustments happen before bounded output reaches `risk.py`, but they do not create a new hard gate.
- `TradingDecisionAgent` now resolves a prompt family from `strategy_engine × trigger_type`.
- Current prompt families:
  - `entry_pullback_review`
  - `entry_continuation_review`
  - `breakout_exception_review`
  - `range_mean_reversion_review`
  - `open_position_thesis_review`
  - `protection_reduce_review`
  - `periodic_backstop_review`
- `AgentRun.metadata_json` for `trading_decision` now carries:
  - `trigger_type`
  - `strategy_engine_name`
  - `prompt_family`
  - `allowed_actions`
  - `forbidden_actions`
  - `bounded_output_applied`
  - `fallback_reason_codes`
  - `fail_closed_applied`
  - `provider_status`
  - `should_abstain`
  - `abstain_reason_codes`
- `TradingDecision.output_payload` can also carry the same bounded/fail-closed flags through:
  - `bounded_output_applied`
  - `fallback_reason_codes`
  - `fail_closed_applied`
  - `provider_status`
  - `should_abstain`
  - `abstain_reason_codes`
- Fail-closed applies only to new-entry-capable review families. Provider timeout, provider unavailable, malformed output, or schema validation failure normalizes the decision to `hold` before `risk.py`.
- `protection_review_event`, `reduce`, `exit`, protection recovery, and other deterministic survival paths remain executable without AI and are not blocked by provider failure.

## 2026-04 Hybrid AI Review Fields

### Trigger reasons

- `entry_candidate_event`
- `breakout_exception_event`
- `open_position_recheck_due`
- `protection_review_event`
- `manual_review_event`
- `periodic_backstop_due`

### Settings payload additions

- `symbol_cadence_overrides` rows may now include:
  - `ai_backstop_enabled_override`
  - `ai_backstop_interval_minutes_override`
- `symbol_effective_cadences` rows now include:
  - `ai_backstop_enabled`
  - `ai_backstop_interval_minutes`

### Operator / dashboard observability additions

- `GET /api/dashboard/operator` symbol decision snapshot can now expose:
  - `last_ai_trigger_reason`
  - `last_ai_invoked_at`
  - `next_ai_review_due_at`
  - `trigger_deduped`
  - `trigger_fingerprint`
  - `last_ai_skip_reason`
- These fields are sourced from the latest decision metadata and may be overlaid by the latest `interval_decision_cycle` scheduler outcome when the latest scheduler state is newer than the latest decision row.

### Interval decision cycle audit states

- `interval_decision_cycle` outcomes distinguish:
  - AI invoked
  - no event
  - deduped trigger
  - periodic backstop due
- Expected audit events include:
  - `decision_ai_invoked`
  - `decision_ai_skipped`
  - `decision_ai_no_event`
  - `decision_ai_deduped`
  - `decision_ai_backstop_due`

### Execution boundary reminder

- Hybrid review changes only affect when AI is consulted.
- The execution boundary remains:
  - schema validation
  - `risk.py` final gate
  - `execution.py`
- `reduce`, `exit`, `reduce_only`, `protection recovery`, and `emergency` flows remain deterministic-capable paths and are not blocked on AI review availability.

## Holding Profile Fields

- `TradeDecision`, decision metadata, candidate ranking, pending entry plan snapshot, `ExecutionIntent`, execution result, and `position_management` metadata can now carry:
  - `holding_profile`: `scalp | swing | position`
  - `holding_profile_reason`
  - `initial_stop_type`
  - `ai_stop_management_allowed`
  - `hard_stop_active`
- 기본값은 `holding_profile=scalp`입니다.
- `risk_guard.debug_payload.holding_profile` is the source-of-truth for holding-profile operating overlays and blocker codes.
- `position_management` metadata also stores `stop_widening_allowed=false`; stop widening is not a supported management action.

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
    - additive drawdown operating layer:
      - `current_drawdown_state`: `normal | caution | drawdown_containment | recovery`
      - `drawdown_state_entered_at`
      - `drawdown_transition_reason`
      - `drawdown_policy_adjustments`
  - `auto_resume_status`, `auto_resume_last_blockers`
  - `account_sync_summary`, `sync_freshness_summary`, `market_freshness_summary`
    - `equity`, `wallet_balance`, `available_balance`
    - `realized_pnl`, `fee_total`, `funding_total`, `net_pnl`
- `can_enter_new_position`
- `current_drawdown_state`
- `drawdown_state_entered_at`
- `drawdown_transition_reason`
- `drawdown_policy_adjustments`
  - hard risk policy 위의 adaptive operating layer 요약입니다.
  - `risk_pct_multiplier`, `leverage_multiplier`, `notional_multiplier`
  - `max_non_priority_selected`, `entry_capacity_multiplier`, `entry_score_threshold_uplift`
  - `winner_only_pyramiding`, `breakout_exception_allowed`

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
  - adaptive multiplier 요약 외에 setup auto-disable 상태를 함께 내립니다.
  - `setup_disable_active`
  - `active_setup_disable_buckets`
  - `setup_disable`
    - `bucket_key`
    - `symbol`, `timeframe`, `scenario`, `regime`, `entry_mode`
    - `disable_reason_codes`
    - `disabled_at`
    - `cooldown_expires_at`
    - `metrics`
    - `recovery_condition`
- `position_management_summary`
  - `active_holding_profiles`: 현재 open live position의 `scalp / swing / position` 분포
  - `hard_stop_active_positions`: deterministic hard stop이 active인 position 수
  - `deterministic_hard_stop_positions`: `initial_stop_type=deterministic_hard_stop` 기준 position 수
  - `stop_widening_forbidden_positions`: `stop_widening_allowed=false` position 수
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
- `GET /api/performance`
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


### `GET /api/performance`

신호/결정 성과를 24h·7d·30d 윈도우로 집계해 반환합니다.

- 공개 API path는 유지되며(`GET /api/performance`), 내부 구현은 성과 리포팅 서비스로 정리되어 backlog 의미를 사용하지 않습니다.
- 응답 핵심 필드
  - `generated_at`
  - `window_hours`
  - `items` (기본 24h 기준 rationale code별 성과)
  - `windows` (`24h`, `7d`, `30d` 상세 breakdown)

### `GET /api/dashboard/overview`

기존 overview 화면과 운영 요약 카드가 사용하는 기본 상태 응답입니다.

- `operational_status`
  - overview / account / settings가 공통으로 소비할 표준 운영 상태 payload
  - `trading_paused`, `live_execution_ready`, `approval_armed`, `approval_expires_at`
  - `operating_state`, `guard_mode_reason_*`, `blocked_reasons`, `latest_blocked_reasons`
  - `control_status_summary`: `exchange_can_trade`, `app_live_armed`, `approval_window_open`, `approval_state`, `approval_detail`, `paused`, `degraded`, `risk_allowed`, `blocked_reasons_current_cycle`
    - additive drawdown state:
      - `current_drawdown_state`
      - `drawdown_state_entered_at`
      - `drawdown_transition_reason`
      - `drawdown_policy_adjustments`
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
  - `control_status_summary`: `exchange_can_trade`, `rollout_mode`, `exchange_submit_allowed`, `limited_live_max_notional`, `app_live_armed`, `approval_window_open`, `approval_state`, `approval_detail`, `paused`, `degraded`, `risk_allowed`, `blocked_reasons_current_cycle`, `approval_control_blocked_reasons`, `live_arm_disabled`, `live_arm_disable_reason_code`, `live_arm_disable_reason`
    - additive drawdown state:
      - `current_drawdown_state`
      - `drawdown_state_entered_at`
      - `drawdown_transition_reason`
      - `drawdown_policy_adjustments`
  - `auto_resume_status`, `account_sync_summary`, `sync_freshness_summary`, `market_freshness_summary`
    - `account_sync_summary`에는 `wallet_balance`, `available_balance`, `realized_pnl`, `fee_total`, `funding_total`, `net_pnl`가 additive로 포함됩니다.
  - `operator_alert` (critical banner용 additive payload)
    - one-way position mode requirement 위반 시 `level=critical`, `source=reconciliation_position_mode`, `message=one-way required for current local position model`, `position_mode`, `position_mode_checked_at`, `guarded_symbols_count`
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
  - `open_position`: 기존 포지션 snapshot 외에 `holding_profile`, `holding_profile_reason`, `initial_stop_type`, `ai_stop_management_allowed`, `hard_stop_active`, `stop_widening_allowed`가 additive로 포함될 수 있습니다.
  - `execution.recent_fills`: 최근 fill ladder 요약. `execution_id`, `external_trade_id`, `fill_price`, `fill_quantity`, `fee_paid`, `commission_asset`, `realized_pnl`, `created_at`
  - `protection_status`: 기본 protected/missing 상태 외에 `recovery_status`, `auto_recovery_active`, `failure_count`, `last_error`, `last_transition_at`, `trigger_source`, `lifecycle_state`, `verification_status`, `last_event_type`, `last_event_message`, `last_event_at`
  - `audit_events`: operator dashboard에서는 raw payload 전체 대신 approval / protection / execution 설명에 필요한 compact payload만 유지합니다.
- 전역 최신 1건 `ai_decision / risk_guard / execution` 필드는 더 이상 대표값으로 내려주지 않습니다.

운영자 메인 화면 전용 snapshot입니다. 같은 흐름의 정보를 한 응답으로 묶어 보여줍니다.

- `control`
  - 지금 신규 진입 가능한지 판단하는 제어 상태
  - `control_status_summary`
    - `operational_status.control_status_summary`의 same-value passthrough
    - drawdown operating layer도 same-value passthrough로 포함됩니다.
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
    - `adaptive_setup_disable`
      - `active`
      - `reason_code`
    - `decision_agreement`
      - `level`
      - `ai_used`
      - `risk_pct_multiplier`, `leverage_multiplier`, `notional_multiplier`
      - `agreement_adjusted_notional`, `agreement_adjusted_quantity`
      - `blocked_reason_code`
    - `strategy_engine`
      - `selected_engine`
        - `engine_name`
        - `scenario`
        - `decision_hint`
        - `entry_mode`
        - `eligible`
        - `priority`
        - `reasons`
      - `candidates`
      - `session_context`
        - `utc_hour`
        - `session_label`
        - `time_of_day_bucket`
    - `meta_gate`
      - `gate_decision`
      - `expected_hit_probability`
      - `expected_time_to_profit_minutes`
      - `reject_reason_codes`
      - `confidence_adjustment`
      - `risk_multiplier`, `leverage_multiplier`, `notional_multiplier`
      - `soft_adjusted_notional`, `soft_adjusted_quantity`
      - `components`
    - `setup_cluster_state`
      - `matched`, `active`
      - `cluster_key`
      - `disable_reason_codes`
      - `disabled_at`, `cooldown_expires_at`
    - `suppression_context`
      - `level`: `none | risk_haircut | soft_bias | hard_block`
      - `sources`
      - `reason_codes`
      - `applies_hard_block`, `applies_risk_haircut`, `applies_soft_bias`
      - `metrics`, `recovery_condition`
    - `drawdown_state`
      - `current_drawdown_state`
      - `previous_drawdown_state`
      - `entered_at`
      - `transition_reason`
      - `drawdown_depth_pct`
      - `recent_net_pnl`, `recent_net_pnl_pct`
      - `consecutive_losses`
      - `recovery_progress`
      - `policy_adjustments`
      - `same_side_pyramiding`
      - `winner_only_pyramiding`
      - `breakout_exception_allowed`
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
- 일반 신규 진입 아이디어의 `entry_mode`는 `pullback_confirm` 또는 드문 `breakout_confirm` 위주로 정규화되며, `immediate`는 plan watcher가 confirm 후 넘긴 실행 직전 decision 같은 제한된 예외에서만 사용됩니다.
- `risk_guard`는 신규 `long / short`에 한해 결정론적 entry trigger를 다시 검사합니다.
- 신규 차단 사유는 `ENTRY_TRIGGER_NOT_MET`, `CHASE_LIMIT_EXCEEDED`, `INVALID_INVALIDATION_PRICE`를 `reason_codes`와 `blocked_reason_codes`로 남깁니다.
- adaptive setup disable bucket 이 active인 신규 진입은 `UNDERPERFORMING_SETUP_DISABLED`를 추가 blocker로 남깁니다.
- `reduce / exit / protection / emergency` 계열은 이 trigger 때문에 막지 않습니다.

### Pending entry plan lifecycle

- 15분 decision cycle은 신규 `long / short` 아이디어를 즉시 주문으로 연결하지 않고, 조건부 진입이면 `PendingEntryPlan`을 `armed` 상태로 저장할 수 있습니다.
- plan은 `symbol + side` 기준 active 1개만 유지하며, 같은 실행 시도는 `symbol + side + source_decision_run_id + expires_at` 기반 `idempotency_key`로 중복을 억제합니다.
- plan에는 `entry_mode`, `entry_zone_min`, `entry_zone_max`, `invalidation_price`, `max_chase_bps`, `idea_ttl_minutes`, `stop_loss`, `take_profit`, `risk_pct_cap`, `leverage_cap`가 함께 저장됩니다.
- `ENTRY_TRIGGER_NOT_MET`, `CHASE_LIMIT_EXCEEDED`, `SLIPPAGE_THRESHOLD_EXCEEDED`처럼 현재가 기준으로만 미충족인 entry blocker만 남아 있으면 plan을 `armed`로 유지할 수 있습니다.
- 반대로 `hold`, 반대 방향 신규 계획, TTL 만료, invalidation 붕괴, stale sync, protection 불일치가 발생하면 기존 armed plan은 `canceled` 또는 `expired`로 종료됩니다.
- 1분 watcher cycle은 최신 시장/계좌/포지션/오픈오더/보호주문 snapshot을 다시 모은 뒤 plan zone 진입 여부를 보고, 1분 confirm의 `quality_score`가 threshold를 넘는 경우에만 `risk_guard`를 재실행합니다.
- watcher 실행에서도 순서는 항상 `decision(plan) -> risk_guard -> execution`이며, `risk_guard.allowed=true`가 아니면 live order를 제출하지 않습니다.
- watcher가 주문을 성공적으로 제출하거나 동일 `idempotency_key` 실행이 이미 완료된 것을 확인하면 plan은 `triggered`로 종료됩니다.
- `trigger_details`에는 additive confirm quality fields가 함께 남습니다.
  - `quality_score`
  - `quality_threshold`
  - `quality_state`: `trigger | waiting | cancel`
  - `quality_components.reclaim_signal_strength`
  - `quality_components.candle_body_quality`
  - `quality_components.wick_imbalance_quality`
  - `quality_components.late_chase_quality`
  - `quality_components.expected_rr_quality`
  - `candle_body_ratio`
  - `wick_imbalance`
  - `late_chase`, `late_chase_ratio`
  - `baseline_expected_rr`, `current_expected_rr`, `expected_rr_deterioration_pct`
- `quality_state=waiting`이면 plan은 그대로 `armed_waiting_confirmation`으로 유지됩니다.
- `quality_state=cancel`이면 severe late chase 또는 reward-to-risk 붕괴로 `PLAN_CONFIRM_QUALITY_REJECTED`가 기록되고 plan은 취소됩니다.
- decision/watcher payloads now include additive cadence fields:
  - `cadence.mode`: `idle | watch | active_position | armed_entry_plan | high_priority_recovery`
  - `cadence.reasons`
  - `cadence.skip_reason`
  - `cadence.effective_cadence.market_refresh_interval_minutes`
  - `cadence.effective_cadence.position_management_interval_seconds`
  - `cadence.effective_cadence.decision_cycle_interval_minutes`
  - `cadence.effective_cadence.ai_call_interval_minutes`
  - `cadence.effective_cadence.entry_plan_watcher_interval_minutes`
  - `ai_skipped_reason`: deterministic-only decision path reason when AI inference was intentionally skipped

#### Entry trigger and auto-resize

- `TradeDecision` payload에는 신규 진입 아이디어를 제한하기 위한 optional 필드 `entry_mode`, `invalidation_price`, `max_chase_bps`, `idea_ttl_minutes`가 포함될 수 있습니다.
- 일반 agent/AI 신규 진입 아이디어는 `immediate`로 바로 실행되지 않으며, `immediate`는 confirm을 마친 armed plan trigger 같은 제한된 실행 컨텍스트에만 남습니다.
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
- `debug_payload.decision_agreement`는 deterministic baseline 대비 최종 AI decision 합의도를 함께 남깁니다.
  - `full_agreement`: 방향 + `entry_mode` 일치
  - `partial_agreement`: 방향만 일치
  - `disagreement`: 방향 불일치 또는 한쪽만 `hold`
  - `partial_agreement`는 entry 전용 soft multiplier를 적용하고, `disagreement`는 신규 진입 blocker `DETERMINISTIC_BASELINE_DISAGREEMENT`를 남깁니다.
- `debug_payload.setup_cluster_state`는 `symbol + timeframe + scenario + entry_mode + regime.primary_regime + regime.trend_alignment` 기준 setup cluster 상태를 남깁니다.
  - disable 조건:
    - `expectancy < 0`
    - `net_pnl_after_fees < 0`
    - 그리고 `loss_streak >= 3` 또는 `avg_signed_slippage_bps >= 12`
- `debug_payload.suppression_context`는 recent performance suppression의 최종 stage를 남깁니다.
  - `hard_block`: adaptive setup disable 또는 setup cluster disable이 활성화되어 신규 진입을 최종 차단
  - `soft_bias`: hold bias만 강하고 최종 차단은 아님
  - `risk_haircut`: agents가 confidence/risk_pct를 낮춰 넘긴 상태
  - source-of-truth는 `risk_guard`이며, `agents`는 suppression 정보를 metadata로만 전달합니다.
    - 최소 sample size `4`, lookback `8`
  - 상태 필드:
    - `status`: `active_disabled`, `cooldown_elapsed`, `metrics_recovered`, `monitoring`, `insufficient_data`
    - `cooldown_active`
    - `recovery_trigger`: `cooldown_elapsed`, `positive_recent_metrics`, 또는 `null`
    - `thresholds`
  - recovery 조건:
    - cooldown `180분` 경과
    - 또는 최근 cluster 지표가 `expectancy >= 0` and `net_pnl_after_fees >= 0`로 회복
  - 최근 같은 cluster에서 연속 손실, negative expectancy, signed slippage 악화가 반복되면 신규 진입 blocker `SETUP_CLUSTER_DISABLED`가 추가될 수 있습니다.
  - `reduce`, `exit`, `reduce_only`, `protection recovery`는 이 code 때문에 막지 않습니다.
- 신규 진입 노출 계산에서 `reduce_only`, `close_only`, `STOP*`, `TAKE_PROFIT*`, `TRAILING_STOP*` open order는 reserved exposure에 포함하지 않습니다.
- `debug_payload.sync_timestamps`는 `account`, `positions`, `open_orders`, `protective_orders` 마지막 sync 시각을 같이 내려 stale state 확인에 사용합니다.
- `debug_payload.market_derivatives_context`는 현재 risk 평가에 사용된 파생시장 공개 데이터 요약입니다.
  - `open_interest`, `open_interest_change_pct`, `funding_rate`, `taker_buy_sell_imbalance`, `perp_basis_bps`, `crowding_bias`
  - `top_trader_long_short_ratio`, `best_bid`, `best_ask`, `spread_bps`, `spread_stress_score`
  - 값이 없으면 `None`으로 남고, source/fallback 정보는 market snapshot payload의 `derivatives_context`에서 확인합니다.
- drawdown state reason / blocker codes:
  - adjustment-only:
    - `DRAWDOWN_STATE_CAUTION`
    - `DRAWDOWN_STATE_CONTAINMENT`
    - `DRAWDOWN_STATE_RECOVERY`
  - entry blocker:
    - `DRAWDOWN_STATE_BREAKOUT_RESTRICTED`
    - `DRAWDOWN_STATE_PYRAMIDING_REQUIRES_WINNER`
  - survival path (`reduce`, `exit`, `reduce_only`, `protection recovery`, `emergency`)는 이 drawdown operating layer 때문에 막히지 않습니다.
- 자동 축소 승인 정보 코드는 `ENTRY_AUTO_RESIZED`, `ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT`, `ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT`, `ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT`, `ENTRY_CLAMPED_TO_SAME_TIER_LIMIT`이며 모두 `adjustment_reason_codes`에 기록됩니다.
- `reduce / exit / protection / emergency` 계열은 trigger와 auto-resize 정책 때문에 막히지 않습니다.

#### Meta gate

- `decision metadata.meta_gate` is an additive secondary approval layer computed after the decision is generated and before `risk_guard` makes the final allow/block decision.
- Input sources:
  - `selection_context.score`
  - `selection_context.performance_summary`
  - `selection_context.universe_breadth`
  - `features.derivatives`
  - `features.lead_lag`
  - `decision_agreement`
- Output fields:
  - `gate_decision`: `pass | soft_pass | reject`
  - `expected_hit_probability`
  - `expected_time_to_profit_minutes`
  - `reject_reason_codes`
  - `confidence_adjustment`
  - `risk_multiplier`, `leverage_multiplier`, `notional_multiplier`
- `risk_guard.debug_payload.meta_gate` mirrors the same structure and is the source-of-truth used when the secondary gate down-sizes or rejects a new entry.
- `gate_decision=soft_pass` is non-blocking and adds `META_GATE_SOFT_PASS` to `adjustment_reason_codes` while down-sizing `approved_risk_pct`, `approved_leverage`, and `approved_projected_notional`.
- `gate_decision=reject` only applies to new entry paths and adds blocker codes such as `META_GATE_LOW_HIT_PROBABILITY`, `META_GATE_NEGATIVE_EXPECTANCY`, `META_GATE_ADVERSE_SIGNED_SLIPPAGE`, `META_GATE_LEAD_LAG_DIVERGENCE`, or `META_GATE_DERIVATIVES_HEADWIND`.
- `risk_guard.debug_payload.slot_allocation` is an additive portfolio allocator layer used only for new entry soft caps.
  - `assigned_slot`
  - `candidate_weight`
  - `slot_conviction_score`
  - `meta_gate_probability`
  - `agreement_alignment_score`
  - `execution_quality_score`
  - `risk_pct_multiplier`
  - `leverage_multiplier`
  - `notional_multiplier`
- When a non-priority entry is down-sized by slot policy, `adjustment_reason_codes` includes `PORTFOLIO_SLOT_SOFT_CAP`.
- Survival paths remain exempt:
  - `reduce`
  - `exit`
  - `reduce_only`
  - `protection recovery`
  - `emergency`
- Final `execution` payloads may include additive `meta_gate` detail so operators can see the secondary gate state alongside execution quality and risk debug data.

#### Holding profile / hard stop

- `decision metadata.holding_profile_context` carries the selected operating profile and the structural reasons behind it.
- `risk_guard.debug_payload.holding_profile` includes:
  - `holding_profile`
  - `holding_profile_reason`
  - `risk_policy`
  - `cadence_hint`
  - `management_policy`
  - `initial_stop_type`
  - `ai_stop_management_allowed`
  - `hard_stop_active`
  - holding-profile blocker codes such as:
    - `HOLDING_PROFILE_REQUIRES_META_GATE_PASS`
    - `HOLDING_PROFILE_SWING_REQUIRES_INTRADAY_ALIGNMENT`
    - `HOLDING_PROFILE_POSITION_REQUIRES_STRONG_REGIME`
    - `HOLDING_PROFILE_POSITION_BREADTH_WEAK`
    - `HOLDING_PROFILE_POSITION_LEAD_LAG_MISMATCH`
    - `HOLDING_PROFILE_POSITION_RELATIVE_STRENGTH_WEAK`
    - `HOLDING_PROFILE_POSITION_DERIVATIVES_HEADWIND`
    - `HOLDING_PROFILE_BREAKOUT_SCALP_ONLY`
- execution payloads and `position_management.metadata` mirror:
  - `holding_profile`
  - `holding_profile_reason`
  - `initial_stop_type=deterministic_hard_stop`
  - `ai_stop_management_allowed`
  - `hard_stop_active`
  - `stop_widening_allowed=false`

#### Winner-only pyramiding / add-on

- same-side open position이 있는 신규 `long / short`는 execution 직전 `intent_type=scale_in`으로 해석될 수 있으며, `risk_guard`는 이 경로를 winner-only add-on으로 다시 검증합니다.
- `risk_guard.debug_payload.add_on` is the source-of-truth for add-on gating and sizing:
  - `same_side_pyramiding`
  - `current_r_multiple`
  - `existing_unrealized_pnl`
  - `protective_stop_ready`
  - `protected_r_multiple`
  - `trend_alignment_ok`
  - `breadth_veto`
  - `lead_lag_veto`
  - `derivatives_veto`
  - `spread_bps`
  - `spread_headwind`
  - `current_position_notional`
  - `risk_pct_multiplier`
  - `leverage_multiplier`
  - `notional_multiplier`
  - `add_on_reason`
- 신규 add-on blocker reason codes:
  - `ADD_ON_REQUIRES_WINNING_POSITION`
  - `ADD_ON_PROTECTIVE_STOP_REQUIRED`
  - `ADD_ON_TREND_ALIGNMENT_REQUIRED`
  - `ADD_ON_BREADTH_VETO`
  - `ADD_ON_LEAD_LAG_VETO`
  - `ADD_ON_DERIVATIVES_VETO`
  - `ADD_ON_SPREAD_HEADWIND`
- add-on이 허용되면 non-blocking adjustment reason `ADD_ON_RISK_DOWNSIZED`가 남고, `approved_risk_pct`, `approved_leverage`, `approved_projected_notional`은 add-on 전용 soft multiplier와 현재 보유 포지션 notional cap 기준으로 더 보수적으로 축소됩니다.
- `reduce`, `exit`, `reduce_only`, `protection recovery`, `emergency`는 add-on blocker 때문에 막지 않습니다.

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

drawdown state transition audit:

- `event_type=drawdown_state_transition`
- `event_category=health_system`
- payload may include:
  - `previous_drawdown_state`
  - `current_drawdown_state`
  - `entered_at`
  - `transition_reason`
  - `policy_adjustments`
  - `drawdown_depth_pct`
  - `recent_net_pnl`
  - `consecutive_losses`

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

State-based cadence notes:

- scheduler keeps the configured symbol schedule as the baseline and applies a runtime cadence overlay per symbol
- `idle` slows market refresh / decision / AI cadence in low-edge conditions
- `idle` is also used when there is no open position and no armed entry plan, and the current symbol is inside an active setup-disable cooldown or setup-cluster cooldown
- `armed_entry_plan` keeps the 1m watcher active while an armed plan exists
- `active_position` prioritizes position management cadence and slows fresh decision cadence
- `high_priority_recovery` prioritizes recovery/management cadence when protection or degraded state needs urgent attention
- scheduler run outcomes and decision cycle payloads surface the current cadence mode instead of silently reinterpreting timing in the frontend
- additional idle reasons may include:
  - `SETUP_DISABLE_COOLDOWN_ACTIVE`
  - `SETUP_CLUSTER_COOLDOWN_ACTIVE`
- additive cadence flags may include:
  - `setup_disable_active`
  - `setup_disable_reasons`
  - `setup_cluster_active`
  - `setup_cluster_reasons`
- additive `ai_skipped_reason` values may include:
  - `CADENCE_IDLE_SETUP_DISABLE_ACTIVE`
  - `CADENCE_IDLE_SETUP_CLUSTER_ACTIVE`

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
  - `guarded_symbols_count`
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
- operator/settings approval control summary additionally surfaces this as `live_arm_disabled=true` and `live_arm_disable_reason="one-way required for current local position model"` so operators can see direct policy context even when blocker reasons overlap with risk_guard.

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
  - `net_pnl_after_fees`
  - `avg_win`
  - `avg_loss`
  - `expectancy`
  - `average_hold_time_minutes`
  - `cancel_attempts`
  - `cancel_successes`
  - `cancel_success_rate`
  - `stop_hit_rate`
  - `tp_hit_rate`
  - `partial_tp_contribution`
  - `runner_contribution`
  - `average_mfe_pct`
  - `average_mae_pct`
- variant breakdowns:
  - `by_symbol`
  - `by_timeframe`
  - `by_scenario`
  - `by_regime`
  - `by_trend_alignment`
  - `by_execution_policy_profile`
  - `by_entry_mode`
  - `by_rationale_code`
- variant recent walk-forward fields:
  - `recent_window_summary`
  - `walk_forward_recommendation`
  - `underperforming_buckets`
- comparison blocks:
  - `symbol_comparison`
  - `timeframe_comparison`
  - `scenario_comparison`
  - `regime_comparison`
  - `trend_alignment_comparison`
  - `execution_policy_profile_comparison`
  - `entry_mode_comparison`
  - `rationale_comparison`
- top-level recommendation fields:
  - `recent_walk_forward_recommendation`
  - `underperforming_buckets`

`walk_forward_recommendation` is additive only. It does not auto-apply live settings, but it is shaped so it can
be wired into future `adaptive_signal_context` / `risk_context` usage:

- `risk_pct_multiplier`
- `leverage_multiplier`
- `max_chase_bps`
- `entry_mode_preference`
- `partial_tp_rr`
- `partial_tp_size_pct`
- `time_stop_minutes`
- `trailing_aggressiveness`
- `adaptive_signal_context_patch`
- `risk_context_patch`

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

### Position management MFE rollback metadata

Live position management context and execution metadata can now carry additive MFE-protection fields:

- `current_r_multiple`
- `mfe_r`
- `mae_r`
- `entry_time_profile`
  - `breakout_fast`
  - `continuation_balanced`
  - `pullback_flexible`
- `planned_max_holding_minutes`
- `effective_max_holding_minutes`
- `early_fail_minutes`
- `early_fail_r_floor`
- `hold_extension_minutes`
- `hold_extension_active`
- `time_to_fail_basis`
- `time_to_fail_ready`
- `time_to_fail_action`
- `time_to_fail_reason`
- `mfe_rollback_pct`
- `mfe_rollback_threshold`
- `mfe_protection_action`
  - `monitor`
  - `tighten_stop`
  - `reduce`
  - `exit`
- `management_stage`
  - `initial`
  - `partial_taken`
  - `trailing_runner`
  - `defensive_reduce`

When rollback protection is triggered, rationale / audit payloads may include:

- `POSITION_MANAGEMENT_MFE_ROLLBACK`
- `POSITION_MANAGEMENT_MFE_ROLLBACK_TIGHTEN`
- `POSITION_MANAGEMENT_MFE_ROLLBACK_REDUCE`
- `POSITION_MANAGEMENT_MFE_ROLLBACK_EXIT`
- `POSITION_MANAGEMENT_TIME_TO_FAIL`
- `POSITION_MANAGEMENT_BREAKOUT_TIME_FAIL_REDUCE`
- `POSITION_MANAGEMENT_BREAKOUT_TIME_FAIL_EXIT`
- `POSITION_MANAGEMENT_CONTINUATION_TIME_FAIL_REDUCE`
- `POSITION_MANAGEMENT_CONTINUATION_TIME_FAIL_EXIT`
- `POSITION_MANAGEMENT_PULLBACK_TIME_FAIL_REDUCE`
- `POSITION_MANAGEMENT_PULLBACK_TIME_FAIL_EXIT`
- `position_management` metadata may also include winner-only pyramiding fields for approved `scale_in` fills:
  - `add_on_count`
  - `pyramiding_stage`
  - `last_add_on_at`
  - `add_on_reason`
  - `add_on_r_multiple`
  - `last_add_on`
    - `at`
    - `stage`
    - `reason`
    - `r_multiple`
    - `risk_multiplier`
    - `leverage_multiplier`
    - `notional_multiplier`

Agent-side entry decisions now also add setup timing rationale codes:

- `SETUP_TIME_PROFILE_BREAKOUT_FAST`
- `SETUP_TIME_PROFILE_CONTINUATION_BALANCED`
- `SETUP_TIME_PROFILE_PULLBACK_FLEXIBLE`

### Candidate selection / ranking summary

Market and feature payloads can now carry a derivatives context summary:

- `market_snapshot.derivatives_context`
  - `source`: `binance_public`, `seed_fallback`, `unavailable`
  - `fallback_used`, `fetch_failed`
  - `open_interest`, `open_interest_change_pct`, `funding_rate`
  - `taker_buy_sell_imbalance`, `perp_basis_bps`, `crowding_bias`
  - `top_trader_long_short_ratio`
  - `best_bid`, `best_ask`, `spread_bps`, `spread_stress_score`
- `features.derivatives`
  - `oi_expanding_with_price`, `oi_falling_on_breakout`
  - `crowded_long_risk`, `crowded_short_risk`
  - `taker_flow_alignment`, `funding_bias`, `basis_bias`
  - `top_trader_long_short_ratio`, `top_trader_crowding_bias`
  - `top_trader_long_crowded`, `top_trader_short_crowded`
  - `long_alignment_score`, `short_alignment_score`
  - `best_bid`, `best_ask`, `spread_bps`
  - `spread_stress_score`, `spread_headwind`, `spread_stress`, `breakout_spread_headwind`
  - `entry_veto_reason_codes`, `breakout_veto_reason_codes`
  - `long_discount_magnitude`, `short_discount_magnitude`
- `features.lead_lag`
  - `available`
  - `leader_bias`: `bullish | bearish | mixed | neutral | unknown`
  - `reference_symbols`, `missing_reference_symbols`
  - `bullish_alignment_score`, `bearish_alignment_score`
  - `bullish_breakout_confirmed`, `bearish_breakout_confirmed`
  - `bullish_breakout_ahead`, `bearish_breakout_ahead`
  - `bullish_pullback_supported`, `bearish_pullback_supported`
  - `bullish_continuation_supported`, `bearish_continuation_supported`
  - `strong_reference_confirmation`, `weak_reference_confirmation`
  - `references`
    - `symbol`, `timeframe`
    - `trend_score`, `momentum_score`
    - `breakout_direction`, `pullback_state`
    - `primary_regime`, `trend_alignment`, `weak_volume`, `momentum_state`
    - `volume_ratio`

If derivatives public data is missing, the engine keeps the candle-based path and sets fallback flags instead of failing the cycle.

Funding / OI / spread filters are entry-side veto filters first, not entry boosters.

- `SPREAD_HEADWIND`
  - wide spread relative to current setup quality
  - used for score discount and deterministic hold bias
- `SPREAD_STRESS`
  - wide spread plus shallow top-of-book depth or breakout-specific spread deterioration
  - used for stronger hold bias / score discount than `SPREAD_HEADWIND`
- `TOP_TRADER_LONG_CROWDED`
  - long-side crowding is elevated in top trader positioning
  - used for long hold bias / score discount only
- `TOP_TRADER_SHORT_CROWDED`
  - short-side crowding is elevated in top trader positioning
  - used for short hold bias / score discount only
- `BREAKOUT_OI_SPREAD_FILTER`
  - breakout-like entry with no OI expansion and degraded spread
  - used to keep breakout exception more conservative

If BTC/ETH lead references are missing, the engine keeps the existing candle path and marks `LEAD_LAG_CONTEXT_UNAVAILABLE` instead of failing the cycle.

Overview, operator control, and settings operational payloads now expose:

- `candidate_selection_summary`
  - `generated_at`
  - `mode`
  - `max_selected`
  - `current_drawdown_state`
  - `drawdown_entered_at`
  - `drawdown_transition_reason`
  - `drawdown_policy_adjustments`
  - `drawdown_capacity_reason`
  - `breadth_regime`
  - `breadth_summary`
    - `tracked_symbols`
    - `bullish_aligned_count`
    - `bearish_aligned_count`
    - `weak_volume_count`
    - `transition_count`
    - `entry_candidates`
    - `directional_bias`
    - `bullish_alignment_ratio`
    - `bearish_alignment_ratio`
    - `weak_volume_ratio`
    - `transition_ratio`
    - `entry_score_multiplier`
    - `hold_bias_multiplier`
  - `capacity_reason`
  - `entry_score_threshold`
  - `portfolio_allocator`
    - `allocator_mode`
    - `selected_entry_symbols`
    - `weights`
    - `slot_mode`
    - `slot_plan`
      - `available_slots`
      - `high_conviction_threshold`
      - `medium_conviction_threshold`
      - `low_conviction_action`
    - `slot_assignments`
      - `slot_1` / `slot_2` / `slot_3`
        - `label`
        - `symbol`
        - `candidate_weight`
        - `slot_conviction_score`
        - `meta_gate_probability`
  - `selected_symbols`
  - `skipped_symbols`
  - `rankings`

Ranking payloads include:

- `candidate`
  - `strategy_engine`
  - `strategy_engine_context`
  - `derivatives_summary`
  - `lead_lag_summary`
  - `derivatives_summary.discount_magnitude`
  - `derivatives_summary.veto_reason_codes`
- `performance_summary`
  - `avg_time_to_profit_minutes`
  - `avg_drawdown_impact`
  - `components.engine`
- `score`
  - `regime_fit`
  - `expected_rr`
  - `recent_signal_performance`
    - approval-rate가 아니라 realized `expectancy`, `net_pnl_after_fees`, `avg_signed_slippage_bps`, scenario/regime bucket 성과를 합성한 expectancy 중심 점수
  - `derivatives_alignment`
  - `lead_lag_alignment`
  - `meta_gate_probability`
  - `agreement_alignment`
  - `execution_quality`
  - `slot_conviction`
  - `slippage_sensitivity`
  - `exposure_impact`
  - `confidence_consistency`
  - `correlation_penalty`
  - `total_score`
- `selection_reason`
- `selected_reason`
- `rejected_reason`
  - 저성과 후보는 correlation penalty 이전에 `underperforming_expectancy_bucket`, `expectancy_below_threshold`, `adverse_signed_slippage`로 탈락할 수 있음
- `max_abs_correlation`
- `breadth_regime`
- `capacity_reason`
- `entry_score_threshold`
- `breadth_score_multiplier`
- `breadth_score_adjustment`
- `breadth_hold_bias`
- `breadth_adjustment_reasons`
- `assigned_slot`
- `slot_label`
- `slot_reason`
- `candidate_weight`
- `portfolio_weight`
- `slot_conviction_score`
- `meta_gate_probability`
- `agreement_alignment_score`
- `agreement_level_hint`
- `execution_quality_score`
- `slot_risk_pct_multiplier`
- `slot_leverage_multiplier`
- `slot_notional_multiplier`
- `low-conviction` 신규 진입 후보는 correlation rotation 전에 `rejected_reason=low_conviction_slot_excluded`로 제외될 수 있음
- `performance_summary`
  - `score`
  - `sample_size`
  - `hit_rate`
  - `expectancy`
  - `net_pnl_after_fees`
  - `avg_signed_slippage_bps`
  - `loss_streak`
  - `underperforming`
  - `components`
    - `symbol`
    - `scenario`
    - `regime`
    - `bucket`
- `portfolio_weight`
- `weight_reason`

Decision metadata and audit payloads can also carry breadth context when the symbol is selected through the portfolio rotation cycle:

- `analysis_context.universe_breadth`
- `analysis_context.lead_lag`
- `selection_context`
  - `universe_breadth`
  - `breadth_regime`
  - `capacity_reason`
  - `portfolio_weight`
  - `strategy_engine`
  - `strategy_engine_context`
  - `breadth_score_multiplier`
  - `breadth_score_adjustment`
  - `breadth_hold_bias`
  - `breadth_adjustment_reasons`
  - `selected_reason`

Operational rule:

- current mode is `portfolio_rotation_top_n`
- weak breadth / weak-volume-heavy universes reduce non-priority entry capacity first
- weak breadth also discounts candidate entry score and raises hold bias for structurally weak new-entry candidates
- strong breadth keeps the top expectancy-aligned candidates concentrated instead of widening the entry universe indiscriminately
- priority symbols for open position management or protection recovery remain selected even when entry capacity is reduced
- selected entry symbols receive score-weighted `portfolio_weight` values so operators can see which symbols the rotation layer is concentrating on
- candidate ranking only narrows which symbols enter the decision cycle
- `risk_guard` still remains the final allow/block gate before execution
