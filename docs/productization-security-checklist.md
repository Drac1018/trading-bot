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
