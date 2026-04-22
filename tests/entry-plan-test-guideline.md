# Entry-Plan Test Guideline

## Purpose
Reduce brittle failures where entry-plan tests drift with live `risk` or `meta_gate` changes.
Keep real policy coverage intact.

## Use Deterministic Helpers For
Use deterministic decision or risk helpers when the test is mainly about orchestration shape.

- `PendingEntryPlan` arm or existence
- watcher state transitions
- `trigger / cancel / expire / hold-cancel` flow
- result payload shape
- `cycle_id / snapshot_id / metadata_json` persistence

Example files:

- `tests/test_pending_entry_plan_watcher.py`
- `tests/test_pipeline.py`
- `tests/test_symbol_lock_idempotency.py`

Rules:

- Treat these as orchestration tests, not policy tests.
- Stub only the minimum decision or risk output needed for the flow.
- If the test expects plan arm instead of direct execution, make `execute_live_trade()` fail on call.

## Keep Real Policy For
Keep real `evaluate_risk()` and `meta_gate` calculation when the test is mainly about policy.

- `reason_codes`
- `blocked_reason_codes`
- `meta_gate pass / soft_pass / reject`
- `headroom / sizing / slippage`
- `debug_payload` source of truth

Example files:

- `tests/test_risk_engine.py`
- `tests/test_meta_gate.py`
- `tests/test_risk_guard_entry_and_headroom.py`

Rules:

- Do not replace real policy calculation with helper stubs.
- Do not hide policy regressions behind deterministic orchestration fixtures.

## What Must Not Be Stubbed
Do not stub decision or risk outputs in tests that assert:

- exact `reason_codes`, `blocked_reason_codes`, or `adjustment_reason_codes`
- `meta_gate` decision, probability, or multipliers
- `headroom`, `approved_notional`, `approved_leverage`, or `slippage`
- computed `debug_payload` fields
- dashboard or operator views that must reflect the latest real risk payload

## Checklist For New Tests
- Is this test about `plan lifecycle` or `policy calculation`?
- Does it expect a `PendingEntryPlan` row or `entry_plan` payload?
- Does it verify watcher state or orchestration shape?
- Does it verify exact policy outputs such as `reason_codes`?
- Does it verify `meta_gate`, `headroom`, or `debug_payload`?
- Is an orchestration test accidentally relying on live policy outcomes?
- Is a policy test accidentally using stubs that can hide regressions?

## Anti-Patterns
- A plan-arm test depends on a real `meta_gate reject`.
- A watcher-shape test depends on real `headroom`, `sizing`, or `slippage`.
- A metadata persistence test depends on drifting live decision output.
- A policy test is converted to helper stubs and stops catching `reason_codes` regressions.
- One test tries to prove both orchestration shape and policy math.

## Operating Rule
- Direct `pending entry plan` expectations are usually orchestration tests.
- Policy regressions should be caught in `risk_engine`, `meta_gate`, and `risk_guard` tests.
- Decide the group before writing the test.
- Do not extract shared helpers unless the duplication is clearly worth the coupling.
