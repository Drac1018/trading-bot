# Productization Security Checklist

Public HTTP operator UI is a productization blocker. Do not expose an operator dashboard such as `http://1.233.93.187:3000/` directly on the public internet.

Required before public operation:

- Serve the operator UI only through HTTPS reverse proxy.
- Keep direct `0.0.0.0:3000` access blocked from the public internet.
- Keep `TRADING_MVP_ALLOW_PUBLIC_FRONTEND_BIND=0` unless a private listener is protected from direct public HTTP by firewall/VPN/allowlist.
- Enable operator authentication with strong `OPERATOR_UI_PASSWORD` or bearer token.
- Keep failed operator login rate limiting enabled with `FRONTEND_AUTH_MAX_FAILED_ATTEMPTS` and `FRONTEND_AUTH_RATE_LIMIT_WINDOW_SECONDS`.
- Use a dedicated operator UI password of at least 16 characters and a dedicated `FRONTEND_AUTH_SECRET` or `OPERATOR_AUTH_TOKEN` of at least 32 characters; do not reuse `OPERATOR_API_KEY` as the UI session signing or CSRF secret.
- Ensure the HTTPS proxy overwrites client IP headers and sends `X-Operator-Client-IP` from the real remote address.
- Set a high-entropy `OPERATOR_UI_TRUSTED_PROXY_SECRET` on both Caddy and the Next service so forged `X-Forwarded-*` headers are ignored.
- Restrict operator access with VPN, firewall IP allowlist, or equivalent network policy.
- Keep browser API calls on relative `/api/...` paths so Next proxies requests server-side with `OPERATOR_API_KEY`.
- Set `OPERATOR_UI_BEHIND_TLS_PROXY=1` only after TLS, auth, and allowlist are verified.

Validation proof rule:

- When `OPERATOR_UI_BEHIND_TLS_PROXY=1`, a direct `http://127.0.0.1:3000` or public `http://...:3000` browser screenshot is not productized UI proof.
- Acceptable proof is either a valid HTTPS reverse-proxy operator URL with auth/CSRF/allowlist in place, or source/unit/build validation plus authenticated read-only backend API evidence.

TLS-proxy UI proof matrix:

- Local direct browser proof: use only as developer smoke evidence. It does not prove productized UI readiness when `OPERATOR_UI_BEHIND_TLS_PROXY=1`.
- Productized rendered proof: use an HTTPS reverse-proxy operator URL and verify login/session, CSRF-protected write controls, IP allowlist or VPN policy, and that browser API calls stay on relative `/api/...` paths.
- Source/build fallback proof: when no valid HTTPS operator URL is available, require frontend lint/build, relevant unit or smoke tests, source checks for `frontend/proxy.ts`, `frontend/app/api/[...path]/route.ts`, `frontend/lib/operator-session.ts`, `frontend/lib/operator-trusted-proxy.ts`, and authenticated read-only backend API evidence.
- Runtime no-entry proof: record only redacted `.env` key presence plus `/health`, `/api/runtime/service-gate`, `/api/settings`, `/api/dashboard/operator`, `/api/dashboard/profitability`, `/api/settings/ai-usage`, and `/api/analytics/cost-breakdown` summaries. Never print secret values.
- Productization blocker rule: keep productization blocked if the only rendered evidence is direct HTTP localhost/public `:3000`, or if `can_enter_new_position`, `live_execution_ready`, approval state, profitability readiness, or slippage quality contradict the release claim.

Exchange auth permission blocker runbook:

- Treat `EXCHANGE_AUTH_PERMISSION_REJECTED` and Binance `-2015` responses as external credential, IP allowlist, or exchange-permission blockers. They are productization blockers but not code-fixable by automation.
- Accepted read-only proof: `/api/runtime/service-gate` shows only scheduler/health blockers caused by exchange auth rejection, `/api/settings` reports `can_enter_new_position=false` and `live_execution_ready=false`, and `/api/dashboard/operator` shows approval closed or paused.
- Operator action required: verify the Binance key permissions, source IP allowlist, and production secret ownership outside Codex. Do not print or paste raw key values into reports.
- Do not fix this by editing `.env`, weakening service-gate checks, arming live execution, changing pause/resume policy, creating test orders, or marking productization ready.
- Done condition: the read-only service gate has no recent exchange-auth scheduler/health blockers, the exchange permission reason is cleared without relaxing safety gates, and profitability/slippage readiness evidence is still evaluated separately.

Service-gate scheduler/health blocker classification runbook:

