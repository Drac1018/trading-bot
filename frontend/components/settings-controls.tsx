"use client";

import { useMemo, useState, useTransition, type ReactNode } from "react";

import { AIUsagePanel } from "./ai-usage-panel";
import { formatDisplayValue } from "../lib/ui-copy";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const scheduleOptions = ["1h", "4h", "12h", "24h"] as const;
const symbolOptions = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"];
const monthlyLabels: Record<string, string> = {
  trading_decision: "거래 의사결정 AI",
  integration_planner: "통합 기획 AI",
  ui_ux: "UI/UX AI",
  product_improvement: "제품 개선 AI",
};

type AutoResumeResult = {
  attempted?: boolean;
  resumed?: boolean;
  allowed?: boolean;
  status?: string;
  reason_code?: string | null;
  pause_origin?: string | null;
  pause_severity?: string | null;
  pause_recovery_class?: string | null;
  trigger_source?: string;
  blockers?: string[];
  symbol_blockers?: Record<string, string[]>;
  blocker_details?: Array<Record<string, unknown>>;
  evaluated_symbols?: string[];
  protective_orders?: Record<string, string>;
  market_data_status?: Record<string, string>;
  sync_status?: Record<string, string>;
  approval_state?: string;
  approval_detail?: Record<string, unknown>;
  auto_resume_after?: string | null;
};

type ProtectionSyncState = {
  status?: string;
  protected?: boolean;
  protective_order_count?: number;
  has_stop_loss?: boolean;
  has_take_profit?: boolean;
  missing_components?: string[];
};

export type SettingsPayload = {
  id: number;
  mode: string;
  operating_state: string;
  protection_recovery_status: string;
  protection_recovery_active: boolean;
  protection_recovery_failure_count: number;
  missing_protection_symbols: string[];
  missing_protection_items: Record<string, string[]>;
  live_trading_enabled: boolean;
  live_trading_env_enabled: boolean;
  manual_live_approval: boolean;
  live_execution_armed: boolean;
  live_execution_armed_until: string | null;
  live_approval_window_minutes: number;
  live_execution_ready: boolean;
  trading_paused: boolean;
  pause_reason_code: string | null;
  pause_origin: string | null;
  pause_reason_detail: Record<string, unknown>;
  pause_triggered_at: string | null;
  auto_resume_after: string | null;
  auto_resume_whitelisted: boolean;
  auto_resume_eligible: boolean;
  auto_resume_status: string;
  auto_resume_last_blockers: string[];
  pause_severity: string | null;
  pause_recovery_class: string | null;
  default_symbol: string;
  tracked_symbols: string[];
  default_timeframe: string;
  schedule_windows: string[];
  max_leverage: number;
  max_risk_per_trade: number;
  max_daily_loss: number;
  max_consecutive_losses: number;
  stale_market_seconds: number;
  slippage_threshold_pct: number;
  starting_equity: number;
  ai_enabled: boolean;
  ai_provider: "openai" | "mock";
  ai_model: string;
  ai_call_interval_minutes: number;
  decision_cycle_interval_minutes: number;
  ai_max_input_candles: number;
  ai_temperature: number;
  binance_market_data_enabled: boolean;
  binance_testnet_enabled: boolean;
  binance_futures_enabled: boolean;
  openai_api_key_configured: boolean;
  binance_api_key_configured: boolean;
  binance_api_secret_configured: boolean;
  estimated_monthly_ai_calls: number;
  estimated_monthly_ai_calls_breakdown: Record<string, number>;
  projected_monthly_ai_calls_if_enabled: number;
  projected_monthly_ai_calls_breakdown_if_enabled: Record<string, number>;
  recent_ai_calls_24h: number;
  recent_ai_calls_7d: number;
  recent_ai_successes_24h: number;
  recent_ai_successes_7d: number;
  recent_ai_failures_24h: number;
  recent_ai_failures_7d: number;
  recent_ai_tokens_24h: Record<string, number>;
  recent_ai_tokens_7d: Record<string, number>;
  recent_ai_role_calls_24h: Record<string, number>;
  recent_ai_role_calls_7d: Record<string, number>;
  recent_ai_role_failures_24h: Record<string, number>;
  recent_ai_role_failures_7d: Record<string, number>;
  recent_ai_failure_reasons: string[];
  observed_monthly_ai_calls_projection: number;
  observed_monthly_ai_calls_projection_breakdown: Record<string, number>;
  manual_ai_guard_minutes: number;
};

type ConnectionTestResult = { ok: boolean; provider: string; message: string; details: Record<string, unknown> };

type LiveSyncResult = {
  symbols?: string[];
  synced_orders?: number;
  synced_positions?: number;
  equity?: number;
  operating_state?: string;
  protection_recovery_status?: string;
  protection_recovery_active?: boolean;
  missing_protection_symbols?: string[];
  missing_protection_items?: Record<string, string[]>;
  symbol_protection_state?: Record<string, ProtectionSyncState>;
  unprotected_positions?: string[];
  emergency_actions_taken?: Array<Record<string, unknown>>;
  auto_resume_precheck?: AutoResumeResult | null;
  auto_resume_postcheck?: AutoResumeResult | null;
  auto_resume?: AutoResumeResult | null;
};

