# Productization Security Scan - 2026-05-25

Automation: productization B (`b`)
Run time: 2026-05-25T23:38:49+09:00
Mode: read-only productization security scan artifact
Branch: `codex/runtime-api-call-cleanup`
Commit: `f7685ed6b058a3185f1fa9d67001f0b405b205fd`

## Safety Boundary

- No live exchange calls, order creation, live-arm/disarm, pause/resume, DB destructive command, migration, service restart, or `.env` edit was performed.
- `.env` was used only for redacted key-presence and runtime-target summary. Secret values, API keys, JWT/session material, and DB credentials were not printed.
- The scan focused on productization security boundaries that affect operator access and runtime publication evidence. It did not relax any live-trading gate.

## Runtime Proof

- Redacted runtime target: PostgreSQL via `postgresql+psycopg`, host `127.0.0.1`, database `trading_mvp`; credentials not printed.
- `/health`: HTTP 200, `status=ok`, `mode=service_ready`, `database=ready`.
- `/api/runtime/service-gate`: `gate_clear=true`, blockers empty, runtime counts show no open positions, active orders, active pending entry plans, armed pending entry plans, unresolved submissions, recent scheduler failures, or recent health errors.
- `/api/settings`: `rollout_mode=full_live`, `live_trading_enabled=true`, `exchange_submit_allowed=true`, `trading_paused=false`, `live_execution_armed=false`, `live_execution_ready=false`, `approval_armed=null`, `can_enter_new_position=false`, `guard_mode_reason_code=LIVE_APPROVAL_REQUIRED`, `blocked_reasons=[HOLD_DECISION]`.
- `/api/dashboard/operator?view=market`: `operating_state=TRADABLE`, `can_enter_new_position=false`, `live_execution_ready=false`, `approval_armed=false`, `trading_paused=false`, `guard_mode_reason_code=LIVE_APPROVAL_REQUIRED`.
- `/api/dashboard/profitability`: `limited_live_readiness.status=not_ready`, reason codes `[insufficient_sample,productization_profitability_unverified]`.
- `/api/settings/ai-usage`: today's and 24h AI calls are `0`; 7d AI calls are `511`; projected observed monthly cost is about `11.68121314` USD.
- `/api/analytics/cost-breakdown?period=today`: live runtime still reports `data_quality.slippage_data_status=UNKNOWN`, warning `slippage_data_status:UNKNOWN`; source-level `NO_SAMPLE` behavior still needs controlled backend publication.

## Current Runtime Supersession - 2026-05-26T00:06:06+09:00

The runtime proof above is retained as historical scan evidence. Current productization decisions should use the refreshed read-only proof below until the next controlled runtime refresh is recorded.

- Redacted runtime target: PostgreSQL via `postgresql+psycopg`, host `127.0.0.1`, database `trading_mvp`; credentials not printed.
- `/health`: HTTP 200, `status=ok`, `mode=service_ready`, `database=ready`.
- `/api/runtime/service-gate`: `gate_clear=false`, blockers `[recent_scheduler_non_success,recent_health_errors]`; counts show no open positions, active orders, active pending entry plans, armed pending entry plans, unresolved submissions, FOR UPDATE waits, or Redis cache blocker. Current counts include `recent_scheduler_non_success=17` and `recent_health_errors=20`.
- `/api/settings`: HTTP 200, `rollout_mode=full_live`, `live_trading_enabled=true`, `exchange_submit_allowed=true`, `trading_paused=true`, `live_execution_armed=false`, `live_execution_ready=false`, `approval_armed=null`, `can_enter_new_position=false`, `guard_mode_reason_code=EXCHANGE_AUTH_PERMISSION_REJECTED`, `blocked_reasons=[HOLD_DECISION]`.
- `/api/dashboard/operator?view=market`: HTTP 200, `generated_at=2026-05-25T15:04:10.901963`, `operating_state=PAUSED`, `can_enter_new_position=false`, `live_execution_ready=false`, `approval_armed=false`, `trading_paused=true`, `guard_mode_reason_code=EXCHANGE_AUTH_PERMISSION_REJECTED`.
- `/api/dashboard/profitability`: HTTP 200, `limited_live_readiness.status=not_ready`, reason codes `[insufficient_sample,productization_profitability_unverified]`.
- `/api/settings/ai-usage`: HTTP 200, `recent_ai_calls_today_kst=0`, `recent_ai_calls_24h=0`, `recent_ai_calls_7d=511`, `observed_monthly_ai_cost_projection_usd=11.68121314`.
- `/api/analytics/cost-breakdown?period=today`: HTTP 200, live runtime still reports `data_quality.slippage_data_status=UNKNOWN`, warning `slippage_data_status:UNKNOWN`; this remains runtime-publication pending for the prior source fix.
- Productization implication: no code-fixable P0/P1 security finding is introduced by this refresh, but productization remains blocked by the exchange-auth scheduler/health service-gate blocker, profitability/slippage evidence, and cost-breakdown runtime-publication evidence.

