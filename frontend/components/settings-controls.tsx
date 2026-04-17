"use client";

import { useMemo, useState, useTransition, type ReactNode } from "react";

import { AIUsagePanel } from "./ai-usage-panel";
import { formatDisplayValue } from "../lib/ui-copy";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const scheduleOptions = ["1h", "4h", "12h", "24h"] as const;
const symbolOptions = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"];
const settingsStageLabels = ["실거래 제어", "시장 / 리스크", "운영 주기", "AI 설정", "Binance 연동"] as const;
const rolloutModeOptions = ["shadow", "live_dry_run", "limited_live", "full_live"] as const;

type RolloutMode = (typeof rolloutModeOptions)[number];

type ProtectionSyncState = {
  status?: string;
  protected?: boolean;
  protective_order_count?: number;
  has_stop_loss?: boolean;
  has_take_profit?: boolean;
  missing_components?: string[];
};

type SymbolCadenceOverride = {
  symbol: string;
  enabled: boolean;
  timeframe_override: string | null;
  market_refresh_interval_minutes_override: number | null;
  position_management_interval_seconds_override: number | null;
  decision_cycle_interval_minutes_override: number | null;
  ai_call_interval_minutes_override: number | null;
};

type SymbolEffectiveCadence = {
  symbol: string;
  enabled: boolean;
  uses_global_defaults: boolean;
  timeframe: string;
  market_refresh_interval_minutes: number;
  position_management_interval_seconds: number;
  decision_cycle_interval_minutes: number;
  ai_call_interval_minutes: number;
  estimated_monthly_ai_calls: number;
  last_market_refresh_at: string | null;
  last_position_management_at: string | null;
  last_decision_at: string | null;
  last_ai_decision_at: string | null;
  next_market_refresh_due_at: string | null;
  next_position_management_due_at: string | null;
  next_decision_due_at: string | null;
  next_ai_call_due_at: string | null;
};