type FormState = Omit<
  SettingsPayload,
  | "id" | "mode" | "operating_state" | "protection_recovery_status" | "protection_recovery_active"
  | "protection_recovery_failure_count" | "missing_protection_symbols" | "missing_protection_items"
  | "live_trading_env_enabled" | "live_execution_armed" | "live_execution_armed_until" | "live_execution_ready"
  | "trading_paused" | "pause_reason_code" | "pause_origin" | "pause_reason_detail" | "pause_triggered_at" | "auto_resume_after"
  | "auto_resume_whitelisted" | "auto_resume_eligible" | "auto_resume_status" | "auto_resume_last_blockers" | "pause_severity"
  | "pause_recovery_class" | "openai_api_key_configured" | "binance_api_key_configured" | "binance_api_secret_configured"
  | "estimated_monthly_ai_calls" | "estimated_monthly_ai_calls_breakdown" | "projected_monthly_ai_calls_if_enabled"
  | "projected_monthly_ai_calls_breakdown_if_enabled" | "recent_ai_calls_24h" | "recent_ai_calls_7d" | "recent_ai_successes_24h"
  | "recent_ai_successes_7d" | "recent_ai_failures_24h" | "recent_ai_failures_7d" | "recent_ai_tokens_24h"
  | "recent_ai_tokens_7d" | "recent_ai_role_calls_24h" | "recent_ai_role_calls_7d" | "recent_ai_role_failures_24h"
  | "recent_ai_role_failures_7d" | "recent_ai_failure_reasons" | "observed_monthly_ai_calls_projection"
  | "observed_monthly_ai_calls_projection_breakdown" | "manual_ai_guard_minutes"
> & {
  openai_api_key: string;
  binance_api_key: string;
  binance_api_secret: string;
  custom_symbols: string;
  clear_openai_api_key: boolean;
  clear_binance_api_key: boolean;
  clear_binance_api_secret: boolean;
};

const inputClass = "w-full rounded-2xl border border-amber-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-amber-400";

class ApiRequestError extends Error {
  payload?: unknown;

  constructor(message: string, payload?: unknown) {
    super(message);
    this.name = "ApiRequestError";
    this.payload = payload;
  }
}

function uniqueSymbols(values: string[]) { return Array.from(new Set(values.map((item) => item.trim().toUpperCase()).filter(Boolean))); }

function toFormState(initial: SettingsPayload): FormState {
  return {
    live_trading_enabled: initial.live_trading_enabled,
    manual_live_approval: initial.manual_live_approval,
    live_approval_window_minutes: initial.live_approval_window_minutes,
    default_symbol: initial.default_symbol,
    tracked_symbols: initial.tracked_symbols,
    default_timeframe: initial.default_timeframe,
    schedule_windows: initial.schedule_windows,
    max_leverage: initial.max_leverage,
    max_risk_per_trade: initial.max_risk_per_trade,
    max_daily_loss: initial.max_daily_loss,
    max_consecutive_losses: initial.max_consecutive_losses,
    stale_market_seconds: initial.stale_market_seconds,
    slippage_threshold_pct: initial.slippage_threshold_pct,
    starting_equity: initial.starting_equity,
    ai_enabled: initial.ai_enabled,
    ai_provider: initial.ai_provider,
    ai_model: initial.ai_model,
    ai_call_interval_minutes: initial.ai_call_interval_minutes,
    decision_cycle_interval_minutes: initial.decision_cycle_interval_minutes,
    ai_max_input_candles: initial.ai_max_input_candles,
    ai_temperature: initial.ai_temperature,
    binance_market_data_enabled: initial.binance_market_data_enabled,
    binance_testnet_enabled: initial.binance_testnet_enabled,
    binance_futures_enabled: initial.binance_futures_enabled,
    openai_api_key: "",
    binance_api_key: "",
    binance_api_secret: "",
    custom_symbols: initial.tracked_symbols.filter((symbol) => !symbolOptions.includes(symbol)).join(", "),
    clear_openai_api_key: false,
    clear_binance_api_key: false,
    clear_binance_api_secret: false,
  };
}

function StatusPill({ tone = "neutral", children }: { tone?: "neutral" | "good" | "warn" | "danger"; children: ReactNode }) {
  const className = {
    neutral: "border border-slate-200 bg-slate-50 text-slate-700",
    good: "border border-emerald-200 bg-emerald-50 text-emerald-700",
    warn: "border border-amber-200 bg-amber-50 text-amber-800",
    danger: "border border-rose-200 bg-rose-50 text-rose-800",
  }[tone];
  return <span className={`rounded-full px-3 py-1 text-xs font-semibold ${className}`}>{children}</span>;
}

function Field(props: { label: string; hint?: string; children: ReactNode }) {
  return <label className="flex flex-col gap-2"><span className="text-sm font-semibold text-slate-900">{props.label}</span>{props.children}{props.hint ? <span className="text-xs text-slate-500">{props.hint}</span> : null}</label>;
}

