from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from trading_mvp import config as config_module

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_settings_default_database_url_is_postgresql(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)

    settings = config_module.Settings(_env_file=None)

    assert settings.data_dir == (PROJECT_ROOT / "data").resolve()
    assert settings.database_url == "postgresql+psycopg://trading:trading@127.0.0.1:5432/trading_mvp"
    assert settings.api_host == "127.0.0.1"


def test_settings_default_decision_cycle_is_cost_conservative(monkeypatch) -> None:
    monkeypatch.delenv("DECISION_CYCLE_INTERVAL_MINUTES", raising=False)

    settings = config_module.Settings(_env_file=None)

    assert settings.decision_cycle_interval_minutes == 15


def test_runtime_secret_seed_rejects_default_in_production() -> None:
    settings = config_module.Settings(
        _env_file=None,
        app_env="production",
        app_secret_seed=config_module.DEFAULT_APP_SECRET_SEED,
        operator_api_key="operator-test-key",
    )

    try:
        config_module.validate_runtime_secret_seed(settings)
    except RuntimeError as exc:
        assert "APP_SECRET_SEED" in str(exc)
    else:
        raise AssertionError("default APP_SECRET_SEED should fail in production")


def test_runtime_security_errors_require_operator_key_for_full_live() -> None:
    settings = config_module.Settings(
        _env_file=None,
        app_secret_seed="test-secret-that-is-not-the-default",
        operator_api_key="",
    )

    errors = config_module.runtime_security_errors(settings, rollout_mode="full_live")

    assert errors == ["OPERATOR_API_KEY or role-specific operator API keys are required before production/full_live runtime."]


def test_default_app_secret_seed_is_rejected_for_full_live(monkeypatch) -> None:
    monkeypatch.delenv("APP_SECRET_SEED", raising=False)

    settings = config_module.Settings(_env_file=None)

    try:
        config_module.validate_runtime_secret_seed(settings, rollout_mode="full_live")
    except RuntimeError as exc:
        assert "APP_SECRET_SEED" in str(exc)
    else:
        raise AssertionError("default APP_SECRET_SEED should fail in full_live")


def test_default_app_secret_seed_allowed_for_local_paper(monkeypatch) -> None:
    monkeypatch.delenv("APP_SECRET_SEED", raising=False)

    settings = config_module.Settings(_env_file=None)

    config_module.validate_runtime_secret_seed(settings, rollout_mode="paper")


def test_secret_store_marks_default_seed_insecure() -> None:
    from trading_mvp.services.secret_store import secret_seed_is_insecure

    assert secret_seed_is_insecure("change-me-local-dev-secret") is True
    assert secret_seed_is_insecure("prod-random-secret") is False


def test_public_user_stream_detail_masks_listen_key() -> None:
    from trading_mvp.services.runtime_state import public_user_stream_detail

    payload = public_user_stream_detail({"status": "connected", "listen_key": "raw-listen-key"})

    assert payload["status"] == "connected"
    assert payload["listen_key_present"] is True
    assert "listen_key" not in payload


