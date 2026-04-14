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
  pnl_summary: Record<string, unknown>;
  account_sync_summary: Record<string, unknown>;
  exposure_summary: Record<string, unknown>;
  execution_policy_summary: Record<string, unknown>;
  market_context_summary: Record<string, unknown>;
  adaptive_protection_summary: Record<string, unknown>;
  adaptive_signal_summary: Record<string, unknown>;
  position_management_summary: Record<string, unknown>;
  live_trading_enabled: boolean;
  live_trading_env_enabled: boolean;
  manual_live_approval: boolean;
  live_execution_armed: boolean;
  live_execution_armed_until: string | null;
  live_approval_window_minutes: number;
  live_execution_ready: boolean;
  trading_paused: boolean;
  guard_mode_reason_category: string | null;
  guard_mode_reason_code: string | null;
  guard_mode_reason_message: string | null;
  pause_reason_code: string | null;
  pause_origin: string | null;
  pause_reason_detail: Record<string, unknown>;
  pause_triggered_at: string | null;
  auto_resume_after: string | null;
  auto_resume_whitelisted: boolean;
  auto_resume_eligible: boolean;
  auto_resume_status: string;
  auto_resume_last_blockers: string[];
  latest_blocked_reasons: string[];
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
  adaptive_signal_enabled: boolean;
  position_management_enabled: boolean;
  break_even_enabled: boolean;
  atr_trailing_stop_enabled: boolean;
  partial_take_profit_enabled: boolean;
  holding_edge_decay_enabled: boolean;
  reduce_on_regime_shift_enabled: boolean;
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
  | "pnl_summary" | "account_sync_summary" | "exposure_summary" | "execution_policy_summary" | "market_context_summary" | "adaptive_protection_summary" | "adaptive_signal_summary"
  | "position_management_summary"
  | "live_trading_env_enabled" | "live_execution_armed" | "live_execution_armed_until" | "live_execution_ready"
  | "trading_paused" | "guard_mode_reason_category" | "guard_mode_reason_code" | "guard_mode_reason_message" | "pause_reason_code" | "pause_origin" | "pause_reason_detail" | "pause_triggered_at" | "auto_resume_after"
  | "auto_resume_whitelisted" | "auto_resume_eligible" | "auto_resume_status" | "auto_resume_last_blockers" | "latest_blocked_reasons" | "pause_severity"
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
    adaptive_signal_enabled: initial.adaptive_signal_enabled,
    position_management_enabled: initial.position_management_enabled,
    break_even_enabled: initial.break_even_enabled,
    atr_trailing_stop_enabled: initial.atr_trailing_stop_enabled,
    partial_take_profit_enabled: initial.partial_take_profit_enabled,
    holding_edge_decay_enabled: initial.holding_edge_decay_enabled,
    reduce_on_regime_shift_enabled: initial.reduce_on_regime_shift_enabled,
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
  if (typeof detail.symbol === "string" && detail.symbol.trim()) return `${detail.symbol} 관련 상태 점검이 필요합니다.`;
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

