"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const pollMs = 15000;
const logicalCooldownMs = 15 * 60 * 1000;
const seenStorageKey = "trading-mvp.seen-alert-ids";
const logicalSeenStorageKey = "trading-mvp.seen-alert-logical";
const disabledStorageKey = "trading-mvp.alert-notifier-disabled";
const nonActionableReasonCodes = new Set([
  "HOLD_DECISION",
  "ENTRY_TRIGGER_NOT_MET",
  "NO_EDGE",
  "RANGE_CHOP",
  "WEAK_VOLUME",
  "MOMENTUM_WEAKENING",
  "DETERMINISTIC_BASELINE_DISAGREEMENT",
]);

type AlertRow = {
  id: number;
  category?: string;
  title: string;
  message: string;
  severity: string;
  created_at: string;
  payload?: Record<string, unknown> | null;
};

function readSeenIds() {
  if (typeof window === "undefined") {
    return new Set<number>();
  }
  try {
    const raw = window.localStorage.getItem(seenStorageKey);
    if (!raw) {
      return new Set<number>();
    }
    return new Set<number>((JSON.parse(raw) as number[]).filter((item) => Number.isInteger(item)));
  } catch {
    return new Set<number>();
  }
}

function writeSeenIds(ids: Set<number>) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(seenStorageKey, JSON.stringify([...ids]));
}

function readLogicalSeen() {
  if (typeof window === "undefined") {
    return {} as Record<string, number>;
  }
  try {
    const raw = window.localStorage.getItem(logicalSeenStorageKey);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return Object.fromEntries(
      Object.entries(parsed).filter(([, value]) => typeof value === "number" && value > cutoff),
    ) as Record<string, number>;
  } catch {
    return {};
  }
}

function writeLogicalSeen(seen: Record<string, number>) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(logicalSeenStorageKey, JSON.stringify(seen));
}

function readNotifierDisabled() {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage.getItem(disabledStorageKey) === "true";
  } catch {
    return false;
  }
}

function writeNotifierDisabled(disabled: boolean) {
  if (typeof window === "undefined") {
    return;
  }
  if (disabled) {
    window.localStorage.setItem(disabledStorageKey, "true");
    return;
  }
  window.localStorage.removeItem(disabledStorageKey);
}

function alertReasonCodes(row: AlertRow) {
  const raw = row.payload?.reason_codes;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean)
    .sort();
}

function alertPayloadString(row: AlertRow, key: string) {
  const value = row.payload?.[key];
  return typeof value === "string" ? value.trim() : "";
}

function isOperatorAttentionAlert(row: AlertRow) {
  if (row.severity === "error" || row.severity === "critical") {
    return true;
  }
  if (row.severity !== "warning") {
    return false;
  }

  const reasonCodes = alertReasonCodes(row);
  const decision = alertPayloadString(row, "decision").toLowerCase();
  if (decision === "hold" && reasonCodes.every((code) => nonActionableReasonCodes.has(code))) {
    return false;
  }
  if (reasonCodes.length > 0 && reasonCodes.every((code) => nonActionableReasonCodes.has(code))) {
    return false;
  }
  return true;
}

function alertLogicalKey(row: AlertRow) {
  const reasonCodes = alertReasonCodes(row);
  const symbol = alertPayloadString(row, "symbol") || "all";
  return [row.category ?? "alert", row.severity, row.title, symbol, reasonCodes.join(",")].join("|");
}

