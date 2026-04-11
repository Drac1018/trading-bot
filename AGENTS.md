# AGENTS.md

This repository is being prepared for a production-credible MVP of a multi-agent automated trading platform. Until the codebase is fully built, future Codex runs should treat the prompt in `prompts/first-task.md` as the primary build brief and this file as the operating guide.

## First task

Start by reading `prompts/first-task.md`.
That file is the canonical initial implementation prompt for the project.

## Repository navigation

The repository is currently minimal, so be ready to scaffold from scratch if needed.
Prefer the target structure described in `prompts/first-task.md`:
- `backend/`
- `frontend/`
- `workers/`
- `docs/`
- `infra/`
- `scripts/`
- `tests/`
- `prompts/`
- `schemas/`

## Core mission

Implement, do not stop at design.
Inspect the existing repo first, adapt to any stack or conventions that already exist, and scaffold missing pieces when the repo is incomplete.
Choose the simplest architecture that is robust, testable, extensible, and safe by default.

## Product scope

Build a multi-agent automated trading MVP with these five AI roles:
- Chief Review / Aggregation AI
- Integration Planner AI
- Trading Decision AI
- UI/UX AI
- Product Improvement / Planning AI

The platform must support end-to-end paper trading by default and keep a guarded path for future live trading.

## Safety principles

Deterministic policy and execution controls always override AI recommendations.
Real money movement must never depend on free-form AI output alone.
Paper trading must be ON by default.
Live trading must remain OFF by default and only become possible with:
- an explicit environment flag
- a manual approval gate
- deterministic risk-engine approval
- full audit logging

The system must stay safe even if exchange credentials are missing.
Include an emergency kill switch and trading pause capability.

## Mandatory risk rules

Implement and enforce at minimum:
- maximum leverage: `3x`
- maximum risk per trade: `1.00%` of account equity
- daily loss limit: `2.00%` of account equity
- prefer `HOLD` after `3` consecutive losses
- block entries when stop loss or take profit is missing or invalid
- block execution when market data is stale or incomplete
- block or cancel when slippage exceeds the configured threshold
- discard malformed or schema-invalid agent output
- deterministic policy wins on every conflict

## Required implementation behaviors

Do not hardcode secrets.
Provide `.env.example`.
Use explicit typed schemas and DTOs.
Validate all agent outputs strictly.
Persist agent runs, risk checks, orders, executions, PnL, scheduler runs, alerts, and audit history.
Use working mocks or adapter boundaries where external integrations are unavailable.
Avoid placeholder code that only returns success without real behavior.

## Preferred default stack

If the repository still has no clear stack, prefer:
- backend API: Python 3.12 + FastAPI
- workers: Python workers with Redis queue
- database: PostgreSQL
- cache / queue: Redis
- frontend: Next.js + TypeScript + Tailwind
- infrastructure: Docker Compose
- tests: pytest plus minimal UI smoke coverage

## Expected workflows

Implement these end-to-end flows:
- market snapshot ingestion and normalization
- feature calculation and persistence
- Trading Decision AI decision cycle
- schema validation
- deterministic risk evaluation
- paper execution pipeline
- Chief Review summary after decision cycles
- scheduled 1h / 4h / 12h / 24h review runs
- Product Improvement, UI/UX, and Integration Planner batch workflows
- audit timeline and dashboard visibility
- replay / backtesting mode through CLI or API

Do not call every agent on every tick.

## Minimum deliverables

Before calling the MVP done, ensure the project provides:
- a runnable full-stack local setup
- paper trading end-to-end
- deterministic risk validation
- schema-validated multi-agent orchestration
- audit logs and health/status visibility
- dashboard pages for overview, markets, decisions, positions, orders, risk, agents, scheduler, audit, settings, and backlog
- seed data so the UI is meaningfully populated
- tests for the risk engine and key pipeline paths
- documentation in `README.md` and `docs/`

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

## Coding conventions

Prefer modular, cohesive functions and readable naming.
Favor correctness, observability, maintainability, and explicit boundaries over cleverness.
Keep AI roles replaceable so each one can evolve independently later.
Document safe defaults and any mock boundaries clearly.

## Definition of done

A task is not done when there is only a plan or partial scaffold.
A task is done when the implementation runs locally, key tests pass, safety controls are enforced, major flows are documented, and the final handoff includes:
1. what changed
2. exact run commands
3. what is fully working
4. what is mocked or stubbed
5. remaining risks or next steps
6. exact files created or modified