def test_frontend_service_guard_is_on_production_start_paths() -> None:
    run_frontend = (PROJECT_ROOT / "scripts" / "run_frontend.ps1").read_text(encoding="utf-8")
    release_day = (PROJECT_ROOT / "scripts" / "run_release_day.ps1").read_text(encoding="utf-8")
    service = (PROJECT_ROOT / "scripts" / "run_frontend_service.ps1").read_text(encoding="utf-8")
    clear_lock = (PROJECT_ROOT / "frontend" / "scripts" / "clear-next-build-lock.mjs").read_text(
        encoding="utf-8"
    )
    next_config = (PROJECT_ROOT / "frontend" / "next.config.mjs").read_text(encoding="utf-8")
    package_json = (PROJECT_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    runtime_script = (PROJECT_ROOT / "scripts" / "use_node_runtime.ps1").read_text(encoding="utf-8")

    assert "run_frontend_service.ps1" in run_frontend
    assert "next start --hostname 0.0.0.0" not in run_frontend
    assert "run_frontend_service.ps1" in release_day
    assert "OPERATOR_UI_BEHIND_TLS_PROXY" in service
    assert "Assert-ProductizedFrontendExposure" in service
    assert "Assert-ProductizedOperatorAuthSecrets" in service
    assert "FRONTEND_AUTH_SECRET" in service
    assert 'Test-Path -LiteralPath $buildId' in service
    assert '@("pnpm", "run", "build")' in service
    assert "Remove-Item -LiteralPath $generatedPath" in service
    assert '[string]$Hostname = "127.0.0.1"' in service
    assert "Productization guard: command failed" in service
    assert "BUILD_ID" in service
    assert "pages-manifest.json" in service
    assert "middleware-manifest.json" in service
    assert "middleware.js" in service
    assert "proxy.js" in service
    assert "SkipBuild" in service
    assert '"build", "--webpack"' in service
    assert '"build": "tsc --noEmit && node scripts/clear-next-build-lock.mjs && next build --webpack"' in package_json
    assert "tsc before next build" in next_config
    assert "webpackBuildWorker: false" in next_config
    assert "ignoreBuildErrors: true" in next_config
    assert "start --hostname $Hostname --port $Port" in service
    assert '".next", "BUILD_ID"' in clear_lock
    assert '".next", "lock"' in clear_lock
    assert '".next", "server"' in clear_lock
    assert '".next", "static"' in clear_lock
    assert '".next", "types"' in clear_lock
    assert '".next", "turbopack"' in clear_lock
    assert '(Join-Path $nextBuildPath "server")' in service
    assert '(Join-Path $nextBuildPath "static")' in service
    assert "v22.21.1" in runtime_script
    assert 'Source = "portable"' in runtime_script
    assert '(Join-Path $nextBuildPath "turbopack")' in service
    assert "mkdirSync(nextServerPath" in clear_lock


def test_backend_service_defaults_to_loopback_and_blocks_public_bind() -> None:
    script = (PROJECT_ROOT / "scripts" / "run_backend.ps1").read_text(encoding="utf-8")

    assert 'if ($env:TRADING_MVP_BACKEND_HOST)' in script
    assert '"127.0.0.1"' in script
    assert "TRADING_MVP_ALLOW_PUBLIC_BACKEND_BIND" in script
    assert "--host\", $backendHost" in script
    assert '"--host", "0.0.0.0"' not in script


def test_https_proxy_script_uses_caddy_loopback_frontend() -> None:
    script = (PROJECT_ROOT / "scripts" / "run_https_proxy.ps1").read_text(encoding="utf-8")
    installer = (PROJECT_ROOT / "scripts" / "install_https_proxy_service.ps1").read_text(encoding="utf-8")
    caddyfile = (PROJECT_ROOT / "infra" / "caddy" / "operator.Caddyfile").read_text(encoding="utf-8")

    assert "OPERATOR_HTTPS_HOST" in script
    assert ".tools\\caddy\\caddy.exe" in script
    assert "infra\\caddy\\operator.Caddyfile" in script
    assert "TradingMvpHttpsProxy" in installer
    assert "run_https_proxy.ps1" in installer
    assert "reverse_proxy 127.0.0.1:3000" in caddyfile
    assert "X-Forwarded-Proto https" in caddyfile
    assert "X-Forwarded-For {remote_host}" in caddyfile
    assert "X-Operator-Client-IP {remote_host}" in caddyfile
    assert "X-Operator-Proxy-Secret {$OPERATOR_UI_TRUSTED_PROXY_SECRET}" in caddyfile


def test_operator_auth_uses_login_page_and_session_cookie() -> None:
    proxy = (PROJECT_ROOT / "frontend" / "proxy.ts").read_text(encoding="utf-8")
    session = (PROJECT_ROOT / "frontend" / "lib" / "operator-session.ts").read_text(encoding="utf-8")
    rate_limit = (PROJECT_ROOT / "frontend" / "lib" / "operator-login-rate-limit.ts").read_text(encoding="utf-8")
    login_route = (PROJECT_ROOT / "frontend" / "app" / "api" / "operator" / "login" / "route.ts").read_text(
        encoding="utf-8"
    )
    logout_route = (PROJECT_ROOT / "frontend" / "app" / "api" / "operator" / "logout" / "route.ts").read_text(
        encoding="utf-8"
    )
    dashboard = (PROJECT_ROOT / "frontend" / "components" / "operator-friendly-dashboard.tsx").read_text(
        encoding="utf-8"
    )

    assert "loginRedirect" in proxy
    assert "WWW-Authenticate" not in proxy
    assert "OPERATOR_ADMIN_API_KEY" in proxy
    assert 'pathname === "/api/operator/logout"' not in proxy
    assert "operator_session" in session
    assert "OPERATOR_TRADER_API_KEY" in session
    assert "httpOnly: true" in login_route
    assert "sameSite: \"strict\"" in login_route
    assert "maxAge: 0" in logout_route
    assert "operatorCsrfHeader" in logout_route
    assert "Operator CSRF token is required." in logout_route
    assert "operatorAuthConfigurationError" in proxy
    assert "operatorAuthConfigurationError" in login_route
    assert "OPERATOR_AUTH_CONFIG_INVALID" in proxy
    assert "OPERATOR_AUTH_CONFIG_INVALID" in login_route
    assert "productizedOperatorSurface" in session
    assert 'envFlag("OPERATOR_UI_BEHIND_TLS_PROXY")' in session
    assert "Dedicated operator session secret is required for production/TLS-proxied operator UI." in session
    assert "Operator UI password does not meet productized length policy." in session
    assert "withOperatorWriteProtection(\"/api/operator/logout\"" in dashboard
    assert "recordFailedOperatorLogin" in login_route
    assert "clearOperatorLoginRateLimit" in login_route
    assert "Too many failed operator login attempts." in login_route
    assert "FRONTEND_AUTH_MAX_FAILED_ATTEMPTS" in rate_limit
    assert "FRONTEND_AUTH_RATE_LIMIT_WINDOW_SECONDS" in rate_limit
    assert "x-operator-client-ip" in rate_limit
    assert "x-forwarded-for" not in rate_limit
    assert "trustOperatorForwardedHeaders" in proxy
    assert "trustOperatorForwardedHeaders" in rate_limit


def test_operator_forwarded_headers_require_trusted_proxy_secret() -> None:
    proxy = (PROJECT_ROOT / "frontend" / "proxy.ts").read_text(encoding="utf-8")
    next_proxy = (PROJECT_ROOT / "frontend" / "app" / "api" / "[...path]" / "route.ts").read_text(encoding="utf-8")
    trusted_proxy = (PROJECT_ROOT / "frontend" / "lib" / "operator-trusted-proxy.ts").read_text(encoding="utf-8")
    csrf = (PROJECT_ROOT / "frontend" / "lib" / "operator-csrf.ts").read_text(encoding="utf-8")
    playwright_config = (PROJECT_ROOT / "frontend" / "playwright.config.ts").read_text(encoding="utf-8")
    service = (PROJECT_ROOT / "scripts" / "run_frontend_service.ps1").read_text(encoding="utf-8")
    productization = (PROJECT_ROOT / "scripts" / "run_productization_services.ps1").read_text(encoding="utf-8")
    checks = (PROJECT_ROOT / "scripts" / "run_productization_checks.ps1").read_text(encoding="utf-8")
    https_proxy = (PROJECT_ROOT / "scripts" / "run_https_proxy.ps1").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    checklist = (PROJECT_ROOT / "docs" / "productization-security-checklist.md").read_text(encoding="utf-8")

    assert "OPERATOR_UI_TRUSTED_PROXY_SECRET" in trusted_proxy
    assert "X-Operator-Proxy-Secret" in trusted_proxy
    assert "requestHasTrustedProxySecret" in trusted_proxy
    assert "trustOperatorForwardedHeaders(request.headers) && forwardedProtoIsHttps(request)" in proxy
    assert "if (!trustOperatorForwardedHeaders(request.headers))" in next_proxy
    assert "!safeEqual(submittedToken, operatorCsrfToken())" in next_proxy
    assert "process.env.FRONTEND_AUTH_SECRET" in csrf
    assert "process.env.OPERATOR_AUTH_TOKEN" in csrf
    assert "process.env.OPERATOR_API_KEY" not in csrf
    assert "process.env.APP_SECRET_SEED" not in csrf
    assert "FRONTEND_AUTH_SECRET" in playwright_config
    assert "timeout: 180_000" in playwright_config
    assert "OPERATOR_UI_TRUSTED_PROXY_SECRET is required" in service
    assert "OPERATOR_UI_TRUSTED_PROXY_SECRET is required" in productization
    assert '"X-Operator-Proxy-Secret"] = $trustedProxySecret' in productization
    assert '"X-Operator-Proxy-Secret"] = $trustedProxySecret' in checks
    assert "OPERATOR_UI_TRUSTED_PROXY_SECRET is required" in https_proxy
    assert "OPERATOR_UI_TRUSTED_PROXY_SECRET=" in env_example
    assert "TRADING_MVP_ALLOW_PUBLIC_FRONTEND_BIND" in service
    assert "TRADING_MVP_ALLOW_PUBLIC_FRONTEND_BIND=0" in env_example
    assert "forged `X-Forwarded-*` headers are ignored" in checklist
    assert "private listener is protected from direct public HTTP" in checklist


def test_operator_write_routes_require_specific_intents() -> None:
    main_source = (PROJECT_ROOT / "backend" / "trading_mvp" / "main.py").read_text(encoding="utf-8")
    next_proxy = (PROJECT_ROOT / "frontend" / "app" / "api" / "[...path]" / "route.ts").read_text(encoding="utf-8")
    api_client = (PROJECT_ROOT / "frontend" / "lib" / "api.ts").read_text(encoding="utf-8")

    for intent in (
        "system.seed",
        "binance.account_refresh",
        "settings.operator_event_view",
        "settings.operator_event_view_clear",
        "settings.manual_no_trade_window",
        "settings.integration_test",
        "review.run",
        "replay.run",
        "replay.validation",
    ):
        assert intent in main_source
        assert intent in next_proxy
        assert intent in api_client

    assert 'Depends(require_operator_write_intent("settings.manual_no_trade_window"))' in main_source
    assert 'Depends(require_operator_write_intent("replay.validation"))' in main_source
    assert 'normalizedPath.startsWith("/api/settings/manual-no-trade-windows/")' in next_proxy
    assert 'normalizedPath.startsWith("/api/reviews/")' in next_proxy


def test_operator_ui_redirects_expired_api_sessions_to_login() -> None:
    api_client = (PROJECT_ROOT / "frontend" / "lib" / "api.ts").read_text(encoding="utf-8")
    settings_controls = (PROJECT_ROOT / "frontend" / "components" / "settings-controls.tsx").read_text(
        encoding="utf-8"
    )
    operator_dashboard = (
        PROJECT_ROOT / "frontend" / "components" / "operator-friendly-dashboard.tsx"
    ).read_text(encoding="utf-8")
    login_form = (PROJECT_ROOT / "frontend" / "app" / "login" / "login-form.tsx").read_text(encoding="utf-8")

    assert "handleOperatorApiAuthFailure(response)" in api_client
    assert "response.status !== 401" in api_client
    assert "/login?next=" in api_client
    assert "운영자 세션이 만료되어 로그인 화면으로 이동합니다." in settings_controls
    assert "운영자 세션이 만료되어 로그인 화면으로 이동합니다." in operator_dashboard
    assert "운영자 세션이 만료되었거나 로그인이 필요합니다." in login_form


def test_operator_proxy_rejects_path_traversal_segments() -> None:
    next_proxy = (PROJECT_ROOT / "frontend" / "app" / "api" / "[...path]" / "route.ts").read_text(encoding="utf-8")
    middleware_proxy = (PROJECT_ROOT / "frontend" / "proxy.ts").read_text(encoding="utf-8")

    assert "unsafeProxyPathSegment" in next_proxy
    assert "decodeURIComponent(segment)" in next_proxy
    assert "invalid_operator_proxy_path" in next_proxy
    assert 'value === ".."' in next_proxy
    assert 'value === "."' in next_proxy
    assert 'value.includes("/")' in next_proxy
    assert 'value.includes("\\\\")' in next_proxy
    assert "Invalid operator API proxy path." in next_proxy
    assert "unsafeOperatorApiPath" in middleware_proxy
    assert "rawRequestPath(request)" in middleware_proxy
    assert "unsafeRawApiSegment" in middleware_proxy
    assert "invalidApiPath()" in middleware_proxy
    assert "Invalid operator API proxy path." in middleware_proxy


def test_productization_service_preflight_fails_when_service_gate_not_clear() -> None:
    script = (PROJECT_ROOT / "scripts" / "run_productization_services.ps1").read_text(encoding="utf-8")

    assert "$serviceGate.gate_clear" in script
    assert "Productization preflight failed: service gate is not clear." in script
    assert "run_backend.ps1" in script
    assert "run_backend_dev.ps1" not in script
    assert "Assert-ProductizedFrontendExposure" in script
    assert "Assert-FrontendRuntimeCurrent" in script
    assert "Wait-SchedulerFresh" in script


def test_productization_preflight_blocks_direct_http_operator_ui_in_production() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required for frontend exposure guard regression.")

    env = os.environ.copy()
    env["APP_ENV"] = "production"
    env["OPERATOR_UI_BEHIND_TLS_PROXY"] = "0"
    scripts = [
        PROJECT_ROOT / "scripts" / "run_frontend_service.ps1",
        PROJECT_ROOT / "scripts" / "run_productization_services.ps1",
    ]

    for script in scripts:
        result = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}"
        assert result.returncode != 0
        assert "OPERATOR_UI_BEHIND_TLS_PROXY=1" in output
        assert "operator UI" in output


