"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { DataTable } from "../../../components/data-table";
import { PageShell } from "../../../components/page-shell";
import { fetchJson, postJson } from "../../../lib/api";
import { formatDisplayValue } from "../../../lib/ui-copy";

type AccountSummary = {
  connected: boolean;
  message: string;
  testnet_enabled: boolean;
  futures_enabled: boolean;
  available_balance: number;
  total_wallet_balance: number;
  total_unrealized_profit: number;
  total_margin_balance: number;
  open_positions: number;
  open_orders: number;
  exchange_update_time: string | null;
};

type AccountPayload = {
  summary: AccountSummary;
  assets: Record<string, unknown>[];
  positions: Record<string, unknown>[];
  open_orders: Record<string, unknown>[];
};

type AccountSource = "local" | "cached_live";

type AccountCachePayload = {
  status: string;
  source: AccountSource;
  message: string;
  requested_at?: string | null;
  started_at?: string | null;
  refreshed_at?: string | null;
  last_error?: string | null;
  duration_ms?: number | null;
  payload: AccountPayload;
};

type AccountCacheLoadResult = {
  cache: AccountCachePayload;
  cacheError?: string | null;
};

const unknownAccountErrorMessage = "알 수 없는 오류가 발생했습니다.";

function errorMessageFromUnknown(error: unknown) {
  return error instanceof Error ? error.message : unknownAccountErrorMessage;
}

async function fetchAccountCacheWithLocalFallback(): Promise<AccountCacheLoadResult> {
  try {
    return { cache: await fetchJson<AccountCachePayload>("/api/binance/account/cache") };
  } catch (cacheError: unknown) {
    const cacheErrorMessage = errorMessageFromUnknown(cacheError);
    try {
      const payload = await fetchJson<AccountPayload>("/api/binance/account/local");
      return {
        cache: {
          status: "cache_unavailable",
          source: "local",
          message: "계정 캐시 API 응답 실패로 최근 로컬 동기화 기준을 표시합니다.",
          requested_at: null,
          started_at: null,
          refreshed_at: null,
          last_error: cacheErrorMessage,
          duration_ms: null,
          payload,
        },
        cacheError: cacheErrorMessage,
      };
    } catch (localError: unknown) {
      throw new Error(`${cacheErrorMessage}; 로컬 동기화 조회도 실패: ${errorMessageFromUnknown(localError)}`);
    }
  }
}

function isRefreshPending(status: string) {
  return status === "queued" || status === "refreshing" || status === "already_running";
}

function cacheStatusLabel(status: string) {
  if (status === "queued") {
    return "갱신 요청됨";
  }
  if (status === "refreshing") {
    return "갱신 중";
  }
  if (status === "already_running") {
    return "이미 갱신 중";
  }
  if (status === "failed") {
    return "갱신 실패";
  }
  if (status === "ready") {
    return "캐시 준비됨";
  }
  if (status === "cache_unavailable") {
    return "캐시 조회 실패";
  }
  return "로컬 기준";
}

const accountCacheStaleAfterMs = 15 * 60 * 1000;

