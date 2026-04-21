"use client";

import { useEffect, useMemo, useState, useTransition, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { AIUsagePanel, type AIUsagePayload } from "./ai-usage-panel";
import {
  csvToSymbols,
  describeEnrichmentVendors,
  describeEventSourceProvenance,
  describeEventSourceVendor,
  describeAlignmentStatus,
  describeEffectivePolicyPreview,
  describeEnforcementMode,
  describeEventBias,
  describeEventReasonCode,
  describeImportance,
  describeManualWindowFlags,
  describePolicySource,
  describeRiskState,
  describeSourceStatus,
  describeWindowScope,
  formatUtcTimestamp,
  inferEventSourceProvenance,
  isoToUtcInputValue,
  toneForAlignment,
  toneForPolicyPreview,
  toneForSourceStatus,
  type EventOperatorControlPayload,
  type ManualNoTradeWindowPayload,
  utcInputValueToIso,
} from "../lib/event-operator-control.js";
import {
  normalizeSettingsView,
  settingsViewTabs,
  type SettingsView,
} from "../lib/page-config";
import { buildSettingsEventPreviewSummary } from "../lib/settings-event-preview.js";
import { formatDisplayValue } from "../lib/ui-copy";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const symbolOptions = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"];
const rolloutModeOptions = ["paper", "shadow", "live_dry_run", "limited_live", "full_live"] as const;
const operatorBiasOptions = ["bullish", "bearish", "neutral", "no_trade", "unknown"] as const;
const operatorRiskStateOptions = ["risk_on", "risk_off", "neutral", "unknown"] as const;
const operatorEnforcementModeOptions = ["observe_only", "approval_required", "block_on_conflict", "force_no_trade"] as const;
const eventSourceProviderOptions = ["stub", "fred"] as const;

type RolloutMode = (typeof rolloutModeOptions)[number];
type EventSourceProvider = (typeof eventSourceProviderOptions)[number];
type FeedbackTone = "neutral" | "good" | "warn" | "danger";
type FeedbackMessage = { tone: FeedbackTone; text: string };
type FeedbackKey =
  | "control_save"
  | "integration_save"
  | "live_actions"
  | "operator_event"
  | "manual_window";

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
  approval_control_blocked_reasons?: string[];
  live_arm_disabled?: boolean;
  live_arm_disable_reason_code?: string | null;
  live_arm_disable_reason?: string | null;
};

type ReconciliationSummary = {
  position_mode?: string;
  position_mode_checked_at?: string | null;
  guarded_symbols_count?: number;
};

type SettingsCadencePayload = {
  items: SymbolEffectiveCadence[];
};

type OperatorEventFormState = {
  operator_bias: "bullish" | "bearish" | "neutral" | "no_trade" | "unknown";
  operator_risk_state: "risk_on" | "risk_off" | "neutral" | "unknown";
  applies_to_symbols: string;
  horizon: string;
  valid_from: string;
  valid_to: string;
  enforcement_mode: "observe_only" | "approval_required" | "block_on_conflict" | "force_no_trade";
  note: string;
  created_by: string;
};

type ManualWindowFormState = {
  window_id: string | null;
  scope_type: "global" | "symbols";
  symbols: string;
  start_at: string;
  end_at: string;
  reason: string;
  auto_resume: boolean;
  require_manual_rearm: boolean;
  created_by: string;
};

export type SettingsPayload = {
  can_enter_new_position: boolean;
  blocked_reasons: string[];
  id: number;
  mode: string;
  operating_state: string;
  protection_recovery_status: string;
  protection_recovery_active: boolean;
  protection_recovery_failure_count: number;
  missing_protection_symbols: string[];
  missing_protection_items: Record<string, string[]>;
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
  reconciliation_summary: ReconciliationSummary;
  operator_alert?: Record<string, unknown>;
  event_operator_control?: EventOperatorControlPayload | null;
  pause_severity: string | null;
  pause_recovery_class: string | null;
  default_symbol: string;
  tracked_symbols: string[];
  default_timeframe: string;
  exchange_sync_interval_seconds: number;
  market_refresh_interval_minutes: number;
  position_management_interval_seconds: number;
  symbol_cadence_overrides: SymbolCadenceOverride[];
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
  event_source_provider: EventSourceProvider | null;
  event_source_api_url: string | null;
  event_source_timeout_seconds: number | null;
  event_source_default_assets: string[];
  event_source_fred_release_ids: number[];
  event_source_bls_enrichment_url: string | null;
  event_source_bls_enrichment_static_params: Record<string, string>;
  event_source_bea_enrichment_url: string | null;
  event_source_bea_enrichment_static_params: Record<string, string>;
  openai_api_key_configured: boolean;
  binance_api_key_configured: boolean;
  binance_api_secret_configured: boolean;
  event_source_api_key_configured: boolean;
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
  | "adaptive_signal_summary"
  | "position_management_summary"
  | "can_enter_new_position" | "blocked_reasons" | "reconciliation_summary" | "operator_alert"
  | "exchange_submit_allowed"
  | "live_trading_env_enabled" | "live_execution_armed" | "live_execution_armed_until" | "live_execution_ready"
  | "trading_paused" | "guard_mode_reason_category" | "guard_mode_reason_code" | "guard_mode_reason_message" | "pause_reason_code" | "pause_origin" | "pause_reason_detail" | "pause_triggered_at" | "auto_resume_after"
  | "auto_resume_whitelisted" | "auto_resume_eligible" | "auto_resume_status" | "auto_resume_last_blockers" | "latest_blocked_reasons" | "pause_severity"
  | "pause_recovery_class" | "control_status_summary" | "event_operator_control" | "openai_api_key_configured" | "binance_api_key_configured" | "binance_api_secret_configured" | "event_source_api_key_configured"
  | "event_source_provider" | "event_source_api_url" | "event_source_timeout_seconds" | "event_source_default_assets" | "event_source_fred_release_ids"
  | "event_source_bls_enrichment_url" | "event_source_bls_enrichment_static_params" | "event_source_bea_enrichment_url" | "event_source_bea_enrichment_static_params"
> & {
  openai_api_key: string;
  binance_api_key: string;
  binance_api_secret: string;
  event_source_provider: "" | EventSourceProvider;
  event_source_api_url: string;
  event_source_timeout_seconds: number | null;
  event_source_default_assets_input: string;
  event_source_fred_release_ids_input: string;
  event_source_bls_enrichment_url: string;
  event_source_bea_enrichment_url: string;
  event_source_api_key: string;
  custom_symbols: string;
  clear_openai_api_key: boolean;
  clear_binance_api_key: boolean;
  clear_binance_api_secret: boolean;
  clear_event_source_api_key: boolean;
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

function hasCadenceCustomization(row: SymbolCadenceOverride) {
  return (
    !row.enabled ||
    Boolean(row.timeframe_override?.trim()) ||
    row.market_refresh_interval_minutes_override !== null ||
    row.position_management_interval_seconds_override !== null ||
    row.decision_cycle_interval_minutes_override !== null ||
    row.ai_call_interval_minutes_override !== null
  );
}

function csvToPositiveIntegers(value: string) {
  const seen = new Set<number>();
  return value
    .split(",")
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isInteger(item) && item > 0)
    .filter((item) => {
      if (seen.has(item)) return false;
      seen.add(item);
      return true;
    });
}

function describeEnrichmentConfigState(url: string | null | undefined, overrideEnabled: boolean) {
  if (url && url.trim()) {
    return "settings URL 사용";
  }
  return overrideEnabled ? "미설정 (env fallback 가능)" : "env fallback";
}

function describeEventSourceProviderOverride(value: SettingsPayload["event_source_provider"]) {
  if (value === "fred") return "settings=FRED";
  if (value === "stub") return "settings=stub";
  return "env fallback";
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
    event_source_provider: initial.event_source_provider ?? "",
    event_source_api_url: initial.event_source_api_url ?? "",
    event_source_timeout_seconds: initial.event_source_timeout_seconds,
    event_source_default_assets_input: initial.event_source_default_assets.join(", "),
    event_source_fred_release_ids_input: initial.event_source_fred_release_ids.join(", "),
    event_source_bls_enrichment_url: initial.event_source_bls_enrichment_url ?? "",
    event_source_bea_enrichment_url: initial.event_source_bea_enrichment_url ?? "",
    openai_api_key: "",
    binance_api_key: "",
    binance_api_secret: "",
    event_source_api_key: "",
    custom_symbols: initial.tracked_symbols.filter((symbol) => !symbolOptions.includes(symbol)).join(", "),
    clear_openai_api_key: false,
    clear_binance_api_key: false,
    clear_binance_api_secret: false,
    clear_event_source_api_key: false,
  };
}

function toOperatorEventFormState(initial: SettingsPayload): OperatorEventFormState {
  const current = initial.event_operator_control?.operator_event_view;
  return {
    operator_bias: current?.operator_bias ?? "unknown",
    operator_risk_state: current?.operator_risk_state ?? "unknown",
    applies_to_symbols: current?.applies_to_symbols.join(", ") ?? "",
    horizon: current?.horizon ?? "",
    valid_from: isoToUtcInputValue(current?.valid_from),
    valid_to: isoToUtcInputValue(current?.valid_to),
    enforcement_mode: current?.enforcement_mode ?? "observe_only",
    note: current?.note ?? "",
    created_by: current?.created_by ?? "operator-ui",
  };
}