def test_ensure_dev_environment_documents_broken_venv_repair_path() -> None:
    script = (PROJECT_ROOT / "scripts" / "ensure_dev_environment.ps1").read_text(encoding="utf-8")

    assert "Set PYTHON_EXE" in script
    assert "powershell -ExecutionPolicy Bypass -File scripts\\ensure_dev_environment.ps1 -Repair" in script


def test_productization_checks_automate_slo_backup_restore_and_rollback() -> None:
    script = (PROJECT_ROOT / "scripts" / "run_productization_checks.ps1").read_text(encoding="utf-8")

    assert "SkipDatabaseCredentialPolicy" in script
    assert "SkipAiCostGate" in script
    assert "SkipReadinessGate" in script
    assert "RuntimeGateWaitSeconds" in script
    assert "RuntimeGateEvidencePath" in script
    assert "runtime-gates-latest.json" in script
    assert "runtime_guard" in script
    assert "guard_active" in script
    assert "runtime_guard_status" in script
    assert "AI runtime guard missing or inactive" in script
    assert "pause/disarm live runtime first" in script
    assert "low_actionability_cost_guard_active" in script
    assert "Invoke-OperatorAuthSecretPolicyCheck" in script
    assert "Productization operator auth check failed" in script
    assert "Productization database credential check failed" in script
    assert "Productization AI cost check failed" in script
    assert "Productization runtime gates did not clear" in script
    assert "api/settings/ai-usage" in script
    assert "net_after_ai_cost_7d" in script
    assert "Productization readiness check failed" in script
    assert "$readinessErrors = New-Object System.Collections.Generic.List[string]" in script
    assert "service gate is not clear. blockers=$blockers" in script
    assert "recent scheduler failures remain." in script
    assert "$readinessErrors -join ' | '" in script
    assert "recent_candidate_events=" in script
    assert "min_actual_entries=" in script
    assert "api/runtime/service-gate" in script
    assert "trading:trading" in script
    assert "P95" in script
    assert "P99" in script
    assert "first_hit_ms" in script
    assert "api/dashboard/profitability" in script
    assert "dashboard/trading" in script
    assert "dashboard/cost-breakdown" in script
    assert "pg_dump" in script
    assert "pg_restore" in script
    assert "git diff --check" in script
    assert "use_node_runtime.ps1" in script
    assert "COREPACK_ENABLE_DOWNLOAD_PROMPT" in script
    assert '"pnpm", "-C", $frontendDir, "run", "lint"' in script
    assert '"pnpm", "-C", $frontendDir, "run", "build"' in script
    assert "test:smoke" in script


