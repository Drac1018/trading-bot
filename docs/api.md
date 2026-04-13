# API

## Health

- `GET /health`

서비스와 데이터베이스 초기화 상태를 확인한다. FastAPI `lifespan` 초기화가 끝난 뒤에만 `database=ready`가 반환된다.

## Settings

- `GET /api/settings`
- `PUT /api/settings`
- `POST /api/settings/pause`
- `POST /api/settings/resume`
- `POST /api/settings/live/arm`
- `POST /api/settings/live/disarm`

### `GET /api/settings`

운영자가 설정 화면에서 바로 확인해야 하는 핵심 상태:

- `mode`
- `live_execution_ready`
- `trading_paused`
- `pause_reason_code`
- `pause_origin`
- `pause_reason_detail`
- `pause_triggered_at`
- `pause_severity`
- `pause_recovery_class`
- `auto_resume_after`
- `auto_resume_whitelisted`
- `auto_resume_eligible`
- `auto_resume_status`
- `auto_resume_last_blockers`
- `latest_blocked_reasons`
- `operating_state`
- `protection_recovery_status`
- `protection_recovery_active`
- `protection_recovery_failure_count`
- `missing_protection_symbols`
- `missing_protection_items`

운영 요약 필드:

- `pnl_summary`
  - `basis`
  - `basis_note`
  - `equity`
  - `cash_balance`
  - `net_realized_pnl`
  - `unrealized_pnl`
  - `daily_pnl`
  - `cumulative_pnl`
  - `consecutive_losses`
  - `snapshot_time`
- `account_sync_summary`
  - `status`
  - `reconciliation_mode`
  - `freshness_seconds`
  - `stale_after_seconds`
  - `last_synced_at`
  - `last_warning_reason_code`
  - `last_warning_message`
  - `note`
- `exposure_summary`
  - `reference_symbol`
  - `reference_tier`
  - `metrics`
  - `limits`
  - `headroom`
  - `status`
- `execution_policy_summary`
  - `slippage_threshold_pct`
  - `entry`
  - `scale_in`
  - `reduce`
  - `exit`
  - `protection`
- `market_context_summary`
  - `symbol`
  - `base_timeframe`
  - `context_timeframes`
  - `primary_regime`
  - `trend_alignment`
  - `volatility_regime`
  - `volume_regime`
  - `momentum_state`
  - `data_quality_flags`
- `adaptive_protection_summary`
  - `mode`
  - `status`
  - `active`
  - `failure_count`
  - `missing_symbols`
  - `missing_items`
  - `primary_regime`
  - `volatility_regime`
  - `summary`

## Dashboard

- `GET /api/dashboard/overview`
- `GET /api/market/snapshots`
- `GET /api/market/features`
- `GET /api/decisions`
- `GET /api/positions`
- `GET /api/orders`
- `GET /api/executions`
- `GET /api/risk/checks`
- `GET /api/agents`
- `GET /api/scheduler`
- `GET /api/audit`
- `GET /api/alerts`

### `GET /api/dashboard/overview`

개요 화면에서 사용하는 주요 운영 상태:

- `trading_paused`
- `pause_reason_code`
- `pause_origin`
- `pause_triggered_at`
- `auto_resume_after`
- `auto_resume_status`
- `auto_resume_eligible`
- `auto_resume_last_blockers`
- `latest_blocked_reasons`
- `pause_severity`
- `pause_recovery_class`
- `operating_state`
- `protection_recovery_status`
- `protection_recovery_active`
- `protection_recovery_failure_count`
- `missing_protection_symbols`
- `missing_protection_items`
- `protected_positions`
- `unprotected_positions`
- `position_protection_summary`
- `pnl_summary`
- `account_sync_summary`
- `exposure_summary`
- `execution_policy_summary`
- `market_context_summary`
- `adaptive_protection_summary`

## Live Sync

- `POST /api/live/sync`

거래소 주문, 포지션, 계좌, 보호주문 상태를 동기화하고 운영 상태를 함께 반환한다.

기본 응답 필드:

- `symbols`
- `synced_orders`
- `synced_positions`
- `equity`
- `symbol_protection_state`
- `unprotected_positions`
- `emergency_actions_taken`
- `operating_state`
- `protection_recovery_status`
- `protection_recovery_active`
- `missing_protection_symbols`
- `missing_protection_items`

auto-resume 관련 필드:

- `auto_resume_precheck`
- `auto_resume_postcheck`
- `auto_resume`

각 auto-resume 결과의 공통 shape:

- `attempted`
- `resumed`
- `allowed`
- `status`
- `reason_code`
- `pause_origin`
- `pause_severity`
- `pause_recovery_class`
- `trigger_source`
- `blockers`
- `symbol_blockers`
- `blocker_details`
- `evaluated_symbols`
- `protective_orders`
- `market_data_status`
- `sync_status`
- `approval_state`
- `approval_detail`

## Binance Account

- `GET /api/binance/account`

Binance 계정 응답은 아래 세 가지를 분리해서 보여준다.

- `exchange_can_trade`
  - Binance 원본 `account_info.canTrade`
  - 거래소 계정 권한만 의미하며, 앱 내부 실주문 가능 여부를 뜻하지 않는다.
- `app_live_execution_ready`
  - 앱 내부 `live_execution_ready`
  - 키, 승인창, 환경 게이트 등 앱 실행 준비 상태를 의미한다.
- `app_trading_paused` / `app_operating_state` / `latest_blocked_reasons`
  - 현재 앱이 왜 신규 진입을 막고 있는지 보여주는 운영 상태 요약이다.

호환성을 위해 기존 `can_trade` 필드는 유지되며, 값은 `exchange_can_trade`와 동일하다.

## Reviews / Cycles

- `POST /api/cycles/run`
- `POST /api/reviews/{window}`
- `POST /api/replay/run`

## Backlog

- `GET /api/backlog`
- `GET /api/backlog/{backlog_id}`
- `GET /api/backlog/{backlog_id}/codex-draft`
- `POST /api/backlog/requests`
- `POST /api/backlog/applied`
- `POST /api/backlog/{backlog_id}/auto-apply`
- `POST /api/backlog/auto-apply-supported`

## CLI

- `python -m trading_mvp.cli seed`
- `python -m trading_mvp.cli cycle`
- `python -m trading_mvp.cli replay --cycles 5 --start-index 140`
- `python -m trading_mvp.cli review --window 24h`
- `python -m trading_mvp.cli export-schemas`
