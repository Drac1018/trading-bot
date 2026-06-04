# Codex automation state

이 문서는 Codex 자동화가 제품화 차단/검증 부족/상태 게시 문제를 다룰 때 따르는 실행 하네스다.
`AGENTS.md`를 먼저 읽은 뒤 이 문서를 읽고, 사용자가 더 좁은 범위를 지정하지 않으면 아래 계약을 우선한다.

## Current harness

- Mode: `forced_source_repair`
- Autonomy: `max_with_hard_safety`
- Runtime source of truth: Docker prod stack
- Primary goal: Docker prod runtime에서 드러나는 제품화 차단, 검증 부족, 상태 게시 불일치 중 하나를 골라 실제 source/test 수정을 완료한다.
- Non-closure rule: `docs-only`, `report-only`, `memory/state-only`, `p2_safe_improvement` 완료로 포장하지 않는다.
- State values such as `false`, `null`, `not_ready`, `NO_SAMPLE`, `BLOCKED`, `UNKNOWN`, `NOT_READY` are valid runtime evidence. They are not reasons to stop by themselves.

## Mandatory preflight

1. Read `AGENTS.md`.
2. Read this file.
3. Run `git status --short` and record pre-existing dirty files separately from this run's changes.
4. Do not print raw secret values from `.env`, `.env.production`, shell environment, Docker env, database rows, or service logs.
5. If an existing dirty file must be modified, state why it is in scope and isolate this run's diff.

## Docker prod runtime contract

Use Docker prod stack as the runtime source of truth.

```bash
docker compose -p my-trading-bot-prod --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml ps
```

Allowed Docker actions:

```bash
# Use only if the stack is down and read-only runtime proof is required.
docker compose -p my-trading-bot-prod --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml up -d

# Use only after source changes when runtime publication proof is required.
docker compose -p my-trading-bot-prod --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Read-only runtime proof must use Docker/Caddy/backend-container paths only:

- `docker compose ... ps`
- Caddy HTTPS: `https://localhost:18443`
- backend container internal HTTP: `http://127.0.0.1:8000`

Do not start or restart these runtime paths:

- Windows services: `TradingMvpBackend`, `TradingMvpFrontend`, `TradingMvpWorker`, `TradingMvpHttpsProxy`
- local `.venv` uvicorn runtime
- direct local frontend/backend operation on public `localhost:3000` or `localhost:8000`

Do not open host-public backend/frontend/postgres/redis ports. Treat Caddy HTTPS reverse proxy as the external access path.

## Forbidden changes

Never do any of the following in this harness:

- live write or Binance order/write endpoint call
- credential, API key, `.env`, `.env.production`, secret, account credential mutation
- risk_guard weakening
- execution/order path weakening
- approval, live-arm, pause, emergency, manual-control policy weakening
- hard conversion of `UNKNOWN`, `NO_SAMPLE`, `NOT_READY`, or `BLOCKED` into `READY` without real source evidence
- DB destructive migration
- dependency upgrade
- generated artifact, `egg-info`, cache, lockfile mutation unless the chosen source repair explicitly requires it and the user requested it
- changing runtime readiness by hiding blocked reasons

## Allowed change scope

Prefer the smallest safe source/test slice that can close one productization blocker.

Allowed areas:

- read-only backend endpoint publication logic
- serializer/schema/DTO/Pydantic model that publishes existing state more accurately
- service-gate state/reason/root-cause publication
- profitability, slippage readiness, sample-size, reason publication
- cost breakdown publication or normalization
- operator dashboard blocked/not_ready reason display
- frontend type/interface/copy mapping for already published backend state
- regression/unit/endpoint/render tests proving the selected publication contract
- this document's result ledger after the source/test change

## Candidate repair targets

Pick exactly one primary repair target per run unless the source contract proves that two files must change together.

Priority candidates:

1. `service-gate` blocked/root-cause publication mismatch
2. profitability/slippage readiness reason publication mismatch
3. analytics cost-breakdown vs dashboard profitability response mismatch
4. operator dashboard blocked/not_ready reason omission
5. stale Docker runtime vs current source contract mismatch
6. missing regression test for one of the above