def test_operator_ai_cost_runtime_guard_blocker_is_visible_in_dashboards() -> None:
    ai_usage_panel = (PROJECT_ROOT / "frontend" / "components" / "ai-usage-panel.tsx").read_text(
        encoding="utf-8"
    )
    dashboard_views = (PROJECT_ROOT / "frontend" / "components" / "dashboard-views.tsx").read_text(
        encoding="utf-8"
    )

    for source in (ai_usage_panel, dashboard_views):
        assert "runtimeGuardMissingForWaste" in source
        assert "신규 진입 AI 비용 게이트 미확인" in source
        assert "신규 진입 AI 비용 게이트 활성" in source
        assert "low_actionability_cost_guard_active" in source


def test_productization_frontend_start_rejects_weak_operator_ui_password() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required for frontend auth guard regression.")

    env = os.environ.copy()
    env["APP_ENV"] = "production"
    env["OPERATOR_UI_BEHIND_TLS_PROXY"] = "1"
    env["OPERATOR_UI_TRUSTED_PROXY_SECRET"] = "proxy-secret-with-enough-length"
    env["OPERATOR_UI_PASSWORD"] = "short"
    env["FRONTEND_AUTH_SECRET"] = "session-secret-with-more-than-thirty-two-chars"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_ROOT / "scripts" / "run_frontend_service.ps1"),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode != 0
    assert "OPERATOR_UI_PASSWORD or FRONTEND_AUTH_PASSWORD must be at least 16 characters" in output


