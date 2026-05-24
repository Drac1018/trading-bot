import { safeEqual } from "./operator-session";

export const operatorTrustedProxyHeader = "X-Operator-Proxy-Secret";

function envFlag(name: string): boolean {
  return ["1", "true", "yes", "on"].includes((process.env[name] ?? "").trim().toLowerCase());
}

function trustedProxySecret(): string {
  return (process.env.OPERATOR_UI_TRUSTED_PROXY_SECRET ?? "").trim();
}

export function operatorTlsProxyEnabled(): boolean {
  return envFlag("OPERATOR_UI_BEHIND_TLS_PROXY");
}

export function requestHasTrustedProxySecret(headers: Headers): boolean {
  const secret = trustedProxySecret();
  if (!secret) {
    return false;
  }
  return safeEqual(headers.get(operatorTrustedProxyHeader) ?? "", secret);
}

export function trustOperatorForwardedHeaders(headers: Headers): boolean {
  return operatorTlsProxyEnabled() && requestHasTrustedProxySecret(headers);
}
