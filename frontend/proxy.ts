import { NextResponse, type NextRequest } from "next/server";

import {
  expectedOperatorUsername,
  operatorAuthConfigurationError,
  operatorSessionCookieName,
  safeEqual,
  validateOperatorCredentials,
  verifyOperatorSession,
} from "./lib/operator-session";
import { trustOperatorForwardedHeaders } from "./lib/operator-trusted-proxy";

function productionLike(): boolean {
  const env = (process.env.APP_ENV ?? process.env.NODE_ENV ?? "").toLowerCase();
  return env === "prod" || env === "production";
}

function authEnabled(): boolean {
  const env = (process.env.APP_ENV ?? process.env.NODE_ENV ?? "").toLowerCase();
  if (process.env.OPERATOR_AUTH_ENABLED === "1") {
    return true;
  }
  return (
    env === "prod" ||
    env === "production" ||
    Boolean(
      process.env.OPERATOR_UI_PASSWORD ||
        process.env.OPERATOR_AUTH_TOKEN ||
        process.env.OPERATOR_API_KEY ||
        process.env.OPERATOR_ADMIN_API_KEY ||
        process.env.OPERATOR_TRADER_API_KEY ||
        process.env.OPERATOR_VIEWER_API_KEY,
    )
  );
}

function operatorAuthSecret(): string {
  return (
    process.env.OPERATOR_UI_PASSWORD ??
    process.env.OPERATOR_AUTH_TOKEN ??
    process.env.OPERATOR_API_KEY ??
    process.env.OPERATOR_ADMIN_API_KEY ??
    process.env.OPERATOR_TRADER_API_KEY ??
    process.env.OPERATOR_VIEWER_API_KEY ??
    ""
  );
}

function forwardedProtoIsHttps(request: NextRequest): boolean {
  const forwarded = request.headers.get("forwarded") ?? "";
  const forwardedProto = forwarded
    .split(";")
    .map((part) => part.trim().toLowerCase())
    .find((part) => part.startsWith("proto="));
  return (
    request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim().toLowerCase() === "https" ||
    request.headers.get("x-forwarded-ssl")?.toLowerCase() === "on" ||
    forwardedProto === "proto=https"
  );
}

function requestIsSecure(request: NextRequest): boolean {
  return (
    request.nextUrl.protocol === "https:" ||
    (trustOperatorForwardedHeaders(request.headers) && forwardedProtoIsHttps(request))
  );
}

function requestIsLoopback(request: NextRequest): boolean {
  const host = (request.headers.get("host") ?? request.nextUrl.host).split(":")[0]?.toLowerCase();
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]" || host === "::1";
}