def test_productization_frontend_start_rejects_tls_proxy_without_dedicated_session_secret() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required for frontend auth guard regression.")

    env = os.environ.copy()
    env["APP_ENV"] = "development"
    env["OPERATOR_UI_BEHIND_TLS_PROXY"] = "1"
    env["OPERATOR_UI_TRUSTED_PROXY_SECRET"] = "proxy-secret-with-enough-length"
    env["OPERATOR_UI_PASSWORD"] = "operator-password-with-enough-length"
    env["FRONTEND_AUTH_SECRET"] = "short"
    env["OPERATOR_AUTH_TOKEN"] = "short"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_ROOT / "scripts" / "run_frontend_service.ps1"),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode != 0
    assert "FRONTEND_AUTH_SECRET or OPERATOR_AUTH_TOKEN must be at least 32 characters" in output


def test_productization_frontend_start_rejects_public_bind_by_default() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required for frontend bind guard regression.")

    env = os.environ.copy()
    env["APP_ENV"] = "development"
    env["TRADING_MVP_ALLOW_PUBLIC_FRONTEND_BIND"] = "0"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_ROOT / "scripts" / "run_frontend_service.ps1"),
            "-Hostname",
            "0.0.0.0",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode != 0
    assert "frontend public bind is blocked" in output
    assert "TRADING_MVP_ALLOW_PUBLIC_FRONTEND_BIND=1" in output


