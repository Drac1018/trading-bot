import { NextResponse } from "next/server";

import { operatorCsrfConfigured, operatorCsrfHeader, operatorCsrfToken } from "../../../../lib/operator-csrf";
import { operatorSessionCookieName, operatorSessionCookieSecure, safeEqual } from "../../../../lib/operator-session";

function csrfFailure(request: Request): NextResponse | null {
  if (!operatorCsrfConfigured()) {
    return NextResponse.json(
      { detail: "Operator CSRF secret is not configured." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
  const submittedToken = request.headers.get(operatorCsrfHeader) ?? "";
  if (!safeEqual(submittedToken, operatorCsrfToken())) {
    return NextResponse.json(
      { detail: "Operator CSRF token is required." },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  return null;
}

export function POST(request: Request): NextResponse {
  const failure = csrfFailure(request);
  if (failure) {
    return failure;
  }

  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(operatorSessionCookieName, "", {
    httpOnly: true,
    secure: operatorSessionCookieSecure(),
    sameSite: "strict",
    path: "/",
    maxAge: 0,
  });
  return response;
}