## Threat Model Summary

Primary assets:

- Binance credentials, exchange permission truth, order/execution path, protective order state, runtime DB, operator API keys, operator UI session cookies, CSRF secret, OpenAI/event-source API keys, audit log integrity, and productization readiness evidence.

Trust boundaries:

- Public or operator network to Next operator UI.
- Next server proxy to FastAPI backend.
- FastAPI backend to PostgreSQL/Redis.
- Scheduler/workers to Binance, OpenAI, FRED/BLS/BEA/event-source integrations.
- Operator-controlled settings to runtime execution, event context, and external data fetchers.

Highest impact failure classes:

- Missing operator auth or role checks on state-changing APIs.
- CSRF or same-origin bypass through the Next proxy.
- Operator API key exposure to the browser.
- Path traversal through catch-all API proxy routing.
- Public HTTP exposure of productized operator UI or backend.
- Live-entry gate relaxation, approval-window bypass, or order path mutation without risk/audit gates.
- SSRF or unwanted egress through operator-configured event-source URLs.
- SQL injection in dynamic query paths.

## Coverage Ledger

| Row | Boundary | Files checked | Disposition | Evidence |
| --- | --- | --- | --- | --- |
| SG-1 | Runtime no-entry gate | `backend/trading_mvp/main.py`, runtime GETs | suppressed | Runtime proves `can_enter_new_position=false`, `live_execution_ready=false`, and `approval_armed` closed while service gate is clear. |
| AUTH-1 | Backend write APIs | `backend/trading_mvp/main.py:389`, `backend/trading_mvp/main.py:427`, `backend/trading_mvp/main.py:440`, `backend/trading_mvp/main.py:452` | suppressed | Writes require operator credential, role, allowed origin, and exact intent in full-live posture. |
| AUTH-2 | Backend read APIs | `backend/trading_mvp/main.py:989`, `backend/trading_mvp/main.py:1002` | suppressed | Runtime has operator keys configured; read APIs require a valid `X-Operator-API-Key`. |
| RATE-1 | Backend operator API rate limit | `backend/trading_mvp/main.py:981`, `tests/test_settings_and_connectivity.py` | suppressed | API middleware covers `/api/` calls when operator credentials are configured; tests cover invalid-key bucketing and 429 behavior. |
| UI-1 | Next operator auth/session | `frontend/proxy.ts:223`, `frontend/lib/operator-session.ts:50`, `frontend/lib/operator-session.ts:82`, `frontend/app/api/operator/login/route.ts` | suppressed | Productized/TLS surface fails closed on weak auth config; session cookie is HTTP-only, strict SameSite, and secure in productized posture. |
| UI-2 | Next write proxy CSRF/same-origin | `frontend/app/api/[...path]/route.ts:151`, `frontend/app/api/[...path]/route.ts:169`, `frontend/app/api/[...path]/route.ts:211`, `frontend/app/api/[...path]/route.ts:222` | suppressed | Unsafe methods require same-origin Origin/Referer, configured CSRF token, server-side operator API key, and mapped `X-Operator-Intent`. |
| UI-3 | Catch-all API proxy traversal | `frontend/proxy.ts:178`, `frontend/proxy.ts:190`, `frontend/app/api/[...path]/route.ts:112`, `frontend/app/api/[...path]/route.ts:186` | suppressed | Raw and decoded path segments reject empty, `.`, `..`, slash, and backslash segments before backend proxying. |
| TLS-1 | Productized UI exposure | `frontend/proxy.ts:49`, `frontend/proxy.ts:62`, `frontend/proxy.ts:74`, `frontend/lib/operator-trusted-proxy.ts` | suppressed | Public HTTP is blocked in production-like UI unless request is secure or loopback; forwarded HTTPS headers are trusted only with `X-Operator-Proxy-Secret`. |
| EGRESS-1 | Operator-configured event-source URLs | `backend/trading_mvp/services/settings.py:4324`, `backend/trading_mvp/services/event_context.py:473`, `backend/trading_mvp/services/event_context.py:775`, `backend/trading_mvp/services/event_context_adapters.py:222` | deferred P2 hardening; runbook recorded | Arbitrary event/enrichment URLs are admin/operator-configurable and later fetched by scheduler/provider code. This is not a P0/P1 blocker because the write path is admin-gated and not public, but a future egress allowlist would reduce blast radius after operator credential compromise. |
| SQL-1 | Dynamic SQL | `backend/trading_mvp/sqlite_to_postgresql_copy.py:123`, `backend/trading_mvp/sqlite_to_postgresql_copy.py:127`, `backend/trading_mvp/services/scheduler.py:149`, `backend/trading_mvp/services/service_gate.py:307` | suppressed | Runtime SQL hits are constant or parameterized. The one f-string table-count helper quotes identifiers and is in an operator-run migration/copy utility, not an HTTP user-input path. |
| BLS-1 | BLS wrapper outbound fetch | `backend/trading_mvp/bls_wrapper_app.py:132`, `backend/trading_mvp/bls_wrapper_app.py:446`, `backend/trading_mvp/bls_wrapper_app.py:560` | suppressed | Request parameters choose event metadata only; upstream base URL comes from local wrapper config/env, not the public request. |