function parseApiDate(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const parsed = new Date(value.endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function accountCacheAgeMs(value: string | null | undefined) {
  const parsed = parseApiDate(value);
  return parsed ? Math.max(0, Date.now() - parsed.getTime()) : null;
}

function isAccountCacheStale(cache: AccountCachePayload) {
  const ageMs = accountCacheAgeMs(cache.refreshed_at);
  return cache.source === "cached_live" && ageMs !== null && ageMs > accountCacheStaleAfterMs;
}

function formatCacheAge(value: string | null | undefined) {
  const ageMs = accountCacheAgeMs(value);
  if (ageMs === null) {
    return null;
  }
  const minutes = Math.max(0, Math.round(ageMs / 60000));
  if (minutes < 60) {
    return `${minutes}분`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours}시간 ${remainingMinutes}분` : `${hours}시간`;
}

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">{label}</p>
      <p className="mt-3 break-words text-xl font-semibold text-slate-950 sm:text-2xl">{value}</p>
      {hint ? <p className="mt-2 text-sm leading-6 text-slate-600">{hint}</p> : null}
    </div>
  );
}

function StatusBadge({
  tone,
  label,
}: {
  tone: "neutral" | "good" | "warn" | "danger";
  label: string;
}) {
  const className = {
    neutral: "border border-slate-200 bg-slate-50 text-slate-700",
    good: "border border-emerald-200 bg-emerald-50 text-emerald-700",
    warn: "border border-amber-200 bg-amber-50 text-amber-800",
    danger: "border border-rose-200 bg-rose-50 text-rose-800",
  }[tone];
  return <span className={`rounded-md px-3 py-2 text-sm font-semibold ${className}`}>{label}</span>;
}

function LoadingPanel() {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-wrap gap-2">
        <StatusBadge tone="neutral" label="계정 상태 확인 중" />
      </div>
      <p className="mt-5 text-sm leading-7 text-slate-700">
        캐시된 Binance 원본 응답이나 최근 로컬 동기화 정보를 불러오고 있습니다.
      </p>
    </section>
  );
}

function ErrorPanel({
  message,
  onRetry,
  retrying,
}: {
  message: string;
  onRetry: () => void;
  retrying: boolean;
}) {
  return (
    <section className="rounded-lg border border-rose-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-wrap gap-2">
        <StatusBadge tone="danger" label="계정 정보 로드 실패" />
      </div>
      <p className="mt-5 text-sm leading-7 text-slate-700">
        계정 화면 데이터를 불러오지 못했습니다. 운영 상태 요약은{" "}
        <Link className="font-semibold text-blue-700 underline decoration-blue-300 underline-offset-4" href="/">
          개요 화면
        </Link>
        에서도 확인할 수 있습니다.
      </p>
      <p className="mt-3 rounded-md border border-rose-100 bg-rose-50 p-4 text-sm leading-6 text-rose-800">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        disabled={retrying}
        className="mt-4 inline-flex min-h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-800 transition hover:border-slate-400 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {retrying ? "다시 확인 중" : "다시 확인"}
      </button>
    </section>
  );
}

function SnapshotSourcePanel({
  cache,
  refreshingCache,
  cacheError,
  onRefreshCache,
}: {
  cache: AccountCachePayload;
  refreshingCache: boolean;
  cacheError: string | null;
  onRefreshCache: () => void;
}) {
  const isCachedLive = cache.source === "cached_live";
  const cacheStale = isAccountCacheStale(cache);
  const cacheAgeText = formatCacheAge(cache.refreshed_at);
  const sourceTone = cacheStale ? "warn" : isCachedLive ? "good" : "neutral";
  const statusTone = cache.status === "failed" ? "warn" : isRefreshPending(cache.status) ? "neutral" : cacheStale ? "warn" : "good";
  const refreshedText = cache.refreshed_at
    ? `마지막 원본 갱신: ${formatDisplayValue(cache.refreshed_at, "exchange_update_time")}`
    : "아직 성공한 원본 캐시가 없습니다.";
  const durationText =
    typeof cache.duration_ms === "number" ? `최근 갱신 소요: ${Math.round(cache.duration_ms)}ms` : null;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap gap-2">
            <StatusBadge tone={sourceTone} label={cacheStale ? "Binance 원본 캐시 지연" : isCachedLive ? "캐시된 Binance 원본" : "최근 로컬 동기화"} />
            <StatusBadge tone={statusTone} label={cacheStale ? "원본 캐시 오래됨" : cacheStatusLabel(cache.status)} />
          </div>
          <p className="mt-4 text-sm leading-7 text-slate-700">
            {cache.message} 갱신 버튼은 요청만 보내고, 화면은 사용 가능한 최근 계정 기준을 즉시 표시합니다.
          </p>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            {refreshedText}
            {cacheAgeText ? ` · 경과 ${cacheAgeText}` : ""}
            {durationText ? ` · ${durationText}` : ""}
          </p>
          {cacheStale ? (
            <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-amber-900">
              Binance 원본 캐시가 {cacheAgeText ?? "기준 시간 초과"} 전 값입니다. 최신 진입 가능 여부는 개요 화면의 운영 상태를 기준으로 확인하세요.
            </p>
          ) : null}
          {cacheError ? (
            <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-amber-900">
              캐시 갱신 실패: {cacheError}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onRefreshCache}
          disabled={refreshingCache}
          className="inline-flex min-h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-800 transition hover:border-slate-400 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {refreshingCache ? "갱신 요청됨" : "캐시 갱신 요청"}
        </button>
      </div>
    </section>
  );
}

function AccountContent({
  cache,
  refreshingCache,
  cacheError,
  onRefreshCache,
}: {
  cache: AccountCachePayload;
  refreshingCache: boolean;
  cacheError: string | null;
  onRefreshCache: () => void;
}) {
  const payload = cache.payload;
  const summary = payload.summary;
  const cacheStale = isAccountCacheStale(cache);
  const cacheAgeMs = accountCacheAgeMs(cache.refreshed_at);
  const hasExchangeOpenOrders = cache.source === "cached_live";
  const displayedOpenOrders = hasExchangeOpenOrders ? payload.open_orders : [];
  const sourceLabel = cache.source === "cached_live" ? "Binance 원본 캐시" : "최근 로컬 동기화";
  const openOrdersSourceDescription = hasExchangeOpenOrders
    ? `${sourceLabel} 기준 미체결 주문 목록입니다.`
    : "Binance 원본 캐시가 준비된 경우에만 미체결 주문을 표시합니다.";
  const openOrdersMetricValue = hasExchangeOpenOrders
    ? formatDisplayValue(summary.open_orders, "open_orders")
    : "원본 캐시 필요";
  const openOrdersMetricHint = hasExchangeOpenOrders
    ? "거래소 원본에서 가져온 미체결 주문 수입니다."
    : "로컬 DB 주문 기록은 미체결 주문으로 표시하지 않습니다. 캐시 갱신 후 Binance 원본 주문만 표시합니다.";
  const exchangeSummaryRow: Record<string, unknown> = {
    data_source: sourceLabel,
    cache_status: cacheStale ? "원본 캐시 오래됨" : cacheStatusLabel(cache.status),
    cache_stale: cacheStale,
    cache_age_minutes: cacheAgeMs !== null ? Math.round(cacheAgeMs / 60000) : null,
    connected: summary.connected,
    testnet_enabled: summary.testnet_enabled,
    futures_enabled: summary.futures_enabled,
    available_balance: summary.available_balance,
    total_wallet_balance: summary.total_wallet_balance,
    total_unrealized_profit: summary.total_unrealized_profit,
    total_margin_balance: summary.total_margin_balance,
    open_positions: summary.open_positions,
    open_orders: summary.open_orders,
    exchange_update_time: summary.exchange_update_time,
    cache_refreshed_at: cache.refreshed_at,
    message: summary.message,
  };

  return (
    <>
      <SnapshotSourcePanel
        cache={cache}
        refreshingCache={refreshingCache}
        cacheError={cacheError}
        onRefreshCache={onRefreshCache}
      />

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-wrap gap-2">
          <StatusBadge
            tone={cacheStale ? "warn" : summary.connected ? "good" : "warn"}
            label={cacheStale ? "계정 캐시 지연" : summary.connected ? "계정 연결됨" : "계정 연결 확인 필요"}
          />
          <StatusBadge tone={summary.futures_enabled ? "good" : "warn"} label={summary.futures_enabled ? "선물 계정 사용" : "선물 계정 꺼짐"} />
          <StatusBadge tone={summary.testnet_enabled ? "neutral" : "good"} label={summary.testnet_enabled ? "테스트넷" : "실거래 계정"} />
        </div>

        <div className="mt-5 rounded-md border border-slate-200 bg-slate-50 p-5 text-sm leading-7 text-slate-700">
          거래 가능 여부의 최종 판단은{" "}
          <Link className="font-semibold text-blue-700 underline decoration-blue-300 underline-offset-4" href="/">
            개요 화면
          </Link>
          의 운영 상태를 기준으로 봅니다. 이 화면은 계정 원본 상태와 잔고 확인에 집중합니다.
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="사용 가능 잔고" value={formatDisplayValue(summary.available_balance, "available_balance")} />
          <MetricCard label="총 지갑 잔고" value={formatDisplayValue(summary.total_wallet_balance, "total_wallet_balance")} />
          <MetricCard label="미실현 손익" value={formatDisplayValue(summary.total_unrealized_profit, "total_unrealized_profit")} />
          <MetricCard label="마진 잔고" value={formatDisplayValue(summary.total_margin_balance, "total_margin_balance")} />
          <MetricCard label="열린 포지션" value={formatDisplayValue(summary.open_positions, "open_positions")} />
          <MetricCard label="미체결 주문" value={openOrdersMetricValue} hint={openOrdersMetricHint} />
          <MetricCard label="현재 안내" value={summary.connected ? "응답 정상" : "연결 확인 필요"} hint={summary.message} />
        </div>
      </section>

      <DataTable
        title="거래소 계정 요약"
        description={`${sourceLabel} 기준 계정 요약입니다.`}
        rows={[exchangeSummaryRow]}
      />
      <DataTable
        title="보유 자산"
        description={`${sourceLabel}에서 잔고가 있는 자산만 표시합니다.`}
        rows={payload.assets}
      />
      <div className="grid gap-6 xl:grid-cols-2">
        <DataTable title="포지션" description={`${sourceLabel} 기준 열린 포지션입니다.`} rows={payload.positions} />
        <DataTable
          title="미체결 주문"
          description={openOrdersSourceDescription}
          rows={displayedOpenOrders}
          emptyStateTitle={hasExchangeOpenOrders ? "거래소 미체결 주문이 없습니다." : "Binance 원본 캐시가 필요합니다."}
          emptyStateDescription={
            hasExchangeOpenOrders
              ? "현재 캐시된 Binance 원본 응답 기준으로 열린 주문이 없습니다."
              : "화면에서 로컬 테스트/이관 주문 로그를 미체결 주문으로 표시하지 않도록 차단했습니다."
          }
        />
      </div>
    </>
  );
}

export default function BinanceAccountPage() {
  const [cache, setCache] = useState<AccountCachePayload | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [cacheError, setCacheError] = useState<string | null>(null);
  const [refreshingCache, setRefreshingCache] = useState(false);
  const [loadingCache, setLoadingCache] = useState(false);
  const [autoRefreshRequested, setAutoRefreshRequested] = useState(false);

  const applyCacheResponse = useCallback((nextCache: AccountCachePayload, nextCacheError?: string | null) => {
    setCache(nextCache);
    setErrorMessage(null);
    setCacheError(nextCacheError ?? (nextCache.status === "failed" ? nextCache.last_error ?? nextCache.message : null));
    setRefreshingCache(isRefreshPending(nextCache.status));
  }, []);

  const loadAccountCache = useCallback(() => {
    setLoadingCache(true);
    fetchAccountCacheWithLocalFallback()
      .then(({ cache: nextCache, cacheError: nextCacheError }) => {
        applyCacheResponse(nextCache, nextCacheError);
      })
      .catch((error: unknown) => {
        setErrorMessage(errorMessageFromUnknown(error));
      })
      .finally(() => {
        setLoadingCache(false);
      });
  }, [applyCacheResponse]);

  useEffect(() => {
    loadAccountCache();
  }, [loadAccountCache]);

  useEffect(() => {
    if (!errorMessage || cache) {
      return;
    }
    const intervalId = window.setInterval(loadAccountCache, 3000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [cache, errorMessage, loadAccountCache]);

  useEffect(() => {
    if (!refreshingCache) {
      return;
    }

    let active = true;
    const intervalId = window.setInterval(() => {
      fetchAccountCacheWithLocalFallback()
        .then(({ cache: nextCache, cacheError: nextCacheError }) => {
          if (active) {
            applyCacheResponse(nextCache, nextCacheError);
          }
        })
        .catch((error: unknown) => {
          if (active) {
            setCacheError(errorMessageFromUnknown(error));
            setRefreshingCache(false);
          }
        });
    }, 2000);

    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [applyCacheResponse, refreshingCache]);

  const refreshAccountCache = useCallback(() => {
    setRefreshingCache(true);
    setCacheError(null);

    postJson<AccountCachePayload>("/api/binance/account/refresh")
      .then((nextCache) => {
        applyCacheResponse(nextCache);
      })
      .catch((error: unknown) => {
        setCacheError(errorMessageFromUnknown(error));
        setRefreshingCache(false);
      });
  }, [applyCacheResponse]);

  useEffect(() => {
    if (!cache || autoRefreshRequested || refreshingCache || cache.source === "cached_live") {
      return;
    }

    setAutoRefreshRequested(true);
    refreshAccountCache();
  }, [autoRefreshRequested, cache, refreshAccountCache, refreshingCache]);

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="계좌 상태"
        title="거래소 계정 / 자산 현황"
        description="Binance 원본 캐시 또는 최근 로컬 동기화 기준으로 잔고와 계정 원본 상태를 확인합니다. 최종 진입 가능 여부는 개요 화면의 운영 상태를 따릅니다."
      />

      {errorMessage ? (
        <ErrorPanel message={errorMessage} onRetry={loadAccountCache} retrying={loadingCache} />
      ) : cache ? (
        <AccountContent
          cache={cache}
          refreshingCache={refreshingCache}
          cacheError={cacheError}
          onRefreshCache={refreshAccountCache}
        />
      ) : (
        <LoadingPanel />
      )}
    </div>
  );
}
