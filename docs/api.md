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

## CLI

- `python -m trading_mvp.cli seed`
- `python -m trading_mvp.cli cycle`
- `python -m trading_mvp.cli replay --cycles 5 --start-index 140`
- `python -m trading_mvp.cli review --window 24h`
- `python -m trading_mvp.cli export-schemas`