function renderMetricMap(values: unknown, empty = "-") {
  if (!values || typeof values !== "object" || Array.isArray(values)) return empty;
  const entries = Object.entries(values as Record<string, unknown>);
  if (entries.length === 0) return empty;
  return entries
    .map(([key, value]) => `${formatDisplayValue(key)} ${formatDisplayValue(value, key)}`)
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
        <StatusPill tone={result.resumed ? "good" : result.allowed ? "good" : result.status === "blocked" ? "danger" : "warn"}>
          {formatDisplayValue(result.status, "auto_resume_status")}
        </StatusPill>
        {result.trigger_source ? <StatusPill>{result.trigger_source}</StatusPill> : null}
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="rounded-2xl bg-canvas px-4 py-3">
          <p className="text-xs text-slate-500">중지 사유</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(result.reason_code, "pause_reason_code")}</p>
        </div>
        <div className="rounded-2xl bg-canvas px-4 py-3">
          <p className="text-xs text-slate-500">승인 상태</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(result.approval_state, "approval_state")}</p>
        </div>
      </div>
      {blockers.length > 0 ? (
        <div className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          <p className="font-semibold">자동 복구 차단 사유</p>
          <p className="mt-2">{blockers.map((item) => formatDisplayValue(item)).join(" / ")}</p>
        </div>
      ) : null}
      {symbolBlockers.length > 0 ? (
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {symbolBlockers.map(([symbol, values]) => (
            <div key={`blocker-${symbol}`} className="rounded-2xl bg-canvas px-4 py-3">
              <p className="text-xs text-slate-500">{symbol} 차단 상태</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {values.length > 0 ? values.map((item) => formatDisplayValue(item)).join(" / ") : "차단 없음"}
              </p>
            </div>
          ))}
        </div>
      ) : null}
      {protectiveOrders.length > 0 || marketStatus.length > 0 || syncStatus.length > 0 ? (
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {protectiveOrders.map(([symbol, status]) => (
            <div key={`protect-${symbol}`} className="rounded-2xl bg-canvas px-4 py-3">
              <p className="text-xs text-slate-500">{symbol} 보호 상태</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(status, "status")}</p>
            </div>
          ))}
          {marketStatus.map(([symbol, status]) => (
            <div key={`market-${symbol}`} className="rounded-2xl bg-canvas px-4 py-3">
              <p className="text-xs text-slate-500">{symbol} 시장 데이터</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(status, "status")}</p>
            </div>
          ))}
          {syncStatus.map(([symbol, status]) => (
            <div key={`sync-${symbol}`} className="rounded-2xl bg-canvas px-4 py-3">
              <p className="text-xs text-slate-500">{symbol} 동기화 상태</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(status, "status")}</p>
            </div>
          ))}
        </div>
      ) : null}
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
        <StatusPill>주문 {result.synced_orders ?? 0}</StatusPill>
        <StatusPill>포지션 {result.synced_positions ?? 0}</StatusPill>
        {typeof result.equity === "number" ? <StatusPill>자산 {formatDisplayValue(result.equity, "equity")}</StatusPill> : null}
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
      {protectionEntries.length > 0 ? (
        <div className="grid gap-3 md:grid-cols-2">
          {protectionEntries.map(([symbol, state]) => (
            <div key={symbol} className="rounded-2xl bg-canvas px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill>{symbol}</StatusPill>
                <StatusPill tone={state.protected ? "good" : "danger"}>{state.protected ? "보호됨" : "보호 필요"}</StatusPill>
              </div>
              <p className="mt-3 text-sm text-slate-700">
                상태 {formatDisplayValue(state.status, "status")} / 보호 주문 {state.protective_order_count ?? 0}개
              </p>
              <p className="mt-2 text-sm text-slate-600">
                손절 {formatDisplayValue(state.has_stop_loss, "has_stop_loss")} / 익절 {formatDisplayValue(state.has_take_profit, "has_take_profit")}
              </p>
              {!state.protected && (state.missing_components?.length ?? 0) > 0 ? (
                <p className="mt-2 text-sm text-rose-700">누락: {state.missing_components?.join(", ")}</p>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-amber-300 px-4 py-5 text-sm text-slate-500">
          이번 동기화 응답에는 심볼별 보호 상태가 포함되지 않았습니다.
        </div>
      )}
      {(result.unprotected_positions?.length ?? 0) > 0 ? (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          미보호 포지션 감지: {result.unprotected_positions?.join(", ")}
        </div>
      ) : null}
      {(result.emergency_actions_taken?.length ?? 0) > 0 ? (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <p className="font-semibold">비상 조치 발생</p>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(result.emergency_actions_taken, null, 2)}</pre>
        </div>
      ) : null}
      <AutoResumeStatusCard result={result.auto_resume_precheck} title="자동 복구 사전 점검" />
      <AutoResumeStatusCard result={result.auto_resume_postcheck} title="자동 복구 사후 점검" />
      {!result.auto_resume_precheck && !result.auto_resume_postcheck ? <AutoResumeStatusCard result={result.auto_resume} title="자동 복구 결과" /> : null}
    </div>
  );
}

function OperationalStatusPanel({ state }: { state: SettingsPayload }) {
  const latestBlockedReasonsText =
    state.latest_blocked_reasons.length > 0
      ? state.latest_blocked_reasons.map((item) => formatDisplayValue(item)).join(" / ")
      : "최신 리스크 차단 사유 없음";
  const blockerText = state.auto_resume_last_blockers.length > 0 ? state.auto_resume_last_blockers.map((item) => formatDisplayValue(item)).join(" / ") : "차단 사유 없음";
  const missingProtectionText = renderMissingProtectionItems(state.missing_protection_items);
  const pnlSummary = state.pnl_summary ?? {};
  const accountSyncSummary = state.account_sync_summary ?? {};
  const exposureSummary = state.exposure_summary ?? {};
  const executionPolicySummary = state.execution_policy_summary ?? {};
  const marketContextSummary = state.market_context_summary ?? {};
  const adaptiveProtectionSummary = state.adaptive_protection_summary ?? {};
  const adaptiveSignalSummary = state.adaptive_signal_summary ?? {};
  const guardModeReasonMessage =
    state.guard_mode_reason_message ??
    (state.live_execution_ready ? "가드 모드가 아닙니다." : "실주문 준비 조건이 충족되지 않아 가드 모드입니다.");
  return (
    <section className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-semibold text-slate-900">운영 상태</h3>
        <StatusPill tone={state.trading_paused ? "danger" : state.live_execution_ready ? "good" : "warn"}>
          {state.trading_paused ? "거래 중지" : state.live_execution_ready ? "실주문 가능" : "가드 모드"}
        </StatusPill>
        <StatusPill tone={state.operating_state === "TRADABLE" ? "good" : state.operating_state === "PAUSED" ? "danger" : "warn"}>
          운영 상태 {formatDisplayValue(state.operating_state, "operating_state")}
        </StatusPill>
        {state.auto_resume_status !== "not_paused" ? (
          <StatusPill tone={state.auto_resume_status === "resumed" ? "good" : state.auto_resume_status === "blocked" ? "danger" : "warn"}>
            자동 복구 {formatDisplayValue(state.auto_resume_status, "auto_resume_status")}
          </StatusPill>
        ) : null}
      </div>
      {state.guard_mode_reason_code ? (
        <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">직접 원인</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{guardModeReasonMessage}</p>
          <p className="mt-2 text-xs text-slate-600">
            {formatDisplayValue(state.guard_mode_reason_category, "guard_mode_reason_category")} /{" "}
            {formatDisplayValue(state.guard_mode_reason_code, "guard_mode_reason_code")}
          </p>
        </div>
      ) : null}
      <p className="mt-3 text-sm leading-7 text-slate-600">
        현재 중지는 신규 진입을 막는 운영 pause입니다. 기존 포지션의 보호 주문 유지, 축소, 비상 청산 같은 생존 경로는 계속 허용됩니다.
      </p>
      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">가드 모드 직접 원인</p><p className="mt-2 text-sm font-semibold text-slate-900">{guardModeReasonMessage}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">가드 분류 / 코드</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.guard_mode_reason_category, "guard_mode_reason_category")} / {formatDisplayValue(state.guard_mode_reason_code, "guard_mode_reason_code")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">실주문 준비 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.live_execution_ready, "live_execution_ready")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">운영 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.operating_state, "operating_state")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">보호 복구 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.protection_recovery_status, "protection_recovery_status")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">보호 복구 진행 여부</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.protection_recovery_active, "protection_recovery_active")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">거래 중지 여부</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.trading_paused, "trading_paused")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">중지 사유</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.pause_reason_code, "pause_reason_code")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">중지 발생 주체</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.pause_origin, "pause_origin")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">중지 심각도</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.pause_severity, "pause_severity")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">복구 분류</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.pause_recovery_class, "pause_recovery_class")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">중지 발생 시각</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.pause_triggered_at, "pause_triggered_at")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">보호 복구 실패 누적</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.protection_recovery_failure_count, "protection_recovery_failure_count")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">누락 보호 심볼</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatCodeList(state.missing_protection_symbols, "없음")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">자동 복구 정책 대상</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.auto_resume_whitelisted, "auto_resume_whitelisted")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">자동 복구 가능</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.auto_resume_eligible, "auto_resume_eligible")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">자동 복구 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.auto_resume_status, "auto_resume_status")}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3 md:col-span-2 xl:col-span-3"><p className="text-xs text-slate-500">현재 거래 차단 사유</p><p className="mt-2 text-sm font-semibold text-slate-900">{latestBlockedReasonsText}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3 md:col-span-2 xl:col-span-3"><p className="text-xs text-slate-500">자동 복구 차단 사유</p><p className="mt-2 text-sm font-semibold text-slate-900">{blockerText}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3 md:col-span-2 xl:col-span-3"><p className="text-xs text-slate-500">누락 보호 항목</p><p className="mt-2 text-sm font-semibold text-slate-900">{missingProtectionText}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3 md:col-span-2 xl:col-span-3"><p className="text-xs text-slate-500">중지 상세</p><p className="mt-2 text-sm leading-6 text-slate-900">{stringifyPauseDetail(state.pause_reason_detail)}</p></div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3 md:col-span-2 xl:col-span-3"><p className="text-xs text-slate-500">다음 자동 복구 예정 시각</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue(state.auto_resume_after, "auto_resume_after")}</p></div>
      </div>
      <div className="mt-5 grid gap-4 xl:grid-cols-2">
        <div className="rounded-2xl border border-amber-200 bg-white p-4">
          <p className="text-sm font-semibold text-slate-900">손익 집계 기준</p>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">기준</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((pnlSummary as Record<string, unknown>).basis, "pnl_basis")}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">스냅샷 시각</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((pnlSummary as Record<string, unknown>).snapshot_time, "snapshot_time")}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">순실현 손익 / 일일 손익</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((pnlSummary as Record<string, unknown>).net_realized_pnl, "daily_pnl")} / {formatDisplayValue((pnlSummary as Record<string, unknown>).daily_pnl, "daily_pnl")}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">누적 손익 / 연속 손실</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((pnlSummary as Record<string, unknown>).cumulative_pnl, "cumulative_pnl")} / {formatDisplayValue((pnlSummary as Record<string, unknown>).consecutive_losses, "consecutive_losses")}</p></div>
          </div>
          <p className="mt-3 text-sm leading-6 text-slate-600">{String((pnlSummary as Record<string, unknown>).basis_note ?? "손익 집계 기준 설명이 아직 없습니다.")}</p>
        </div>

        <div className="rounded-2xl border border-amber-200 bg-white p-4">
          <p className="text-sm font-semibold text-slate-900">계좌 동기화 / 보정</p>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">동기화 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((accountSyncSummary as Record<string, unknown>).status, "account_sync_status")}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">보정 방식</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((accountSyncSummary as Record<string, unknown>).reconciliation_mode, "account_reconciliation_mode")}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">마지막 동기화 / 최신도</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((accountSyncSummary as Record<string, unknown>).last_synced_at, "last_synced_at")} / {formatDisplayValue((accountSyncSummary as Record<string, unknown>).freshness_seconds, "freshness_seconds")}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">마지막 경고</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((accountSyncSummary as Record<string, unknown>).last_warning_reason_code, "pause_reason_code")}</p></div>
          </div>
          <p className="mt-3 text-sm leading-6 text-slate-600">{String((accountSyncSummary as Record<string, unknown>).note ?? "계좌 동기화 설명이 아직 없습니다.")}</p>
        </div>

        <div className="rounded-2xl border border-amber-200 bg-white p-4">
          <p className="text-sm font-semibold text-slate-900">노출 / 여유 한도</p>
          <div className="mt-3 grid gap-3">
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">상태 / 기준 심볼</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((exposureSummary as Record<string, unknown>).status, "exposure_status")} / {formatDisplayValue((exposureSummary as Record<string, unknown>).reference_symbol, "symbol")}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">현재 노출</p><p className="mt-2 text-sm font-semibold text-slate-900">{renderMetricMap((exposureSummary as Record<string, unknown>).metrics)}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">남은 여유 한도</p><p className="mt-2 text-sm font-semibold text-slate-900">{renderMetricMap((exposureSummary as Record<string, unknown>).headroom)}</p></div>
          </div>
        </div>

        <div className="rounded-2xl border border-amber-200 bg-white p-4">
          <p className="text-sm font-semibold text-slate-900">실행 정책 / 컨텍스트</p>
          <div className="mt-3 space-y-3">
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">실행 정책</p><p className="mt-2 text-sm font-semibold text-slate-900">{String(((executionPolicySummary as Record<string, unknown>).entry as Record<string, unknown> | undefined)?.summary ?? "-")}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">메인 타임프레임 / 레짐</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((marketContextSummary as Record<string, unknown>).primary_regime, "primary_regime")} / {formatDisplayValue((marketContextSummary as Record<string, unknown>).trend_alignment, "trend_alignment")} / {Array.isArray((marketContextSummary as Record<string, unknown>).context_timeframes) && ((marketContextSummary as Record<string, unknown>).context_timeframes as string[]).length > 0 ? ((marketContextSummary as Record<string, unknown>).context_timeframes as string[]).join(", ") : "-"}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">적응형 보호</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((adaptiveProtectionSummary as Record<string, unknown>).mode, "adaptive_protection_mode")} / {formatDisplayValue((adaptiveProtectionSummary as Record<string, unknown>).status, "protection_recovery_status")}</p><p className="mt-2 text-sm leading-6 text-slate-600">{String((adaptiveProtectionSummary as Record<string, unknown>).summary ?? "-")}</p></div>
            <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-xs text-slate-500">적응형 신호</p><p className="mt-2 text-sm font-semibold text-slate-900">{formatDisplayValue((adaptiveSignalSummary as Record<string, unknown>).status, "status")} / 가중치 {formatDisplayValue((adaptiveSignalSummary as Record<string, unknown>).signal_weight, "signal_weight")}</p><p className="mt-2 text-sm leading-6 text-slate-600">신뢰도 x {formatDisplayValue((adaptiveSignalSummary as Record<string, unknown>).confidence_multiplier, "confidence_multiplier")} / 리스크 x {formatDisplayValue((adaptiveSignalSummary as Record<string, unknown>).risk_pct_multiplier, "risk_pct_multiplier")} / 홀드 편향 {formatDisplayValue((adaptiveSignalSummary as Record<string, unknown>).hold_bias, "hold_bias")}</p></div>
          </div>
        </div>
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
  const adaptiveSignalSummary = state.adaptive_signal_summary ?? {};
  const positionManagementSummary = state.position_management_summary ?? {};

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
    adaptive_signal_enabled: form.adaptive_signal_enabled,
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
          <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-600">이 화면에서 실거래 사용 여부, 수동 승인 정책, AI 호출 주기, Binance 연결, 자동 복구 상태를 함께 확인합니다.</p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <StatusPill tone={state.openai_api_key_configured ? "good" : "warn"}>OpenAI: {state.openai_api_key_configured ? "설정됨" : "없음"}</StatusPill>
          <StatusPill tone={state.binance_api_key_configured ? "good" : "warn"}>Binance Key: {state.binance_api_key_configured ? "설정됨" : "없음"}</StatusPill>
          <StatusPill tone={state.binance_api_secret_configured ? "good" : "warn"}>Binance Secret: {state.binance_api_secret_configured ? "설정됨" : "없음"}</StatusPill>
          <StatusPill tone={state.live_execution_ready ? "good" : state.trading_paused ? "danger" : "warn"}>실거래 상태: {state.live_execution_ready ? "실주문 가능" : state.trading_paused ? "중지" : "가드 모드"}</StatusPill>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-6">
        <MetricCard label="현재 모드" value={formatDisplayValue(state.mode, "mode")} tone="dark" />
        <MetricCard label="현재 월간 AI 호출" value={`${state.estimated_monthly_ai_calls.toLocaleString("ko-KR")}회`} tone="warm" />
        <MetricCard label="예상 월간 AI 호출" value={`${state.projected_monthly_ai_calls_if_enabled.toLocaleString("ko-KR")}회`} />
        {projectedBreakdown.map(([key, value]) => <MetricCard key={key} label={monthlyLabels[key] ?? key} value={`${value.toLocaleString("ko-KR")}회`} />)}
      </div>

      <OperationalStatusPanel state={state} />
      <AIUsagePanel settings={state} />

      <section className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">실거래 제어</h3>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Field label="승인 유지 시간(분)"><input className={inputClass} min={0} max={240} type="number" value={form.live_approval_window_minutes} onChange={(event) => updateField("live_approval_window_minutes", Number(event.target.value))} /></Field>
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">환경 게이트</p><p className="mt-2 text-sm font-semibold text-slate-900">{state.live_trading_env_enabled ? "활성" : "비활성"}</p></div>
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">승인 창 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{state.live_execution_armed ? `열림 (${formatDisplayValue(state.live_execution_armed_until, "live_execution_armed_until")})` : "닫힘"}</p></div>
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">실주문 준비 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{state.live_execution_ready ? "준비됨" : "승인 필요"}</p></div>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <Toggle checked={form.live_trading_enabled} label="실거래 경로 사용" onChange={(value) => updateField("live_trading_enabled", value)} />
            <Toggle checked={form.manual_live_approval} label="수동 승인 정책 사용" onChange={(value) => updateField("manual_live_approval", value)} />
          </div>
          <p className="mt-4 text-sm leading-6 text-slate-600">즉시 중지는 신규 진입만 막는 운영 pause입니다. 기존 포지션의 보호 주문 유지, 축소, 비상 청산은 계속 허용됩니다.</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button className="rounded-full bg-rose-600 px-4 py-2 text-sm font-semibold text-white" onClick={() => runPost("/api/settings/pause", "거래를 일시 중지했습니다.", syncSettings)} type="button">즉시 중지</button>
            <button className="rounded-full bg-emerald-600 px-4 py-2 text-sm font-semibold text-white" onClick={() => runPost("/api/settings/resume", "거래 일시 중지를 해제했습니다.", syncSettings)} type="button">중지 해제</button>
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
          <h3 className="text-lg font-semibold text-slate-900">시장 / 리스크</h3>
          <div className="mt-4 space-y-4">
            <Field label="기본 심볼">
              <select className={inputClass} value={form.default_symbol} onChange={(event) => updateField("default_symbol", event.target.value)}>
                {mergedSymbols.map((symbol) => (
                  <option key={symbol} value={symbol}>
                    {symbol}
                  </option>
                ))}
              </select>
            </Field>

            <div>
              <p className="text-sm font-semibold text-slate-900">추적 심볼</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {symbolOptions.map((symbol) => {
                  const active = form.tracked_symbols.includes(symbol);
                  return (
                    <button
                      key={symbol}
                      className={`rounded-full px-4 py-2 text-sm font-semibold ${active ? "bg-amber-400 text-slate-900" : "border border-amber-200 bg-white text-slate-700"}`}
                      onClick={() =>
                        updateField(
                          "tracked_symbols",
                          active
                            ? form.tracked_symbols.filter((item) => item !== symbol)
                            : uniqueSymbols([...form.tracked_symbols, symbol]),
                        )
                      }
                      type="button"
                    >
                      {symbol}
                    </button>
                  );
                })}
              </div>
            </div>

            <Field label="사용자 지정 심볼" hint="쉼표로 구분하면 추적 심볼 목록에 함께 합쳐집니다.">
              <input className={inputClass} value={form.custom_symbols} onChange={(event) => updateField("custom_symbols", event.target.value.toUpperCase())} placeholder="APTUSDT, AVAXUSDT" />
            </Field>

            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3">
              <p className="text-xs text-slate-500">현재 심볼 집합</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{mergedSymbols.join(", ")}</p>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <Field label="기본 타임프레임">
                <input className={inputClass} value={form.default_timeframe} onChange={(event) => updateField("default_timeframe", event.target.value)} />
              </Field>
              <Field label="최대 레버리지" hint="런타임 하드 상한은 5x로 유지됩니다.">
                <input className={inputClass} type="number" min={1} max={5} step="0.1" value={form.max_leverage} onChange={(event) => updateField("max_leverage", Number(event.target.value))} />
              </Field>
              <Field label="거래당 최대 리스크" hint="런타임 하드 상한은 2%로 유지됩니다.">
                <input className={inputClass} type="number" min={0.001} max={0.02} step="0.001" value={form.max_risk_per_trade} onChange={(event) => updateField("max_risk_per_trade", Number(event.target.value))} />
              </Field>
              <Field label="일일 최대 손실" hint="런타임 하드 상한은 5%로 유지됩니다.">
                <input className={inputClass} type="number" min={0.001} max={0.05} step="0.001" value={form.max_daily_loss} onChange={(event) => updateField("max_daily_loss", Number(event.target.value))} />
              </Field>
              <Field label="최대 연속 손실">
                <input className={inputClass} type="number" min={1} max={20} value={form.max_consecutive_losses} onChange={(event) => updateField("max_consecutive_losses", Number(event.target.value))} />
              </Field>
              <Field label="시장 데이터 최신도 한계(초)">
                <input className={inputClass} type="number" min={30} value={form.stale_market_seconds} onChange={(event) => updateField("stale_market_seconds", Number(event.target.value))} />
              </Field>
              <Field label="슬리피지 임계값">
                <input className={inputClass} type="number" min={0.0001} max={0.1} step="0.0001" value={form.slippage_threshold_pct} onChange={(event) => updateField("slippage_threshold_pct", Number(event.target.value))} />
              </Field>
              <Field label="시작 자본">
                <input className={inputClass} type="number" min={1} value={form.starting_equity} onChange={(event) => updateField("starting_equity", Number(event.target.value))} />
              </Field>
            </div>

            <Toggle checked={form.adaptive_signal_enabled} label="적응형 신호 조정 사용" onChange={(value) => updateField("adaptive_signal_enabled", value)} />
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3">
              <p className="text-xs text-slate-500">적응형 조정 상한/하한</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                가중치 {formatDisplayValue(((adaptiveSignalSummary as Record<string, unknown>).bounds as Record<string, unknown> | undefined)?.signal_weight_min, "signal_weight")} - {formatDisplayValue(((adaptiveSignalSummary as Record<string, unknown>).bounds as Record<string, unknown> | undefined)?.signal_weight_max, "signal_weight")}
              </p>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                최근 성과가 나쁠 때만 신뢰도와 리스크를 할인합니다. 데이터가 부족하면 중립값으로 되돌립니다.
              </p>
            </div>

            <div>
              <p className="text-sm font-semibold text-slate-900">스케줄 윈도우</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {scheduleOptions.map((window) => {
                  const active = form.schedule_windows.includes(window);
                  return (
                    <button
                      key={window}
                      className={`rounded-full px-4 py-2 text-sm font-semibold ${active ? "bg-amber-400 text-slate-900" : "border border-amber-200 bg-white text-slate-600"}`}
                      onClick={() =>
                        updateField(
                          "schedule_windows",
                          active ? form.schedule_windows.filter((item) => item !== window) : [...form.schedule_windows, window],
                        )
                      }
                      type="button"
                    >
                      {window}
                    </button>
                  );
                })}
              </div>
            </div>

            <Toggle checked={form.position_management_enabled} label="보수적 포지션 관리 사용" onChange={(value) => updateField("position_management_enabled", value)} />
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <Toggle checked={form.break_even_enabled} label="1R 도달 시 본절 이동" onChange={(value) => updateField("break_even_enabled", value)} />
              <Toggle checked={form.atr_trailing_stop_enabled} label="ATR 트레일링 스탑" onChange={(value) => updateField("atr_trailing_stop_enabled", value)} />
              <Toggle checked={form.partial_take_profit_enabled} label="부분 익절" onChange={(value) => updateField("partial_take_profit_enabled", value)} />
              <Toggle checked={form.holding_edge_decay_enabled} label="보유 시간 경과 감쇠" onChange={(value) => updateField("holding_edge_decay_enabled", value)} />
              <Toggle checked={form.reduce_on_regime_shift_enabled} label="레짐 전환 시 축소 강화" onChange={(value) => updateField("reduce_on_regime_shift_enabled", value)} />
            </div>

            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3">
              <p className="text-xs text-slate-500">포지션 관리 규칙</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                1R 본절 이동 / ATR x {formatDisplayValue(((positionManagementSummary as Record<string, unknown>).fixed_parameters as Record<string, unknown> | undefined)?.trailing_atr_multiple)} 트레일링 / {formatDisplayValue(((positionManagementSummary as Record<string, unknown>).fixed_parameters as Record<string, unknown> | undefined)?.partial_take_profit_fraction, "risk_pct")} 부분 익절
              </p>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                {String(
                  (positionManagementSummary as Record<string, unknown>).summary ??
                    "포지션 관리는 손절을 넓히지 않습니다. 보호를 더 타이트하게 하거나 보수적 축소만 권고합니다.",
                )}
              </p>
            </div>
          </div>
        </div>
      </section>
      <section className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">AI 설정</h3>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle checked={form.ai_enabled} label="OpenAI 사용" onChange={(value) => updateField("ai_enabled", value)} />
            <Field label="제공자"><select className={inputClass} value={form.ai_provider} onChange={(event) => updateField("ai_provider", event.target.value as "openai" | "mock")}><option value="openai">OpenAI</option><option value="mock">Mock</option></select></Field>
            <Field label="모델"><input className={inputClass} value={form.ai_model} onChange={(event) => updateField("ai_model", event.target.value)} /></Field>
            <Field label="Temperature"><input className={inputClass} type="number" min={0} max={1} step="0.05" value={form.ai_temperature} onChange={(event) => updateField("ai_temperature", Number(event.target.value))} /></Field>
            <Field label="의사결정 주기(분)"><input className={inputClass} type="number" min={1} value={form.decision_cycle_interval_minutes} onChange={(event) => updateField("decision_cycle_interval_minutes", Number(event.target.value))} /></Field>
            <Field label="최소 AI 호출 간격(분)"><input className={inputClass} type="number" min={5} value={form.ai_call_interval_minutes} onChange={(event) => updateField("ai_call_interval_minutes", Number(event.target.value))} /></Field>
            <Field label="AI 입력 캔들 수"><input className={inputClass} type="number" min={16} max={200} value={form.ai_max_input_candles} onChange={(event) => updateField("ai_max_input_candles", Number(event.target.value))} /></Field>
            <Field label="OpenAI API Key"><input className={inputClass} type="password" autoComplete="off" value={form.openai_api_key} onChange={(event) => updateField("openai_api_key", event.target.value)} placeholder="sk-..." /></Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_openai_api_key} onChange={(event) => updateField("clear_openai_api_key", event.target.checked)} /> 저장된 키 제거</label>
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
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_binance_api_key} onChange={(event) => updateField("clear_binance_api_key", event.target.checked)} /> 저장된 Key 제거</label>
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_binance_api_secret} onChange={(event) => updateField("clear_binance_api_secret", event.target.checked)} /> 저장된 Secret 제거</label>
            <button className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white" onClick={async () => { try { setBinanceResult(await requestJson<ConnectionTestResult>("/api/settings/test/binance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: form.binance_api_key || null, api_secret: form.binance_api_secret || null, testnet_enabled: form.binance_testnet_enabled, symbol: form.default_symbol, timeframe: form.default_timeframe }) })); } catch (error: unknown) { setMessage(error instanceof Error ? error.message : "Binance 연결 확인에 실패했습니다."); } }} type="button">Binance 연결 확인</button>
          </div>
          <div className="mt-4"><ResultCard title="Binance 연결 확인" result={binanceResult} /></div>
        </div>
      </section>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <p className="text-sm text-slate-600">추적 심볼, 실거래 설정, 스케줄 윈도우, 인증 정보 변경은 한 번에 함께 저장됩니다.</p>
        <button className="rounded-full bg-amber-400 px-5 py-3 text-sm font-semibold text-slate-900 disabled:opacity-60" disabled={isPending} onClick={save} type="button">{isPending ? "저장 중..." : "설정 저장"}</button>
      </div>
      {message ? <p className="text-sm text-slate-600">{message}</p> : null}
    </div>
  );
}
