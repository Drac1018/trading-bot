"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const pollMs = 15000;
const seenStorageKey = "trading-mvp.seen-alert-ids";

type AlertRow = {
  id: number;
  title: string;
  message: string;
  severity: string;
  created_at: string;
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

export function AlertNotifier() {
  const [permission, setPermission] = useState<NotificationPermission | "unsupported" | "loading">("loading");
  const [latestAlerts, setLatestAlerts] = useState<AlertRow[]>([]);
  const [expanded, setExpanded] = useState(false);
  const seenIdsRef = useRef<Set<number>>(new Set());

  useEffect(() => {
    seenIdsRef.current = readSeenIds();
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
        setLatestAlerts(rows);

        if (!("Notification" in window) || window.Notification.permission !== "granted") {
          return;
        }

        const nextSeenIds = new Set(seenIdsRef.current);
        for (const row of rows) {
          if (!["warning", "error", "critical"].includes(row.severity)) {
            nextSeenIds.add(row.id);
            continue;
          }
          if (nextSeenIds.has(row.id)) {
            continue;
          }
          new window.Notification(row.title, {
            body: row.message,
            tag: `alert-${row.id}`,
          });
          nextSeenIds.add(row.id);
        }
        seenIdsRef.current = nextSeenIds;
        writeSeenIds(nextSeenIds);
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
  }, []);

  const unreadCount = useMemo(
    () => latestAlerts.filter((row) => !seenIdsRef.current.has(row.id)).length,
    [latestAlerts],
  );

  if (permission === "loading" || permission === "unsupported") {
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
      <button
        type="button"
        aria-expanded={expanded}
        aria-label="거래 알림"
        onClick={() => setExpanded((value) => !value)}
        className="ml-auto flex min-h-11 items-center gap-2 rounded-md border border-slate-200 bg-white/95 px-3 text-sm font-semibold text-slate-800 shadow-sm backdrop-blur transition hover:bg-slate-50"
      >
        <span
          aria-hidden="true"
          className={`h-2.5 w-2.5 rounded-full ${
            unreadCount > 0 ? "bg-amber-500" : permission === "granted" ? "bg-emerald-500" : "bg-slate-300"
          }`}
        />
        거래 알림
        <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{statusLabel}</span>
      </button>
    </div>
  );
}
