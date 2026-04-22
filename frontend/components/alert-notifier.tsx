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

  return (
    <div className="fixed bottom-4 right-4 z-40 max-w-xs rounded-2xl border border-amber-200/80 bg-white/95 p-4 shadow-frame backdrop-blur">
      <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">거래 알림</p>
      <p className="mt-2 text-sm font-semibold text-ink">
        {permission === "granted"
          ? unreadCount > 0
            ? `새 알림 ${unreadCount}건`
            : "브라우저 알림 연결됨"
          : permission === "denied"
            ? "브라우저 알림이 차단됨"
            : "브라우저 알림을 활성화하세요"}
      </p>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        경고와 오류 알림은 대시보드가 열려 있을 때 즉시 브라우저 알림으로 전달됩니다.
      </p>
      {permission === "default" ? (
        <button
          className="mt-3 rounded-full bg-amber-400 px-4 py-2 text-sm font-semibold text-slate-900"
          onClick={async () => {
            if (typeof window === "undefined" || !("Notification" in window)) {
              return;
            }
            const next = await window.Notification.requestPermission();
            setPermission(next);
          }}
          type="button"
        >
          브라우저 알림 켜기
        </button>
      ) : null}
    </div>
  );
}