def test_productization_ci_runs_backend_frontend_and_guard_checks() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "productization-ci.yml").read_text(
        encoding="utf-8"
    )

    assert "Productization CI" in workflow
    assert "python -m ruff check backend tests workers" in workflow
    assert "python -m pytest" in workflow
    assert "pnpm run lint" in workflow
    assert "pnpm run build" in workflow
    assert "pnpm run test:smoke" in workflow
    assert "run_productization_checks.ps1" in workflow
    assert "-SkipAiCostGate" in workflow
    assert "-SkipReadinessGate" in workflow


def test_env_example_does_not_ship_default_postgres_credentials() -> None:
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "trading:trading" not in env_example
    assert "replace-with-strong-password" in env_example


def test_productization_startup_warms_expensive_read_models() -> None:
    main_source = (PROJECT_ROOT / "backend" / "trading_mvp" / "main.py").read_text(encoding="utf-8")
    ai_usage_source = (PROJECT_ROOT / "backend" / "trading_mvp" / "services" / "ai_usage.py").read_text(
        encoding="utf-8"
    )
    performance_source = (
        PROJECT_ROOT / "backend" / "trading_mvp" / "services" / "performance_reporting.py"
    ).read_text(encoding="utf-8")

    assert "warm_ai_usage_metrics_cache" in main_source
    assert "warm_profitability_dashboard_cache(read_model_session_factory, synchronous=True)" in main_source
    assert "warm_ai_usage_metrics_cache(read_model_session_factory, synchronous=True)" in main_source
    assert "warm_opportunity_attribution_summary_cache" in main_source
    assert "warm_opportunity_attribution_summary_cache(read_model_session_factory, synchronous=True)" in main_source
    assert "ai-usage-metrics-warmup" in ai_usage_source
    assert "opportunity-attribution-summary-refresh" in performance_source


