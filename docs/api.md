# API

## Health

- `GET /health`

## Dashboard / Data

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
- `GET /api/settings`
- `GET /api/backlog`
- `GET /api/backlog/{backlog_id}`
- `GET /api/backlog/{backlog_id}/codex-draft`
- `POST /api/backlog/{backlog_id}/auto-apply`
- `POST /api/backlog/auto-apply-supported`

### `GET /api/dashboard/overview`

운영 개요 응답에는 최신 시장/의사결정/리스크 요약뿐 아니라, 현재 운영 상태를 바로 보여주기 위한 필드가 포함됩니다.

주요 운영 상태 필드:
- `trading_paused`
- `pause_reason_code`
- `pause_origin`
- `pause_triggered_at`
- `auto_resume_after`
- `auto_resume_status`
- `auto_resume_eligible`
- `auto_resume_last_blockers`
- `pause_severity`
- `pause_recovery_class`
- `protected_positions`
- `unprotected_positions`
- `position_protection_summary`

## Control

- `POST /api/system/seed`
- `POST /api/settings/pause`
- `POST /api/settings/resume`
- `POST /api/backlog/requests`
- `POST /api/backlog/applied`
- `POST /api/cycles/run`
- `POST /api/replay/run`
- `POST /api/reviews/{window}`
- `POST /api/live/sync`

### `POST /api/live/sync`

라이브 동기화는 거래소 주문/포지션 상태를 갱신하면서 보호 주문 상태와 자동 복구 상태도 함께 반환합니다.

기본 응답 필드:
- `symbols`
- `synced_orders`
- `synced_positions`
- `equity`
- `symbol_protection_state`
- `unprotected_positions`
- `emergency_actions_taken`

auto-resume 관련 필드:
- `auto_resume_precheck`
- `auto_resume_postcheck`
- `auto_resume`

auto-resume payload 공통 필드:
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

## CLI

- `python -m trading_mvp.cli seed`
- `python -m trading_mvp.cli cycle`
- `python -m trading_mvp.cli replay --cycles 5 --start-index 140`
- `python -m trading_mvp.cli review --window 24h`
- `python -m trading_mvp.cli export-schemas`
