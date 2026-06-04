export const operatorSessionCookieName = "operator_session";

type OperatorSessionPayload = {
  sub: string;
  iat: number;
  exp: number;
};

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function envFlag(name: string): boolean {
  return ["1", "true", "yes", "on"].includes((process.env[name] ?? "").trim().toLowerCase());
}

function productionLike(): boolean {
  const env = (process.env.APP_ENV ?? process.env.NODE_ENV ?? "").trim().toLowerCase();
  return env === "prod" || env === "production";
}

function productizedOperatorSurface(): boolean {
  return productionLike() || envFlag("OPERATOR_UI_BEHIND_TLS_PROXY");
}

function sessionSecret(): string {
  return (
    process.env.FRONTEND_AUTH_SECRET ??
    process.env.OPERATOR_AUTH_TOKEN ??
    process.env.OPERATOR_API_KEY ??
    process.env.OPERATOR_ADMIN_API_KEY ??
    process.env.OPERATOR_TRADER_API_KEY ??
    process.env.OPERATOR_VIEWER_API_KEY ??
    process.env.OPERATOR_UI_PASSWORD ??
    ""
  ).trim();
}

export function expectedOperatorUsername(): string {
  return (process.env.OPERATOR_UI_USERNAME ?? process.env.FRONTEND_AUTH_USERNAME ?? "operator").trim();
}

function expectedOperatorPassword(): string {
  return (process.env.OPERATOR_UI_PASSWORD ?? process.env.FRONTEND_AUTH_PASSWORD ?? "").trim();
}

function dedicatedSessionSecret(): string {
  return (process.env.FRONTEND_AUTH_SECRET ?? process.env.OPERATOR_AUTH_TOKEN ?? "").trim();
}

export function operatorAuthConfigurationError(): string | null {
  if (!productizedOperatorSurface()) {
    return null;
  }

  const expectedPassword = expectedOperatorPassword();
  if (!expectedPassword) {
    return "Operator UI password is required for production/TLS-proxied operator UI.";
  }
  if (expectedPassword.length < 16) {
    return "Operator UI password does not meet productized length policy.";
  }

  const signingSecret = dedicatedSessionSecret();
  if (!signingSecret) {
    return "Dedicated operator session secret is required for production/TLS-proxied operator UI.";
  }
  if (signingSecret.length < 32) {
    return "Operator session secret does not meet productized length policy.";
  }

  return null;
}

export function operatorSessionTtlSeconds(): number {
  const parsed = Number.parseInt(process.env.FRONTEND_AUTH_SESSION_TTL_SECONDS ?? "", 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return 12 * 60 * 60;
  }
  return Math.min(parsed, 7 * 24 * 60 * 60);
}

export function operatorSessionCookieSecure(): boolean {
  return envFlag("FRONTEND_AUTH_COOKIE_SECURE") || productionLike() || envFlag("OPERATOR_UI_BEHIND_TLS_PROXY");
}

export function safeEqual(left: string, right: string): boolean {
  const maxLength = Math.max(left.length, right.length);
  let diff = left.length ^ right.length;
  for (let index = 0; index < maxLength; index += 1) {
    diff |= (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
  }
  return diff === 0;
}

export function validateOperatorCredentials(username: string, password: string): boolean {
  const expectedPassword = expectedOperatorPassword();
  if (!expectedPassword) {
    return false;
  }
  return safeEqual(username, expectedOperatorUsername()) && safeEqual(password, expectedPassword);
}

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

function base64UrlToBytes(value: string): Uint8Array {
  const padded = `${value}${"=".repeat((4 - (value.length % 4)) % 4)}`;
  const binary = atob(padded.replaceAll("-", "+").replaceAll("_", "/"));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function stringToBase64Url(value: string): string {
  return bytesToBase64Url(encoder.encode(value));
}

function base64UrlToString(value: string): string {
  return decoder.decode(base64UrlToBytes(value));
}

async function sign(value: string): Promise<string> {
  const secret = sessionSecret();
  if (!secret) {
    return "";
  }
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(value));
  return bytesToBase64Url(new Uint8Array(signature));
}

export async function createOperatorSession(username: string): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const payload: OperatorSessionPayload = {
    sub: username,
    iat: now,
    exp: now + operatorSessionTtlSeconds(),
  };
  const encodedPayload = stringToBase64Url(JSON.stringify(payload));
  const signature = await sign(encodedPayload);
  if (!signature) {
    throw new Error("Operator session secret is not configured.");
  }
  return `${encodedPayload}.${signature}`;
}

export async function verifyOperatorSession(value: string | undefined): Promise<boolean> {
  if (!value) {
    return false;
  }
  const [encodedPayload, signature, extra] = value.split(".");
  if (!encodedPayload || !signature || extra !== undefined) {
    return false;
  }
  const expectedSignature = await sign(encodedPayload);
  if (!expectedSignature || !safeEqual(signature, expectedSignature)) {
    return false;
  }
  try {
    const payload = JSON.parse(base64UrlToString(encodedPayload)) as Partial<OperatorSessionPayload>;
    const now = Math.floor(Date.now() / 1000);
    return payload.sub === expectedOperatorUsername() && typeof payload.exp === "number" && payload.exp > now;
  } catch {
    return false;
  }
}
