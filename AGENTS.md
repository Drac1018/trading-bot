# AGENTS.md

## Project
This repository is a real-trading-oriented crypto trading bot.
The system must prioritize safety, deterministic behavior, auditability, and operational clarity over aggressiveness.

Primary goals:
- protect capital first
- keep execution deterministic
- separate AI judgment from hard risk controls
- preserve clear operator visibility in dashboard / scheduler / audit log
- prevent silent state mismatch

---

## Core principles

1. AI is advisory or decision-producing, but never the final unrestricted authority.
2. A hard risk guard must always run before any live order submission.
3. If data is stale, missing, contradictory, or low-confidence, prefer HOLD / BLOCK / NO-TRADE.
4. Position protection is more important than new entry.
5. Dashboard values, scheduler values, and audit-log values must be derived from consistent sources of truth.
6. Never fake successful execution. If uncertain, expose degraded/unknown state explicitly.
7. Live trading safety is more important than UI completeness.

---

## Architecture intent

The expected high-level flow is:

1. market/account/position data collection
2. AI or rule-based decision generation
3. hard risk validation
4. execution eligibility check
5. exchange order submission
6. order/protection sync
7. state persistence
8. dashboard / scheduler / audit-log reflection

AI must not bypass:
- exchange tradability checks
- app-level live-trading approval state
- risk limits
- protection-order requirements
- failure backoff / guard mode

---

## Non-negotiable rules

- Do not remove hard risk checks in order to increase trading frequency.
- Do not merge app-level approval and exchange-level tradability into one ambiguous field.
- Do not mark an order as successful until exchange acknowledgement is confirmed.
- Do not show optimistic UI states that are not backed by persisted state.
- Do not silently swallow exceptions in live-order paths.
- Do not introduce broad refactors unless required for correctness.
- Do not break existing API response shapes unless explicitly updating schema and docs together.

---

## Trading safety requirements

### Approval and tradability
The system must clearly separate:
- exchange raw tradability status (example: canTrade)
- app internal live-trading approval status
- risk-engine allow/block result
- temporary guard/backoff state

These must never be conflated into a single boolean without explanation.

### Risk guard
Before live execution, always validate at minimum:
- max single-position limit
- directional bias limit
- total exposure
- available margin / balance sanity
- daily loss limit
- consecutive loss limit
- manual guard mode
- failure backoff
- required protection-order constraints if applicable

### Protection orders
Protection-order handling must be consistent across:
- submit
- query
- cancel
- sync
- dashboard reflection

### Fail-safe behavior
When exchange/account sync fails:
- degrade safely
- block new live entry if correctness is uncertain
- preserve observability
- expose reason in API/UI/audit trail

---

## Code change policy

When making changes:
1. first inspect related schemas, services, routes, UI bindings, tests, and docs
2. prefer minimal targeted patches
3. keep naming explicit and operator-readable
4. preserve backward compatibility when possible
5. update tests with every behavior change
6. update docs/api.md or equivalent when response fields or meanings change

Do not patch UI text only if the underlying state logic is incorrect.
Do not patch backend logic only if the dashboard interpretation remains inconsistent.

---

## Repository areas to inspect first

Typical priority:
- backend/trading_mvp/services/
- backend/trading_mvp/schemas.py
- backend/trading_mvp/main.py
- frontend/components/
- frontend/app/
- docs/api.md
- tests/

If issue is about order execution, inspect first:
- execution service
- exchange/binance service
- risk service
- pause/guard/backoff control
- dashboard aggregation logic

If issue is about UI inconsistency, inspect both:
- backend response generation
- frontend rendering / label mapping / fallback copy

---

## Expected response style for analysis tasks

When asked to analyze before coding, respond in this structure:

1. Current behavior
2. Root cause
3. Risk / impact
4. Files to change
5. Proposed patch plan
6. Test cases
7. Open assumptions / uncertainties

Be concrete. Prefer file-level guidance over generic advice.

---

## Expected response style for implementation tasks

When asked to implement:
- summarize intended change briefly
- list files you will modify
- implement minimal coherent patch
- add/update tests
- note any migrations or manual verification steps
- report remaining edge cases honestly

---

## Testing expectations

At minimum, after code changes:
- run the most relevant unit/integration tests for modified area
- verify schemas and API docs if response meanings changed
- verify UI state mapping if dashboard text changed

Priority test themes:
- approval state separation
- risk block reasons
- guard/backoff transitions
- protection-order sync
- scheduler/dashboard/audit consistency
- stale account/position handling
- deterministic fallback behavior

---

## Auditability requirements

Any block/hold/guard/live-trading denial should be explainable through user-visible fields.
Reasons should be operator-readable, not only developer-readable.
Avoid ambiguous labels like "unavailable" if the actual state is:
- approval missing
- exchange denied
- risk blocked
- backoff active
- account sync stale

---

## Performance and scope discipline

- Prefer correctness over premature optimization.
- Avoid large rewrites unless needed to eliminate repeated inconsistency.
- If a fix is too broad, propose phased patches:
  - phase 1: correctness and safety
  - phase 2: UX cleanup
  - phase 3: refactor

---

## Done definition

A task is considered done only when:
- logic is corrected
- UI meaning matches backend meaning
- tests are updated
- docs are updated where needed
- no known silent inconsistency remains in the modified path