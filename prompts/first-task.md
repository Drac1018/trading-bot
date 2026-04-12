# First Task Prompt

You are Codex working inside this repository.

Your task is to **IMPLEMENT**, not just design.

This repository is intended to grow into a multi-agent automated trading platform, but the current implementation priority is more concrete:

**Inspect, stabilize, and refactor the repository toward a live-trading-safe Binance Futures short-term trading system, while preserving a modular architecture that can later expand into a broader multi-agent platform.**

---

## Important execution behavior

- Do not stop after giving a plan.
- First inspect the repository and understand its current structure.
- Adapt to the existing stack and conventions if they already exist.
- Do not force a full rewrite just because a different ideal architecture is possible.
- If parts are empty or missing, scaffold only what is necessary.
- Make reasonable decisions autonomously when details are missing.
- Prefer the simplest architecture that is robust, testable, extensible, observable, and safe by default.
- Run commands, tests, lint, type checks, and verification steps when needed.
- Fix issues until the project is runnable.
- At the end, provide:
  1. what changed
  2. exact run commands
  3. what is fully working
  4. what is mocked or stubbed
  5. remaining risks / next steps
  6. exact files created or modified

---

## Current goal

The immediate goal is **not** to build a generic demo platform first.

The immediate goal is to make the repository safer and more operationally credible as a:

**Binance Futures live-trading-oriented short-term / scalping system**

while keeping modular boundaries that support a future multi-agent platform.

---

## Current implementation priority

Prioritize the following work in order:

1. inspect the full repository
2. map the current trading loop and boundaries
3. identify dangerous coupling and missing safety controls
4. refactor toward a live-trading-safe structure
5. separate:
   - AI judgment
   - deterministic risk validation
   - order execution
   - position / account management
   - settings / control
   - audit / observability
6. preserve or explain legacy behavior where needed
7. stabilize the real trading core before expanding non-core product surfaces

Do not prioritize frontend expansion, broad multi-agent orchestration, or non-essential batch workflows over the trading core.

---

## Long-term product scope

The long-term platform can still include these 5 AI roles:
- Chief Review / Aggregation AI
- Integration Planner AI
- Trading Decision AI
- UI/UX AI
- Product Improvement / Planning AI

But the current implementation focus must remain on:

- market snapshot ingestion and normalization
- feature calculation and persistence
- Trading Decision AI cycle
- strict schema validation
- deterministic risk evaluation
- safe execution pipeline
- position tracking and management
- audit logging
- emergency control and manual intervention capability

The other agent roles should remain modular and replaceable, but they must not compromise live trading core safety or clarity.

---

## Live trading operating profile

Assume the current live trading core is designed for:

- Exchange: Binance Futures
- Runtime: 24/7 automated operation
- Mode: live-trading-oriented
- Primary decision timeframe: 15m
- Multi-symbol trading: allowed
- Multi-timeframe analysis: recommended when available
- Multiple simultaneous positions: allowed
- Add-on entries / scale-ins: allowed only if safely validated
- Trading style: short-term / scalping oriented
- Preference: 70 profit / 30 safety, while preserving hard safety controls
- External risk mode: must support ON/OFF
- Forced stop and manual liquidation: mandatory

Paper trading, replay, and simulation are still useful for development and verification.
However, this project must not be treated as a paper-only sandbox.

Live trading must be an explicit, auditable, controlled capability.

---

## Critical architecture principle

AI may analyze and decide trade intent, but deterministic risk rules and execution controls must have final authority.

Real money movement must never be governed by free-form AI output alone.

This project must enforce a strict separation between:
- Trading Decision AI
- `risk_guard`
- `order_executor`

AI determines intent.
`risk_guard` determines whether execution is actually allowed.
`order_executor` sends orders only after approval.

---

## Safety principles

The system must remain safe even when:
- exchange credentials are missing
- market data is partial
- exchange state is inconsistent
- connectivity is degraded
- one subsystem fails

Mandatory controls:
- emergency kill switch
- global trading pause
- degraded mode support
- manual liquidation path
- auditable control changes
- logged approval and rejection reasons

In failure scenarios, the system must restrict new entries while preserving at least minimal position-management functionality.

---

## Mandatory risk rules

### Leverage policy

Leverage policy must be centrally managed through configuration/policy modules, not scattered hardcoded checks.

At minimum enforce:

- BTC: maximum `5x`
- major alts: maximum `3x`
- general alts: maximum `2x`

Create a centralized symbol-group policy mechanism if it does not already exist.

### Account risk policy

At minimum enforce:

- max loss per trade: `2.00%` of account equity
- daily loss limit: `5.00%` of account equity
- after `3` consecutive losses, apply more conservative restrictions
- block new entries if account state, market state, or position state cannot be computed reliably
- block or cancel execution if slippage exceeds configured or context-aware tolerance
- discard malformed or schema-invalid AI output
- deterministic policy always wins on conflict

### Exposure awareness

Even if there is no hard total exposure cap yet, the system must still calculate and track:

- total margin usage
- inter-symbol correlation risk
- directional concentration
- portfolio / position aggregate risk

No hard cap is acceptable for now.
No missing calculation is acceptable.

---

## `risk_guard` responsibilities

The `risk_guard` layer is the final execution gate.

It must validate, at minimum:

