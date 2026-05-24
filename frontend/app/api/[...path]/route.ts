import { operatorCsrfConfigured, operatorCsrfHeader, operatorCsrfToken } from "../../../lib/operator-csrf";
import { safeEqual } from "../../../lib/operator-session";
import { trustOperatorForwardedHeaders } from "../../../lib/operator-trusted-proxy";

const backendApiBaseUrl =
  process.env.API_BASE_URL ??
  "http://127.0.0.1:8000";

const unsafeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const forwardedRequestHeaders = new Set(["accept", "accept-language", "content-type"]);
const skippedResponseHeaders = new Set(["connection", "content-encoding", "content-length", "transfer-encoding"]);
const operatorApiKeyHeader = "X-Operator-API-Key";

type RouteParams = {
  path?: string[];
};

type RouteContext = {
  params: Promise<RouteParams>;
};

function operatorIntentFor(method: string, backendPath: string): string {
  const normalizedMethod = method.toUpperCase();
  const normalizedPath = backendPath.split("?")[0];
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
    "POST /api/cycles/run": "cycle.run",
    "POST /api/replay/run": "replay.run",
    "POST /api/replay/validation": "replay.validation",
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

function serverOperatorApiKey(): string {
  return (
    process.env.OPERATOR_API_KEY?.trim() ||
    process.env.OPERATOR_ADMIN_API_KEY?.trim() ||
    process.env.OPERATOR_TRADER_API_KEY?.trim() ||
    process.env.OPERATOR_VIEWER_API_KEY?.trim() ||
    ""
  );
}

function backendUrl(request: Request, backendPath: string): URL {
  const incomingUrl = new URL(request.url);
  const targetUrl = new URL(backendPath, backendApiBaseUrl);
  targetUrl.search = incomingUrl.search;
  return targetUrl;
}

function outboundHeaders(request: Request): Headers {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    const normalized = key.toLowerCase();
    if (forwardedRequestHeaders.has(normalized)) {
      headers.set(key, value);
    }
  });
  return headers;
}

function responseHeaders(response: Response): Headers {
  const headers = new Headers();
  response.headers.forEach((value, key) => {
    if (!skippedResponseHeaders.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });
  headers.set("Cache-Control", "no-store");
  return headers;
}

function forbidden(detail: string): Response {
  return Response.json({ detail }, { status: 403, headers: { "Cache-Control": "no-store" } });
}

function badRequest(detail: string): Response {
  return Response.json({ detail }, { status: 400, headers: { "Cache-Control": "no-store" } });
}

function unsafeProxyPathSegment(segment: string): boolean {
  let decodedSegment: string;
  try {
    decodedSegment = decodeURIComponent(segment);
  } catch {
    return true;
  }
  return [segment, decodedSegment].some(
    (value) => !value || value === "." || value === ".." || value.includes("/") || value.includes("\\"),
  );
}

function firstForwardedValue(value: string | null): string {
  return (value ?? "").split(",")[0]?.trim() ?? "";
}

function forwardedProto(request: Request): string {
  const directProto = firstForwardedValue(request.headers.get("x-forwarded-proto"));
  if (directProto) {
    return directProto;
  }
  const forwarded = request.headers.get("forwarded") ?? "";
  const match = forwarded.match(/(?:^|;|,)\s*proto=([^;,]+)/i);
  return match ? match[1].replace(/^"|"$/g, "").trim() : "";
}

function expectedRequestOrigin(request: Request): string {
  const url = new URL(request.url);
  if (!trustOperatorForwardedHeaders(request.headers)) {
    return url.origin;
  }
  const proto = forwardedProto(request) || url.protocol.replace(":", "");
  const host =
    firstForwardedValue(request.headers.get("x-forwarded-host")) ||
    request.headers.get("host") ||
    url.host;
  return `${proto}://${host}`;
}

function sameOriginWriteRequest(request: Request): boolean {
  const requestOrigin = expectedRequestOrigin(request);
  const origin = request.headers.get("origin");
  if (origin) {
    return origin === requestOrigin;
  }

  const referer = request.headers.get("referer");
  if (!referer) {
    return false;
  }
  try {
    return new URL(referer).origin === requestOrigin;
  } catch {
    return false;
  }
}

function validateUnsafeOperatorRequest(request: Request): Response | null {
  if (!sameOriginWriteRequest(request)) {
    return forbidden("Operator write requests require same-origin Origin or Referer.");
  }
  if (!operatorCsrfConfigured()) {
    return Response.json(
      { detail: "Operator CSRF secret is not configured." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
  const submittedToken = request.headers.get(operatorCsrfHeader) ?? "";
  if (!safeEqual(submittedToken, operatorCsrfToken())) {
    return forbidden("Operator CSRF token is required.");
  }
  return null;
}

async function routePath(context: RouteContext): Promise<string> {
  const params = await context.params;
  const segments = params.path ?? [];
  for (const segment of segments) {
    if (unsafeProxyPathSegment(segment)) {
      throw new Error("invalid_operator_proxy_path");
    }
  }
  return `/${segments.join("/")}`;
}

async function proxy(request: Request, context: RouteContext): Promise<Response> {
  const method = request.method.toUpperCase();
  let backendPath: string;
  try {
    backendPath = `/api${await routePath(context)}`;
  } catch (error) {
    if (error instanceof Error && error.message === "invalid_operator_proxy_path") {
      return badRequest("Invalid operator API proxy path.");
    }
    throw error;
  }
  const headers = outboundHeaders(request);
  const operatorApiKey = serverOperatorApiKey();

  if (unsafeMethods.has(method)) {
    const validationFailure = validateUnsafeOperatorRequest(request);
    if (validationFailure) {
      return validationFailure;
    }
    if (!operatorApiKey) {
      return Response.json(
        { detail: "OPERATOR_API_KEY or role-specific operator API key is required for operator write proxy requests." },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }
    headers.set("X-Operator-Intent", operatorIntentFor(method, backendPath));
  }

  if (operatorApiKey) {
    headers.set(operatorApiKeyHeader, operatorApiKey);
  }

  const hasBody = !["GET", "HEAD"].includes(method);
  const upstream = await fetch(backendUrl(request, backendPath), {
    method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    cache: "no-store",
    redirect: "manual",
  });

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders(upstream),
  });
}

export async function GET(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function HEAD(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function OPTIONS(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function POST(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function PUT(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function PATCH(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function DELETE(request: Request, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}
