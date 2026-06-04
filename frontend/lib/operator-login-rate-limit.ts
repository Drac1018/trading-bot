import { trustOperatorForwardedHeaders } from "./operator-trusted-proxy";

const defaultMaxFailedAttempts = 5;
const defaultWindowSeconds = 5 * 60;

type LoginAttemptBucket = {
  count: number;
  resetAt: number;
};

const loginAttempts = new Map<string, LoginAttemptBucket>();

function positiveIntegerEnv(name: string, fallback: number): number {
  const parsed = Number.parseInt(process.env[name] ?? "", 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return parsed;
}

function maxFailedAttempts(): number {
  return positiveIntegerEnv("FRONTEND_AUTH_MAX_FAILED_ATTEMPTS", defaultMaxFailedAttempts);
}

function windowSeconds(): number {
  return positiveIntegerEnv("FRONTEND_AUTH_RATE_LIMIT_WINDOW_SECONDS", defaultWindowSeconds);
}

function firstHeaderValue(value: string | null): string {
  return value?.split(",")[0]?.trim() ?? "";
}

function trustedProxyClientIp(headers: Headers): string {
  if (!trustOperatorForwardedHeaders(headers)) {
    return "";
  }
  return firstHeaderValue(headers.get("x-operator-client-ip"));
}

export function operatorLoginRateLimitKey(request: Request, username: string): string {
  const host = request.headers.get("host") ?? "";
  const remote = trustedProxyClientIp(request.headers) || host || "unknown";
  return `${remote.toLowerCase()}|${username.trim().toLowerCase() || "unknown"}`;
}

export function operatorLoginRetryAfterSeconds(key: string, now = Date.now()): number {
  const bucket = loginAttempts.get(key);
  if (!bucket) {
    return 0;
  }
  if (bucket.resetAt <= now) {
    loginAttempts.delete(key);
    return 0;
  }
  if (bucket.count < maxFailedAttempts()) {
    return 0;
  }
  return Math.max(1, Math.ceil((bucket.resetAt - now) / 1000));
}

export function recordFailedOperatorLogin(key: string, now = Date.now()): void {
  const resetAt = now + windowSeconds() * 1000;
  const current = loginAttempts.get(key);
  if (!current || current.resetAt <= now) {
    loginAttempts.set(key, { count: 1, resetAt });
    return;
  }
  current.count += 1;
}

export function clearOperatorLoginRateLimit(key: string): void {
  loginAttempts.delete(key);
}