function insecurePublicHttpBlocked(request: NextRequest): NextResponse | null {
  if (!authEnabled() || !productionLike() || requestIsSecure(request) || requestIsLoopback(request)) {
    return null;
  }
  return new NextResponse("Operator UI requires HTTPS in production.", {
    status: 503,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}

function bearerAuthMatches(authorization: string, expectedSecret: string): boolean {
  const prefix = "Bearer ";
  if (!authorization.startsWith(prefix)) {
    return false;
  }
  return safeEqual(authorization.slice(prefix.length).trim(), expectedSecret);
}

function basicAuthMatches(authorization: string, expectedUser: string, expectedSecret: string): boolean {
  const prefix = "Basic ";
  if (!authorization.startsWith(prefix)) {
    return false;
  }

  let decoded = "";
  try {
    decoded = atob(authorization.slice(prefix.length).trim());
  } catch {
    return false;
  }

  const separatorIndex = decoded.indexOf(":");
  if (separatorIndex < 0) {
    return false;
  }

  const username = decoded.slice(0, separatorIndex);
  const password = decoded.slice(separatorIndex + 1);
  return safeEqual(username, expectedUser) && safeEqual(password, expectedSecret) && validateOperatorCredentials(username, password);
}

async function operatorAuthenticated(request: NextRequest): Promise<boolean> {
  if (await verifyOperatorSession(request.cookies.get(operatorSessionCookieName)?.value)) {
    return true;
  }

  const expectedSecret = operatorAuthSecret();
  if (!expectedSecret) {
    return false;
  }

  const authorization = request.headers.get("authorization") ?? "";
  const expectedUser = expectedOperatorUsername();
  return bearerAuthMatches(authorization, expectedSecret) || basicAuthMatches(authorization, expectedUser, expectedSecret);
}

function loginPathFor(request: NextRequest): string {
  const current = `${request.nextUrl.pathname}${request.nextUrl.search}`;
  return `/login?next=${encodeURIComponent(current)}`;
}

function unauthorizedApi(): NextResponse {
  return NextResponse.json(
    { detail: "Operator authentication required." },
    {
      status: 401,
      headers: { "Cache-Control": "no-store" },
    },
  );
}

function authConfigurationFailure(request: NextRequest, detail: string): NextResponse {
  if (request.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json(
      { detail: "Operator authentication is not productized.", reason_code: "OPERATOR_AUTH_CONFIG_INVALID" },
      {
        status: 503,
        headers: { "Cache-Control": "no-store" },
      },
    );
  }
  return new NextResponse(`Operator authentication is not productized: ${detail}`, {
    status: 503,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}

function rawRequestPath(request: NextRequest): string {
  const withoutHash = request.url.split("#", 1)[0] ?? request.url;
  const withoutQuery = withoutHash.split("?", 1)[0] ?? withoutHash;
  const schemeIndex = withoutQuery.indexOf("://");
  if (schemeIndex < 0) {
    return withoutQuery || "/";
  }
  const pathStart = withoutQuery.indexOf("/", schemeIndex + 3);
  return pathStart >= 0 ? withoutQuery.slice(pathStart) : "/";
}

function unsafeRawApiSegment(segment: string): boolean {
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

function unsafeOperatorApiPath(request: NextRequest): boolean {
  const rawSegments = rawRequestPath(request).split("/");
  const apiSegment = rawSegments[1] ?? "";
  let decodedApiSegment = "";
  try {
    decodedApiSegment = decodeURIComponent(apiSegment);
  } catch {
    return true;
  }
  if (apiSegment.toLowerCase() !== "api" && decodedApiSegment.toLowerCase() !== "api") {
    return false;
  }
  return rawSegments.slice(2).some(unsafeRawApiSegment);
}

function invalidApiPath(): NextResponse {
  return NextResponse.json(
    { detail: "Invalid operator API proxy path." },
    {
      status: 400,
      headers: { "Cache-Control": "no-store" },
    },
  );
}

function loginRedirect(request: NextRequest): NextResponse {
  return NextResponse.redirect(new URL(loginPathFor(request), request.url));
}

function authBypassPath(pathname: string): boolean {
  return pathname === "/login" || pathname === "/api/operator/login";
}

export async function proxy(request: NextRequest) {
  if (unsafeOperatorApiPath(request)) {
    return invalidApiPath();
  }
  const insecureBlock = insecurePublicHttpBlocked(request);
  if (insecureBlock) {
    return insecureBlock;
  }
  const authConfigError = operatorAuthConfigurationError();
  if (authConfigError) {
    return authConfigurationFailure(request, authConfigError);
  }
  if (!authEnabled()) {
    return NextResponse.next();
  }
  const authenticated = await operatorAuthenticated(request);
  if (authBypassPath(request.nextUrl.pathname)) {
    if (authenticated && request.nextUrl.pathname === "/login") {
      return NextResponse.redirect(new URL("/", request.url));
    }
    return NextResponse.next();
  }
  if (authenticated) {
    return NextResponse.next();
  }
  if (request.nextUrl.pathname.startsWith("/api/")) {
    return unauthorizedApi();
  }
  return loginRedirect(request);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml).*)"],
};
