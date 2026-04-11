# First Task Prompt

You are Codex working inside this repository. Your task is to IMPLEMENT, not just design, a production-credible MVP of a multi-agent automated trading product.

Important execution behavior:
- Do not stop after giving a plan.
- First inspect the repository, understand the current structure, and adapt to the existing stack and conventions if they exist.
- If the repository is empty or incomplete, scaffold the project from scratch using sensible defaults.
- Make decisions autonomously when details are missing.
- Prefer the simplest architecture that is robust, testable, and extensible.
- Run commands, linters, type checks, and tests as needed.
- Fix issues until the project is runnable.
- At the end, provide a concise implementation summary, exact run commands, what remains incomplete, and any risks.

Project goal:
Build a multi-agent automated trading platform with 5 AI roles and deterministic safety controls. The system must be able to run in paper-trading mode end-to-end by default, and support optional future live trading through a guarded adapter.

High-level product concept:
1) Chief Review / Aggregation AI
   - Reviews outputs from other agents
   - Aggregates findings
   - Resolves conflicts
   - Produces final operational summary and recommended mode (hold / monitor / act)
   - Does NOT directly place orders

2) Integration Planner AI
   - Identifies where AI should be attached in the product
   - Reviews system workflows, logs, metrics, and bottlenecks
   - Suggests integration points, automation opportunities, and technical improvements
   - Works mostly in batch / review mode, not in the tight real-time execution path

3) Trading Decision AI
   - Produces structured trade decisions: hold / long / short / reduce / exit
   - Returns confidence, rationale codes, entry zone, stop loss, take profit, max hold duration, and position sizing suggestion
   - Does NOT directly execute orders
   - All output must be structured JSON and validated against schema

4) UI/UX AI
   - Improves dashboard clarity, alert copy, explanations, workflow usability, settings UX
   - Generates suggestions, annotations, and improvement tickets
   - Can propose UI copy and dashboard enhancements
   - Must not auto-deploy changes without explicit human approval

5) Product Improvement / Planning AI
   - Reviews KPIs, experiment results, user behavior, historical performance, and competitor notes
   - Generates backlog items, priorities, and improvement proposals
   - Creates planning artifacts and product recommendations
   - Must not change trading policy automatically

Critical architecture principle:
AI may analyze, recommend, summarize, and plan, but deterministic risk rules and execution controls must have final authority.
Real money movement must never be governed by free-form AI output alone.

Default safety mode:
- Paper trading ON by default
- Live trading OFF by default
- If live trading is ever enabled later, it must require:
  - explicit environment flag
  - explicit manual approval gate
  - deterministic risk engine approval
  - full audit logging
- Build the software so it is safe even when exchange credentials are absent

Core risk constraints to implement:
- Maximum leverage: 3x
- Maximum risk per trade: 1.00% of account equity
- Daily loss limit: 2.00% of account equity
- If 3 consecutive losses occur, system should prefer HOLD mode
- If stop loss or take profit is missing or invalid, new entry is blocked
- If market data is stale or incomplete, block execution
- If slippage exceeds configured threshold, block or cancel execution
- If agent output fails schema validation, discard it
- If deterministic policy conflicts with AI recommendation, policy wins
- Must include emergency kill switch / trading pause

Primary deliverable:
A runnable full-stack MVP that demonstrates:
- multi-agent orchestration
- paper trading
- deterministic risk validation
- order simulation / execution pipeline
- audit logs
- dashboard UI
- scheduled re-evaluation
- product improvement workflow

If the repo has no clear stack already, use this preferred stack:
- Backend API: Python 3.12 + FastAPI
- Worker / background jobs: Python workers with Redis queue
- Database: PostgreSQL
- Cache / task queue: Redis
- Frontend: Next.js + TypeScript + Tailwind
- Infra: Docker Compose
- Tests: pytest for backend, Playwright or equivalent minimal UI smoke test, unit tests for risk engine and agent schema validation

Repository structure to create if needed:
- backend/
- frontend/
- workers/
- docs/
- infra/
- scripts/
- tests/
- prompts/
- schemas/

Required system components:
1. Market Data Layer
   - ingest market data from a mock / simulated source
   - optionally add an exchange adapter interface for future real exchange integration
   - support historical candle replay for testing
   - normalize timestamps and symbols
   - provide clean internal market snapshots

2. Feature Layer
   - calculate baseline indicators and features
   - trend, volatility, volume, drawdown-related features
   - data quality checks
   - feature snapshot persisted for each decision cycle

3. Agent Layer
   - implement all 5 agent roles
   - all agent outputs must use strict schemas
   - store every agent input/output with timestamps
   - orchestrator decides which agents to call depending on the event:
     - real-time decision cycle: primarily Trading Decision AI
     - post-decision summary: Chief Review AI
     - scheduled product review: Product Improvement AI
     - system/integration review: Integration Planner AI
     - UX review: UI/UX AI
   - do NOT call all agents on every tick

4. Deterministic Policy / Risk Engine
   - final authority before any execution
   - validates leverage, size, stop loss, take profit, daily loss limit, consecutive loss state, stale data, slippage threshold
   - produces allow / deny with reason codes
   - must be unit tested

5. Execution Layer
   - paper execution engine required
   - support pending/open/filled/cancelled/rejected states
   - record simulated fills and realized/unrealized PnL
   - use deterministic logic for paper fills
   - add adapter interface so live trading can be added later
   - live adapter can remain disabled or stubbed if credentials are unavailable

6. Scheduler
   - configurable review cycles for 1h, 4h, 12h, 24h
   - each cycle can trigger specific agent workflows
   - store outcomes and next actions