function toManualWindowFormState(window?: ManualNoTradeWindowPayload | null): ManualWindowFormState {
  return {
    window_id: window?.window_id ?? null,
    scope_type: window?.scope.scope_type ?? "global",
    symbols: window?.scope.symbols.join(", ") ?? "",
    start_at: isoToUtcInputValue(window?.start_at),
    end_at: isoToUtcInputValue(window?.end_at),
    reason: window?.reason ?? "",
    auto_resume: window?.auto_resume ?? false,
    require_manual_rearm: window?.require_manual_rearm ?? false,
    created_by: window?.created_by ?? "operator-ui",
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

function InlineFeedback({ message }: { message?: FeedbackMessage | null }) {
  if (!message) {
    return null;
  }
  const className = {
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
    good: "border-emerald-200 bg-emerald-50 text-emerald-800",
    warn: "border-amber-200 bg-amber-50 text-amber-900",
    danger: "border-rose-200 bg-rose-50 text-rose-900",
  }[message.tone];
  return (
    <div aria-live="polite" className={`rounded-2xl border px-4 py-3 text-sm ${className}`} role="status">
      {message.text}
    </div>
  );
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
    case "paper":
      return "페이퍼";
    case "shadow":
      return "섀도";
    case "live_dry_run":
      return "실거래 드라이런";
    case "limited_live":
      return "제한 실거래";
    case "full_live":
      return "전체 실거래";
    default:
      return mode;
  }
}

function resolveControlStatusSummary(state: SettingsPayload): ControlStatusSummary {
  const summary = state.control_status_summary;
  const reconciliation = state.reconciliation_summary ?? {};
  const mode = String(reconciliation.position_mode ?? "").toLowerCase();
  const liveArmDisabledByPositionMode = mode === "hedge" || mode === "unknown";
  const liveArmDisableReason = liveArmDisabledByPositionMode
    ? "one-way required for current local position model"
    : null;
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
    approval_control_blocked_reasons: dedupeReasons(
      summary?.approval_control_blocked_reasons ?? state.blocked_reasons,
    ),
    live_arm_disabled: summary?.live_arm_disabled ?? liveArmDisabledByPositionMode,
    live_arm_disable_reason_code: summary?.live_arm_disable_reason_code ?? null,
    live_arm_disable_reason: summary?.live_arm_disable_reason ?? liveArmDisableReason,
  };
}