- leverage limits by symbol group
- max loss per trade
- daily loss limit
- degraded mode restrictions
- entry blocking during pause / emergency states
- entry blocking when account or market data is missing or inconsistent
- quantity sizing and exchange precision rules
- exchange-rule compatibility
- total exposure awareness
- directional bias awareness
- inter-symbol correlation awareness
- aggregate portfolio / position risk
- slippage thresholds
- failure-mode minimum position-management behavior

If AI output conflicts with hard risk rules, hard risk rules must win every time.

---

## Execution flow to build or refactor

Required flow:

1. collect market / account / position data
2. build AI input
3. request Trading Decision AI
4. parse and schema-validate AI response
5. run deterministic `risk_guard`
6. execute only if approved
7. persist approval or rejection results
8. persist position / order / fill / PnL updates
9. log the full decision and execution trail

This must be the highest-priority end-to-end path.

---

## AI decision authority

The Trading Decision AI / ChatGPT path may determine:

- whether to enter
- whether to exit
- long / short direction
- whether to hold
- whether to reduce
- whether to add to a position
- whether to re-enter the same symbol
- whether to use stop loss
- whether to use take profit
- max holding duration
- order style preference
- external risk mode handling

However, AI must never bypass deterministic validation or execution controls.

---

## System components to inspect and refactor

### 1. Market data layer
- collect and normalize market snapshots
- support Binance Futures market data if already present
- support replay / verification mode where practical
- normalize timestamps and symbols
- provide clean internal market snapshots

### 2. Feature layer
- calculate baseline indicators and features
- support short-term decision use cases
- persist feature snapshots per decision cycle
- include data-quality checks

### 3. Agent layer
- Trading Decision AI is the priority path
- Chief Review / Integration Planner / UI/UX / Product Improvement remain modular
- all agent outputs must use strict schemas
- store every agent input/output with timestamps
- do not call every agent on every tick

### 4. Deterministic policy / `risk_guard`
- final authority before execution
- validates leverage, size, stop loss, take profit, daily loss, consecutive losses, stale data, exposure awareness, and slippage
- must be unit tested
- must clearly explain rejection reason codes

### 5. Execution layer
- protected Binance Futures execution path
- support order states such as pending / filled / canceled / rejected / expired
- record fills, fees, realized/unrealized PnL
- support protective orders
- support position synchronization
- support manual liquidation capability
- preserve auditable live control boundaries

### 6. Control layer
- global trading pause
- emergency stop
- manual live approval gating
- degraded mode
- external risk mode ON/OFF
- manual liquidation
- audit trail for all control actions

### 7. Audit / observability
- persistent audit log for:
  - agent outputs
  - risk checks
  - execution attempts
  - state transitions
  - alerts
  - control changes
- structured logs
- health/status endpoint
- operational timeline visibility

### 8. Frontend / dashboard
Prioritize operational visibility over broad product polish.

Important surfaces include:
- overview
- market / signal snapshot
- decisions
- positions
- orders / executions
- risk status
- agents
- scheduler
- audit log
- settings / controls

The UI must clearly distinguish:
- AI recommendation
- risk approval / rejection
- actual execution status
- live / paused / degraded state

---

## Required schemas

At minimum, the repository should have explicit strict schemas around:

- TradeDecision
- RiskCheckResult
- ExecutionIntent
- AgentRunRecord
- SchedulerRunRecord

Additional multi-agent schemas can remain in place, but the trading-core schemas must stay correct, strict, and auditable.

Malformed payloads must be rejected.

---

## Implementation rules

- do not hardcode secrets
- provide `.env.example`
- provide clear config loading
- prefer typed interfaces and explicit DTOs
- do not replace working code with fake placeholders
- if something cannot be fully integrated, create a working boundary or mock and document it clearly
- preserve existing working live-trading safety controls where possible
- improve traceability, not just surface features

---

## Testing and verification expectations

You must verify the live trading core, especially:

- risk engine logic
- leverage policy by symbol group
- order sizing and exchange validation
- execution path safety
- AI schema validation
- pause / emergency behavior
- degraded mode behavior
- replay / historical non-live behavior
- multi-symbol isolation behavior

Paper/replay tests are good.
But they must serve validation of the live-safe core, not replace it conceptually.

---

## Documentation to produce or update

Prefer documenting the current real architecture instead of an idealized one.

At minimum update or provide:

- `README.md`
- `docs/architecture.md`
- `docs/agent-design.md`
- `docs/risk-policy.md`
- `docs/execution-flow.md`
- `docs/api.md`
- `AGENTS.md`

Documentation must explain:
- how the current trading core works
- where AI judgment ends
- where deterministic control begins
- how live execution is guarded
- how pause / emergency / degraded behaviors work

---

## Acceptance criteria for the current phase

The current phase should not be considered done unless the repository provides:

- a runnable local environment
- a live-trading-capable but safety-controlled structure
- deterministic risk validation
- schema-validated AI decision handling
- auditable decision / execution / control trails
- visibility into positions, orders, executions, risk, and settings
- emergency pause / control flows
- tests for the risk engine and key pipeline paths
- documentation sufficient for another engineer to continue safely

Large UI scope or broader multi-agent polish does not outrank correctness and safety of the trading core.

---

## Strong implementation preference

Build a real, operationally credible MVP.

Favor:
- correctness
- safety
- observability
- maintainability
- explicit boundaries

Avoid:
- vague architectural fluff
- fake success placeholders
- uncontrolled AI execution authority
- broad non-core expansion before the live trading path is safe and understandable