export function AlertNotifier() {
  const [permission, setPermission] = useState<NotificationPermission | "unsupported" | "loading">("loading");
  const [latestAlerts, setLatestAlerts] = useState<AlertRow[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [notificationsDisabled, setNotificationsDisabled] = useState<boolean | "loading">("loading");
  const seenIdsRef = useRef<Set<number>>(new Set());
  const logicalSeenRef = useRef<Record<string, number>>({});

  useEffect(() => {
    seenIdsRef.current = readSeenIds();
    logicalSeenRef.current = readLogicalSeen();
    setNotificationsDisabled(readNotifierDisabled());
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (!("Notification" in window)) {
      setPermission("unsupported");
      return;
    }
    setPermission(window.Notification.permission);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (notificationsDisabled !== false) {
      return;
    }

    let active = true;

    const refresh = async () => {
      try {
        const response = await fetch(`${apiBaseUrl}/api/alerts?limit=10`, { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const rows = (await response.json()) as AlertRow[];
        if (!active) {
          return;
        }
        const visibleRows = rows.filter(isOperatorAttentionAlert);
        setLatestAlerts(visibleRows);

        if (!("Notification" in window) || window.Notification.permission !== "granted") {
          return;
        }

        const nextSeenIds = new Set(seenIdsRef.current);
        const nextLogicalSeen = { ...logicalSeenRef.current };
        const now = Date.now();
        for (const row of rows) {
          if (!isOperatorAttentionAlert(row)) {
            nextSeenIds.add(row.id);
            continue;
          }
          if (!["warning", "error", "critical"].includes(row.severity)) {
            nextSeenIds.add(row.id);
            continue;
          }
          if (nextSeenIds.has(row.id)) {
            continue;
          }
          const logicalKey = alertLogicalKey(row);
          const lastNotifiedAt = nextLogicalSeen[logicalKey] ?? 0;
          if (now - lastNotifiedAt < logicalCooldownMs) {
            nextSeenIds.add(row.id);
            continue;
          }
          new window.Notification(row.title, {
            body: row.message,
            tag: `alert-${logicalKey}`,
          });
          nextLogicalSeen[logicalKey] = now;
          nextSeenIds.add(row.id);
        }
        seenIdsRef.current = nextSeenIds;
        logicalSeenRef.current = nextLogicalSeen;
        writeSeenIds(nextSeenIds);
        writeLogicalSeen(nextLogicalSeen);
      } catch {
        return;
      }
    };

    void refresh();
    const interval = window.setInterval(() => {
      void refresh();
    }, pollMs);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [notificationsDisabled]);

  const disableNotifier = () => {
    const nextSeenIds = new Set(seenIdsRef.current);
    latestAlerts.forEach((row) => nextSeenIds.add(row.id));
    seenIdsRef.current = nextSeenIds;
    writeSeenIds(nextSeenIds);
    writeNotifierDisabled(true);
    setLatestAlerts([]);
    setExpanded(false);
    setNotificationsDisabled(true);
  };

  const unreadCount = useMemo(
    () => latestAlerts.filter((row) => !seenIdsRef.current.has(row.id)).length,
    [latestAlerts],
  );

  if (
    notificationsDisabled === "loading" ||
    notificationsDisabled ||
    permission === "loading" ||
    permission === "unsupported"
  ) {
    return null;
  }

  const statusLabel =
    permission === "granted"
      ? unreadCount > 0
        ? `새 알림 ${unreadCount}건`
        : "연결됨"
      : permission === "denied"
        ? "차단됨"
        : "켜기 필요";

  return (
    <div className="fixed bottom-4 right-4 z-40 max-w-[calc(100vw-2rem)] sm:max-w-xs">
      {expanded ? (
        <div className="mb-3 rounded-lg border border-slate-200 bg-white/95 p-4 shadow-sm backdrop-blur">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">거래 알림</p>
              <p className="mt-2 text-sm font-semibold text-slate-950">{statusLabel}</p>
            </div>
            <button
              type="button"
              aria-label="거래 알림 닫기"
              onClick={() => setExpanded(false)}
              className="rounded-md border border-slate-200 px-2 py-1 text-xs font-semibold text-slate-500 transition hover:bg-slate-50"
            >
              닫기
            </button>
          </div>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            경고와 오류는 브라우저 알림으로 받을 수 있습니다.
          </p>
          {permission === "default" ? (
            <button
              className="mt-3 rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white"
              onClick={async () => {
                if (typeof window === "undefined" || !("Notification" in window)) {
                  return;
                }
                const next = await window.Notification.requestPermission();
                setPermission(next);
              }}
              type="button"
            >
              알림 켜기
            </button>
          ) : null}
        </div>
      ) : null}
      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          aria-expanded={expanded}
          aria-label="거래 알림"
          onClick={() => setExpanded((value) => !value)}
          className="flex min-h-11 min-w-0 items-center gap-2 rounded-md border border-slate-200 bg-white/95 px-3 text-sm font-semibold text-slate-800 shadow-sm backdrop-blur transition hover:bg-slate-50"
        >
          <span
            aria-hidden="true"
            className={`h-2.5 w-2.5 shrink-0 rounded-full ${
              unreadCount > 0 ? "bg-amber-500" : permission === "granted" ? "bg-emerald-500" : "bg-slate-300"
            }`}
          />
          <span className="shrink-0">거래 알림</span>
          <span className="min-w-0 truncate rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
            {statusLabel}
          </span>
        </button>
        <button
          type="button"
          aria-label="거래 알림 종료"
          onClick={disableNotifier}
          className="flex min-h-11 shrink-0 items-center rounded-md border border-red-200 bg-white/95 px-3 text-xs font-semibold text-red-600 shadow-sm backdrop-blur transition hover:bg-red-50"
        >
          종료
        </button>
      </div>
    </div>
  );
}
