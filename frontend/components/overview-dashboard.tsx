"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ModeChip } from "./mode-chip";
import { formatDisplayValue } from "../lib/ui-copy";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const refreshIntervalMs = 15000;

type ProtectionSummary = {
  symbol: string;
  side: string;
  status: string;
  protected: boolean;
  protective_order_count: number;
  has_stop_loss: boolean;
  has_take_profit: boolean;
  missing_components: string[];
  position_size: number;
};

type Overview = {
  mode: string;
  symbol: string;
  tracked_symbols: string[];
  timeframe: string;
  latest_price: number;
  latest_decision: Record<string, unknown> | null;
  latest_risk: Record<string, unknown> | null;
  open_positions: number;
  live_trading_enabled: boolean;
  live_execution_ready: boolean;
  trading_paused: boolean;
  daily_pnl: number;
  cumulative_pnl: number;
  blocked_reasons: string[];
  protected_positions: number;
  unprotected_positions: number;
  position_protection_summary: ProtectionSummary[];
};

type Row = Record<string, unknown>;

type Payload = {
  overview: Overview;
  alerts: Row[];
  decisions: Row[];
  orders: Row[];
  positions: Row[];
};

function SummaryCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
      <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">{label}</p>
      <p className="mt-3 text-3xl font-semibold text-ink">{value}</p>
      <p className="mt-2 text-sm leading-6 text-slate-600">{hint}</p>
    </div>
  );
}