function ControlStatusPanel({ state }: { state: SettingsPayload }) {
  const summary = resolveControlStatusSummary(state);
  const currentCycleBlockedReasons = summary.blocked_reasons_current_cycle;
  const approvalBlockedReasons = dedupeReasons(summary.approval_control_blocked_reasons ?? []);
  const primaryBlocker = currentCycleBlockedReasons[0];
  const cards = [
    {
      label: "운영 모드",
      value: rolloutModeLabel(summary.rollout_mode),
      detail:
        summary.rollout_mode === "paper"
          ? "페이퍼 경로만 사용하고 거래소 주문 제출은 비활성화됩니다."
          : summary.rollout_mode === "shadow"
            ? "AI / 리스크 / 실행 intent와 감사 로그까지만 수행하고 실제 주문 제출은 금지됩니다."
            : summary.rollout_mode === "live_dry_run"
              ? "거래소 동기화와 사전 점검까지만 수행하고 실제 주문 제출은 금지됩니다."
              : summary.rollout_mode === "limited_live"
                ? `실제 주문 제출은 허용되지만 주문당 notional이 ${formatDisplayValue(summary.limited_live_max_notional, "limited_live_max_notional")}로 제한됩니다.`
                : "전체 실거래 주문 제출 경로를 사용합니다.",
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
          ? "최근 계좌 동기화에 거래소 canTrade 상태가 없습니다."
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
      label: "실거래 승인 창",
      value: summary.approval_window_open ? "열림" : "닫힘",
      detail: summary.approval_window_open
        ? state.live_execution_armed_until
          ? `만료 ${formatDisplayValue(state.live_execution_armed_until, "live_execution_armed_until")}`
          : "실거래 승인 창이 현재 유효합니다."
        : "신규 진입 전 실거래 승인 창을 다시 열어야 합니다.",
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
      <div className="grid gap-3 md:grid-cols-2">
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

      <div className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-slate-900">approval control summary</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              can_enter_new_position 외에도 승인/운영 제어 관점에서 현재 차단 사유를 분리해 보여줍니다.
            </p>
          </div>
          <StatusPill tone={approvalBlockedReasons.length > 0 ? "danger" : "good"}>
            {approvalBlockedReasons.length > 0 ? `${approvalBlockedReasons.length}건` : "정상"}
          </StatusPill>
        </div>
        <div className="mt-4 space-y-2">
          {approvalBlockedReasons.length === 0 ? (
            <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-500">
              승인/운영 제어 관점에서 즉시 차단 사유가 없습니다.
            </div>
          ) : (
            approvalBlockedReasons.map((reason) => (
              <div key={reason} className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-900">
                {formatDisplayValue(reason, "blocked_reason_codes")}
              </div>
            ))
          )}
        </div>
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
  const customizedRows = overrideRows.filter((row) => hasCadenceCustomization(row));
  const customizedSymbolsSummary =
    customizedRows.length > 0 ? customizedRows.map((row) => row.symbol).join(", ") : "예외 심볼 없음";

  return (
    <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-lg font-semibold text-slate-900">심볼별 운영 주기 override</h3>
          <StatusPill>{mergedSymbols.length}개 심볼</StatusPill>
          <StatusPill tone={customizedRows.length > 0 ? "warn" : "neutral"}>
            예외 적용 {customizedRows.length}개
          </StatusPill>
        </div>
        <p className="text-sm leading-6 text-slate-600">
          기본 화면에서는 전역 운영 주기를 먼저 보고, 예외 심볼은 필요할 때만 열어 수정합니다. 비워 두면 전역 기본값을 그대로 상속합니다.
        </p>
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-4">
          <p className="text-xs text-slate-500">전역 운영 주기 우선</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">
            시장 {form.market_refresh_interval_minutes}분 / 포지션 {form.position_management_interval_seconds}초 / 재검토 {form.decision_cycle_interval_minutes}분 / AI 기준 {form.ai_call_interval_minutes}분
          </p>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            기본 화면에서는 전역 기본값만 빠르게 확인하고, 예외 심볼은 아래 고급 설정에서만 수정합니다.
          </p>
        </div>
        <div className="rounded-2xl border border-amber-200 bg-white px-4 py-4">
          <p className="text-xs text-slate-500">예외 심볼 요약</p>
          <p className="mt-2 text-sm font-semibold text-slate-900">{customizedRows.length}개 심볼에 override 또는 운영 제외 적용</p>
          <p className="mt-2 text-sm leading-6 text-slate-600">{customizedSymbolsSummary}</p>
        </div>
      </div>
      <details className="mt-4 rounded-2xl border border-dashed border-amber-300 bg-white">
        <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3 px-4 py-4">
          <div>
            <p className="text-sm font-semibold text-slate-900">고급 설정: 심볼별 override 상세</p>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              기본 화면 복잡도를 줄이기 위해 상세 입력은 접어 두고, 필요할 때만 펼쳐 수정합니다.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <StatusPill tone={customizedRows.length > 0 ? "warn" : "neutral"}>
              예외 {customizedRows.length}개
            </StatusPill>
            <StatusPill tone="neutral">펼쳐서 수정</StatusPill>
          </div>
        </summary>
        <div className="border-t border-amber-100 px-4 py-4">
          <div className="grid gap-4 2xl:grid-cols-2">
            {overrideRows.map((row) => {
              const effective = effectiveCadenceBySymbol[row.symbol];
              return (
                <div key={row.symbol} className="rounded-2xl border border-amber-200 bg-canvas p-4">
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
                    <label className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-white px-3 py-2 text-sm font-medium text-slate-700">
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
                    <Field label="재검토 확인 주기(분)" hint={`전역 ${form.decision_cycle_interval_minutes}분 · 주기 cycle이 재검토 이벤트를 확인하는 기준`}>
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
                    <Field label="AI 기본 검토 간격(분)" hint={`전역 ${form.ai_call_interval_minutes}분 · 열린 포지션 재검토 기준과 수동 재실행 보호에 사용`}>
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

                  <div className="mt-4 rounded-2xl border border-slate-200 bg-white p-4">
                    {effective ? (
                      <div className="space-y-3">
                        <div className="grid gap-3 sm:grid-cols-2">
                          <div><p className="text-xs text-slate-500">타임프레임</p><p className="mt-1 text-sm font-semibold text-slate-900">{effective.timeframe}</p></div>
                          <div><p className="text-xs text-slate-500">시장 갱신</p><p className="mt-1 text-sm font-semibold text-slate-900">{effective.market_refresh_interval_minutes}분</p></div>
                          <div><p className="text-xs text-slate-500">포지션 관리</p><p className="mt-1 text-sm font-semibold text-slate-900">{effective.position_management_interval_seconds}초</p></div>
                          <div><p className="text-xs text-slate-500">재검토 확인 주기</p><p className="mt-1 text-sm font-semibold text-slate-900">{effective.decision_cycle_interval_minutes}분</p></div>
                          <div><p className="text-xs text-slate-500">AI 기본 검토 간격</p><p className="mt-1 text-sm font-semibold text-slate-900">{effective.ai_call_interval_minutes}분</p></div>
                        </div>
                        <div className="grid gap-3 lg:grid-cols-2">
                          <div className="rounded-2xl bg-slate-50 px-4 py-3">
                            <p className="text-xs text-slate-500">마지막 AI 호출 / AI 검토 기준</p>
                            <p className="mt-2 break-all text-sm font-semibold text-slate-900">{formatDisplayValue(effective.last_ai_decision_at, "last_ai_decision_at")}</p>
                            <p className="mt-1 break-all text-sm text-slate-700">
                              이벤트가 생기면 AI 검토를 시도합니다. 이 값은 열린 포지션 재검토 기준과 수동 재실행 보호에 함께 사용됩니다.
                            </p>
                          </div>
                          <div className="rounded-2xl bg-slate-50 px-4 py-3">
                            <p className="text-xs text-slate-500">최근 사이클 상태</p>
                            <p className="mt-2 break-all text-sm text-slate-700">시장 갱신 {formatDisplayValue(effective.last_market_refresh_at, "last_market_refresh_at")}</p>
                            <p className="mt-1 break-all text-sm text-slate-700">포지션 관리 {formatDisplayValue(effective.last_position_management_at, "last_position_management_at")}</p>
                            <p className="mt-1 break-all text-sm text-slate-700">재검토 확인 {formatDisplayValue(effective.last_decision_at, "last_decision_at")}</p>
                            <p className="mt-2 break-all text-sm font-semibold text-slate-900">
                              재검토 확인 주기 {effective.decision_cycle_interval_minutes}분
                            </p>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <p className="text-sm text-slate-500">저장 후 실제 주기와 마지막 실행 시각이 계산됩니다.</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </details>
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

export function SettingsControls({
  initial,
  initialView = "control",
}: {
  initial: SettingsPayload;
  initialView?: SettingsView;
}) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [state, setState] = useState(initial);
  const [symbolCadences, setSymbolCadences] = useState<SymbolEffectiveCadence[]>([]);
  const [aiUsage, setAiUsage] = useState<AIUsagePayload | null>(null);
  const [form, setForm] = useState<FormState>(() => toFormState(initial));
  const [operatorEventForm, setOperatorEventForm] = useState<OperatorEventFormState>(() => toOperatorEventFormState(initial));
  const [manualWindowForm, setManualWindowForm] = useState<ManualWindowFormState>(() => toManualWindowFormState());
  const [feedback, setFeedback] = useState<Partial<Record<FeedbackKey, FeedbackMessage>>>({});
  const [liveSyncResult, setLiveSyncResult] = useState<LiveSyncResult | null>(null);
  const [isPending, startTransition] = useTransition();
  const activeView = normalizeSettingsView(searchParams.get("view") ?? initialView);
  const viewHref = (view: SettingsView) => {
    const nextParams = new URLSearchParams(searchParams.toString());
    nextParams.set("view", view);
    const query = nextParams.toString();
    return query ? `${pathname}?${query}` : pathname;
  };

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
    () => Object.fromEntries(symbolCadences.map((item) => [item.symbol, item])),
    [symbolCadences],
  );
  const adaptiveSignalSummary = state.adaptive_signal_summary ?? {};
  const positionManagementSummary = state.position_management_summary ?? {};
  const reconciliationSummary = state.reconciliation_summary ?? {};
  const controlSummary = resolveControlStatusSummary(state);
  const eventOperatorControl = state.event_operator_control ?? null;
  const eventContext = eventOperatorControl?.event_context ?? null;
  const eventSourceProvenance = inferEventSourceProvenance(eventContext);
  const eventSourceVendor = eventContext?.source_vendor ?? null;
  const eventEnrichmentVendors = eventContext?.enrichment_vendors ?? [];
  const aiEventView = eventOperatorControl?.ai_event_view ?? null;
  const operatorEventView = eventOperatorControl?.operator_event_view ?? null;
  const alignmentDecision = eventOperatorControl?.alignment_decision ?? null;
  const manualWindows = eventOperatorControl?.manual_no_trade_windows ?? [];
  const activeManualWindows = manualWindows.filter((window) => window.is_active);
  const eventPreviewSummary = buildSettingsEventPreviewSummary(eventOperatorControl);
  const entryPolicySummary = eventPreviewSummary.entryPolicySummary;
  const alignmentReasonSummary = eventPreviewSummary.alignmentReasonSummary;
  const eventSourceHelp = eventPreviewSummary.eventSourceHelp;
  const eventSourceOverrideEnabled = form.event_source_provider !== "";
  const blsEnrichmentConfigState = describeEnrichmentConfigState(form.event_source_bls_enrichment_url, eventSourceOverrideEnabled);
  const beaEnrichmentConfigState = describeEnrichmentConfigState(form.event_source_bea_enrichment_url, eventSourceOverrideEnabled);
  const liveArmBlocked = Boolean(controlSummary.live_arm_disabled);
  const liveArmDisableReason = controlSummary.live_arm_disable_reason;
  const operatorAlertMessage =
    typeof state.operator_alert?.message === "string" ? state.operator_alert.message : null;
  const showOneWayRequiredBanner =
    liveArmBlocked && (operatorAlertMessage === "one-way required for current local position model" || liveArmDisableReason === "one-way required for current local position model");

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

  const refreshAuxiliaryData = async () => {
    const [cadencePayload, usagePayload] = await Promise.all([
      requestJson<SettingsCadencePayload>("/api/settings/cadences"),
      requestJson<AIUsagePayload>("/api/settings/ai-usage"),
    ]);
    setSymbolCadences(cadencePayload.items);
    setAiUsage(usagePayload);
  };

  useEffect(() => {
    void refreshAuxiliaryData().catch(() => {
      setSymbolCadences([]);
      setAiUsage(null);
    });
  }, []);

  const syncSettings = (next: SettingsPayload) => {
    setState(next);
    setForm(toFormState(next));
    setOperatorEventForm(toOperatorEventFormState(next));
    setManualWindowForm(toManualWindowFormState());
    void refreshAuxiliaryData().catch(() => {
      setSymbolCadences([]);
      setAiUsage(null);
    });
  };
  const setSectionFeedback = (key: FeedbackKey, tone: FeedbackTone, text: string) =>
    setFeedback((current) => ({ ...current, [key]: { tone, text } }));
  const updateField = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((current) => ({ ...current, [key]: value }));
  const updateOperatorEventField = <K extends keyof OperatorEventFormState>(key: K, value: OperatorEventFormState[K]) =>
    setOperatorEventForm((current) => ({ ...current, [key]: value }));
  const updateManualWindowField = <K extends keyof ManualWindowFormState>(key: K, value: ManualWindowFormState[K]) =>
    setManualWindowForm((current) => ({ ...current, [key]: value }));
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
    live_trading_enabled: form.rollout_mode !== "paper",
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
    event_source_provider: form.event_source_provider || null,
    event_source_api_url: form.event_source_api_url,
    event_source_timeout_seconds: form.event_source_timeout_seconds,
    event_source_default_assets: uniqueSymbols(form.event_source_default_assets_input.split(",")),
    event_source_fred_release_ids: csvToPositiveIntegers(form.event_source_fred_release_ids_input),
    event_source_bls_enrichment_url: form.event_source_bls_enrichment_url || null,
    event_source_bls_enrichment_static_params: {},
    event_source_bea_enrichment_url: form.event_source_bea_enrichment_url || null,
    event_source_bea_enrichment_static_params: {},
    openai_api_key: form.openai_api_key || null,
    binance_api_key: form.binance_api_key || null,
    binance_api_secret: form.binance_api_secret || null,
    event_source_api_key: form.event_source_api_key || null,
    clear_openai_api_key: form.clear_openai_api_key,
    clear_binance_api_key: form.clear_binance_api_key,
    clear_binance_api_secret: form.clear_binance_api_secret,
    clear_event_source_api_key: form.clear_event_source_api_key,
  };

  const save = (feedbackKey: "control_save" | "integration_save") => {
    startTransition(() => {
      void requestJson<SettingsPayload>("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
        .then((result) => {
          syncSettings(result);
          setSectionFeedback(feedbackKey, "good", "전체 설정을 저장했습니다.");
        })
        .catch((error: unknown) => {
          setSectionFeedback(feedbackKey, "danger", error instanceof Error ? error.message : "설정 저장에 실패했습니다.");
        });
    });
  };

  const runPost = (
    path: string,
    successMessage: string,
    feedbackKey: "live_actions" | "operator_event" | "manual_window",
    onSuccess?: (data: any) => void,
    body?: object,
  ) => {
    startTransition(() => {
      void requestJson(path, { method: "POST", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined })
        .then((result) => {
          onSuccess?.(result);
          setSectionFeedback(feedbackKey, "good", successMessage);
        })
        .catch((error: unknown) => {
          setSectionFeedback(feedbackKey, "danger", error instanceof Error ? error.message : "요청 처리에 실패했습니다.");
        });
    });
  };
  const actionsUseSavedSettings =
    form.live_approval_window_minutes !== state.live_approval_window_minutes ||
    form.default_symbol !== state.default_symbol;

  const runPut = (
    path: string,
    successMessage: string,
    feedbackKey: "operator_event" | "manual_window",
    body: object,
    onSuccess?: (data: any) => void,
  ) => {
    startTransition(() => {
      void requestJson(path, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then((result) => {
          onSuccess?.(result);
          setSectionFeedback(feedbackKey, "good", successMessage);
        })
        .catch((error: unknown) => {
          setSectionFeedback(feedbackKey, "danger", error instanceof Error ? error.message : "요청 처리에 실패했습니다.");
        });
    });
  };

  const saveOperatorEventView = () => {
    const validFrom = utcInputValueToIso(operatorEventForm.valid_from);
    const validTo = utcInputValueToIso(operatorEventForm.valid_to);
    if (validFrom && validTo && new Date(validTo).getTime() <= new Date(validFrom).getTime()) {
      setSectionFeedback("operator_event", "danger", "운영자 이벤트 뷰의 종료 시각은 시작 시각보다 뒤여야 합니다.");
      return;
    }
    runPut(
      "/api/settings/operator-event-view",
      "운영자 이벤트 뷰를 저장했습니다.",
      "operator_event",
      {
        operator_bias: operatorEventForm.operator_bias,
        operator_risk_state: operatorEventForm.operator_risk_state,
        applies_to_symbols: csvToSymbols(operatorEventForm.applies_to_symbols),
        horizon: operatorEventForm.horizon.trim() || null,
        valid_from: validFrom,
        valid_to: validTo,
        enforcement_mode: operatorEventForm.enforcement_mode,
        note: operatorEventForm.note.trim() || null,
        created_by: operatorEventForm.created_by.trim() || "operator-ui",
      },
      syncSettings,
    );
  };

  const clearOperatorEventView = () => {
    runPost(
      "/api/settings/operator-event-view/clear",
      "운영자 이벤트 뷰를 해제했습니다.",
      "operator_event",
      syncSettings,
      { created_by: operatorEventForm.created_by.trim() || "operator-ui" },
    );
  };

  const resetManualWindowForm = () => {
    setManualWindowForm(toManualWindowFormState());
  };

  const editManualWindow = (window: ManualNoTradeWindowPayload) => {
    setManualWindowForm(toManualWindowFormState(window));
  };

  const saveManualWindow = () => {
    const startAt = utcInputValueToIso(manualWindowForm.start_at);
    const endAt = utcInputValueToIso(manualWindowForm.end_at);
    if (!startAt || !endAt) {
      setSectionFeedback("manual_window", "danger", "수동 노트레이드 윈도우의 시작/종료 시각을 UTC 기준으로 입력해야 합니다.");
      return;
    }
    if (new Date(endAt).getTime() <= new Date(startAt).getTime()) {
      setSectionFeedback("manual_window", "danger", "수동 노트레이드 윈도우의 종료 시각은 시작 시각보다 뒤여야 합니다.");
      return;
    }
    const scopeSymbols = csvToSymbols(manualWindowForm.symbols);
    if (manualWindowForm.scope_type === "symbols" && scopeSymbols.length === 0) {
      setSectionFeedback("manual_window", "danger", "심볼 지정 범위를 선택한 경우 최소 1개 심볼이 필요합니다.");
      return;
    }
    const payload = {
      scope: {
        scope_type: manualWindowForm.scope_type,
        symbols: manualWindowForm.scope_type === "symbols" ? scopeSymbols : [],
      },
      start_at: startAt,
      end_at: endAt,
      reason: manualWindowForm.reason.trim(),
      auto_resume: manualWindowForm.auto_resume,
      require_manual_rearm: manualWindowForm.require_manual_rearm,
      created_by: manualWindowForm.created_by.trim() || "operator-ui",
    };
    if (!payload.reason) {
      setSectionFeedback("manual_window", "danger", "수동 노트레이드 윈도우 사유를 입력해야 합니다.");
      return;
    }
    if (manualWindowForm.window_id) {
      runPut(
        `/api/settings/manual-no-trade-windows/${encodeURIComponent(manualWindowForm.window_id)}`,
        "수동 노트레이드 윈도우를 수정했습니다.",
        "manual_window",
        payload,
        syncSettings,
      );
      return;
    }
    runPost(
      "/api/settings/manual-no-trade-windows",
      "수동 노트레이드 윈도우를 생성했습니다.",
      "manual_window",
      syncSettings,
      payload,
    );
  };

  const endManualWindow = (windowId: string) => {
    runPost(
      `/api/settings/manual-no-trade-windows/${encodeURIComponent(windowId)}/end`,
      "수동 노트레이드 윈도우를 종료했습니다.",
      "manual_window",
      syncSettings,
      { created_by: manualWindowForm.created_by.trim() || "operator-ui" },
    );
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
          <StatusPill tone={state.openai_api_key_configured ? "good" : "warn"}>OpenAI 키: {state.openai_api_key_configured ? "설정됨" : "없음"}</StatusPill>
          <StatusPill tone={state.binance_api_key_configured ? "good" : "warn"}>Binance 키: {state.binance_api_key_configured ? "설정됨" : "없음"}</StatusPill>
          <StatusPill tone={state.binance_api_secret_configured ? "good" : "warn"}>Binance 시크릿: {state.binance_api_secret_configured ? "설정됨" : "없음"}</StatusPill>
          <StatusPill tone={state.event_source_api_key_configured ? "good" : "neutral"}>FRED 키: {state.event_source_api_key_configured ? "설정됨" : "없음"}</StatusPill>
          <StatusPill tone="neutral">이벤트 소스 설정: {describeEventSourceProviderOverride(state.event_source_provider)}</StatusPill>
          <StatusPill tone="neutral">심볼별 AI / 리스크 / 실행 흐름은 개요 화면에서 확인</StatusPill>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {settingsViewTabs.map((tab) => {
          const active = activeView === tab.value;
          return (
            <Link
              key={tab.value}
              href={viewHref(tab.value)}
              className={`rounded-[1.5rem] border px-4 py-4 transition ${
                active
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-amber-200 bg-canvas text-slate-700 hover:border-amber-300 hover:bg-white"
              }`}
            >
              <p className="text-sm font-semibold">{tab.label}</p>
              <p className={`mt-2 text-sm leading-6 ${active ? "text-white/80" : "text-slate-500"}`}>{tab.description}</p>
            </Link>
          );
        })}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <MetricCard label="운영 모드" value={rolloutModeLabel(form.rollout_mode)} tone="dark" />
        <MetricCard label="기본 심볼 / 타임프레임" value={`${form.default_symbol} / ${form.default_timeframe}`} />
        <MetricCard label="AI 동작 방식" value="이벤트 기반 + 주기 백스톱" tone="warm" />
        <MetricCard
          label="최근 24시간 AI 호출"
          value={aiUsage ? `${aiUsage.recent_ai_calls_24h.toLocaleString("ko-KR")}회` : "불러오는 중"}
        />
      </div>

      {showOneWayRequiredBanner ? (
        <div className="rounded-2xl border border-rose-300 bg-rose-50 px-4 py-4 text-sm text-rose-900">
          <p className="font-semibold">실거래 승인 제한</p>
          <p className="mt-2">{operatorAlertMessage ?? liveArmDisableReason}</p>
          <p className="mt-2">
            position_mode={formatDisplayValue(reconciliationSummary.position_mode ?? "unknown")} / guarded_symbols=
            {formatDisplayValue(reconciliationSummary.guarded_symbols_count ?? 0)} / checked_at=
            {formatDisplayValue(reconciliationSummary.position_mode_checked_at ?? null)}
          </p>
        </div>
      ) : null}

      <div className={activeView === "control" ? "space-y-5" : "hidden"} aria-hidden={activeView !== "control"}>
        <section className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">실거래 제어</h3>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
                운영 중지, 승인 창 제어, 거래소 재동기화처럼 즉시 반응이 필요한 제어를 상단 핵심 패널로 모았습니다.
                아래 상태는 백엔드가 내려준 현재 gate 요약이며, 심볼별 세부 흐름은 개요 화면에서 확인합니다.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <StatusPill tone={state.trading_paused ? "danger" : "good"}>
                {state.trading_paused ? "pause 활성" : "운영 중"}
              </StatusPill>
              <StatusPill tone={state.live_execution_armed ? "good" : "warn"}>
                {state.live_execution_armed ? "approval armed" : "approval 닫힘"}
              </StatusPill>
              <StatusPill tone={state.live_execution_ready ? "good" : "warn"}>
                {state.live_execution_ready ? "live ready" : "live guard"}
              </StatusPill>
              <StatusPill tone={state.exchange_submit_allowed ? "good" : "neutral"}>
                {state.exchange_submit_allowed ? "실주문 제출 허용" : "실주문 제출 제한"}
              </StatusPill>
            </div>
          </div>
          <ControlStatusPanel state={state} />
          <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">즉시 실행 액션</p>
                  <p className="mt-1 text-sm leading-6 text-slate-600">
                    pause/resume/live arm/sync는 저장된 현재 설정 기준으로만 실행합니다.
                  </p>
                </div>
                <StatusPill tone="neutral">저장값 기준 실행</StatusPill>
              </div>
              <p className="mt-4 text-sm leading-6 text-slate-600">
                즉시 중지는 신규 진입만 막는 운영 중지입니다. 기존 포지션의 보호 주문 유지, 축소, 비상 청산은 계속 허용됩니다.
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <button className="rounded-full bg-rose-600 px-4 py-2 text-sm font-semibold text-white" onClick={() => runPost("/api/settings/pause", "거래를 일시 중지했습니다.", "live_actions", syncSettings)} type="button">즉시 중지</button>
                <button className="rounded-full bg-emerald-600 px-4 py-2 text-sm font-semibold text-white" onClick={() => runPost("/api/settings/resume", "거래 일시 중지를 해제했습니다.", "live_actions", syncSettings)} type="button">중지 해제</button>
                <button
                  className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-400"
                  disabled={liveArmBlocked}
                  onClick={() => runPost("/api/settings/live/arm", "실거래 승인 창을 열었습니다.", "live_actions", syncSettings, { minutes: state.live_approval_window_minutes })}
                  type="button"
                >
                  실거래 승인
                </button>
                <button className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700" onClick={() => runPost("/api/settings/live/disarm", "실거래 승인 창을 닫았습니다.", "live_actions", syncSettings)} type="button">승인 해제</button>
                <button
                  className="rounded-full border border-amber-200 px-4 py-2 text-sm font-semibold text-slate-700"
                  onClick={async () => {
                    try {
                      setLiveSyncResult(await requestJson<LiveSyncResult>(`/api/live/sync?symbol=${encodeURIComponent(state.default_symbol)}`, { method: "POST" }));
                      setSectionFeedback("live_actions", "good", "거래소 상태와 보호 주문 상태를 동기화했습니다.");
                    } catch (error: unknown) {
                      if (error instanceof ApiRequestError && error.payload && typeof error.payload === "object") {
                        const detail = "detail" in error.payload ? (error.payload as { detail?: unknown }).detail : error.payload;
                        if (detail && typeof detail === "object") {
                          setLiveSyncResult(detail as LiveSyncResult);
                        }
                      }
                      setSectionFeedback("live_actions", "danger", error instanceof Error ? error.message : "거래소 동기화에 실패했습니다.");
                    }
                  }}
                  type="button"
                >
                  거래소 동기화
                </button>
              </div>
              <p className="mt-3 text-xs leading-5 text-slate-500">
                실거래 승인 시간: 저장값 {state.live_approval_window_minutes}분 / 동기화 심볼: 저장값 {state.default_symbol}
                {actionsUseSavedSettings ? " / 현재 form 입력과 저장값이 다르면 저장 후 다시 실행하세요." : ""}
              </p>
              <div className="mt-3">
                <InlineFeedback message={feedback.live_actions} />
              </div>
              {liveArmBlocked && liveArmDisableReason ? (
                <p className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
                  실거래 승인 버튼 비활성화 사유: {liveArmDisableReason}
                </p>
              ) : null}
              <LiveSyncPanel result={liveSyncResult} />
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">저장형 운영 기본값</p>
                  <p className="mt-1 text-sm leading-6 text-slate-600">
                    운영 모드, 승인 유지 시간, limited live 한도는 저장 후에만 런타임에 반영됩니다.
                  </p>
                </div>
                <StatusPill tone="neutral">저장 후 반영</StatusPill>
              </div>
              <div className="mt-4 grid gap-4 md:grid-cols-2">
                <Field label="운영 모드">
                  <select
                    className={inputClass}
                    value={form.rollout_mode}
                    onChange={(event) => {
                      const nextMode = event.target.value as RolloutMode;
                      updateField("rollout_mode", nextMode);
                      updateField("live_trading_enabled", nextMode !== "paper");
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
                <div className="rounded-2xl border border-amber-200 bg-canvas px-4 py-3"><p className="text-xs text-slate-500">환경 게이트</p><p className="mt-2 text-sm font-semibold text-slate-900">{state.live_trading_env_enabled ? "활성" : "비활성"}</p></div>
                <div className="rounded-2xl border border-amber-200 bg-canvas px-4 py-3"><p className="text-xs text-slate-500">승인 창 상태</p><p className="mt-2 text-sm font-semibold text-slate-900">{state.live_execution_armed ? `열림 (${formatDisplayValue(state.live_execution_armed_until, "live_execution_armed_until")})` : "닫힘"}</p></div>
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <Toggle checked={form.manual_live_approval} label="수동 승인 정책 사용" onChange={(value) => updateField("manual_live_approval", value)} />
              </div>
            </div>
          </div>
        </section>

        <section className="grid gap-5 xl:grid-cols-2">
          <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">시장 / 리스크</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            심볼 구성, 기본 시장 타임프레임, 손실 한도와 같은 전역 입력 기준을 이 영역에서 관리합니다.
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
              <Field label="기본 시장 타임프레임" hint="AI 호출 주기가 아니라 캔들/시장 기준 타임프레임입니다.">
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
            </div>
          </div>
          </div>
        </section>
      <section className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">이벤트 대응 현황</h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                시장 분위기와 별도로, 이벤트 전후에 신규 진입을 어떻게 다룰지 보여주는 화면입니다. 실제 신규 진입 판단에도 같은 기준을 사용합니다.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <StatusPill tone="neutral">{state.default_symbol}</StatusPill>
              <StatusPill tone={toneForSourceStatus(eventContext?.source_status)}>
                {describeSourceStatus(eventContext?.source_status, { kind: "event_context" })}
              </StatusPill>
              <StatusPill tone="neutral">{describeEventSourceProvenance(eventSourceProvenance)}</StatusPill>
              <StatusPill tone={toneForPolicyPreview(eventOperatorControl?.effective_policy_preview)}>실제 진입 판단과 동일</StatusPill>
            </div>
          </div>

          <div className="mt-4 rounded-2xl border border-slate-200 bg-white px-4 py-4">
            <p className="text-xs text-slate-500">신규 진입 1줄 요약</p>
            <p className="mt-2 text-sm font-semibold text-slate-900">{entryPolicySummary}</p>
            <p className="mt-2 text-xs text-slate-500">
              데이터 안내: {eventSourceHelp}
            </p>
          </div>

          <div className="mt-4 space-y-4">
            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">예정 이벤트 리스크</p>
                  <p className="mt-1 text-sm text-slate-600">데이터가 늦거나 일부 비어 있어도 그대로 알려줍니다.</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <StatusPill tone={toneForSourceStatus(eventContext?.source_status)}>
                    {describeSourceStatus(eventContext?.source_status, { kind: "event_context" })}
                  </StatusPill>
                  <StatusPill tone="neutral">{describeEventSourceProvenance(eventSourceProvenance)}</StatusPill>
                  {eventContext?.is_stale ? <StatusPill tone="warn">지연</StatusPill> : null}
                  {eventContext && !eventContext.is_complete ? <StatusPill tone="warn">불완전</StatusPill> : null}
                </div>
              </div>
              <p className="mt-3 text-xs text-slate-500">{eventSourceHelp}</p>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">다음 이벤트</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{eventContext?.next_event_name ?? "정보 없음"}</p>
                </div>
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">이벤트 시각</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{formatUtcTimestamp(eventContext?.next_event_at)}</p>
                </div>
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">남은 시간</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">
                    {typeof eventContext?.minutes_to_next_event === "number" ? `${eventContext.minutes_to_next_event}분` : "정보 없음"}
                  </p>
                </div>
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">중요도</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{describeImportance(eventContext?.next_event_importance)}</p>
                </div>
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">위험 구간</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">
                    {eventContext?.active_risk_window ? "활성" : "비활성"}
                  </p>
                  <p className="mt-2 text-xs text-slate-500">
                    {eventContext?.active_risk_window_detail?.event_name ?? eventContext?.summary_note ?? "추가 안내 없음"}
                  </p>
                </div>
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">생성 시각</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{formatUtcTimestamp(eventContext?.generated_at)}</p>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <p className="text-sm font-semibold text-slate-900">AI 이벤트 뷰</p>
              <p className="mt-1 text-sm text-slate-600">AI가 이벤트 관련 의견을 남기지 않았으면 그대로 "미설정"으로 표시합니다.</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <StatusPill tone={toneForSourceStatus(aiEventView?.source_state)}>
                  {describeSourceStatus(aiEventView?.source_state, { kind: "ai_event_view" })}
                </StatusPill>
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">AI 방향</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{describeEventBias(aiEventView?.ai_bias)}</p>
                </div>
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">AI 리스크 상태</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{describeRiskState(aiEventView?.ai_risk_state)}</p>
                </div>
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">AI 신뢰도</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">
                    {typeof aiEventView?.ai_confidence === "number" ? aiEventView.ai_confidence.toFixed(2) : "정보 없음"}
                  </p>
                </div>
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">AI 의견 상태</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">
                    {describeSourceStatus(aiEventView?.source_state, { kind: "ai_event_view" })}
                  </p>
                </div>
              </div>
              <div className="mt-3 grid gap-3">
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">시나리오 메모</p>
                  <p className="mt-2 text-sm text-slate-800">{aiEventView?.scenario_note ?? "정보 없음"}</p>
                </div>
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">신뢰도 조정 이유</p>
                  <p className="mt-2 text-sm text-slate-800">{aiEventView?.confidence_penalty_reason ?? "정보 없음"}</p>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">정렬 결과</p>
                  <p className="mt-1 text-sm text-slate-600">AI 의견과 운영자 설정을 비교한 결과입니다.</p>
                </div>
                <StatusPill tone={toneForAlignment(alignmentDecision?.alignment_status)}>{describeAlignmentStatus(alignmentDecision?.alignment_status)}</StatusPill>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {alignmentDecision && alignmentDecision.reason_codes.length > 0 ? alignmentDecision.reason_codes.map((reason) => (
                  <StatusPill key={reason} tone={reason.includes("conflict") || reason.includes("no_trade") ? "danger" : reason.includes("unavailable") || reason.includes("outside_valid_window") || reason.includes("insufficient_data") ? "warn" : "neutral"}>
                    {describeEventReasonCode(reason)}
                  </StatusPill>
                )) : <StatusPill tone="neutral">추가 사유 없음</StatusPill>}
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">핵심 사유</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">
                    {alignmentReasonSummary}
                  </p>
                </div>
                <div className="rounded-2xl bg-slate-50 px-4 py-3">
                  <p className="text-xs text-slate-500">평가 시각</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{formatUtcTimestamp(alignmentDecision?.evaluated_at)}</p>
                </div>
              </div>
              <div className="mt-3 rounded-2xl border border-dashed border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                지금 신규 진입은 "{describeEffectivePolicyPreview(eventOperatorControl?.effective_policy_preview)}" 상태입니다.
                {" "}이 판단은 {describePolicySource(eventOperatorControl?.policy_source)} 기준으로 계산되며,
                {" "}청산·축소 같은 안전 조치는 계속 허용됩니다.
              </div>
            </div>
          </div>
        </div>

        <div className="space-y-5">
          <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">운영자 이벤트 뷰</h3>
                <p className="mt-2 text-sm leading-6 text-slate-600">
                  운영자가 이벤트 전후 대응 방향을 직접 저장하는 곳입니다. 여기에 넣은 내용은 신규 진입 판단에 반영됩니다.
                </p>
              </div>
              <StatusPill tone="neutral">{describeEnforcementMode(operatorEventView?.enforcement_mode)}</StatusPill>
            </div>

              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl bg-white px-4 py-3">
                  <p className="text-xs text-slate-500">운영자 방향</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{describeEventBias(operatorEventView?.operator_bias)}</p>
                </div>
                <div className="rounded-2xl bg-white px-4 py-3">
                  <p className="text-xs text-slate-500">운영자 리스크 상태</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">{describeRiskState(operatorEventView?.operator_risk_state)}</p>
                </div>
                <div className="rounded-2xl bg-white px-4 py-3">
                  <p className="text-xs text-slate-500">유효 시간</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">
                    {formatUtcTimestamp(operatorEventView?.valid_from)} ~ {formatUtcTimestamp(operatorEventView?.valid_to)}
                  </p>
                </div>
                <div className="rounded-2xl bg-white px-4 py-3">
                  <p className="text-xs text-slate-500">적용 심볼</p>
                  <p className="mt-2 text-sm font-semibold text-slate-900">
                    {operatorEventView && operatorEventView.applies_to_symbols.length > 0 ? operatorEventView.applies_to_symbols.join(", ") : "전체 심볼"}
                  </p>
                </div>
              </div>

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <Field label="운영자 방향">
                <select className={inputClass} value={operatorEventForm.operator_bias} onChange={(event) => updateOperatorEventField("operator_bias", event.target.value as OperatorEventFormState["operator_bias"])}>
                  {operatorBiasOptions.map((option) => <option key={option} value={option}>{describeEventBias(option)}</option>)}
                </select>
              </Field>
              <Field label="운영자 리스크 상태">
                <select className={inputClass} value={operatorEventForm.operator_risk_state} onChange={(event) => updateOperatorEventField("operator_risk_state", event.target.value as OperatorEventFormState["operator_risk_state"])}>
                  {operatorRiskStateOptions.map((option) => <option key={option} value={option}>{describeRiskState(option)}</option>)}
                </select>
              </Field>
              <Field label="적용 심볼" hint="비워 두면 모든 심볼에 적용합니다.">
                <input className={inputClass} value={operatorEventForm.applies_to_symbols} onChange={(event) => updateOperatorEventField("applies_to_symbols", event.target.value.toUpperCase())} placeholder="BTCUSDT, ETHUSDT" />
              </Field>
              <Field label="영향 기간/관점" hint="예: 오늘 이벤트 전후, 이번 주, CPI 발표 전후">
                <input className={inputClass} value={operatorEventForm.horizon} onChange={(event) => updateOperatorEventField("horizon", event.target.value)} placeholder="예: 오늘 이벤트 전후" />
              </Field>
              <Field label="적용 시작 시각 (UTC)">
                <input className={inputClass} type="datetime-local" value={operatorEventForm.valid_from} onChange={(event) => updateOperatorEventField("valid_from", event.target.value)} />
              </Field>
              <Field label="적용 종료 시각 (UTC)">
                <input className={inputClass} type="datetime-local" value={operatorEventForm.valid_to} onChange={(event) => updateOperatorEventField("valid_to", event.target.value)} />
              </Field>
              <Field label="반영 방식" hint="참고만 둘지, 다시 확인할지, 신규 진입을 멈출지 정합니다.">
                <select className={inputClass} value={operatorEventForm.enforcement_mode} onChange={(event) => updateOperatorEventField("enforcement_mode", event.target.value as OperatorEventFormState["enforcement_mode"])}>
                  {operatorEnforcementModeOptions.map((option) => <option key={option} value={option}>{describeEnforcementMode(option)}</option>)}
                </select>
              </Field>
              <Field label="입력자">
                <input className={inputClass} value={operatorEventForm.created_by} onChange={(event) => updateOperatorEventField("created_by", event.target.value)} />
              </Field>
            </div>
            <div className="mt-4">
              <Field label="메모" hint="운영자가 나중에 다시 봐도 이해될 정도로 간단히 남기면 됩니다.">
                <textarea className={inputClass} rows={3} value={operatorEventForm.note} onChange={(event) => updateOperatorEventField("note", event.target.value)} placeholder="예: 발표 직후 변동성이 가라앉을 때까지 신규 진입 보수적으로" />
              </Field>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <button className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:bg-slate-400" disabled={isPending} onClick={saveOperatorEventView} type="button">
                {isPending ? "저장 중..." : "내용 저장"}
              </button>
              <button className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700" disabled={isPending} onClick={clearOperatorEventView} type="button">
                설정 해제
              </button>
            </div>
            <div className="mt-3">
              <InlineFeedback message={feedback.operator_event} />
            </div>
          </div>

          <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">수동 노트레이드 윈도우</h3>
                <p className="mt-2 text-sm leading-6 text-slate-600">
                  직접 신규 진입 금지 시간을 설정하는 기능입니다. 시작 시각부터 종료 시각 직전까지 적용되며, 여러 구간이 겹쳐도 함께 유지됩니다.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <StatusPill tone={activeManualWindows.length > 0 ? "danger" : "neutral"}>현재 적용 중 {activeManualWindows.length}개</StatusPill>
                <StatusPill tone="neutral">UTC 기준</StatusPill>
              </div>
            </div>

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <Field label="적용 범위">
                <select className={inputClass} value={manualWindowForm.scope_type} onChange={(event) => updateManualWindowField("scope_type", event.target.value as ManualWindowFormState["scope_type"])}>
                  <option value="global">전체</option>
                  <option value="symbols">심볼 지정</option>
                </select>
              </Field>
              <Field label="입력자">
                <input className={inputClass} value={manualWindowForm.created_by} onChange={(event) => updateManualWindowField("created_by", event.target.value)} />
              </Field>
              <Field label="심볼" hint="적용 범위가 심볼 지정일 때만 사용합니다.">
                <input className={inputClass} value={manualWindowForm.symbols} onChange={(event) => updateManualWindowField("symbols", event.target.value.toUpperCase())} placeholder="BTCUSDT, ETHUSDT" />
              </Field>
              <Field label="사유">
                <input className={inputClass} value={manualWindowForm.reason} onChange={(event) => updateManualWindowField("reason", event.target.value)} placeholder="거시 이벤트 전후 수동 노트레이드" />
              </Field>
              <Field label="적용 시작 시각 (UTC)">
                <input className={inputClass} type="datetime-local" value={manualWindowForm.start_at} onChange={(event) => updateManualWindowField("start_at", event.target.value)} />
              </Field>
              <Field label="적용 종료 시각 (UTC)">
                <input className={inputClass} type="datetime-local" value={manualWindowForm.end_at} onChange={(event) => updateManualWindowField("end_at", event.target.value)} />
              </Field>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <Toggle checked={manualWindowForm.auto_resume} label="종료 후 자동 복귀 표시" onChange={(value) => updateManualWindowField("auto_resume", value)} />
              <Toggle checked={manualWindowForm.require_manual_rearm} label="재개 전 수동 확인 표시" onChange={(value) => updateManualWindowField("require_manual_rearm", value)} />
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              <button className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:bg-slate-400" disabled={isPending} onClick={saveManualWindow} type="button">
                {manualWindowForm.window_id ? "시간 수정" : "시간 추가"}
              </button>
              <button className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700" onClick={resetManualWindowForm} type="button">
                입력 지우기
              </button>
            </div>
            <div className="mt-3">
              <InlineFeedback message={feedback.manual_window} />
            </div>

            <div className="mt-4 space-y-3">
              {manualWindows.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-amber-200 px-4 py-4 text-sm text-slate-500">
                  현재 저장된 수동 노트레이드 윈도우가 없습니다.
                </div>
              ) : (
                manualWindows.map((window) => (
                  <div key={window.window_id} className="rounded-2xl border border-slate-200 bg-white p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex flex-wrap gap-2">
                        <StatusPill tone={window.is_active ? "danger" : "neutral"}>{window.is_active ? "활성" : "비활성"}</StatusPill>
                        <StatusPill tone="neutral">{window.window_id}</StatusPill>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <button className="rounded-full border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-700" onClick={() => editManualWindow(window)} type="button">수정</button>
                        <button className="rounded-full border border-rose-200 px-3 py-1 text-xs font-semibold text-rose-700" onClick={() => endManualWindow(window.window_id)} type="button">종료</button>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <div className="rounded-2xl bg-slate-50 px-4 py-3">
                        <p className="text-xs text-slate-500">적용 범위</p>
                        <p className="mt-2 text-sm font-semibold text-slate-900">{describeWindowScope(window.scope)}</p>
                      </div>
                      <div className="rounded-2xl bg-slate-50 px-4 py-3">
                        <p className="text-xs text-slate-500">적용 시간</p>
                        <p className="mt-2 text-sm font-semibold text-slate-900">
                          {formatUtcTimestamp(window.start_at)} ~ {formatUtcTimestamp(window.end_at)}
                        </p>
                      </div>
                      <div className="rounded-2xl bg-slate-50 px-4 py-3">
                        <p className="text-xs text-slate-500">사유</p>
                        <p className="mt-2 text-sm text-slate-800">{window.reason}</p>
                      </div>
                      <div className="rounded-2xl bg-slate-50 px-4 py-3">
                        <p className="text-xs text-slate-500">부가 플래그</p>
                        <p className="mt-2 text-sm text-slate-800">
                          {describeManualWindowFlags(window.auto_resume, window.require_manual_rearm)}
                        </p>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </section>
      <section className="grid gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">운영 주기 기본값</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            거래소 동기화, 시장 갱신, 포지션 관리, 재검토 확인 주기, AI 기본 검토 간격의 전역 기본값을 분리해 관리합니다. AI 자체는 고정 15분 정기호출이 아니라 이벤트 기반 + 주기 백스톱으로 동작합니다.
          </p>
          <div className="mt-4 rounded-2xl border border-amber-200 bg-white px-4 py-3">
            <p className="text-xs text-slate-500">운영 원칙</p>
            <p className="mt-2 text-sm leading-6 text-slate-700">
              거래소 동기화는 전역 공용 주기만 사용합니다. 심볼별 override는 시장 갱신, 포지션 관리, 재검토 확인 주기, AI 기본 검토 간격에만 적용됩니다.
            </p>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <Field label="거래소 동기화(초)">
              <input className={inputClass} type="number" min={30} max={3600} value={form.exchange_sync_interval_seconds} onChange={(event) => updateField("exchange_sync_interval_seconds", Number(event.target.value))} />
            </Field>
            <Field label="시장 갱신(분)">
              <input className={inputClass} type="number" min={1} max={1440} value={form.market_refresh_interval_minutes} onChange={(event) => updateField("market_refresh_interval_minutes", Number(event.target.value))} />
            </Field>
            <Field label="포지션 관리(초)">
              <input className={inputClass} type="number" min={30} max={3600} value={form.position_management_interval_seconds} onChange={(event) => updateField("position_management_interval_seconds", Number(event.target.value))} />
            </Field>
              <Field label="재검토 확인 주기(분)" hint="주기 cycle이 재검토 이벤트가 있는지 확인하는 기본 간격입니다.">
                <input className={inputClass} type="number" min={1} value={form.decision_cycle_interval_minutes} onChange={(event) => updateField("decision_cycle_interval_minutes", Number(event.target.value))} />
              </Field>
              <Field label="AI 기본 검토 간격(분)" hint="열린 포지션 재검토 기준과 수동 재실행 보호에 사용하는 기본 간격입니다.">
                <input className={inputClass} type="number" min={5} value={form.ai_call_interval_minutes} onChange={(event) => updateField("ai_call_interval_minutes", Number(event.target.value))} />
              </Field>
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
            <div className="grid gap-3 md:grid-cols-2">
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
      <div className="flex flex-col gap-3 rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
        <p className="text-sm text-slate-600">운영 설정 변경은 전체 payload로 함께 저장됩니다. 저장 전까지 즉시 실행 액션에는 반영되지 않습니다.</p>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <button className="rounded-full bg-amber-400 px-5 py-3 text-sm font-semibold text-slate-900 disabled:opacity-60" disabled={isPending} onClick={() => save("control_save")} type="button">{isPending ? "저장 중..." : "운영 설정 저장"}</button>
          <div className="lg:min-w-[18rem]">
            <InlineFeedback message={feedback.control_save} />
          </div>
        </div>
      </div>

      <AIUsagePanel usage={aiUsage} />
      </div>

      <div className={activeView === "integration" ? "space-y-5" : "hidden"} aria-hidden={activeView !== "integration"}>
      <section className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">AI 설정</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            여기서는 제공자, 모델, 입력 길이, 온도만 조정합니다. 호출 타이밍은 위 운영 주기 섹션에서 관리하고, 신규 진입은 이벤트 기반 + 행동 바운딩 + 실패 시 차단 경로를 따릅니다.
          </p>
          <div className="mt-4 rounded-2xl border border-amber-200 bg-white px-4 py-3">
            <p className="text-xs text-slate-500">현재 AI 운영 원칙</p>
            <p className="mt-2 text-sm leading-6 text-slate-700">
              고정 15분 AI 호출이 아니라 트리거 기반으로만 평가를 시도합니다. 위의 재검토 확인 주기는 주기 cycle이 재검토 이벤트를 찾는 간격이고, AI 기본 검토 간격은 열린 포지션 재검토 기준과 수동 재실행 보호에 사용됩니다.
            </p>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle checked={form.ai_enabled} label="OpenAI 사용" onChange={(value) => updateField("ai_enabled", value)} />
            <Field label="제공자"><select className={inputClass} value={form.ai_provider} onChange={(event) => updateField("ai_provider", event.target.value as "openai" | "mock")}><option value="openai">OpenAI</option><option value="mock">모의 응답</option></select></Field>
            <Field label="모델"><input className={inputClass} value={form.ai_model} onChange={(event) => updateField("ai_model", event.target.value)} /></Field>
            <Field label="온도" hint="낮게 유지할수록 응답 분산이 줄어듭니다."><input className={inputClass} type="number" min={0} max={1} step="0.05" value={form.ai_temperature} onChange={(event) => updateField("ai_temperature", Number(event.target.value))} /></Field>
            <Field label="AI 입력 캔들 수"><input className={inputClass} type="number" min={16} max={200} value={form.ai_max_input_candles} onChange={(event) => updateField("ai_max_input_candles", Number(event.target.value))} /></Field>
            <Field label="OpenAI API 키"><input className={inputClass} type="password" autoComplete="off" value={form.openai_api_key} onChange={(event) => updateField("openai_api_key", event.target.value)} placeholder="sk-..." /></Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_openai_api_key} onChange={(event) => updateField("clear_openai_api_key", event.target.checked)} /> 저장된 키 제거</label>
          </div>
        </div>

        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-slate-900">외부 이벤트 소스</h3>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                FRED 기반 매크로 일정 source를 settings에서 고정하거나, 비워 두고 기존 env fallback을 유지할 수 있습니다. 아래 BLS/BEA enrichment API는 발표가 지난 이벤트의 actual 값을 보강하는 observe-only layer이며 `risk_guard`를 직접 바꾸지 않습니다.
              </p>
            </div>
            <StatusPill tone={state.event_source_provider === "fred" ? "good" : "neutral"}>
              {describeEventSourceProviderOverride(state.event_source_provider)}
            </StatusPill>
          </div>
          <div className="mt-4 rounded-2xl border border-amber-200 bg-white px-4 py-3">
            <p className="text-xs text-slate-500">현재 런타임 event source</p>
            <p className="mt-2 text-sm font-semibold text-slate-900">
              {eventSourceProvenance ? describeEventSourceProvenance(eventSourceProvenance) : "외부 이벤트 소스 미연결"}
            </p>
            {eventSourceVendor ? (
              <p className="mt-2 text-sm text-slate-700">primary calendar vendor: {describeEventSourceVendor(eventSourceVendor)}</p>
            ) : null}
            <p className="mt-1 text-sm text-slate-700">
              post-release enrichment: {eventEnrichmentVendors.length > 0 ? describeEnrichmentVendors(eventEnrichmentVendors) : "없음"}
            </p>
            <p className="mt-2 text-sm leading-6 text-slate-700">{eventSourceHelp}</p>
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle
              checked={eventSourceOverrideEnabled}
              label="settings 값 우선 사용"
              onChange={(value) => updateField("event_source_provider", value ? (form.event_source_provider || "stub") : "")}
            />
            <div className="rounded-2xl border border-amber-200 bg-white px-4 py-3">
              <p className="text-xs text-slate-500">적용 방식</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">
                {eventSourceOverrideEnabled ? "저장된 settings 값 우선" : "env fallback 또는 stub"}
              </p>
            </div>
            <Field label="소스 제공자" hint={eventSourceOverrideEnabled ? "1차는 stub / fred만 노출합니다." : "settings override를 켜면 stub 또는 fred를 저장할 수 있습니다."}>
              <select
                className={inputClass}
                disabled={!eventSourceOverrideEnabled}
                value={form.event_source_provider || "stub"}
                onChange={(event) => updateField("event_source_provider", event.target.value as EventSourceProvider)}
              >
                {eventSourceProviderOptions.map((option) => (
                  <option key={option} value={option}>{option === "fred" ? "FRED" : "stub (미연결/기본)"}</option>
                ))}
              </select>
            </Field>
            <Field label="FRED API 키" hint="비워 두면 기존 저장값을 유지합니다. settings override를 끄면 런타임에서는 env fallback만 사용합니다.">
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={form.event_source_api_key}
                onChange={(event) => updateField("event_source_api_key", event.target.value)}
                placeholder="FRED API key"
              />
            </Field>
            <Field label="API URL" hint="비우면 settings override에서는 FRED 기본 URL, override 미사용이면 env 값을 따릅니다.">
              <input
                className={inputClass}
                value={form.event_source_api_url}
                onChange={(event) => updateField("event_source_api_url", event.target.value)}
                placeholder="https://api.stlouisfed.org/fred"
              />
            </Field>
            <Field label="Timeout (seconds)" hint="비우면 env fallback 또는 기본 10초를 사용합니다.">
              <input
                className={inputClass}
                type="number"
                min={1}
                max={120}
                step="1"
                value={form.event_source_timeout_seconds ?? ""}
                onChange={(event) => updateField("event_source_timeout_seconds", numberOrNull(event.target.value))}
              />
            </Field>
            <Field label="기본 자산" hint="예: BTCUSDT, ETHUSDT. 비우면 env fallback 또는 현재 심볼을 사용합니다.">
              <input
                className={inputClass}
                value={form.event_source_default_assets_input}
                onChange={(event) => updateField("event_source_default_assets_input", event.target.value.toUpperCase())}
                placeholder="BTCUSDT, ETHUSDT"
              />
            </Field>
            <Field label="FRED release IDs" hint="예: 10, 46, 50, 53, 101">
              <input
                className={inputClass}
                value={form.event_source_fred_release_ids_input}
                onChange={(event) => updateField("event_source_fred_release_ids_input", event.target.value)}
                placeholder="10, 46, 50, 53, 101"
              />
            </Field>
          </div>
          <div className="mt-4 rounded-2xl border border-amber-200 bg-white px-4 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-900">발표 후 actual enrichment API</p>
                <p className="mt-1 text-sm leading-6 text-slate-600">
                  FRED가 다음 일정과 리스크 윈도우를 유지하고, BLS/BEA는 발표가 지난 이벤트의 actual/prior 값을 보강합니다. 비워 두면 settings 값 대신 env fallback을 사용합니다.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <StatusPill tone={form.event_source_bls_enrichment_url ? "good" : "neutral"}>
                  BLS: {blsEnrichmentConfigState}
                </StatusPill>
                <StatusPill tone={form.event_source_bea_enrichment_url ? "good" : "neutral"}>
                  BEA: {beaEnrichmentConfigState}
                </StatusPill>
              </div>
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <Field
                label="BLS enrichment URL"
                hint="예: CPI/PPI/고용 actual 값을 normalize contract로 돌려주는 wrapper endpoint. series 매핑은 wrapper 내부에서 관리하며, 이 화면에서는 URL만 넣습니다."
              >
                <input
                  className={inputClass}
                  value={form.event_source_bls_enrichment_url}
                  onChange={(event) => updateField("event_source_bls_enrichment_url", event.target.value)}
                  placeholder="https://example.local/bls/releases"
                />
              </Field>
              <Field
                label="BEA enrichment URL"
                hint="예: GDP/PCE actual 값을 normalize contract로 돌려주는 wrapper endpoint. dataset/table 매핑은 wrapper 내부에서 관리하며, 이 화면에서는 URL만 넣습니다."
              >
                <input
                  className={inputClass}
                  value={form.event_source_bea_enrichment_url}
                  onChange={(event) => updateField("event_source_bea_enrichment_url", event.target.value)}
                  placeholder="https://example.local/bea/releases"
                />
              </Field>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_event_source_api_key} onChange={(event) => updateField("clear_event_source_api_key", event.target.checked)} /> 저장된 FRED 키 제거</label>
            <StatusPill tone={state.event_source_api_key_configured ? "good" : "neutral"}>
              {state.event_source_api_key_configured ? "저장된 FRED 키 있음" : "저장된 FRED 키 없음"}
            </StatusPill>
          </div>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            실사용에서는 백엔드 스케줄러가 FRED 일정을 읽고 발표 시각이 지난 이벤트에만 BLS/BEA enrichment URL을 자동 호출합니다. 별도 테스트 입력이나 series_id 수동 입력은 사용하지 않으며, legacy static params가 있었다면 다음 저장 시 자동으로 비워집니다.
          </p>
        </div>

        <div className="rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
          <h3 className="text-lg font-semibold text-slate-900">Binance 연동</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            시세 사용 여부, 선물 / 테스트넷 경로, API 자격증명을 관리합니다. 실제 계좌 상태 확인은 위 실거래 제어의 거래소 동기화 버튼을 사용합니다.
          </p>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Toggle checked={form.binance_market_data_enabled} label="Binance 시세 사용" onChange={(value) => updateField("binance_market_data_enabled", value)} />
            <Toggle checked={form.binance_futures_enabled} label="USD-M 선물" onChange={(value) => updateField("binance_futures_enabled", value)} />
            <Toggle checked={form.binance_testnet_enabled} label="테스트넷 사용" onChange={(value) => updateField("binance_testnet_enabled", value)} />
            <Field label="Binance API 키"><input className={inputClass} type="password" autoComplete="off" value={form.binance_api_key} onChange={(event) => updateField("binance_api_key", event.target.value)} /></Field>
            <Field label="Binance API 시크릿"><input className={inputClass} type="password" autoComplete="off" value={form.binance_api_secret} onChange={(event) => updateField("binance_api_secret", event.target.value)} /></Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_binance_api_key} onChange={(event) => updateField("clear_binance_api_key", event.target.checked)} /> 저장된 키 제거</label>
            <label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={form.clear_binance_api_secret} onChange={(event) => updateField("clear_binance_api_secret", event.target.checked)} /> 저장된 시크릿 제거</label>
          </div>
        </div>
      </section>
      <div className="flex flex-col gap-3 rounded-[1.75rem] border border-amber-100 bg-canvas/80 p-4 sm:p-5">
        <p className="text-sm text-slate-600">연동 설정도 기존 full payload 저장 경로를 그대로 사용합니다. OpenAI/Binance/FRED 키 변경은 저장 후 반영됩니다.</p>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <button className="rounded-full bg-amber-400 px-5 py-3 text-sm font-semibold text-slate-900 disabled:opacity-60" disabled={isPending} onClick={() => save("integration_save")} type="button">{isPending ? "저장 중..." : "연동 설정 저장"}</button>
          <div className="lg:min-w-[18rem]">
            <InlineFeedback message={feedback.integration_save} />
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