def test_settings_relative_sqlite_override_is_project_root_relative(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./runtime/state.db")
    monkeypatch.setenv("DATA_DIR", "./runtime/data")

    settings = config_module.Settings(_env_file=None)

    assert settings.data_dir == (PROJECT_ROOT / "runtime" / "data").resolve()
    assert settings.database_url == f"sqlite:///{(PROJECT_ROOT / 'runtime' / 'state.db').resolve().as_posix()}"


def test_runtime_database_url_requires_explicit_configuration(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TRADING_MVP_ALLOW_SQLITE", raising=False)
    monkeypatch.setattr(config_module, "_read_dotenv_value", lambda _name: None)

    try:
        config_module.require_runtime_database_url("test startup")
    except RuntimeError as exc:
        assert "DATABASE_URL is required" in str(exc)
    else:
        raise AssertionError("missing DATABASE_URL should fail")


def test_runtime_database_url_accepts_utf8_bom_dotenv(monkeypatch, tmp_path) -> None:
    database_url = "postgresql+psycopg://trading:trading@127.0.0.1:5432/trading_mvp"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TRADING_MVP_ALLOW_SQLITE", raising=False)
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text(f"DATABASE_URL={database_url}\n", encoding="utf-8-sig")

    assert config_module.get_explicit_database_url() == database_url
    assert config_module.require_runtime_database_url("test startup") == database_url


def test_runtime_database_url_requires_sqlite_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./runtime/state.db")
    monkeypatch.delenv("TRADING_MVP_ALLOW_SQLITE", raising=False)
    monkeypatch.setattr(config_module, "_read_dotenv_value", lambda _name: None)

    try:
        config_module.require_runtime_database_url("test startup")
    except RuntimeError as exc:
        assert "TRADING_MVP_ALLOW_SQLITE=1" in str(exc)
    else:
        raise AssertionError("SQLite without opt-in should fail")

    monkeypatch.setenv("TRADING_MVP_ALLOW_SQLITE", "1")
    assert config_module.require_runtime_database_url("test startup") == (
        f"sqlite:///{(PROJECT_ROOT / 'runtime' / 'state.db').resolve().as_posix()}"
    )


def test_runtime_database_url_accepts_dotenv_sqlite_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./runtime/state.db")
    monkeypatch.delenv("TRADING_MVP_ALLOW_SQLITE", raising=False)
    monkeypatch.setattr(
        config_module,
        "_read_dotenv_value",
        lambda name: "1" if name == "TRADING_MVP_ALLOW_SQLITE" else None,
    )

    assert config_module.require_runtime_database_url("test startup") == (
        f"sqlite:///{(PROJECT_ROOT / 'runtime' / 'state.db').resolve().as_posix()}"
    )