type ControlStatusSummary = {
  exchange_can_trade: boolean | null;
  rollout_mode: RolloutMode;
  exchange_submit_allowed: boolean;
  limited_live_max_notional: number | null;
  app_live_armed: boolean;
  approval_window_open: boolean;
  paused: boolean;
  degraded: boolean;
  risk_allowed: boolean | null;
  blocked_reasons_current_cycle: string[];
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
  rollout_mode: RolloutMode;
  exchange_submit_allowed: boolean;
  limited_live_max_notional: number | null;
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
  control_status_summary?: ControlStatusSummary | null;
  pause_severity: string | null;
  pause_recovery_class: string | null;
  default_symbol: string;
  tracked_symbols: string[];
  default_timeframe: string;
  exchange_sync_interval_seconds: number;
  market_refresh_interval_minutes: number;
  position_management_interval_seconds: number;
  schedule_windows: string[];
  symbol_cadence_overrides: SymbolCadenceOverride[];
  symbol_effective_cadences: SymbolEffectiveCadence[];
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

type LiveSyncResult = {
  symbols?: string[];
  synced_orders?: number;
  synced_positions?: number;
  equity?: number;
  missing_protection_symbols?: string[];
  missing_protection_items?: Record<string, string[]>;
  symbol_protection_state?: Record<string, ProtectionSyncState>;
  unprotected_positions?: string[];
  emergency_actions_taken?: Array<Record<string, unknown>>;
};

type FormState = Omit<
  SettingsPayload,
  | "id" | "mode" | "operating_state" | "protection_recovery_status" | "protection_recovery_active"
  | "protection_recovery_failure_count" | "missing_protection_symbols" | "missing_protection_items"
  | "pnl_summary" | "account_sync_summary" | "exposure_summary" | "execution_policy_summary" | "market_context_summary" | "adaptive_protection_summary" | "adaptive_signal_summary"
  | "position_management_summary" | "symbol_effective_cadences"
  | "exchange_submit_allowed"
  | "live_trading_env_enabled" | "live_execution_armed" | "live_execution_armed_until" | "live_execution_ready"
  | "trading_paused" | "guard_mode_reason_category" | "guard_mode_reason_code" | "guard_mode_reason_message" | "pause_reason_code" | "pause_origin" | "pause_reason_detail" | "pause_triggered_at" | "auto_resume_after"
  | "auto_resume_whitelisted" | "auto_resume_eligible" | "auto_resume_status" | "auto_resume_last_blockers" | "latest_blocked_reasons" | "pause_severity"
  | "pause_recovery_class" | "control_status_summary" | "openai_api_key_configured" | "binance_api_key_configured" | "binance_api_secret_configured"
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

function numberOrNull(value: string) {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeSymbolOverrides(overrides: SymbolCadenceOverride[]) {
  const seen = new Set<string>();
  return overrides
    .map((item) => ({
      ...item,
      symbol: item.symbol.trim().toUpperCase(),
      timeframe_override: item.timeframe_override?.trim() ? item.timeframe_override.trim() : null,
    }))
    .filter((item) => {
      if (!item.symbol || seen.has(item.symbol)) return false;
      seen.add(item.symbol);
      return true;
    });
}

function toFormState(initial: SettingsPayload): FormState {
  return {
    live_trading_enabled: initial.live_trading_enabled,
    rollout_mode: initial.rollout_mode,
    limited_live_max_notional: initial.limited_live_max_notional ?? 500,
    manual_live_approval: initial.manual_live_approval,
    live_approval_window_minutes: initial.live_approval_window_minutes,
    default_symbol: initial.default_symbol,
    tracked_symbols: initial.tracked_symbols,
    default_timeframe: initial.default_timeframe,
    exchange_sync_interval_seconds: initial.exchange_sync_interval_seconds,
    market_refresh_interval_minutes: initial.market_refresh_interval_minutes,
    position_management_interval_seconds: initial.position_management_interval_seconds,
    schedule_windows: initial.schedule_windows,
    symbol_cadence_overrides: initial.symbol_cadence_overrides,
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

function dedupeReasons(values: string[]) {
  return values.filter((item, index, array) => array.indexOf(item) === index);
}

function rolloutModeLabel(mode: RolloutMode) {
  switch (mode) {
    case "shadow":
      return "shadow";
    case "live_dry_run":
      return "live dry-run";
    case "limited_live":
      return "limited live";
    case "full_live":
      return "full live";
    default:
      return mode;
  }
}

function resolveControlStatusSummary(state: SettingsPayload): ControlStatusSummary {
  const summary = state.control_status_summary;
  return {
    exchange_can_trade: summary?.exchange_can_trade ?? null,
    rollout_mode: summary?.rollout_mode ?? state.rollout_mode,
    exchange_submit_allowed: summary?.exchange_submit_allowed ?? state.exchange_submit_allowed,
    limited_live_max_notional: summary?.limited_live_max_notional ?? state.limited_live_max_notional,
    app_live_armed: summary?.app_live_armed ?? state.live_execution_armed,
    approval_window_open: summary?.approval_window_open ?? state.live_execution_armed,
    paused: summary?.paused ?? state.trading_paused,
    degraded: summary?.degraded ?? state.operating_state === "DEGRADED_MANAGE_ONLY",
    risk_allowed: summary?.risk_allowed ?? null,
    blocked_reasons_current_cycle: dedupeReasons(
      summary?.blocked_reasons_current_cycle ?? state.latest_blocked_reasons,
    ),
  };
}

function ControlStatusPanel({ state }: { state: SettingsPayload }) {
  const summary = resolveControlStatusSummary(state);
  const currentCycleBlockedReasons = summary.blocked_reasons_current_cycle;
  const primaryBlocker = currentCycleBlockedReasons[0];
  const cards = [
    {
      label: "rollout mode",
      value: rolloutModeLabel(summary.rollout_mode),
      detail:
        summary.rollout_mode === "shadow"
          ? "AI/risk/execution intent와 audit까지만 수행하고 실제 submit은 금지됩니다."
          : summary.rollout_mode === "live_dry_run"
            ? "거래소 sync와 preflight까지 수행하고 실제 submit은 금지됩니다."
            : summary.rollout_mode === "limited_live"
              ? `실제 submit은 허용되지만 주문당 notional이 ${formatDisplayValue(summary.limited_live_max_notional, "limited_live_max_notional")}로 제한됩니다.`
              : "기존 full live submit 경로를 사용합니다.",
      tone:
        summary.rollout_mode === "full_live"
          ? ("good" as const)
          : summary.rollout_mode === "limited_live"
            ? ("warn" as const)
            : ("neutral" as const),
    },
    {
      label: "거래소 canTrade",
      value:
        summary.exchange_can_trade === null
          ? "미확인"
          : summary.exchange_can_trade
            ? "주문 가능"
            : "주문 차단",
      detail:
        summary.exchange_can_trade === null
          ? "최근 account sync에 거래소 canTrade truth가 없습니다."
          : summary.exchange_can_trade
            ? "거래소 계좌 상태 기준으로 신규 주문이 가능합니다."
            : "거래소 계좌 상태 기준으로 신규 주문이 차단됩니다.",
      tone:
        summary.exchange_can_trade === null
          ? ("neutral" as const)
          : summary.exchange_can_trade
            ? ("good" as const)
            : ("danger" as const),
    },
    {
      label: "앱 live arm",
      value: summary.app_live_armed ? "Arm됨" : "Arm 해제",
      detail: summary.app_live_armed
        ? "앱 실거래 경로가 arm 상태입니다."
        : "앱 live arm이 내려가 있어 실거래 경로가 열리지 않습니다.",
      tone: summary.app_live_armed ? ("good" as const) : ("warn" as const),
    },
    {
      label: "approval window",
      value: summary.approval_window_open ? "열림" : "닫힘",
      detail: summary.approval_window_open
        ? state.live_execution_armed_until
          ? `만료 ${formatDisplayValue(state.live_execution_armed_until, "live_execution_armed_until")}`
          : "승인 창이 유효합니다."
        : "실주문 승인 창을 다시 열어야 합니다.",
      tone: summary.approval_window_open ? ("good" as const) : ("warn" as const),
    },
    {
      label: "pause",
      value: summary.paused ? "중지" : "운영 중",
      detail: summary.paused
        ? formatDisplayValue(state.pause_reason_code, "pause_reason_code")
        : "운영 중지 플래그가 활성화되어 있지 않습니다.",
      tone: summary.paused ? ("danger" as const) : ("good" as const),
    },
    {
      label: "degraded",
      value: summary.degraded ? "관리 전용" : "정상",
      detail: summary.degraded
        ? `${formatDisplayValue(state.operating_state, "operating_state")} / 보호 복구 ${formatDisplayValue(state.protection_recovery_status, "protection_recovery_status")}`
        : "관리 전용 또는 비상 복구 상태로 내려가 있지 않습니다.",
      tone: summary.degraded ? ("warn" as const) : ("good" as const),
    },
    {
      label: "risk 허용",
      value:
        summary.risk_allowed === null
          ? "미평가"
          : summary.risk_allowed
            ? "허용"
            : "차단",
      detail:
        summary.risk_allowed === null
          ? "현재 cycle risk 결과가 아직 집계되지 않았습니다."
          : summary.risk_allowed
            ? "현재 cycle risk_guard가 신규 진입을 허용했습니다."
            : primaryBlocker
              ? formatDisplayValue(primaryBlocker, "blocked_reason_codes")
              : state.guard_mode_reason_message ?? "현재 cycle risk_guard가 신규 진입을 차단했습니다.",
      tone:
        summary.risk_allowed === null
          ? ("neutral" as const)
          : summary.risk_allowed
            ? ("good" as const)
            : ("danger" as const),
    },
  ];

  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-3 xl:grid-cols-3">
        {cards.map((card) => (
          <div key={card.label} className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-medium text-slate-500">{card.label}</p>
              <StatusPill tone={card.tone}>{card.value}</StatusPill>
            </div>
            <p className="mt-3 text-sm leading-6 text-slate-700">{card.detail}</p>
          </div>
        ))}
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-slate-900">현재 cycle 차단 사유</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              과거 blocker나 auto-resume blocker를 섞지 않고, 지금 cycle 기준으로 신규 진입을 막는
              이유만 보여줍니다.
            </p>
          </div>
          <StatusPill tone={currentCycleBlockedReasons.length > 0 ? "warn" : "good"}>
            {currentCycleBlockedReasons.length > 0 ? `${currentCycleBlockedReasons.length}건` : "없음"}
          </StatusPill>
        </div>
        <div className="mt-4 space-y-2">
          {currentCycleBlockedReasons.length === 0 ? (
            <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
              현재 cycle 기준 차단 사유는 없습니다.
            </div>
          ) : (
            currentCycleBlockedReasons.map((reason) => (
              <div key={reason} className="rounded-2xl bg-amber-50 px-4 py-3 text-sm text-slate-800">
                {formatDisplayValue(reason, "blocked_reason_codes")}
              </div>
            ))
          )}
        </div>
        {state.auto_resume_last_blockers.length > 0 ? (
          <div className="mt-4 rounded-2xl border border-dashed border-slate-200 px-4 py-3 text-sm text-slate-600">
            자동 복구 차단 사유:{" "}
            {state.auto_resume_last_blockers
              .map((reason) => formatDisplayValue(reason, "auto_resume_last_blockers"))
              .join(", ")}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function SymbolCadenceOverridePanel({
  mergedSymbols,
  overrideRows,
  effectiveCadenceBySymbol,
  form,
  updateSymbolOverride,
}: {
  mergedSymbols: string[];
  overrideRows: SymbolCadenceOverride[];
  effectiveCadenceBySymbol: Record<string, SymbolEffectiveCadence>;
  form: FormState;
  updateSymbolOverride: (symbol: string, patch: Partial<SymbolCadenceOverride>) => void;
}) {
  return (
    <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-lg font-semibold text-slate-900">심볼별 운영 주기 override</h3>
          <StatusPill>{mergedSymbols.length}개 심볼</StatusPill>
        </div>
        <p className="text-sm leading-6 text-slate-600">
          core symbol은 더 짧게, satellite symbol은 더 보수적으로 운영할 수 있습니다. 비워 두면 전역 기본값을 그대로 상속합니다.
        </p>
      </div>
      <div className="mt-4 grid gap-4 2xl:grid-cols-2">
        {overrideRows.map((row) => {
          const effective = effectiveCadenceBySymbol[row.symbol];
          return (
            <div key={row.symbol} className="rounded-2xl border border-amber-200 bg-white p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <p className="text-base font-semibold text-slate-900">{row.symbol}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <StatusPill tone={row.enabled ? "good" : "warn"}>
                      {row.enabled ? "운영 사용" : "운영 제외"}
                    </StatusPill>
                    <StatusPill tone={effective?.uses_global_defaults ? "neutral" : "warn"}>
                      {effective?.uses_global_defaults ? "전역 상속" : "override 적용"}
                    </StatusPill>
                  </div>
                </div>
                <label className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-canvas px-3 py-2 text-sm font-medium text-slate-700">
                  <input
                    checked={row.enabled}
                    onChange={(event) => updateSymbolOverride(row.symbol, { enabled: event.target.checked })}
                    type="checkbox"
                  />
                  사용
                </label>
              </div>

              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <Field label="타임프레임 override" hint="비우면 전역 타임프레임을 사용합니다.">
                  <input
                    className={inputClass}
                    value={row.timeframe_override ?? ""}
                    onChange={(event) => updateSymbolOverride(row.symbol, { timeframe_override: event.target.value || null })}
                    placeholder={form.default_timeframe}
                  />
                </Field>
                <Field label="시장 갱신(분)" hint={`전역 ${form.market_refresh_interval_minutes}분`}>
                  <input
                    className={inputClass}
                    type="number"
                    min={1}
                    max={1440}
                    value={row.market_refresh_interval_minutes_override ?? ""}
                    onChange={(event) => updateSymbolOverride(row.symbol, { market_refresh_interval_minutes_override: numberOrNull(event.target.value) })}
                    placeholder={`${form.market_refresh_interval_minutes}`}
                  />
                </Field>
                <Field label="포지션 관리(초)" hint={`전역 ${form.position_management_interval_seconds}초`}>
                  <input
                    className={inputClass}
                    type="number"
                    min={30}
                    max={3600}
                    value={row.position_management_interval_seconds_override ?? ""}
                    onChange={(event) => updateSymbolOverride(row.symbol, { position_management_interval_seconds_override: numberOrNull(event.target.value) })}
                    placeholder={`${form.position_management_interval_seconds}`}
                  />
                </Field>
                <Field label="신규 판단(분)" hint={`전역 ${form.decision_cycle_interval_minutes}분`}>
                  <input
                    className={inputClass}
                    type="number"
                    min={1}
                    max={1440}
                    value={row.decision_cycle_interval_minutes_override ?? ""}
                    onChange={(event) => updateSymbolOverride(row.symbol, { decision_cycle_interval_minutes_override: numberOrNull(event.target.value) })}
                    placeholder={`${form.decision_cycle_interval_minutes}`}
                  />
                </Field>
                <Field label="AI 최소 호출(분)" hint={`전역 ${form.ai_call_interval_minutes}분`}>
                  <input
                    className={inputClass}
                    type="number"
                    min={5}
                    max={1440}
                    value={row.ai_call_interval_minutes_override ?? ""}
                    onChange={(event) => updateSymbolOverride(row.symbol, { ai_call_interval_minutes_override: numberOrNull(event.target.value) })}
                    placeholder={`${form.ai_call_interval_minutes}`}
                  />
                </Field>
              </div>

              <div className="mt-4 rounded-2xl border border-slate-200 bg-canvas p-4">
                {effective ? (
                  <div className="space-y-3">
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                      <div><p className="text-xs text-slate-500">타임프레임</p><p className="mt-1 text-sm font-semibold text-slate-900">{effective.timeframe}</p></div>
                      <div><p className="text-xs text-slate-500">시장 갱신</p><p className="mt-1 text-sm font-semibold text-slate-900">{effective.market_refresh_interval_minutes}분</p></div>
                      <div><p className="text-xs text-slate-500">포지션 관리</p><p className="mt-1 text-sm font-semibold text-slate-900">{effective.position_management_interval_seconds}초</p></div>
                      <div><p className="text-xs text-slate-500">신규 판단</p><p className="mt-1 text-sm font-semibold text-slate-900">{effective.decision_cycle_interval_minutes}분</p></div>
                      <div><p className="text-xs text-slate-500">AI 최소 호출</p><p className="mt-1 text-sm font-semibold text-slate-900">{effective.ai_call_interval_minutes}분</p></div>
                    </div>
                    <div className="grid gap-3 lg:grid-cols-2">
                      <div className="rounded-2xl bg-white px-4 py-3">
                        <p className="text-xs text-slate-500">월간 AI 예상 / 마지막 AI 판단</p>
                        <p className="mt-2 text-sm font-semibold text-slate-900">{effective.estimated_monthly_ai_calls.toLocaleString("ko-KR")}회</p>
                        <p className="mt-1 break-all text-sm text-slate-700">{formatDisplayValue(effective.last_ai_decision_at, "last_ai_decision_at")}</p>
                      </div>
                      <div className="rounded-2xl bg-white px-4 py-3">
                        <p className="text-xs text-slate-500">최근 실행 / 다음 due</p>
                        <p className="mt-2 break-all text-sm text-slate-700">시장 갱신 {formatDisplayValue(effective.last_market_refresh_at, "last_market_refresh_at")}</p>
                        <p className="mt-1 break-all text-sm text-slate-700">포지션 관리 {formatDisplayValue(effective.last_position_management_at, "last_position_management_at")}</p>
                        <p className="mt-1 break-all text-sm text-slate-700">신규 판단 {formatDisplayValue(effective.last_decision_at, "last_decision_at")}</p>
                        <p className="mt-2 break-all text-sm font-semibold text-slate-900">다음 AI {formatDisplayValue(effective.next_ai_call_due_at, "next_ai_call_due_at")}</p>
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-slate-500">저장 후 실효 cadence와 마지막 실행 시각이 계산됩니다.</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
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

function LiveSyncPanel({ result }: { result: LiveSyncResult | null }) {
  if (!result) return null;
  const protectionEntries = Object.entries(result.symbol_protection_state ?? {});
  const missingProtectionText = renderMissingProtectionItems(result.missing_protection_items);
  const hasProtectionIssues =
    (result.unprotected_positions?.length ?? 0) > 0 ||
    (result.missing_protection_symbols?.length ?? 0) > 0 ||
    protectionEntries.some(([, state]) => !state.protected);
  return (
    <div className="mt-3 space-y-3 rounded-2xl border border-amber-200 bg-white p-4">
      <div className="flex flex-wrap gap-2">
        <StatusPill>동기화 심볼 {result.symbols?.join(", ") ?? "-"}</StatusPill>
        <StatusPill>주문 {result.synced_orders ?? 0}</StatusPill>
        <StatusPill>포지션 {result.synced_positions ?? 0}</StatusPill>
        {typeof result.equity === "number" ? <StatusPill>자산 {formatDisplayValue(result.equity, "equity")}</StatusPill> : null}
      </div>
      <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-600">
        이 결과는 방금 실행한 거래소 동기화와 보호 주문 확인 결과입니다. 실거래 준비 상태, 운영 중지, 가드 모드, 차단 사유 해석은 개요 화면을 기준으로 확인합니다.
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div className={`rounded-2xl px-4 py-3 ${hasProtectionIssues ? "border border-rose-200 bg-rose-50" : "border border-emerald-200 bg-emerald-50"}`}>
          <p className="text-xs text-slate-500">보호 확인 결과</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            {hasProtectionIssues ? "미보호 항목이 있어 보호 조치 확인이 필요합니다." : "포지션과 보호 주문 기준으로 추가 조치가 필요하지 않습니다."}
          </p>
        </div>
        <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="text-xs text-slate-500">누락 보호 항목</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{missingProtectionText}</p>
        </div>
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
    </div>
  );
}

export function SettingsControls({ initial }: { initial: SettingsPayload }) {
  const [state, setState] = useState(initial);
  const [form, setForm] = useState<FormState>(() => toFormState(initial));
  const [message, setMessage] = useState("");
  const [liveSyncResult, setLiveSyncResult] = useState<LiveSyncResult | null>(null);
  const [isPending, startTransition] = useTransition();

  const mergedSymbols = useMemo(() => uniqueSymbols([...form.tracked_symbols, ...form.custom_symbols.split(",")]), [form.custom_symbols, form.tracked_symbols]);
  const overrideRows = useMemo(
    () =>
      mergedSymbols.map((symbol) => {
        const existing = form.symbol_cadence_overrides.find((item) => item.symbol === symbol);
        return (
          existing ?? {
            symbol,
            enabled: true,
            timeframe_override: null,
            market_refresh_interval_minutes_override: null,
            position_management_interval_seconds_override: null,
            decision_cycle_interval_minutes_override: null,
            ai_call_interval_minutes_override: null,
          }
        );
      }),
    [form.symbol_cadence_overrides, mergedSymbols],
  );
  const effectiveCadenceBySymbol = useMemo(
    () => Object.fromEntries(state.symbol_effective_cadences.map((item) => [item.symbol, item])),
    [state.symbol_effective_cadences],
  );
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
  const updateSymbolOverride = (
    symbol: string,
    patch: Partial<SymbolCadenceOverride>,
  ) => {
    setForm((current) => {
      const currentRows = normalizeSymbolOverrides(current.symbol_cadence_overrides);
      const existing = currentRows.find((item) => item.symbol === symbol);
      const nextRow: SymbolCadenceOverride = {
        symbol,
        enabled: existing?.enabled ?? true,
        timeframe_override: existing?.timeframe_override ?? null,
        market_refresh_interval_minutes_override: existing?.market_refresh_interval_minutes_override ?? null,
        position_management_interval_seconds_override: existing?.position_management_interval_seconds_override ?? null,
        decision_cycle_interval_minutes_override: existing?.decision_cycle_interval_minutes_override ?? null,
        ai_call_interval_minutes_override: existing?.ai_call_interval_minutes_override ?? null,
        ...patch,
      };
      const withoutCurrent = currentRows.filter((item) => item.symbol !== symbol);
      return {
        ...current,
        symbol_cadence_overrides: normalizeSymbolOverrides([...withoutCurrent, nextRow]),
      };
    });
  };

  const payload = {
    live_trading_enabled: form.live_trading_enabled,
    rollout_mode: form.rollout_mode,
    limited_live_max_notional: form.limited_live_max_notional,
    manual_live_approval: form.manual_live_approval,
    live_approval_window_minutes: form.live_approval_window_minutes,
    default_symbol: form.default_symbol,
    tracked_symbols: mergedSymbols.length > 0 ? mergedSymbols : [form.default_symbol],
    default_timeframe: form.default_timeframe,
    exchange_sync_interval_seconds: form.exchange_sync_interval_seconds,
    market_refresh_interval_minutes: form.market_refresh_interval_minutes,
    position_management_interval_seconds: form.position_management_interval_seconds,
    schedule_windows: form.schedule_windows,
    symbol_cadence_overrides: normalizeSymbolOverrides(
      form.symbol_cadence_overrides.filter((item) => mergedSymbols.includes(item.symbol)),
    ),
    max_leverage: form.max_leverage,
    max_risk_per_trade: form.max_risk_per_trade,
    max_daily_loss: form.max_daily_loss,
    max_consecutive_losses: form.max_consecutive_losses,
    stale_market_seconds: form.stale_market_seconds,
    slippage_threshold_pct: form.slippage_threshold_pct,
    adaptive_signal_enabled: form.adaptive_signal_enabled,
    position_management_enabled: form.position_management_enabled,
    break_even_enabled: form.break_even_enabled,
    atr_trailing_stop_enabled: form.atr_trailing_stop_enabled,
    partial_take_profit_enabled: form.partial_take_profit_enabled,
    holding_edge_decay_enabled: form.holding_edge_decay_enabled,
    reduce_on_regime_shift_enabled: form.reduce_on_regime_shift_enabled,
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
          <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-600">이 화면은 변경 가능한 설정값과 즉시 제어를 다루되, 현재 gate 상태도 함께 보여줍니다. 심볼별 AI 추천, risk 승인, 실제 실행 흐름은 개요 화면에서 이어서 확인합니다.</p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <StatusPill tone={state.openai_api_key_configured ? "good" : "warn"}>OpenAI: {state.openai_api_key_configured ? "설정됨" : "없음"}</StatusPill>
          <StatusPill tone={state.binance_api_key_configured ? "good" : "warn"}>Binance Key: {state.binance_api_key_configured ? "설정됨" : "없음"}</StatusPill>
          <StatusPill tone={state.binance_api_secret_configured ? "good" : "warn"}>Binance Secret: {state.binance_api_secret_configured ? "설정됨" : "없음"}</StatusPill>
          <StatusPill tone="neutral">심볼별 AI / risk / execution 흐름은 개요에서 확인</StatusPill>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {settingsStageLabels.map((label) => (
          <span key={label} className="rounded-full border border-amber-200 bg-canvas px-3 py-1 text-xs font-semibold text-slate-600">
            {label}
          </span>
        ))}
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="기본 심볼" value={form.default_symbol} tone="dark" />
        <MetricCard label="기본 타임프레임" value={form.default_timeframe} />
        <MetricCard label="현재 월간 AI 호출" value={`${state.estimated_monthly_ai_calls.toLocaleString("ko-KR")}회`} tone="warm" />
        <MetricCard label="AI 활성화 예상" value={`${state.projected_monthly_ai_calls_if_enabled.toLocaleString("ko-KR")}회`} />
      </div>

      <section className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">실거래 제어</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            운영 중지, 승인 창 제어, 거래소 재동기화처럼 즉시 반응이 필요한 제어를 모았습니다. 아래 상태는
            백엔드가 내려준 현재 gate 요약이며, 심볼별 세부 흐름은 개요 화면에서 확인합니다.
          </p>
            <ControlStatusPanel state={state} />
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <Field label="rollout mode">
                <select
                  className={inputClass}
                  value={form.rollout_mode}
                  onChange={(event) => {
                    const nextMode = event.target.value as RolloutMode;
                    updateField("rollout_mode", nextMode);
                  }}
                >
                  {rolloutModeOptions.map((option) => (
                    <option key={option} value={option}>{rolloutModeLabel(option)}</option>
                  ))}
                </select>
              </Field>
              <Field label="승인 유지 시간(분)"><input className={inputClass} min={0} max={240} type="number" value={form.live_approval_window_minutes} onChange={(event) => updateField("live_approval_window_minutes", Number(event.target.value))} /></Field>
              <Field label="limited live 주문당 최대 notional">
                <input
                  className={inputClass}
                  min={1}
                  step="1"
                  type="number"
                  value={form.limited_live_max_notional ?? 500}
                  onChange={(event) => updateField("limited_live_max_notional", Number(event.target.value))}
                />
              </Field>
              <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">환경 게이트</p><p className="mt-2 text-sm font-semibold text-slate-900">{state.live_trading_env_enabled ? "활성" : "비활성"}</p></div>
              <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3"><p className="text-xs text-slate-500">승인 창 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{state.live_execution_armed ? `열림 (${formatDisplayValue(state.live_execution_armed_until, "live_execution_armed_until")})` : "닫힘"}</p></div>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <Toggle checked={form.manual_live_approval} label="수동 승인 정책 사용" onChange={(value) => updateField("manual_live_approval", value)} />
            </div>
          <p className="mt-4 text-sm leading-6 text-slate-600">즉시 중지는 신규 진입만 막는 운영 중지입니다. 기존 포지션의 보호 주문 유지, 축소, 비상 청산은 계속 허용됩니다.</p>
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
          </div>
          <LiveSyncPanel result={liveSyncResult} />
        </div>

        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">시장 / 리스크</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            심볼 구성, 기본 타임프레임, 손실 한도와 같은 전역 입력 기준을 이 영역에서 관리합니다.
          </p>
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
          </div>
        </div>
      </section>
      <section className="grid gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">운영 주기 기본값</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            거래소 동기화, 시장 갱신, 포지션 관리, 신규 판단, AI 최소 호출 간격의 전역 기본값을 분리해 관리합니다.
          </p>
          <div className="mt-4 rounded-2xl border border-amber-200 bg-white px-4 py-3">
            <p className="text-xs text-slate-500">운영 원칙</p>
            <p className="mt-2 text-sm leading-6 text-slate-700">
              거래소 동기화는 전역 공용 주기만 사용합니다. 심볼별 override는 시장 갱신, 포지션 관리, 신규 판단, AI 최소 호출 간격에만 적용됩니다.
            </p>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <Field label="거래소 동기화(초)">
              <input className={inputClass} type="number" min={30} max={3600} value={form.exchange_sync_interval_seconds} onChange={(event) => updateField("exchange_sync_interval_seconds", Number(event.target.value))} />
            </Field>
            <Field label="시장 갱신(분)">
              <input className={inputClass} type="number" min={1} max={1440} value={form.market_refresh_interval_minutes} onChange={(event) => updateField("market_refresh_interval_minutes", Number(event.target.value))} />
            </Field>
            <Field label="포지션 관리(초)">
              <input className={inputClass} type="number" min={30} max={3600} value={form.position_management_interval_seconds} onChange={(event) => updateField("position_management_interval_seconds", Number(event.target.value))} />
            </Field>
            <Field label="신규 판단(분)">
              <input className={inputClass} type="number" min={1} value={form.decision_cycle_interval_minutes} onChange={(event) => updateField("decision_cycle_interval_minutes", Number(event.target.value))} />
            </Field>
            <Field label="AI 최소 호출(분)">
              <input className={inputClass} type="number" min={5} value={form.ai_call_interval_minutes} onChange={(event) => updateField("ai_call_interval_minutes", Number(event.target.value))} />
            </Field>
          </div>
          <div className="mt-4">
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
        </div>

        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">보수적 운영 규칙</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            최근 성과가 나쁠 때는 보수화하고, 열린 포지션은 손절을 넓히지 않는 방향으로만 관리합니다.
          </p>
          <div className="mt-4 space-y-4">
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

      <SymbolCadenceOverridePanel
        mergedSymbols={mergedSymbols}
        overrideRows={overrideRows}
        effectiveCadenceBySymbol={effectiveCadenceBySymbol}
        form={form}
        updateSymbolOverride={updateSymbolOverride}
      />
      <section className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">AI 설정</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            모델과 입력 품질만 조정합니다. 신규 판단 주기와 AI 최소 호출 간격은 위 운영 주기 섹션에서 관리합니다.
          </p>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle checked={form.ai_enabled} label="OpenAI 사용" onChange={(value) => updateField("ai_enabled", value)} />
            <Field label="제공자"><select className={inputClass} value={form.ai_provider} onChange={(event) => updateField("ai_provider", event.target.value as "openai" | "mock")}><option value="openai">OpenAI</option><option value="mock">Mock</option></select></Field>
            <Field label="모델"><input className={inputClass} value={form.ai_model} onChange={(event) => updateField("ai_model", event.target.value)} /></Field>
            <Field label="Temperature"><input className={inputClass} type="number" min={0} max={1} step="0.05" value={form.ai_temperature} onChange={(event) => updateField("ai_temperature", Number(event.target.value))} /></Field>
            <Field label="AI 입력 캔들 수"><input className={inputClass} type="number" min={16} max={200} value={form.ai_max_input_candles} onChange={(event) => updateField("ai_max_input_candles", Number(event.target.value))} /></Field>
            <Field label="OpenAI API Key"><input className={inputClass} type="password" autoComplete="off" value={form.openai_api_key} onChange={(event) => updateField("openai_api_key", event.target.value)} placeholder="sk-..." /></Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_openai_api_key} onChange={(event) => updateField("clear_openai_api_key", event.target.checked)} /> 저장된 키 제거</label>
          </div>
        </div>

        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">Binance 연동</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            시세 사용 여부, 선물/Testnet 경로, API 자격증명을 관리합니다. 실제 계좌 상태 확인은 위 실거래 제어의 거래소 동기화 버튼을 사용합니다.
          </p>
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
          </div>
        </div>
      </section>

      <AIUsagePanel settings={state} />

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <p className="text-sm text-slate-600">추적 심볼, 실거래 설정, 스케줄 윈도우, 인증 정보 변경은 한 번에 함께 저장됩니다.</p>
        <button className="rounded-full bg-amber-400 px-5 py-3 text-sm font-semibold text-slate-900 disabled:opacity-60" disabled={isPending} onClick={save} type="button">{isPending ? "저장 중..." : "설정 저장"}</button>
      </div>
      {message ? <p className="text-sm text-slate-600">{message}</p> : null}
    </div>
  );
}
