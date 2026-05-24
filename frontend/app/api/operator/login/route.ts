import { NextResponse } from "next/server";

import {
  createOperatorSession,
  expectedOperatorUsername,
  operatorAuthConfigurationError,
  operatorSessionCookieName,
  operatorSessionCookieSecure,
  operatorSessionTtlSeconds,
  validateOperatorCredentials,
} from "../../../../lib/operator-session";
import {
  clearOperatorLoginRateLimit,
  operatorLoginRateLimitKey,
  operatorLoginRetryAfterSeconds,
  recordFailedOperatorLogin,
} from "../../../../lib/operator-login-rate-limit";

type LoginBody = {
  username?: unknown;
  password?: unknown;
};

function jsonResponse(payload: object, status: number): NextResponse {
  return NextResponse.json(payload, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

function rateLimitedResponse(retryAfterSeconds: number): NextResponse {
  return NextResponse.json(
    { detail: "Too many failed operator login attempts." },
    {
      status: 429,
      headers: {
        "Cache-Control": "no-store",
        "Retry-After": String(retryAfterSeconds),
      },
    },
  );
}

export async function POST(request: Request): Promise<NextResponse> {
  const authConfigError = operatorAuthConfigurationError();
  if (authConfigError) {
    return jsonResponse(
      { detail: "Operator authentication is not productized.", reason_code: "OPERATOR_AUTH_CONFIG_INVALID" },
      503,
    );
  }

  let body: LoginBody;
  try {
    body = (await request.json()) as LoginBody;
  } catch {
    return jsonResponse({ detail: "Invalid login request." }, 400);
  }

  const username = typeof body.username === "string" ? body.username.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  const rateLimitKey = operatorLoginRateLimitKey(request, username);
  const retryAfterSeconds = operatorLoginRetryAfterSeconds(rateLimitKey);
  if (retryAfterSeconds > 0) {
    return rateLimitedResponse(retryAfterSeconds);
  }

  if (!validateOperatorCredentials(username, password)) {
    recordFailedOperatorLogin(rateLimitKey);
    return jsonResponse({ detail: "Operator credentials are invalid." }, 401);
  }

  clearOperatorLoginRateLimit(rateLimitKey);
  const response = jsonResponse({ ok: true, username: expectedOperatorUsername() }, 200);
  response.cookies.set(operatorSessionCookieName, await createOperatorSession(username), {
    httpOnly: true,
    secure: operatorSessionCookieSecure(),
    sameSite: "strict",
    path: "/",
    maxAge: operatorSessionTtlSeconds(),
  });
  return response;
}