function Toggle(props: { checked: boolean; label: string; onChange: (value: boolean) => void }) {
  return <label className="flex items-center gap-3 rounded-2xl border border-amber-200 bg-white px-4 py-3"><input checked={props.checked} onChange={(event) => props.onChange(event.target.checked)} type="checkbox" /><span className="text-sm font-medium text-slate-900">{props.label}</span></label>;
}

function MetricCard({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "dark" | "warm" }) {
  if (tone === "dark") return <div className="rounded-[1.5rem] bg-slate-950 px-4 py-4 text-white"><p className="text-xs uppercase tracking-[0.24em] text-white/60">{label}</p><p className="mt-2 text-xl font-semibold">{value}</p></div>;
  if (tone === "warm") return <div className="rounded-[1.5rem] border border-amber-200 bg-amber-50 px-4 py-4"><p className="text-xs uppercase tracking-[0.24em] text-amber-900">{label}</p><p className="mt-2 text-xl font-semibold text-slate-900">{value}</p></div>;
  return <div className="rounded-[1.5rem] border border-slate-200 bg-white px-4 py-4"><p className="text-xs uppercase tracking-[0.24em] text-slate-500">{label}</p><p className="mt-2 text-xl font-semibold text-slate-900">{value}</p></div>;
}

function ResultCard({ title, result }: { title: string; result: ConnectionTestResult | null }) {
  if (!result) return null;
  return <div className={`rounded-2xl px-4 py-3 text-sm ${result.ok ? "bg-emerald-50 text-emerald-900" : "bg-rose-50 text-rose-900"}`}><p className="font-semibold">{title}</p><p className="mt-1">{result.message}</p><pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(result.details, null, 2)}</pre></div>;
}

function stringifyPauseDetail(detail: Record<string, unknown>) {
  if (!detail || Object.keys(detail).length === 0) return "-";
  if (typeof detail.detail === "string" && detail.detail.trim()) return detail.detail;
  if (typeof detail.error === "string" && detail.error.trim()) return detail.error;
  if (typeof detail.symbol === "string" && detail.symbol.trim()) return `${detail.symbol} 관련 상태 점검 필요`;
  return JSON.stringify(detail, null, 2);
}

function formatCodeList(values: string[] | null | undefined, empty = "-") {
  if (!values || values.length === 0) return empty;
  return values.map((item) => formatDisplayValue(item)).join(", ");
}

function renderMissingProtectionItems(
  missingItems: Record<string, string[]> | null | undefined,
  empty = "누락된 보호 항목 없음",
) {
  const entries = Object.entries(missingItems ?? {}).filter(([, values]) => values.length > 0);
  if (entries.length === 0) return empty;
  return entries
    .map(([symbol, values]) => `${symbol}: ${values.map((item) => formatDisplayValue(item)).join(", ")}`)
    .join(" / ");
}

