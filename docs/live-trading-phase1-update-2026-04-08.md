# Live Trading Phase 1 Update

## What Was Added

- Binance live order submission for USD-M Futures market orders
- Binance live order cancellation and open-order cleanup for protective exits
- Live order status synchronization using `GET /fapi/v1/order`
- Live position synchronization using `GET /fapi/v3/positionRisk`
- Partial fill persistence from Binance user trades
- `reduceOnly` protection for `reduce` and `exit`
- `closePosition` protective stop-loss and take-profit orders after live entries
- Live execution gating tied to:
  - environment flag
  - stored Binance API credentials
  - manual approval policy
  - time-boxed manual approval window
  - trading pause / kill switch
- Settings UI controls for:
  - live arm / disarm
  - live sync
  - Binance live test order preflight
- startup scripts now run migrations before backend / worker / scheduler start

## New Runtime Behavior

### Live route selection

- If `live_trading_enabled` is `false`, the system stays on the paper path.
- If `live_trading_enabled` is `true`, the risk engine only allows live execution when all of these are true:
  - `LIVE_TRADING_ENV_ENABLED` is enabled in environment
  - Binance API key exists
  - Binance API secret exists
  - `manual_live_approval` is enabled
  - live execution has been armed and the approval window is still open
  - trading is not paused

### Manual approval window

- The operator arms live execution from the settings page or API.
- Arming opens a time-boxed approval window.
- When the window expires, live execution is denied again.

### Live protective behavior

- New `long` / `short` live entries:
  - set leverage first
  - submit market entry
  - sync fills and position
  - place stop-loss and take-profit close orders
- `reduce` / `exit` live actions:
  - cancel existing protective exit orders
  - submit reduce-only market order
  - sync fills and live position
  - if position remains and decision includes brackets, recreate protective exits

### Partial fill handling

- Local live orders are updated from Binance order status
- Trade fills are persisted one-by-one using Binance trade ids
- Duplicate execution rows are prevented by `external_trade_id`

## New API Endpoints

- `POST /api/settings/live/arm`
- `POST /api/settings/live/disarm`
- `POST /api/settings/test/binance/live-order`
- `POST /api/live/sync`

## Schema / Model Changes

### Settings

- `live_execution_armed`
- `live_execution_armed_until`
- `live_approval_window_minutes`

### Positions

- `mode`

### Orders

- `external_order_id`
- `client_order_id`
- `reduce_only`
- `close_only`
- `parent_order_id`
- `exchange_status`
- `last_exchange_update_at`

### Executions

- `external_trade_id`
- `commission_asset`

## Additional Items Included

- safer migration flow for legacy local DBs:
  - stamp initial revision
  - upgrade to head
- backend / worker / scheduler startup scripts now migrate first
- live sync without credentials now returns `400` instead of an unhandled `500`
- generated JSON schemas refreshed after schema changes

## Self-Review Findings Applied

- fixed tick-size / step-size normalization to use exchange increments instead of decimal-place rounding
- fixed legacy DB startup failure caused by missing live columns
- fixed settings schema drift by extending tests for live guard fields
- fixed live sync API to fail safely without credentials
- kept paper trading path intact to avoid removing the only fully proven execution fallback too early

## Remaining Work Before Removing Paper Trading

- WebSocket user data stream for lower-latency live order updates
- exchange-side position reconciliation across multiple symbols, not just the default symbol path
- explicit hedge-mode / one-way-mode handling
- live account balance snapshots sourced from Binance instead of only local PnL snapshots
- better retry / idempotency around write-timeout scenarios beyond `clientOrderId` recovery
- pre-trade notional / min-notional enforcement
- live fee and funding-rate accounting refinement
- operator approval audit trail in UI timeline
- richer UI for live protective order states and fill ladder details
- staged rollout path:
  - shadow mode
  - live dry-run monitoring
  - limited notional live mode

## Recommendation

Do not remove paper trading yet.

The live path is now materially implemented, but paper trading should remain until:

- real credentials are tested end-to-end
- live sync is verified against actual Binance responses
- protective order lifecycle is observed in practice
- operator workflow is validated on the settings page and audit trail
