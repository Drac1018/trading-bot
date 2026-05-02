"use client";

import { useEffect, useTransition } from "react";
import { useRouter } from "next/navigation";

type MarketChartTimeframe = "15m" | "1h" | "4h";

const candleRefreshGraceMs = 20_000;
const minInitialRefreshDelayMs = 5_000;
const retryRefreshMs = 30_000;
const maxTimeoutMs = 2_147_483_647;

function timeframeMs(timeframe: MarketChartTimeframe) {
  if (timeframe === "1h") {
    return 60 * 60_000;
  }
  if (timeframe === "4h") {
    return 4 * 60 * 60_000;
  }
  return 15 * 60_000;
}

function parseTimestampMs(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value.endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(parsed) ? null : parsed;
}

export function MarketChartAutoRefresh({
  latestCandleTime,
  timeframe,
}: {
  latestCandleTime: string | null;
  timeframe: MarketChartTimeframe;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();

  useEffect(() => {
    const latestMs = parseTimestampMs(latestCandleTime);
    if (latestMs === null) {
      return;
    }

    let retryTimer: number | null = null;
    const refresh = () => {
      startTransition(() => {
        router.refresh();
      });
    };
    const nextCandleCheckAt = latestMs + timeframeMs(timeframe) + candleRefreshGraceMs;
    const initialDelay = Math.min(
      Math.max(nextCandleCheckAt - Date.now(), minInitialRefreshDelayMs),
      maxTimeoutMs,
    );
    const initialTimer = window.setTimeout(() => {
      refresh();
      retryTimer = window.setInterval(refresh, retryRefreshMs);
    }, initialDelay);

    return () => {
      window.clearTimeout(initialTimer);
      if (retryTimer !== null) {
        window.clearInterval(retryTimer);
      }
    };
  }, [latestCandleTime, router, startTransition, timeframe]);

  return (
    <span
      data-latest-candle-time={latestCandleTime ?? ""}
      data-market-auto-refresh={timeframe}
      hidden
    />
  );
}
