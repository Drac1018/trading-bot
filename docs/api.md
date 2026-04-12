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

실거래 상태 동기화 전에 `auto_resume_precheck`, 동기화 후 `auto_resume_postcheck`를 평가할 수 있다.

성공 응답에는 아래 필드가 함께 포함된다.
- `auto_resume_precheck`
- `auto_resume_postcheck`
- `auto_resume`

auto-resume payload는 운영 관측용으로 아래 공통 필드를 유지한다.
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

## CLI

- `python -m trading_mvp.cli seed`
- `python -m trading_mvp.cli cycle`
- `python -m trading_mvp.cli replay --cycles 5 --start-index 140`
- `python -m trading_mvp.cli review --window 24h`
- `python -m trading_mvp.cli export-schemas`