function AutoResumeStatusCard({ result, title }: { result: AutoResumeResult | null | undefined; title: string }) {
  if (!result) return null;
  const blockers = result.blockers ?? [];
  const symbolBlockers = Object.entries(result.symbol_blockers ?? {});
  const protectiveOrders = Object.entries(result.protective_orders ?? {});
  const marketStatus = Object.entries(result.market_data_status ?? {});
  const syncStatus = Object.entries(result.sync_status ?? {});
  return (
    <div className="rounded-2xl border border-amber-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold text-slate-900">{title}</p>
        <StatusPill tone={result.resumed ? "good" : result.allowed ? "good" : result.status === "blocked" ? "danger" : "warn"}>{formatDisplayValue(result.status, "auto_resume_status")}</StatusPill>
        {result.trigger_source ? <StatusPill>{result.trigger_source}</StatusPill> : null}
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">중지 사유</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(result.reason_code, "pause_reason_code")}</p></div>
        <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">승인 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(result.approval_state, "approval_state")}</p></div>
      </div>
      {blockers.length > 0 ? <div className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"><p className="font-semibold">자동 복구 차단 사유</p><p className="mt-2">{blockers.map((item) => formatDisplayValue(item)).join(" / ")}</p></div> : null}
      {symbolBlockers.length > 0 ? <div className="mt-3 grid gap-3 md:grid-cols-2">{symbolBlockers.map(([symbol, values]) => <div key={`blocker-${symbol}`} className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">{symbol} 차단 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{values.length > 0 ? values.map((item) => formatDisplayValue(item)).join(" / ") : "차단 없음"}</p></div>)}</div> : null}
      {(protectiveOrders.length > 0 || marketStatus.length > 0 || syncStatus.length > 0) ? <div className="mt-3 grid gap-3 md:grid-cols-3">
        {protectiveOrders.map(([symbol, status]) => <div key={`protect-${symbol}`} className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">{symbol} 보호 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(status, "status")}</p></div>)}
        {marketStatus.map(([symbol, status]) => <div key={`market-${symbol}`} className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">{symbol} 시장 데이터</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(status, "status")}</p></div>)}
        {syncStatus.map(([symbol, status]) => <div key={`sync-${symbol}`} className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">{symbol} 동기화 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(status, "status")}</p></div>)}
      </div> : null}
    </div>
  );
}

function LiveSyncPanel({ result }: { result: LiveSyncResult | null }) {
  if (!result) return null;
  const protectionEntries = Object.entries(result.symbol_protection_state ?? {});
  const missingProtectionText = renderMissingProtectionItems(result.missing_protection_items);
  return (
    <div className="mt-3 space-y-3 rounded-2xl border border-amber-200 bg-white p-4">
      <div className="flex flex-wrap gap-2">
        <StatusPill>동기화 심볼 {result.symbols?.join(", ") ?? "-"}</StatusPill>
        <StatusPill>주문 {result.synced_orders ?? 0}건</StatusPill>
        <StatusPill>포지션 {result.synced_positions ?? 0}건</StatusPill>
        {typeof result.equity === "number" ? <StatusPill>Equity {formatDisplayValue(result.equity, "equity")}</StatusPill> : null}
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-2xl bg-canvas px-4 py-3">
          <p className="text-xs text-slate-500">운영 상태</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(result.operating_state, "operating_state")}</p>
        </div>
        <div className="rounded-2xl bg-canvas px-4 py-3">
          <p className="text-xs text-slate-500">보호 복구 상태</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(result.protection_recovery_status, "protection_recovery_status")}</p>
        </div>
        <div className="rounded-2xl bg-canvas px-4 py-3">
          <p className="text-xs text-slate-500">보호 복구 진행 여부</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(result.protection_recovery_active, "protection_recovery_active")}</p>
        </div>
        <div className="rounded-2xl bg-canvas px-4 py-3">
          <p className="text-xs text-slate-500">누락 보호 심볼</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{formatCodeList(result.missing_protection_symbols, "-")}</p>
        </div>
      </div>
      <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3">
        <p className="text-xs text-slate-500">누락 보호 항목</p>
        <p className="mt-2 text-sm font-semibold text-slate-900">{missingProtectionText}</p>
      </div>
      {protectionEntries.length > 0 ? <div className="grid gap-3 md:grid-cols-2">
        {protectionEntries.map(([symbol, state]) => <div key={symbol} className="rounded-2xl bg-canvas px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill>{symbol}</StatusPill>
            <StatusPill tone={state.protected ? "good" : "danger"}>{state.protected ? "보호됨" : "보호 확인 필요"}</StatusPill>
          </div>
          <p className="mt-3 text-sm text-slate-700">상태: {formatDisplayValue(state.status, "status")} / 보호 주문 {state.protective_order_count ?? 0}개</p>
          <p className="mt-2 text-sm text-slate-600">손절 {formatDisplayValue(state.has_stop_loss, "has_stop_loss")} / 익절 {formatDisplayValue(state.has_take_profit, "has_take_profit")}</p>
          {!state.protected && (state.missing_components?.length ?? 0) > 0 ? <p className="mt-2 text-sm text-rose-700">누락: {state.missing_components?.join(", ")}</p> : null}
        </div>)}
      </div> : <div className="rounded-2xl border border-dashed border-amber-300 px-4 py-5 text-sm text-slate-500">이번 동기화 응답에는 심볼별 보호 상태가 포함되지 않았습니다.</div>}
      {(result.unprotected_positions?.length ?? 0) > 0 ? <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">무보호 포지션 감지: {result.unprotected_positions?.join(", ")}</div> : null}
      {(result.emergency_actions_taken?.length ?? 0) > 0 ? <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"><p className="font-semibold">비상 조치 발생</p><pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(result.emergency_actions_taken, null, 2)}</pre></div> : null}
      <AutoResumeStatusCard result={result.auto_resume_precheck} title="자동 복구 사전 점검" />
      <AutoResumeStatusCard result={result.auto_resume_postcheck} title="자동 복구 사후 점검" />
      {!result.auto_resume_precheck && !result.auto_resume_postcheck ? <AutoResumeStatusCard result={result.auto_resume} title="자동 복구 평가" /> : null}
    </div>
  );
}

function OperationalStatusPanel({ state }: { state: SettingsPayload }) {
  const blockerText = state.auto_resume_last_blockers.length > 0 ? state.auto_resume_last_blockers.map((item) => formatDisplayValue(item)).join(" / ") : "차단 사유 없음";
  const missingProtectionText = renderMissingProtectionItems(state.missing_protection_items);
  return (
    <section className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-semibold text-slate-900">운영 상태</h3>
        <StatusPill tone={state.trading_paused ? "danger" : state.live_execution_ready ? "good" : "warn"}>{state.trading_paused ? "거래 중지" : state.live_execution_ready ? "실행 가능" : "가드 유지"}</StatusPill>
        <StatusPill tone={state.operating_state === "TRADABLE" ? "good" : state.operating_state === "PAUSED" ? "danger" : "warn"}>
          운영 상태 {formatDisplayValue(state.operating_state, "operating_state")}
        </StatusPill>
        {state.auto_resume_status !== "not_paused" ? <StatusPill tone={state.auto_resume_status === "resumed" ? "good" : state.auto_resume_status === "blocked" ? "danger" : "warn"}>자동 복구 {formatDisplayValue(state.auto_resume_status, "auto_resume_status")}</StatusPill> : null}
      </div>
      <p className="mt-3 text-sm leading-7 text-slate-600">현재 중지는 신규 진입을 막는 운영 pause입니다. 다만 기존 포지션의 보호 주문 유지, 축소, 비상 청산 같은 생존 경로는 계속 허용될 수 있습니다.</p>
      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">운영 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.operating_state, "operating_state")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">보호 복구 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.protection_recovery_status, "protection_recovery_status")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">보호 복구 진행 여부</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.protection_recovery_active, "protection_recovery_active")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">거래 중지 여부</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.trading_paused, "trading_paused")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">중지 사유</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.pause_reason_code, "pause_reason_code")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">중지 발생 주체</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.pause_origin, "pause_origin")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">심각도</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.pause_severity, "pause_severity")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">복구 분류</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.pause_recovery_class, "pause_recovery_class")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">중지 발생 시각</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.pause_triggered_at, "pause_triggered_at")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">보호 복구 실패 누적</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.protection_recovery_failure_count, "protection_recovery_failure_count")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">누락 보호 심볼</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatCodeList(state.missing_protection_symbols, "없음")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">자동 복구 정책 대상</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.auto_resume_whitelisted, "auto_resume_whitelisted")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">자동 복구 가능</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.auto_resume_eligible, "auto_resume_eligible")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">자동 복구 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.auto_resume_status, "auto_resume_status")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3 md:col-span-2 xl:col-span-3"><p className="text-xs text-slate-500">자동 복구 차단 사유</p><p className="mt-2 text-sm font-semibold text-slate-900">{blockerText}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3 md:col-span-2 xl:col-span-3"><p className="text-xs text-slate-500">누락 보호 항목</p><p className="mt-2 text-sm font-semibold text-slate-900">{missingProtectionText}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3 md:col-span-2 xl:col-span-3"><p className="text-xs text-slate-500">중지 상세</p><p className="mt-2 text-sm leading-6 text-slate-900">{stringifyPauseDetail(state.pause_reason_detail)}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3 md:col-span-2 xl:col-span-3"><p className="text-xs text-slate-500">다음 자동 복구 재시도 시각</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.auto_resume_after, "auto_resume_after")}</p></div>
      </div>
    </section>
  );
}