## Candidate Validation

No reportable P0/P1 code-fixable security finding survived validation in the scanned productization boundary.

Validated controls:

- Backend state-changing endpoints still depend on operator credential, role, origin, intent, audit logging, and existing live safety policy.
- Next proxy keeps the backend API key server-side and rejects unsafe write requests without same-origin and CSRF proof.
- Productized operator UI proof cannot rely on direct HTTP localhost/public `:3000`; TLS-proxy posture and trusted proxy secret remain required for rendered production proof.
- Runtime no-entry state remains fail-closed; the scan did not change `risk_guard`, execution/order paths, approval-window policy, live-arm policy, pause/resume policy, leverage/risk limits, or exchange credentials.

Deferred/non-blocking coverage:

- This pass did not perform a multi-hour exhaustive line-by-line scan of every runtime source file. The highest-impact operator/productization boundaries were reviewed and recorded here.
- `EGRESS-1` is a P2 hardening candidate only. It should not unblock productization by itself and should not be fixed by loosening operator auth or runtime safety gates.

## EGRESS-1 Follow-up Runbook

Purpose:

- Reduce blast radius after operator credential compromise by limiting scheduler/provider outbound event-source fetches to explicitly approved targets.
- Keep this as hardening only. It must not change risk gates, execution/order paths, live-arm policy, approval windows, pause/resume policy, leverage/risk limits, exchange credentials, or productization readiness status.

Allowed future implementation shape:

- Add a config-level allowlist for event/enrichment source hosts or URL prefixes.
- Normalize and validate configured URLs before they reach scheduler/provider fetch code.
- Fail closed with operator-visible reason copy when a configured URL is outside the allowlist.
- Preserve the existing admin/operator write guard; do not use this hardening as a substitute for auth, CSRF, origin, or intent checks.

Required evidence before code change:

- Current configured event/enrichment source inventory with secret values redacted.
- Unit tests that approved sources still fetch and disallowed hosts are rejected before outbound network I/O.
- Read-only runtime proof that no live entry path changed: `/api/settings` still reports `can_enter_new_position=false`, `live_execution_ready=false`, and approval closed when approval is not armed.
- Documentation that productization remains blocked until profitability/slippage readiness has real evidence.

Do not do:

- Do not add broad wildcard domains, localhost/private-network exceptions, or scheme downgrades.
- Do not silently skip configured event sources without an operator-visible reason.
- Do not mark productization ready from this hardening alone.
- Do not call live exchange APIs, create orders, run migrations, edit `.env`, or restart services as part of this documentation-only pass.

## Productization Verdict

Productization remains blocked.

Blocking reasons preserved:

- Current service gate is blocked by `recent_scheduler_non_success` and `recent_health_errors` while `/api/settings` reports `guard_mode_reason_code=EXCHANGE_AUTH_PERMISSION_REJECTED`; this is external infrastructure/operator action, not a code-fixable scan finding.
- Profitability/slippage readiness remains unverified: `/api/dashboard/profitability` is `not_ready` with `[insufficient_sample,productization_profitability_unverified]`.
- Live runtime still returns `slippage_data_status=UNKNOWN` for cost breakdown. The source-level `NO_SAMPLE` fix needs controlled backend publication before runtime proof can change.
- Human/commercial/legal review and controlled live observation remain outside Codex automation.

## Verification Commands

- Read-only runtime GET set for `/health`, `/api/runtime/service-gate`, `/api/settings`, `/api/dashboard/operator?view=market`, `/api/dashboard/profitability`, `/api/settings/ai-usage`, and `/api/analytics/cost-breakdown?period=today`.
- Static security searches for route declarations, operator auth/CSRF/proxy controls, SQL/text execution, SSRF/event-source URL fetchers, path traversal, redirect, XSS sinks, deserialization, and shell/process/file operations.
- Source reads for root/backend/frontend AGENTS, `backend/trading_mvp/main.py`, `frontend/proxy.ts`, `frontend/app/api/[...path]/route.ts`, operator auth/CSRF/session/trusted-proxy/rate-limit modules, event-source fetchers, BLS wrapper, and relevant tests.