function SmallList({
  title,
  description,
  rows,
  empty,
}: {
  title: string;
  description: string;
  rows: Row[];
  empty: string;
}) {
  return (
    <section className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
      <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">{description}</p>
      <h2 className="mt-2 text-xl font-semibold text-ink">{title}</h2>
      {rows.length === 0 ? (
        <div className="mt-4 rounded-2xl border border-dashed border-amber-300 px-4 py-6 text-sm text-slate-500">
          {empty}
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          {rows.map((row, index) => (
            <article key={`${title}-${index}`} className="rounded-2xl bg-canvas p-4">
              <div className="flex flex-wrap gap-2">
                {typeof row.status === "string" ? (
                  <span className="rounded-full border border-amber-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                    {formatDisplayValue(row.status, "status")}
                  </span>
                ) : null}
                {typeof row.symbol === "string" ? (
                  <span className="rounded-full border border-amber-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                    {String(row.symbol)}
                  </span>
                ) : null}
                {typeof row.severity === "string" ? (
                  <span className="rounded-full border border-amber-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                    {formatDisplayValue(row.severity, "severity")}
                  </span>
                ) : null}
              </div>
              <h3 className="mt-3 text-base font-semibold text-ink">
                {typeof row.title === "string"
                  ? row.title
                  : typeof row.summary === "string"
                    ? row.summary
                    : `항목 ${index + 1}`}
              </h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                {typeof row.message === "string"
                  ? row.message
                  : typeof row.explanation_short === "string"
                    ? row.explanation_short
                    : typeof row.summary === "string"
                      ? row.summary
                      : "세부 내용은 상세 화면에서 확인해 주세요."}
              </p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

async function fetchPayload(): Promise<Payload> {
  const [overview, alerts, decisions, orders, positions] = await Promise.all([
    fetch(`${apiBaseUrl}/api/dashboard/overview`, { cache: "no-store" }).then((response) => response.json()),
    fetch(`${apiBaseUrl}/api/alerts`, { cache: "no-store" }).then((response) => response.json()),
    fetch(`${apiBaseUrl}/api/decisions`, { cache: "no-store" }).then((response) => response.json()),
    fetch(`${apiBaseUrl}/api/orders?limit=10`, { cache: "no-store" }).then((response) => response.json()),
    fetch(`${apiBaseUrl}/api/positions`, { cache: "no-store" }).then((response) => response.json()),
  ]);

  return { overview, alerts, decisions, orders, positions };
}

export function OverviewDashboard({ initial }: { initial: Payload }) {
  const [payload, setPayload] = useState(initial);
  const [lastUpdated, setLastUpdated] = useState(() => new Date());
  const [refreshError, setRefreshError] = useState("");

  useEffect(() => {
    let active = true;

    const refresh = async () => {
      try {
        const next = await fetchPayload();
        if (!active) {
          return;
        }
        setPayload(next);
        setLastUpdated(new Date());
        setRefreshError("");
      } catch (error) {
        if (!active) {
          return;
        }
        setRefreshError(error instanceof Error ? error.message : "실시간 상태를 새로고침하지 못했습니다.");
      }
    };

    void refresh();
    const interval = window.setInterval(() => {
      void refresh();
    }, refreshIntervalMs);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const blockedSummary = useMemo(() => {
    if (payload.overview.blocked_reasons.length === 0) {
      return "현재 차단 사유 없이 거래 조건을 평가 중입니다.";
    }
    return payload.overview.blocked_reasons.map((item) => formatDisplayValue(item)).join(" / ");
  }, [payload.overview.blocked_reasons]);

  const latestDecisionText =
    typeof payload.overview.latest_decision?.explanation_short === "string"
      ? payload.overview.latest_decision.explanation_short
      : "최근 의사결정 데이터가 아직 없습니다.";

  const protectionSummaryText =
    payload.overview.unprotected_positions > 0
      ? `${payload.overview.unprotected_positions}개 포지션이 보호 확인 필요 상태입니다.`
      : payload.overview.open_positions > 0
        ? "열린 포지션이 모두 보호주문 기준으로 확인되었습니다."
        : "현재 열린 포지션이 없습니다.";

  return (
    <div className="space-y-6">
      <section className="overflow-hidden rounded-[2rem] border border-amber-200/70 bg-white/85 p-5 shadow-frame sm:rounded-[2.5rem] sm:p-7 lg:p-8">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.34em] text-slate-500">운영 개요</p>
            <h1 className="mt-3 font-display text-3xl leading-tight text-ink sm:text-4xl lg:text-5xl">
              실시간 거래 상태 대시보드
            </h1>
            <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-600 sm:text-base">
              거래 준비 상태, 최근 의사결정, 보호 상태, 주문 흐름을 자동 새로고침으로 확인합니다.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <ModeChip mode={payload.overview.mode} />
            <span
              className={`rounded-full px-4 py-2 text-sm font-semibold ${
                payload.overview.live_execution_ready
                  ? "border border-emerald-300 bg-emerald-50 text-emerald-700"
                  : "border border-amber-300 bg-amber-50 text-amber-800"
              }`}
            >
              {payload.overview.live_execution_ready ? "실거래 실행 가능" : "실거래 가드 모드"}
            </span>
            <span className="rounded-full border border-slate-200 bg-slate-50 px-4 py-2 text-sm text-slate-700">
              마지막 갱신 {lastUpdated.toLocaleTimeString("ko-KR", { hour12: false })}
            </span>
          </div>
        </div>
        <div className="mt-6 rounded-[1.75rem] bg-ink p-5 text-canvas">
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-canvas/70">현재 차단 / HOLD 사유</p>
          <p className="mt-2 text-base leading-7 sm:text-lg">{blockedSummary}</p>
          <p className="mt-3 text-sm text-canvas/70">{latestDecisionText}</p>
        </div>
        {refreshError ? <p className="mt-4 text-sm text-rose-700">{refreshError}</p> : null}
      </section>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <SummaryCard
          label="추적 심볼"
          value={String(payload.overview.tracked_symbols.length)}
          hint={payload.overview.tracked_symbols.join(", ")}
        />
        <SummaryCard
          label="오픈 포지션"
          value={formatDisplayValue(payload.overview.open_positions, "open_positions")}
          hint="현재 실거래 포지션 수"
        />
        <SummaryCard
          label="일일 손익"
          value={formatDisplayValue(payload.overview.daily_pnl, "daily_pnl")}
          hint="오늘 누적 손익 기준"
        />
        <SummaryCard
          label="누적 손익"
          value={formatDisplayValue(payload.overview.cumulative_pnl, "cumulative_pnl")}
          hint="전체 운영 누적 손익"
        />
        <SummaryCard
          label="보호 상태"
          value={`${payload.overview.protected_positions}/${payload.overview.open_positions}`}
          hint={protectionSummaryText}
        />
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">시장 상태</p>
          <div className="mt-4 space-y-3">
            <div className="rounded-2xl bg-canvas p-4">
              <p className="text-xs text-slate-500">기본 심볼</p>
              <p className="mt-2 text-lg font-semibold text-ink">{payload.overview.symbol}</p>
            </div>
            <div className="rounded-2xl bg-canvas p-4">
              <p className="text-xs text-slate-500">타임프레임</p>
              <p className="mt-2 text-lg font-semibold text-ink">{payload.overview.timeframe}</p>
            </div>
            <div className="rounded-2xl bg-canvas p-4">
              <p className="text-xs text-slate-500">현재가</p>
              <p className="mt-2 text-lg font-semibold text-ink">
                {formatDisplayValue(payload.overview.latest_price, "latest_price")}
              </p>
            </div>
          </div>
        </div>

        <div className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">빠른 이동</p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Link className="rounded-full bg-amber-100 px-4 py-2 text-sm font-semibold text-ink" href="/dashboard/orders">
              주문 / 로그 보기
            </Link>
            <Link className="rounded-full bg-amber-100 px-4 py-2 text-sm font-semibold text-ink" href="/dashboard/settings">
              운영 설정
            </Link>
            <Link className="rounded-full bg-amber-100 px-4 py-2 text-sm font-semibold text-ink" href="/dashboard/account">
              Binance 계정
            </Link>
            <Link className="rounded-full bg-amber-100 px-4 py-2 text-sm font-semibold text-ink" href="/dashboard/backlog">
              개선 백로그
            </Link>
          </div>
          <div className="mt-4 rounded-2xl bg-canvas p-4">
            <p className="text-xs text-slate-500">실거래 상태</p>
            <p className="mt-2 text-lg font-semibold text-ink">
              {payload.overview.live_trading_enabled ? "실거래 경로 사용 중" : "실거래 경로 비활성화"}
            </p>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {payload.overview.trading_paused
                ? "현재 거래가 일시중지 상태입니다."
                : "실거래 승인 창과 리스크 엔진 조건을 계속 확인 중입니다."}
            </p>
          </div>
        </div>

        <div className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">최근 포지션</p>
          {payload.positions.length === 0 ? (
            <div className="mt-4 rounded-2xl border border-dashed border-amber-300 px-4 py-6 text-sm text-slate-500">
              아직 열린 실거래 포지션이 없습니다.
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              {payload.positions.slice(0, 3).map((row, index) => (
                <div key={`position-${index}`} className="rounded-2xl bg-canvas p-4">
                  <div className="flex flex-wrap gap-2">
                    <span className="rounded-full border border-amber-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                      {String(row.symbol ?? "-")}
                    </span>
                    <span className="rounded-full border border-amber-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                      {String(row.side ?? "-")}
                    </span>
                    {typeof row.protected === "boolean" ? (
                      <span
                        className={`rounded-full px-3 py-1 text-xs font-semibold ${
                          row.protected ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"
                        }`}
                      >
                        {row.protected ? "보호됨" : "보호 확인 필요"}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-2 text-sm leading-6 text-slate-600">
                    수량 {formatDisplayValue(row.quantity, "quantity")} / 진입가{" "}
                    {formatDisplayValue(row.entry_price, "entry_price")}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">Protection</p>
        <h2 className="mt-2 text-xl font-semibold text-ink">포지션 보호 상태</h2>
        {payload.overview.position_protection_summary.length === 0 ? (
          <div className="mt-4 rounded-2xl border border-dashed border-amber-300 px-4 py-6 text-sm text-slate-500">
            현재 열린 포지션이 없어 보호 상태를 표시할 항목이 없습니다.
          </div>
        ) : (
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {payload.overview.position_protection_summary.map((item) => (
              <article key={`${item.symbol}-${item.side}`} className="rounded-[1.5rem] border border-amber-100 bg-canvas/80 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full border border-amber-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                    {item.symbol}
                  </span>
                  <span className="rounded-full border border-amber-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                    {formatDisplayValue(item.side, "side")}
                  </span>
                  <span
                    className={`rounded-full px-3 py-1 text-xs font-semibold ${
                      item.protected ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"
                    }`}
                  >
                    {item.protected ? "보호됨" : "보호 확인 필요"}
                  </span>
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="rounded-2xl bg-white px-4 py-3">
                    <p className="text-xs text-slate-500">포지션 수량</p>
                    <p className="mt-2 text-sm font-semibold text-ink">{formatDisplayValue(item.position_size, "quantity")}</p>
                  </div>
                  <div className="rounded-2xl bg-white px-4 py-3">
                    <p className="text-xs text-slate-500">보호주문 수</p>
                    <p className="mt-2 text-sm font-semibold text-ink">{item.protective_order_count}개</p>
                  </div>
                  <div className="rounded-2xl bg-white px-4 py-3">
                    <p className="text-xs text-slate-500">손절 주문</p>
                    <p className="mt-2 text-sm font-semibold text-ink">{item.has_stop_loss ? "확인됨" : "없음"}</p>
                  </div>
                  <div className="rounded-2xl bg-white px-4 py-3">
                    <p className="text-xs text-slate-500">익절 주문</p>
                    <p className="mt-2 text-sm font-semibold text-ink">{item.has_take_profit ? "확인됨" : "없음"}</p>
                  </div>
                </div>
                {!item.protected ? (
                  <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
                    누락 항목: {item.missing_components.length > 0 ? item.missing_components.join(", ") : "보호주문 확인 필요"}
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-6 xl:grid-cols-3">
        <SmallList
          title="최근 의사결정"
          description="Trading Decision"
          rows={payload.decisions.slice(0, 5)}
          empty="아직 저장된 의사결정이 없습니다."
        />
        <SmallList
          title="최근 주문"
          description="Orders"
          rows={payload.orders.slice(0, 5)}
          empty="아직 저장된 주문이 없습니다."
        />
        <SmallList
          title="최근 알림"
          description="Alerts"
          rows={payload.alerts.slice(0, 5)}
          empty="아직 발생한 알림이 없습니다."
        />
      </div>
    </div>
  );
}
