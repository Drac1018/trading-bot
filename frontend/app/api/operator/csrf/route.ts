import { operatorCsrfConfigured, operatorCsrfToken } from "../../../../lib/operator-csrf";

export function GET(): Response {
  if (!operatorCsrfConfigured()) {
    return Response.json(
      { detail: "Operator CSRF secret is not configured." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  return Response.json(
    { token: operatorCsrfToken() },
    { headers: { "Cache-Control": "no-store" } },
  );
}
