const backendApiBaseUrl =
  process.env.API_BASE_URL ??
  "http://127.0.0.1:8000";
const apiBaseUrl = typeof window === "undefined" ? backendApiBaseUrl : "";
const operatorApiKeyHeader = "X-Operator-API-Key";
const operatorCsrfHeader = "X-Operator-CSRF";

function currentSafePath() {
  if (typeof window === "undefined") {
    return "/";
  }
  const current = `${window.location.pathname}${window.location.search}`;
  if (!current.startsWith("/") || current.startsWith("//") || current.startsWith("/login")) {
    return "/";
  }
  return current;
}

export function redirectToOperatorLogin() {
  if (typeof window === "undefined") {
    return;
  }
  window.location.assign(`/login?next=${encodeURIComponent(currentSafePath())}`);
}

export function handleOperatorApiAuthFailure(response: Response) {
  if (response.status !== 401 || typeof window === "undefined") {
    return false;
  }
  redirectToOperatorLogin();
  return true;
}

function unsafeMethod(method: string) {
  return !["GET", "HEAD", "OPTIONS"].includes(method);
}

function operatorIntentFor(method: string, path: string): string {
  const normalizedMethod = method.toUpperCase();
  const normalizedPath = path.split("?")[0];
  const routeKey = `${normalizedMethod} ${normalizedPath}`;
  const routeIntents: Record<string, string> = {
    "POST /api/system/seed": "system.seed",
    "POST /api/binance/account/refresh": "binance.account_refresh",
    "PUT /api/settings": "settings.update",
    "PUT /api/settings/operator-event-view": "settings.operator_event_view",
    "POST /api/settings/operator-event-view/clear": "settings.operator_event_view_clear",
    "POST /api/settings/manual-no-trade-windows": "settings.manual_no_trade_window",
    "POST /api/settings/pause": "settings.pause",
    "POST /api/settings/resume": "settings.resume",
    "POST /api/settings/resume/attempt": "settings.resume_attempt",
    "POST /api/settings/live/arm": "settings.live_arm",
    "POST /api/settings/live/disarm": "settings.live_disarm",
    "POST /api/settings/test/openai": "settings.integration_test",
    "POST /api/settings/test/binance": "settings.integration_test",
    "POST /api/settings/test/binance/live-order": "settings.live_test_order",
    "POST /api/replay/run": "replay.run",
    "POST /api/replay/validation": "replay.validation",
    "POST /api/cycles/run": "cycle.run",
    "POST /api/live/sync": "live.sync",
  };
  if (
    normalizedMethod === "PUT" &&
    normalizedPath.startsWith("/api/settings/manual-no-trade-windows/")
  ) {
    return "settings.manual_no_trade_window";
  }
  if (
    normalizedMethod === "POST" &&
    normalizedPath.startsWith("/api/settings/manual-no-trade-windows/") &&
    normalizedPath.endsWith("/end")
  ) {
    return "settings.manual_no_trade_window";
  }
  if (normalizedMethod === "POST" && normalizedPath.startsWith("/api/reviews/")) {
    return "review.run";
  }
  return routeIntents[routeKey] ?? "operator.write";
}

function serverOperatorApiKey() {
  return (
    process.env.OPERATOR_API_KEY?.trim() ||
    process.env.OPERATOR_ADMIN_API_KEY?.trim() ||
    process.env.OPERATOR_TRADER_API_KEY?.trim() ||
    process.env.OPERATOR_VIEWER_API_KEY?.trim() ||
    ""
  );
}

function withServerOperatorApiKey(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  if (typeof window === "undefined" && !headers.has(operatorApiKeyHeader)) {
    const operatorApiKey = serverOperatorApiKey();
    if (operatorApiKey) {
      headers.set(operatorApiKeyHeader, operatorApiKey);
    }
  }
  return { ...init, headers };
}

export function withOperatorAuthHeaders(init?: RequestInit, path = ""): RequestInit {
  const nextInit = withServerOperatorApiKey(init);
  const headers = new Headers(nextInit.headers);
  const method = String(init?.method ?? "GET").toUpperCase();
  if (unsafeMethod(method) && !headers.has("X-Operator-Intent")) {
    headers.set("X-Operator-Intent", operatorIntentFor(method, path));
  }
  return { ...nextInit, headers };
}

async function withBrowserCsrfHeader(init: RequestInit): Promise<RequestInit> {
  const headers = new Headers(init.headers);
  const method = String(init.method ?? "GET").toUpperCase();
  if (typeof window === "undefined" || !unsafeMethod(method) || headers.has(operatorCsrfHeader)) {
    return { ...init, headers };
  }

  const response = await fetch("/api/operator/csrf", { cache: "no-store" });
  if (!response.ok) {
    if (handleOperatorApiAuthFailure(response)) {
      throw new Error("운영자 세션이 만료되어 로그인 화면으로 이동합니다.");
    }
    throw new Error(`Failed to load operator CSRF token: ${response.status}`);
  }
  const payload = (await response.json()) as { token?: string };
  if (!payload.token) {
    throw new Error("Operator CSRF token is missing.");
  }
  headers.set(operatorCsrfHeader, payload.token);
  return {
    ...init,
    headers,
    credentials: init.credentials ?? "same-origin",
    referrer: init.referrer ?? window.location.href,
    referrerPolicy: init.referrerPolicy ?? "same-origin",
  };
}

export async function withOperatorWriteProtection(path: string, init?: RequestInit): Promise<RequestInit> {
  return withBrowserCsrfHeader(withOperatorAuthHeaders(init, path));
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, withServerOperatorApiKey({
    cache: "no-store"
  }));

  if (!response.ok) {
    if (handleOperatorApiAuthFailure(response)) {
      throw new Error("운영자 세션이 만료되어 로그인 화면으로 이동합니다.");
    }
    throw new Error(`Failed to load ${path}: ${response.status}`);
  }

  return (await response.json()) as T;
}

export async function postJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, await withOperatorWriteProtection(path, {
    method: "POST",
    cache: "no-store",
  }));

  if (!response.ok) {
    if (handleOperatorApiAuthFailure(response)) {
      throw new Error("운영자 세션이 만료되어 로그인 화면으로 이동합니다.");
    }
    throw new Error(`Failed to post ${path}: ${response.status}`);
  }

  return (await response.json()) as T;
}

