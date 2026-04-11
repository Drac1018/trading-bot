# AGENTS.md

This repository is being prepared for a production-credible MVP of a multi-agent automated trading platform.
Until the codebase is fully built, future Codex runs should treat the prompt in `prompts/first-task.md` as the primary build brief and this file as the operating guide.

However, the current implementation priority is more specific than the long-term platform vision:
the repository should currently be built and refactored as a live-capable, production-oriented Binance Futures scalping system, while preserving a modular architecture that can later expand into the broader multi-agent platform vision.

## First task

Start by reading `prompts/first-task.md`.
That file is the canonical initial implementation prompt for the project.

Then inspect the repository as it currently exists and adapt the implementation plan to the real codebase, not just the ideal target design.

## Current build priority

The long-term vision remains a multi-agent automated trading platform.

But for current Codex runs, the highest priority is:

1. inspect the entire repository
2. understand the current trading loop and system boundaries
3. identify dangerous couplings and missing safety controls
4. refactor toward a safe live-trading architecture
5. separate AI decision-making, risk validation, execution, position management, configuration, and logging
6. preserve or explain legacy behavior with comments
7. prioritize a working live-capable trading core before expanding secondary platform features

Do not over-prioritize frontend breadth, agent orchestration complexity, or non-critical product workflows before the live-trading core is safe and understandable.

## Repository navigation

The repository may still be minimal or incomplete, so be ready to scaffold from scratch if needed.
Prefer a modular structure, but adapt to the actual codebase when it already exists.

Long-term platform-oriented structure may include:
- `backend/`
- `frontend/`
- `workers/`
- `docs/`
- `infra/`
- `scripts/`
- `tests/`
- `prompts/`
- `schemas/`

For the current trading-core phase, it is acceptable to organize around clear functional boundaries such as:
- exchange connectivity
- market data ingestion
- AI decision pipeline
- deterministic risk engine
- order execution
- position / portfolio management
- controls / emergency handling
- configuration
- state persistence
- audit logging

## Core mission

Implement, do not stop at design.
Inspect the existing repo first, adapt to any stack or conventions that already exist, and scaffold missing pieces when the repo is incomplete.
Choose the simplest architecture that is robust, testable, extensible, and safe by default.

The current operational objective is a real-trading-oriented Binance Futures scalping bot with strong system safety controls.
The long-term product objective is still a multi-agent automated trading platform.

## Product scope

Build toward a multi-agent automated trading platform MVP with these five AI roles:
- Chief Review / Aggregation AI
- Integration Planner AI
- Trading Decision AI
- UI/UX AI
- Product Improvement / Planning AI

However, current implementation priority is centered on the live trading path, especially:
- market snapshot ingestion and normalization
- feature calculation and persistence
- Trading Decision AI cycle
- strict schema validation
- deterministic risk evaluation
- safe order execution pipeline
- position tracking and management
- audit logging
- operational controls for emergency and manual intervention

Additional agent roles should remain modular and replaceable, but should not distract from the correctness and survivability of the live trading core.

## Live trading operating profile

Current operating assumptions for the trading core:

- exchange: Binance Futures
- runtime: 24/7 automated trading
- mode: real-trading-oriented system
- primary decision timeframe: 15m
- multi-symbol trading: allowed
- multi-timeframe analysis: preferred whenever data is available
- multiple simultaneous positions: allowed
- add-to-position: allowed if validated safely
- trading style: short-term / scalping oriented
- project preference: profit-oriented (70) over stability (30), while still enforcing hard safety controls
- external risk mode: must support ON/OFF behavior
- force-stop and manual liquidation controls: required

Paper trading remains a valid development and verification mode, but the system being designed is not only a paper-trading sandbox.
It must be structured so that live trading is a deliberate, auditable, guarded capability.

## Safety principles

Deterministic policy and execution controls always override AI recommendations.
Real money movement must never depend on free-form AI output alone.
Live trading must always remain guarded by deterministic validation, configuration controls, and auditability.
The system must stay safe even if exchange credentials are missing or partially unavailable.
Include an emergency kill switch and trading pause capability.
Include degraded-mode behavior that preserves minimum position-management capability while restricting new entries.

## Mandatory risk rules

Implement and enforce at minimum:

### Leverage policy
- BTC: maximum `5x`
- major alts: maximum `3x`
- other alts: maximum `2x`

Symbol-class mapping must be centrally managed through configuration or policy modules, not scattered hardcoded checks.

### Account risk policy
- maximum risk per trade: `2.00%` of account equity
- daily loss limit: `5.00%` of account equity
- prefer conservative restrictions after `3` consecutive losses
- new entries must be blocked when required account, position, or market state cannot be computed reliably
- block or cancel when slippage exceeds configured or context-adjusted thresholds
- discard malformed or schema-invalid agent output
- deterministic policy wins on every conflict

### Exposure awareness
Even if there is no hard cap on total exposure at this stage, the system must still compute and track:
- total margin usage
- symbol correlation awareness
- directional bias concentration
- combined portfolio / position risk

These calculations must exist even when no fixed exposure ceiling is currently enforced.

## AI-configurable trading behavior

The following may be AI-determined, subject to hard safety controls:
- entry and exit decisions
- long / short direction
- hold vs close decisions
- add-to-position decisions
- same-symbol repeat entry decisions
- stop loss usage
- take profit usage
- holding duration
- execution style preference
- response to external risk mode