Target selection rules:

- Prefer a backend publication mismatch over a frontend copy-only mismatch.
- Prefer a testable deterministic bug over a broad refactor.
- Prefer adding an explicit reason/root-cause field over changing readiness semantics.
- Prefer preserving blocked state and improving the reason path over changing a blocker into ready.
- If only frontend is wrong, change frontend mapping/copy only and preserve backend semantics.
- If only tests are missing and runtime/source contract is already correct, add the focused regression test and exit as `forced_source_repair_test_added`.

## Mandatory search terms

Run repository searches covering these terms before selecting the repair target. Use `rg` or equivalent and keep the relevant findings in the final report.

```text
slippage_data_status
cost-breakdown
cost_breakdown
profitability
readiness
NO_SAMPLE
UNKNOWN
NOT_READY
BLOCKED
insufficient_sample
productization_profitability_unverified
EXCHANGE_AUTH_PERMISSION_REJECTED
recent_scheduler_non_success
recent_health_errors
blocked reason
blocked_reason
not_ready
live_execution_ready
can_enter_new_position
service-gate
service_gate
dashboard/operator
```

## Runtime proof endpoints

Collect read-only proof from all endpoints below before deciding the repair target when the Docker stack is available.

```text
/health
/api/runtime/service-gate
/api/settings
/api/dashboard/operator
/api/dashboard/profitability
/api/settings/ai-usage
/api/analytics/cost-breakdown?period=today
```

Use Caddy HTTPS for operator-facing proof where possible. Backend-container internal GET is acceptable for backend-only proof. Redact secrets and tokens from all output.

## Execution algorithm

1. Run mandatory preflight.
2. Run mandatory searches.
3. Collect Docker runtime proof when available.
4. Choose one repair target from the priority list.
5. Identify source-of-truth functions and publication boundary.
6. Make the smallest allowed backend/frontend/test changes.
7. Preserve hard blockers and hard risk policy.
8. Add or update a focused regression test when practical.
9. Run focused validation.
10. If runtime publication changed, rebuild Docker prod stack with `up -d --build` and repeat the read-only proof endpoints.
11. Run `git diff --check -- <changed files from this run>`.
12. Append a result entry to this file under `Run ledger` only after source/test work is complete or explicitly partial.
13. Final output must follow the required completion format.

## Focused validation contract

Run the narrowest meaningful tests for the selected repair.

Backend examples:

```bash
python -m pytest tests/test_service_gate.py -q
python -m pytest tests/test_analytics_cost_breakdown.py -q
python -m pytest tests/test_dashboard_filters.py -q
python -m pytest tests/test_settings_and_connectivity.py -q
```

Frontend examples:

```bash
pnpm -C frontend lint
pnpm -C frontend build
```

Diff whitespace check:

```bash
git diff --check -- <this-run-changed-files>
```

If a command cannot run because the local environment lacks dependencies, Docker, or a service, report the exact command and failure reason. Do not replace failed validation with a success claim.

## Completion modes

Use exactly one mode in final output.

- `forced_source_repair_done`: source/test change completed, focused validation attempted, runtime proof collected when applicable.
- `forced_source_repair_test_added`: no source change was required; a focused regression test was added for an already-correct contract.
- `forced_source_repair_partial`: safe source/test progress was made but a required validation or runtime proof step could not complete.
- `blocked_due_to_only_forbidden_live_or_risk_path`: the only way forward would require a forbidden live/risk/execution/credential/destructive change.

Do not use `done` for docs-only, report-only, or memory-only work.

## Required final output format

```text
[모드]
[선택한 source repair 대상]
[Docker runtime proof]
[원인 파일/함수]
[수정 파일]
[검증 결과]
[남은 차단/운영 조치]
[docs/ops/codex_automation_state.md 기록 여부]
```

## Result recording rule

Append a short ledger entry only after a run has selected a real source/test target. Include:

- absolute date/time if available
- selected target
- changed files
- validation commands and results
- runtime proof summary
- remaining blockers
- completion mode

## Run ledger

No source repair run has been recorded in this file yet.
