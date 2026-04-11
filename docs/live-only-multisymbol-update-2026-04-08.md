# Live-Only + Multi-Symbol Update

Date: 2026-04-08

## Summary

This update removes the active paper-trading path from the runtime flow and aligns the product around guarded live execution only.

It also adds tracked-symbol multi-select support so one decision cycle can evaluate and act on multiple Binance symbols from the same settings profile.

## Applied Changes

- Live execution is now the only execution path used by the orchestrator.
- Historical replay and seed/demo flows no longer place live orders even if live trading is enabled and armed.
- Settings now store `tracked_symbols` and monthly AI call estimates scale with the number of tracked symbols.
- Dashboard overview and settings expose the tracked symbol list and live execution readiness.
- Live exchange sync defaults to all tracked symbols when no symbol is explicitly provided.
- Position and order defaults were aligned to `live`.
- Example environment and Docker Compose config now advertise tracked symbols instead of paper mode.

## Database

- Added Alembic revision: `7b2f4a9c1d11_add_tracked_symbols.py`
- New settings column: `tracked_symbols` (`JSON`, default `["BTCUSDT"]`)
- Added Alembic revision: `8c0f7e21d55a_remove_paper_trading_flag.py`
- Removed legacy settings column: `paper_trading_enabled`

## Safety Notes

- Live execution is allowed only for real execution triggers:
  - `manual`
  - `realtime_cycle`
  - `scheduled_review`
- The following triggers are analysis-only and will not place live orders:
  - `historical_replay`
  - `seed`

## Validation

Validated locally after the change:

- `python -m pytest -q` -> `14 passed`
- `python -m ruff check backend tests`
- `python -m mypy backend/trading_mvp`
- `python -m trading_mvp.cli export-schemas`
- `python -m trading_mvp.migrate`
- `pnpm lint`
- `pnpm build`

## Service Note

- Frontend service restart succeeded.
- Backend / worker / scheduler Windows service restart was blocked by the current shell permissions, so service-managed processes may need an elevated restart if they are already running an older build.