Stop loss and take profit are AI-configurable in this project.
However, AI-configurable does not mean safety-free.
The system must still enforce hard loss containment, emergency protection logic, execution validation, and deterministic blocking conditions.

## AI Decision and Hard Risk Guard Separation

The AI is responsible for trade intent, not unconditional execution.

This project allows the Trading Decision AI / ChatGPT API path to make final trading decisions such as:
- whether to enter or exit
- long or short direction
- whether to add to a position
- whether to hold, reduce, or close
- preferred execution style
- reaction to external risk mode

However, every AI decision must pass through a hard risk validation layer before any live order is sent.

The `risk_guard` module is the final execution gate.
It does not generate strategy ideas.
It only checks whether the AI decision is safe and executable under current account, position, market, and system conditions.

Even if the AI wants to trade, `risk_guard` must reject or modify execution when any hard constraint is violated.

### `risk_guard` responsibilities
- enforce leverage limits by symbol class
- enforce max loss per trade
- enforce max daily loss
- enforce degraded-mode restrictions
- block new entries during emergency/force-stop state
- block new entries when account or market data is missing or inconsistent
- validate quantity calculation, precision, and exchange rules
- check total exposure, directional bias, correlation-aware portfolio risk, and combined position risk
- reject execution when slippage exceeds allowed conditions
- preserve minimum position-management behavior during failures

### Execution rule
AI decides trade intent.
`risk_guard` decides whether execution is allowed.
`order_executor` only sends the order after `risk_guard` approval.

Required flow:
1. collect market/account/position data
2. build AI input
3. request AI decision
4. parse AI response
5. validate with `risk_guard`
6. execute only if approved
7. log both approval and rejection reasons

### Non-negotiable principle
AI may be aggressive.
The system must remain survivable.

Therefore, AI must never bypass hard safety controls.
If AI output conflicts with system safety rules, system safety rules always win.

## Required implementation behaviors

Do not hardcode secrets.
Provide `.env.example`.
Use explicit typed schemas and DTOs.
Validate all agent outputs strictly.
Persist agent runs, AI decisions, risk checks, orders, executions, PnL, scheduler runs, alerts, and audit history.
Use working mocks or adapter boundaries where external integrations are unavailable.
Avoid placeholder code that only returns success without real behavior.

Where the repository already contains legacy logic, preserve intent where reasonable and annotate non-obvious or risky behavior with comments.

## Preferred default stack

If the repository still has no clear stack, prefer:
- backend API: Python 3.12 + FastAPI
- workers: Python workers with Redis queue
- database: PostgreSQL
- cache / queue: Redis
- frontend: Next.js + TypeScript + Tailwind
- infrastructure: Docker Compose
- tests: pytest plus minimal UI smoke coverage

If the existing repository already uses another practical Python-centered stack, adapt pragmatically instead of forcing a rewrite only for stack preference.

## Expected workflows

Implement these end-to-end flows first:
- market snapshot ingestion and normalization
- feature calculation and persistence
- Trading Decision AI decision cycle
- schema validation
- deterministic risk evaluation
- guarded execution pipeline
- position and PnL tracking
- emergency stop / trading pause flow
- manual close flow
- external risk mode ON/OFF handling
- audit timeline and operational visibility
- replay / backtesting mode through CLI or API where feasible

Platform-expansion workflows may follow later, including:
- Chief Review summary after decision cycles
- scheduled 1h / 4h / 12h / 24h review runs
- Product Improvement, UI/UX, and Integration Planner batch workflows

Do not call every agent on every tick.
Do not overuse expensive model calls where a deterministic pre-filter or event-driven trigger is sufficient.

## Minimum deliverables

Before calling the MVP done, ensure the project provides:
- a runnable local setup
- a live-capable but guarded trading architecture
- deterministic risk validation
- schema-validated AI decision handling
- audit logs and health/status visibility
- position/order/risk/decsion visibility sufficient for safe operation
- seed or mock data where needed for local verification
- tests for the risk engine and key pipeline paths
- documentation in `README.md` and `docs/`

A broad dashboard and multi-agent management UI are valuable, but they are secondary to correctness and safety of the live trading core.

## Commands and verification

Run the relevant commands instead of stopping after a plan:
- install / setup
- migrations
- seed
- backend
- frontend
- workers
- full stack
- tests
- replay simulation
- lint / type check

Fix issues until the project is runnable.

For trading-core changes, prioritize verification of:
- risk engine logic
- order validation
- execution path safety
- AI schema validation
- emergency controls
- degraded-mode behavior

## Coding conventions

Prefer modular, cohesive functions and readable naming.
Favor correctness, observability, maintainability, and explicit boundaries over cleverness.
Keep AI roles replaceable so each one can evolve independently later.
Document safe defaults and any mock boundaries clearly.

Prefer clear separation between:
- AI decision logic
- deterministic risk logic
- exchange execution logic
- position / portfolio state
- orchestration / scheduling
- configuration
- persistence
- audit / logging

## Definition of done

A task is not done when there is only a plan or partial scaffold.
A task is done when the implementation runs locally, key tests pass, safety controls are enforced, major flows are documented, and the final handoff includes:
1. what changed
2. exact run commands
3. what is fully working
4. what is mocked or stubbed
5. remaining risks or next steps
6. exact files created or modified

For any live-trading-related change, "done" also requires that execution safety, risk validation, and traceability were preserved or improved.