export function SettingsControls({ initial }: { initial: SettingsPayload }) {
  const [state, setState] = useState(initial);
  const [form, setForm] = useState<FormState>(() => toFormState(initial));
  const [message, setMessage] = useState("");
  const [openAiResult, setOpenAiResult] = useState<ConnectionTestResult | null>(null);
  const [binanceResult, setBinanceResult] = useState<ConnectionTestResult | null>(null);
  const [liveOrderResult, setLiveOrderResult] = useState<ConnectionTestResult | null>(null);
  const [liveSyncResult, setLiveSyncResult] = useState<LiveSyncResult | null>(null);
  const [isPending, startTransition] = useTransition();

  const projectedBreakdown = useMemo(() => Object.entries(state.projected_monthly_ai_calls_breakdown_if_enabled), [state.projected_monthly_ai_calls_breakdown_if_enabled]);
  const mergedSymbols = useMemo(() => uniqueSymbols([...form.tracked_symbols, ...form.custom_symbols.split(",")]), [form.custom_symbols, form.tracked_symbols]);

  const requestJson = async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const response = await fetch(`${apiBaseUrl}${path}`, init);
    const contentType = response.headers.get("content-type") ?? "";
    const body = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const message = typeof body === "string" ? body : JSON.stringify(body);
      throw new ApiRequestError(message || "요청 처리에 실패했습니다.", body);
    }
    return body as T;
  };

  const syncSettings = (next: SettingsPayload) => { setState(next); setForm(toFormState(next)); };
  const updateField = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((current) => ({ ...current, [key]: value }));

  const payload = {
    live_trading_enabled: form.live_trading_enabled,
    manual_live_approval: form.manual_live_approval,
    live_approval_window_minutes: form.live_approval_window_minutes,
    default_symbol: form.default_symbol,
    tracked_symbols: mergedSymbols.length > 0 ? mergedSymbols : [form.default_symbol],
    default_timeframe: form.default_timeframe,
    schedule_windows: form.schedule_windows,
    max_leverage: form.max_leverage,
    max_risk_per_trade: form.max_risk_per_trade,
    max_daily_loss: form.max_daily_loss,
    max_consecutive_losses: form.max_consecutive_losses,
    stale_market_seconds: form.stale_market_seconds,
    slippage_threshold_pct: form.slippage_threshold_pct,
    starting_equity: form.starting_equity,
    ai_enabled: form.ai_enabled,
    ai_provider: form.ai_provider,
    ai_model: form.ai_model,
    ai_call_interval_minutes: form.ai_call_interval_minutes,
    decision_cycle_interval_minutes: form.decision_cycle_interval_minutes,
    ai_max_input_candles: form.ai_max_input_candles,
    ai_temperature: form.ai_temperature,
    binance_market_data_enabled: form.binance_market_data_enabled,
    binance_testnet_enabled: form.binance_testnet_enabled,
    binance_futures_enabled: form.binance_futures_enabled,
    openai_api_key: form.openai_api_key || null,
    binance_api_key: form.binance_api_key || null,
    binance_api_secret: form.binance_api_secret || null,
    clear_openai_api_key: form.clear_openai_api_key,
    clear_binance_api_key: form.clear_binance_api_key,
    clear_binance_api_secret: form.clear_binance_api_secret,
  };

  const save = () => {
    startTransition(() => {
      void requestJson<SettingsPayload>("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        .then((result) => { syncSettings(result); setMessage("설정을 저장했습니다."); })
        .catch((error: unknown) => { setMessage(error instanceof Error ? error.message : "설정 저장에 실패했습니다."); });
    });
  };

  const runPost = (path: string, successMessage: string, onSuccess?: (data: any) => void, body?: object) => {
    startTransition(() => {
      void requestJson(path, { method: "POST", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined })
        .then((result) => { onSuccess?.(result); setMessage(successMessage); })
        .catch((error: unknown) => { setMessage(error instanceof Error ? error.message : "요청 처리에 실패했습니다."); });
    });
  };

  return (
    <div className="space-y-5 rounded-[2rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">실거래 설정</p>
          <h2 className="mt-2 text-2xl font-semibold text-slate-900 sm:text-3xl">심볼, AI, 거래소 운영 제어</h2>
          <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-600">이 화면에서는 실거래 사용 여부, 수동 승인 정책, AI 호출 주기, Binance 연결, 자동 복구 상태를 함께 확인합니다.</p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <StatusPill tone={state.openai_api_key_configured ? "good" : "warn"}>OpenAI: {state.openai_api_key_configured ? "설정됨" : "미설정"}</StatusPill>
          <StatusPill tone={state.binance_api_key_configured ? "good" : "warn"}>Binance Key: {state.binance_api_key_configured ? "설정됨" : "미설정"}</StatusPill>
          <StatusPill tone={state.binance_api_secret_configured ? "good" : "warn"}>Binance Secret: {state.binance_api_secret_configured ? "설정됨" : "미설정"}</StatusPill>
          <StatusPill tone={state.live_execution_ready ? "good" : state.trading_paused ? "danger" : "warn"}>실거래 상태: {state.live_execution_ready ? "실행 가능" : state.trading_paused ? "중지" : "가드 유지"}</StatusPill>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-6">
        <MetricCard label="현재 모드" value={formatDisplayValue(state.mode, "mode")} tone="dark" />
        <MetricCard label="현재 월간 AI 호출" value={`${state.estimated_monthly_ai_calls.toLocaleString("ko-KR")}회`} tone="warm" />
        <MetricCard label="AI 활성 시 예상" value={`${state.projected_monthly_ai_calls_if_enabled.toLocaleString("ko-KR")}회`} />
        {projectedBreakdown.map(([key, value]) => <MetricCard key={key} label={monthlyLabels[key] ?? key} value={`${value.toLocaleString("ko-KR")}회`} />)}
      </div>

      <OperationalStatusPanel state={state} />
      <AIUsagePanel settings={state} />

      <section className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">실거래 제어</h3>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Field label="승인 유지 시간(분)"><input className={inputClass} min={1} max={240} type="number" value={form.live_approval_window_minutes} onChange={(event) => updateField("live_approval_window_minutes", Number(event.target.value))} /></Field>
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">환경 게이트</p><p className="mt-2 text-sm font-semibold text-slate-900">{state.live_trading_env_enabled ? "활성화" : "비활성화"}</p></div>
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">승인 창</p><p className="mt-2 text-sm font-semibold text-slate-900">{state.live_execution_armed ? `열림 (${formatDisplayValue(state.live_execution_armed_until, "live_execution_armed_until")})` : "닫힘"}</p></div>
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">실행 준비</p><p className="mt-2 text-sm font-semibold text-slate-900">{state.live_execution_ready ? "준비 완료" : "추가 확인 필요"}</p></div>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <Toggle checked={form.live_trading_enabled} label="실거래 경로 사용" onChange={(value) => updateField("live_trading_enabled", value)} />
            <Toggle checked={form.manual_live_approval} label="수동 승인 정책 사용" onChange={(value) => updateField("manual_live_approval", value)} />
          </div>
          <p className="mt-4 text-sm leading-6 text-slate-600">즉시 중지는 신규 진입을 막는 운영 pause입니다. 기존 포지션의 보호 주문 유지, 축소, 비상 청산은 계속 허용될 수 있습니다.</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button className="rounded-full bg-rose-600 px-4 py-2 text-sm font-semibold text-white" onClick={() => runPost("/api/settings/pause", "거래를 일시중지했습니다.", syncSettings)} type="button">즉시 중지</button>
            <button className="rounded-full bg-emerald-600 px-4 py-2 text-sm font-semibold text-white" onClick={() => runPost("/api/settings/resume", "거래 일시중지를 해제했습니다.", syncSettings)} type="button">중지 해제</button>
            <button className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white" onClick={() => runPost("/api/settings/live/arm", "실거래 승인 창을 열었습니다.", syncSettings, { minutes: form.live_approval_window_minutes })} type="button">실거래 승인</button>
            <button className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700" onClick={() => runPost("/api/settings/live/disarm", "실거래 승인 창을 닫았습니다.", syncSettings)} type="button">승인 해제</button>
            <button
              className="rounded-full border border-amber-200 px-4 py-2 text-sm font-semibold text-slate-700"
              onClick={async () => {
                try {
                  setLiveSyncResult(await requestJson<LiveSyncResult>(`/api/live/sync?symbol=${encodeURIComponent(form.default_symbol)}`, { method: "POST" }));
                  setMessage("거래소 상태와 보호 주문 상태를 동기화했습니다.");
                } catch (error: unknown) {
                  if (error instanceof ApiRequestError && error.payload && typeof error.payload === "object") {
                    const detail = "detail" in error.payload ? (error.payload as { detail?: unknown }).detail : error.payload;
                    if (detail && typeof detail === "object") {
                      setLiveSyncResult(detail as LiveSyncResult);
                    }
                  }
                  setMessage(error instanceof Error ? error.message : "거래소 동기화에 실패했습니다.");
                }
              }}
              type="button"
            >
              거래소 동기화
            </button>
            <button className="rounded-full border border-amber-200 px-4 py-2 text-sm font-semibold text-slate-700" onClick={() => runPost("/api/settings/test/binance/live-order", "실주문 사전 점검을 마쳤습니다.", (result) => setLiveOrderResult({ ok: true, provider: "binance-live-test", message: "실주문 사전 점검이 성공했습니다.", details: result }), { symbol: form.default_symbol, side: "BUY" })} type="button">실주문 사전 점검</button>
          </div>
          <LiveSyncPanel result={liveSyncResult} />
          <ResultCard title="실주문 사전 점검" result={liveOrderResult} />
        </div>

        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">심볼 / 리스크</h3>
          <div className="mt-4 space-y-4">
            <Field label="기본 심볼"><select className={inputClass} value={form.default_symbol} onChange={(event) => updateField("default_symbol", event.target.value)}>{mergedSymbols.map((symbol) => <option key={symbol} value={symbol}>{symbol}</option>)}</select></Field>
            <div><p className="text-sm font-semibold text-slate-900">빠른 심볼 선택</p><div className="mt-3 flex flex-wrap gap-2">{symbolOptions.map((symbol) => { const active = form.tracked_symbols.includes(symbol); return <button key={symbol} className={`rounded-full px-4 py-2 text-sm font-semibold ${active ? "bg-amber-400 text-slate-900" : "border border-amber-200 bg-white text-slate-700"}`} onClick={() => updateField("tracked_symbols", active ? form.tracked_symbols.filter((item) => item !== symbol) : uniqueSymbols([...form.tracked_symbols, symbol]))} type="button">{symbol}</button>; })}</div></div>
            <Field label="추가 심볼" hint="쉼표로 구분해 직접 입력할 수 있습니다."><input className={inputClass} value={form.custom_symbols} onChange={(event) => updateField("custom_symbols", event.target.value.toUpperCase())} placeholder="APTUSDT, AVAXUSDT" /></Field>
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">현재 선택 심볼</p><p className="mt-2 text-sm font-semibold text-slate-900">{mergedSymbols.join(", ")}</p></div>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="타임프레임"><input className={inputClass} value={form.default_timeframe} onChange={(event) => updateField("default_timeframe", event.target.value)} /></Field>
              <Field label="최대 레버리지" hint="표시 상한은 5x, 런타임에서는 심볼군 하드 캡이 추가로 적용됩니다."><input className={inputClass} type="number" min={1} max={5} step="0.1" value={form.max_leverage} onChange={(event) => updateField("max_leverage", Number(event.target.value))} /></Field>
              <Field label="거래당 최대 리스크" hint="하드 상한은 2%입니다."><input className={inputClass} type="number" min={0.001} max={0.02} step="0.001" value={form.max_risk_per_trade} onChange={(event) => updateField("max_risk_per_trade", Number(event.target.value))} /></Field>
              <Field label="일일 손실 한도" hint="하드 상한은 5%입니다."><input className={inputClass} type="number" min={0.001} max={0.05} step="0.001" value={form.max_daily_loss} onChange={(event) => updateField("max_daily_loss", Number(event.target.value))} /></Field>
              <Field label="연속 손실 한도"><input className={inputClass} type="number" min={1} max={20} value={form.max_consecutive_losses} onChange={(event) => updateField("max_consecutive_losses", Number(event.target.value))} /></Field>
              <Field label="데이터 유효 시간(초)"><input className={inputClass} type="number" min={30} value={form.stale_market_seconds} onChange={(event) => updateField("stale_market_seconds", Number(event.target.value))} /></Field>
              <Field label="슬리피지 한도"><input className={inputClass} type="number" min={0.0001} max={0.1} step="0.0001" value={form.slippage_threshold_pct} onChange={(event) => updateField("slippage_threshold_pct", Number(event.target.value))} /></Field>
              <Field label="초기 자본"><input className={inputClass} type="number" min={1} value={form.starting_equity} onChange={(event) => updateField("starting_equity", Number(event.target.value))} /></Field>
            </div>
            <div><p className="text-sm font-semibold text-slate-900">리뷰 주기</p><div className="mt-3 flex flex-wrap gap-2">{scheduleOptions.map((window) => { const active = form.schedule_windows.includes(window); return <button key={window} className={`rounded-full px-4 py-2 text-sm font-semibold ${active ? "bg-amber-400 text-slate-900" : "border border-amber-200 bg-white text-slate-600"}`} onClick={() => updateField("schedule_windows", active ? form.schedule_windows.filter((item) => item !== window) : [...form.schedule_windows, window])} type="button">{window}</button>; })}</div></div>
          </div>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">AI 설정</h3>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle checked={form.ai_enabled} label="OpenAI 사용" onChange={(value) => updateField("ai_enabled", value)} />
            <Field label="공급자"><select className={inputClass} value={form.ai_provider} onChange={(event) => updateField("ai_provider", event.target.value as "openai" | "mock")}><option value="openai">OpenAI</option><option value="mock">Mock</option></select></Field>
            <Field label="모델"><input className={inputClass} value={form.ai_model} onChange={(event) => updateField("ai_model", event.target.value)} /></Field>
            <Field label="온도"><input className={inputClass} type="number" min={0} max={1} step="0.05" value={form.ai_temperature} onChange={(event) => updateField("ai_temperature", Number(event.target.value))} /></Field>
            <Field label="의사결정 주기(분)"><input className={inputClass} type="number" min={1} value={form.decision_cycle_interval_minutes} onChange={(event) => updateField("decision_cycle_interval_minutes", Number(event.target.value))} /></Field>
            <Field label="OpenAI 최소 호출 간격(분)"><input className={inputClass} type="number" min={5} value={form.ai_call_interval_minutes} onChange={(event) => updateField("ai_call_interval_minutes", Number(event.target.value))} /></Field>
            <Field label="AI 입력 캔들 수"><input className={inputClass} type="number" min={16} max={200} value={form.ai_max_input_candles} onChange={(event) => updateField("ai_max_input_candles", Number(event.target.value))} /></Field>
            <Field label="OpenAI API Key"><input className={inputClass} type="password" autoComplete="off" value={form.openai_api_key} onChange={(event) => updateField("openai_api_key", event.target.value)} placeholder="sk-..." /></Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_openai_api_key} onChange={(event) => updateField("clear_openai_api_key", event.target.checked)} /> 저장된 키 삭제</label>
            <button className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white" onClick={async () => { try { setOpenAiResult(await requestJson<ConnectionTestResult>("/api/settings/test/openai", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: form.openai_api_key || null, model: form.ai_model }) })); } catch (error: unknown) { setMessage(error instanceof Error ? error.message : "OpenAI 연결 확인에 실패했습니다."); } }} type="button">OpenAI 연결 확인</button>
          </div>
          <div className="mt-4"><ResultCard title="OpenAI 연결 확인" result={openAiResult} /></div>
        </div>

        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">Binance 연동</h3>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle checked={form.binance_market_data_enabled} label="Binance 시세 사용" onChange={(value) => updateField("binance_market_data_enabled", value)} />
            <Toggle checked={form.binance_futures_enabled} label="USD-M Futures" onChange={(value) => updateField("binance_futures_enabled", value)} />
            <Toggle checked={form.binance_testnet_enabled} label="Testnet 사용" onChange={(value) => updateField("binance_testnet_enabled", value)} />
            <Field label="Binance API Key"><input className={inputClass} type="password" autoComplete="off" value={form.binance_api_key} onChange={(event) => updateField("binance_api_key", event.target.value)} /></Field>
            <Field label="Binance API Secret"><input className={inputClass} type="password" autoComplete="off" value={form.binance_api_secret} onChange={(event) => updateField("binance_api_secret", event.target.value)} /></Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_binance_api_key} onChange={(event) => updateField("clear_binance_api_key", event.target.checked)} /> 저장된 Key 삭제</label>
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_binance_api_secret} onChange={(event) => updateField("clear_binance_api_secret", event.target.checked)} /> 저장된 Secret 삭제</label>
            <button className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white" onClick={async () => { try { setBinanceResult(await requestJson<ConnectionTestResult>("/api/settings/test/binance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: form.binance_api_key || null, api_secret: form.binance_api_secret || null, testnet_enabled: form.binance_testnet_enabled, symbol: form.default_symbol, timeframe: form.default_timeframe }) })); } catch (error: unknown) { setMessage(error instanceof Error ? error.message : "Binance 연결 확인에 실패했습니다."); } }} type="button">Binance 연결 확인</button>
          </div>
          <div className="mt-4"><ResultCard title="Binance 연결 확인" result={binanceResult} /></div>
        </div>
      </section>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <p className="text-sm text-slate-600">선택한 심볼 목록은 수동 실행, 스케줄러, 토큰 사용량 계산에 모두 반영됩니다.</p>
        <button className="rounded-full bg-amber-400 px-5 py-3 text-sm font-semibold text-slate-900 disabled:opacity-60" disabled={isPending} onClick={save} type="button">{isPending ? "저장 중..." : "설정 저장"}</button>
      </div>
      {message ? <p className="text-sm text-slate-600">{message}</p> : null}
    </div>
  );
}