- Treat `recent_scheduler_non_success` and `recent_health_errors` as generic publication blockers until their read-only detail rows identify a root cause. A green `/health` response is not enough for productization sign-off while `/api/runtime/service-gate` is blocked.
- Accepted read-only proof: capture `/api/runtime/service-gate` counts and blockers, `/api/settings` no-entry flags, `/api/dashboard/operator` no-entry flags, and a sanitized scheduler or health detail sample that shows the same root cause family, such as `EXCHANGE_AUTH_PERMISSION_REJECTED` or Binance `-2015`.
- Operator action required: if the detail sample points to exchange auth, follow the exchange-auth runbook above. If it points to stale runtime publication, follow the controlled publication path. If it points to a new code exception, open a separate one-item P0/P1 source-fix candidate.
- Do not fix this by clearing scheduler or health rows, suppressing service-gate blockers, restarting services just to hide evidence, editing secrets, or treating generic blocker names as resolved without root-cause detail.
- Done condition: service-gate blockers are empty or the remaining blocker is classified with current read-only root-cause evidence, no live-entry safety flag was relaxed, and the productization verdict still keeps unresolved external/manual/runtime-publication blockers open.

Cost breakdown no-sample publication runbook:

- Treat `slippage_data_status=UNKNOWN` after the source-level `NO_SAMPLE` fix as a runtime-publication evidence blocker, not as permission to manufacture samples or bypass readiness.
- Accepted read-only proof after controlled backend publication: `/api/analytics/cost-breakdown?period=today` returns `data_quality.slippage_data_status=NO_SAMPLE` with `slippage_data_status:NO_SAMPLE` when there are no executions in the period, while `/api/settings` still reports `can_enter_new_position=false` unless the normal live gates are independently satisfied.
- Operator action required: publish or restart the backend through the approved controlled deployment path, then capture the read-only cost endpoint, settings, service-gate, operator dashboard, and profitability summaries. Do not print raw secrets.
- Do not fix this by inserting fake executions, editing production DB rows, loosening approval or risk gates, creating live orders, changing `.env`, or marking profitability ready without real evidence.
- Done condition: the live endpoint reflects `NO_SAMPLE` for empty windows, productization remains blocked until profitability/slippage readiness has sufficient real evidence, and no live-entry safety flag was relaxed.

AI usage cost-readiness runbook:

- Treat `/api/settings/ai-usage` as cost observability, not as a release gate override. A quiet 24h window does not prove profitability readiness when 7d usage, monthly projected cost, service-gate blockers, or profitability readiness still contradict release.
- Accepted read-only proof: capture `recent_ai_calls_today_kst`, `recent_ai_calls_24h`, `recent_ai_calls_7d`, `observed_monthly_ai_cost_projection_usd`, `/api/dashboard/profitability` readiness, and the no-entry flags from `/api/settings` and `/api/dashboard/operator`.
- Operator action required: compare AI cost projection with real profitability and slippage evidence after normal controlled observation. Keep productization blocked when profitability is `not_ready`, slippage quality is unresolved, or service-gate blockers remain.
- Do not fix this by clearing usage records, disabling cost guards, forcing provider calls, lowering AI/risk thresholds, editing `.env`, creating live orders, or treating zero recent calls as proof that AI cost is safe.
- Done condition: AI usage cost is documented alongside profitability/slippage readiness, no live-entry safety flag was relaxed, and any cost/profitability contradiction remains an explicit blocker instead of a hidden dashboard interpretation.

Runtime proof timestamp freshness runbook:

- Treat every productization runtime snapshot as time-bound evidence. A prior `/api/dashboard/operator` `generated_at`, older service-gate sample, or historical security-scan runtime block cannot override the latest read-only GET set.
- Accepted read-only proof: record the collection timestamp with timezone, `/api/dashboard/operator.generated_at`, `/api/runtime/service-gate` blockers and counts, `/api/settings` no-entry flags, profitability readiness, AI usage cost fields, and cost-breakdown data-quality status in the same evidence block.
- Operator action required: refresh the full read-only GET set before productization decisions, handoffs, or blocker closure. If endpoint timestamps disagree or look stale, keep the blocker open and classify the gap as runtime-publication or evidence-freshness until a controlled refresh proves otherwise.
- Do not fix this by editing timestamps, deleting older evidence, restarting services only to hide stale data, weakening readiness gates, or using a green `/health` response as a substitute for endpoint-specific freshness.
- Done condition: the current evidence block includes collection time plus endpoint timestamps, unresolved blockers remain explicit, and no live-entry safety flag was relaxed.