7. Audit / Observability
   - persistent audit log for:
     - agent outputs
     - risk checks
     - execution attempts
     - state transitions
     - alerts
   - structured logs
   - health/status endpoint
   - event timeline view in UI

8. Frontend / Dashboard
   Build a usable internal admin dashboard with at least these pages:
   - Overview
   - Market / signals snapshot
   - Decisions
   - Positions
   - Orders / executions
   - Risk status
   - Agents
   - Scheduler
   - Audit log
   - Settings
   - Product improvement backlog

UI expectations:
- clean, modern, operator-friendly layout
- clear visual distinction between recommendation vs approved execution
- show why system is in HOLD if blocked
- show per-agent outputs in human-readable form
- allow pause / resume trading
- show paper/live mode clearly
- settings for schedule windows: 1h, 4h, 12h, 24h
- human-facing copy should be concise and understandable
- use sensible seed data so the dashboard is not empty

Minimum schemas to implement:
- TradeDecision
- ChiefReviewSummary
- IntegrationSuggestion
- UXSuggestion
- ProductBacklogItem
- RiskCheckResult
- ExecutionIntent
- AgentRunRecord
- SchedulerRunRecord
Use strict validation and reject malformed payloads.

Suggested TradeDecision schema fields:
- decision: hold | long | short | reduce | exit
- confidence: float 0..1
- symbol
- timeframe
- entry_zone_min
- entry_zone_max
- stop_loss
- take_profit
- max_holding_minutes
- risk_pct
- leverage
- rationale_codes: string[]
- explanation_short
- explanation_detailed

Required database entities:
- users (if needed for local admin)
- settings
- market_snapshots
- feature_snapshots
- agent_runs
- risk_checks
- positions
- orders
- executions
- pnl_snapshots
- alerts
- scheduler_runs
- product_backlog
- competitor_notes
- ui_feedback
- system_health_events

Implementation rules:
- Do not hardcode secrets
- Provide .env.example
- Provide clear config loading
- Support local development with Docker Compose
- Seed the DB with sample symbols and mock market data
- Include mock competitor notes so Product Improvement AI has something to process
- Include sample UI feedback data so UI/UX AI has something to review
- Include sample audit history so timeline pages render meaningfully
- Prefer typed interfaces and explicit DTOs
- Use migrations if using an ORM
- Keep functions cohesive and modular
- Avoid fake implementations that only return success placeholders
- If something cannot be fully integrated, implement a working mock or adapter boundary and document it clearly

Agent implementation expectations:
Chief Review / Aggregation AI:
- input: recent trade decisions, risk results, system health, alerts
- output: summary, recommended operating mode, must-do actions, priority
- never directly execute orders

Integration Planner AI:
- input: logs, metrics summaries, error summaries, architecture state
- output: suggested integration points, automation opportunities, tech debt items, priority
- batch/scheduled use only

Trading Decision AI:
- input: market snapshot, features, open positions, risk context
- output: structured TradeDecision
- must be deterministic enough to test around schema + pipeline behavior
- if external model integration is not available locally, implement model client boundaries and a mock provider so the system still runs

UI/UX AI:
- input: UI feedback, usage events, current dashboard structure
- output: UXSuggestion items and improved copy blocks
- do not auto-apply high-risk changes silently

Product Improvement AI:
- input: KPI summaries, performance metrics, competitor notes, prior backlog
- output: backlog recommendations with severity, effort, impact, and rationale

Execution flow to build:
- collect market snapshot
- compute features
- call Trading Decision AI
- validate schema
- run deterministic risk engine
- if denied: log and surface HOLD / BLOCKED state
- if approved in paper mode: send to paper execution engine
- store position/order/execution updates
- call Chief Review AI after decision cycle
- update dashboard and audit logs
- allow scheduled batch jobs for UI/UX AI and Product Improvement AI

Backtesting / replay requirement:
- implement a simple historical replay mode using CSV or seeded data
- allow running a simulation cycle from CLI or API
- persist resulting decisions and paper executions
- enough to validate the end-to-end pipeline

Required commands / scripts:
- local setup
- database migrate
- seed sample data
- run backend
- run frontend
- run workers
- run full stack
- run tests
- run replay simulation
- lint / type check

Documentation to produce:
- README.md with exact setup and run steps
- docs/architecture.md
- docs/agent-design.md
- docs/risk-policy.md
- docs/execution-flow.md
- docs/api.md
- AGENTS.md for future Codex runs explaining:
  - how to navigate the repo
  - which commands to run
  - coding conventions
  - test commands
  - safety principles
  - what “done” means in this project

Acceptance criteria:
- `docker compose up --build` or equivalent starts the MVP locally
- seed data creates a usable dashboard
- an end-to-end paper-trading cycle can run successfully
- the risk engine blocks invalid decisions
- all agent outputs are schema-validated and logged
- scheduler supports 1h / 4h / 12h / 24h review cycles
- dashboard clearly shows mode, positions, decisions, and audit trail
- tests cover core risk rules and key pipeline paths
- docs are sufficient for another engineer to continue development

Strong preference on implementation style:
- Build a real MVP, not a slideware prototype
- Favor correctness, observability, and maintainability over unnecessary complexity
- Keep the system modular so each AI role can later be replaced or improved independently
- Use clean naming and readable code
- Do not ask unnecessary questions if you can infer a reasonable path
- When uncertain, choose a safe default and document it

Final output expected from you after implementation:
1. What you changed
2. How to run the project
3. What is fully working now
4. What is mocked or stubbed
5. Remaining risks / next steps
6. Exact files created or modified
