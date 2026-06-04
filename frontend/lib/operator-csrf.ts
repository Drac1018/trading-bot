import { createHmac } from "node:crypto";

export const operatorCsrfHeader = "X-Operator-CSRF";

function csrfSecret() {
  return (
    process.env.FRONTEND_AUTH_SECRET ??
    process.env.OPERATOR_AUTH_TOKEN ??
    ""
  ).trim();
}

export function operatorCsrfConfigured() {
  return csrfSecret().length > 0;
}

export function operatorCsrfToken() {
  const secret = csrfSecret();
  if (!secret) {
    return "";
  }
  return createHmac("sha256", secret).update("trading-operator-ui-csrf-v1").digest("base64url");
}
