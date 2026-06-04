# Runtime optimization follow-up - 2026-05-16

## Scope

- Do not change `risk.py` final approval policy.
- Do not change `execution.py` order submission or protection-order logic.
- Keep runtime-state and pending-plan changes additive or read-only unless a separate data-cleanup window is approved.

## FOR UPDATE lock wait

Current finding:

- The observed wait query was `SELECT settings.pause_reason_detail ... FOR UPDATE`.
- The high-frequency runtime writers were already reduced in `fix: avoid extra runtime-state write locks`.
- The remaining lock-taking paths are safety-sensitive:
  - execution guard updates
  - pause/protection/auto-resume state updates
- Because the live `8000` backend was still the old pre-restart process when the wait was observed, the first action is to restart onto the patched service runtime and re-measure.

Decision:

- Do not remove the remaining safety locks in this pass.
- Extend `/api/runtime/service-gate` diagnostics so a future `FOR UPDATE` wait includes `blocking_pids` and `blocking_sessions`.
- If waits remain after the official service restart, patch the exact blocker path shown by the diagnostic payload.

Acceptance check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/runtime/service-gate |
  ConvertTo-Json -Depth 8
```

The service-on gate should report `for_update_lock_waits=[]`. If not, inspect `blocking_sessions[*].query` before changing lock policy.

## Stale triggered pending plans

Current rows:

- `#223 BTCUSDT long`, `triggered`, expired, execution status `partially_filled`
- `#262 BTCUSDT long`, `triggered`, expired, execution status missing
- `#271 BTCUSDT short`, `triggered`, expired, execution status missing

Decision:

- Keep these rows as historical/audit evidence.
- Do not rewrite `plan_status` to `expired` or `canceled` in this pass.
- Treat them as `triggered_stale_history` in service-gate reporting and exclude them from service-on blocking.

Reason:

- A `triggered` row records that the watcher reached trigger flow. Overwriting it would blur the execution audit trail.
- The service switch only needs to know whether a plan is still actionable. Expired triggered history is not actionable.
- If later data hygiene is needed, prefer adding metadata such as `service_gate_state=triggered_stale_history` over mutating the primary status.

## Short-side drag analysis

Live report command:

```powershell
.\.venv\Scripts\python.exe scripts\strategy_performance_report.py --short-drag --days 7 --limit 20 --mode live
```

Snapshot at `2026-05-16T12:12:21`:

- 7d live short trades: `2`
- Win rate: `0.0%`
- Gross PnL: `-5.1830 USDT`
- Fees: `1.16632789 USDT`
- Net PnL: `-6.34932789 USDT`
- Average slippage: `5.4951 bps`
- Strategy concentration: `trend_pullback_engine`
- Regime concentration: `transition`

Largest loss rows:

- `position_id=25 BTCUSDT short`
  - strategy `trend_pullback_engine`
  - regime `transition`
  - net `-4.2893084 USDT`
  - hold `7.91 min`
  - slippage `11.5738 bps`
- `position_id=26 ETHUSDT short`
  - strategy `trend_pullback_engine`
  - regime `transition`
  - net `-2.06001949 USDT`
  - hold `9.58 min`
  - slippage `-0.5835 bps`

Interpretation:

- The current 7d short drag is not broad across all shorts. It is concentrated in `transition` regime pullback shorts.
- BTC short had meaningful entry/exit friction, so execution quality amplified the loss.
- ETH short was more signal/regime failure than slippage failure.

Recommended strategy changes for a later trading-policy patch:

1. Reduce or disable `trend_pullback_engine` shorts while regime is `transition`.
2. Require stronger higher-timeframe bearish confirmation before transition-regime shorts.
3. After one transition short loss, temporarily cut short-side size or leverage for that symbol/regime bucket.
4. Tighten BTC transition-short slippage/chase tolerance before entry.
5. Fill missing `confirmation_type` and `risk_mode` metadata so future attribution is not `unknown`.

These are analysis recommendations only. They intentionally do not alter live risk approval or order submission behavior.

## Commit split state

Current local branch is already split into feature-sized commits on top of `origin/main`:

- `3a0d3f6f chore: standardize local runtime on 8000 and 3000`
- `5f368244 feat: add runtime service switch gate`
- `d033120f fix: gate backend background loops on service runtime`
- `a4e0a1f0 feat: add short-side drag analytics`
- `3e20231c fix: serialize market refresh scheduler workflow`
- `ae5bfff6 test: isolate Windows pytest temp directories`
- `2208fb76 fix: avoid extra runtime-state write locks`
- `89d79e87 feat: throttle market settings advisor calls`
- `4d52fb3d fix: expire stale-sync pending entry plans`
- `2c37d024 feat: compact dashboard decision and risk views`

No squash is recommended before service restart validation. Keep rollback units small.
