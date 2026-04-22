"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { type AIUsagePayload } from "./ai-usage-panel";
import { ManualNoTradePanel } from "./settings/manual-no-trade-panel";
import { OperatorEventPanel } from "./settings/operator-event-panel";
import { LiveControlPanel } from "./settings/live-control-panel";
import { CadenceSettingsPanel } from "./settings/cadence-settings-panel";
import { IntegrationSettingsPanel } from "./settings/integration-settings-panel";
import { MarketRiskPanel } from "./settings/market-risk-panel";
import { EventResponseOverviewPanel } from "./settings/event-response-overview-panel";
import {
  type ControlStatusSummary,
  type EventSourceProvider,
  type LiveSyncResult,
  type RolloutMode,
  type SymbolCadenceOverride,
  type SymbolEffectiveCadence,
} from "./settings/types";
import {
  StatusPill,
  type FeedbackMessage,
  type FeedbackTone,
} from "./settings/form-primitives";
import {
  csvToSymbols,
  describeEnrichmentVendors,
  describeEventSourceProvenance,
  describeEventSourceVendor,
  inferEventSourceProvenance,
  isoToUtcInputValue,
  resolveOperatorEventViewConfigured,
  type EventOperatorControlPayload,
  type OperatorEventBias,
  type OperatorEventEnforcementMode,
  type OperatorEventRiskState,
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
const rolloutModeOptions: RolloutMode[] = ["paper", "shadow", "live_dry_run", "limited_live", "full_live"];
const eventSourceProviderOptions: EventSourceProvider[] = ["stub", "fred"];
type FeedbackKey =
  | "control_save"
  | "integration_save"
  | "live_actions"
  | "operator_event"
  | "manual_window";

type ReconciliationSummary = {
  position_mode?: string;
  position_mode_checked_at?: string | null;
  guarded_symbols_count?: number;
};

type SettingsCadencePayload = {
  items: SymbolEffectiveCadence[];
};

type OperatorEventFormState = {
  operator_bias: OperatorEventBias;
  operator_risk_state: OperatorEventRiskState;
  applies_to_symbols: string;
  horizon: string;
  valid_from: string;
  valid_to: string;
  enforcement_mode: OperatorEventEnforcementMode;
  note: string;
  created_by: string;
};

type ManualWindowFormState = {
  window_id: string | null;
  scope_type: ManualNoTradeWindowPayload["scope"]["scope_type"];
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

class ApiRequestError extends Error {
  payload?: unknown;

  constructor(message: string, payload?: unknown) {
    super(message);
    this.name = "ApiRequestError";
    this.payload = payload;
  }
}

function uniqueSymbols(values: string[]) { return Array.from(new Set(values.map((item) => item.trim().toUpperCase()).filter(Boolean))); }

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
  const isConfigured = resolveOperatorEventViewConfigured(initial.event_operator_control);
  return {
    operator_bias: isConfigured ? (current?.operator_bias ?? "unknown") : "unknown",
    operator_risk_state: isConfigured ? (current?.operator_risk_state ?? "unknown") : "unknown",
    applies_to_symbols: isConfigured ? (current?.applies_to_symbols.join(", ") ?? "") : "",
    horizon: isConfigured ? (current?.horizon ?? "") : "",
    valid_from: isConfigured ? isoToUtcInputValue(current?.valid_from) : "",
    valid_to: isConfigured ? isoToUtcInputValue(current?.valid_to) : "",
    enforcement_mode: isConfigured ? (current?.enforcement_mode ?? "observe_only") : "observe_only",
    note: isConfigured ? (current?.note ?? "") : "",
    created_by: isConfigured ? (current?.created_by ?? "operator-ui") : "operator-ui",
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
  const operatorEventViewConfigured = resolveOperatorEventViewConfigured(eventOperatorControl);
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
    event_source_bls_enrichment_static_params: state.event_source_bls_enrichment_static_params,
    event_source_bea_enrichment_url: form.event_source_bea_enrichment_url || null,
    event_source_bea_enrichment_static_params: state.event_source_bea_enrichment_static_params,
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
        <LiveControlPanel
          state={state}
          summary={controlSummary}
          form={{
            rollout_mode: form.rollout_mode,
            live_approval_window_minutes: form.live_approval_window_minutes,
            limited_live_max_notional: form.limited_live_max_notional,
            manual_live_approval: form.manual_live_approval,
          }}
          liveArmBlocked={liveArmBlocked}
          liveArmDisableReason={liveArmDisableReason}
          actionsUseSavedSettings={actionsUseSavedSettings}
          feedback={feedback.live_actions}
          liveSyncResult={liveSyncResult}
          onPause={() => runPost("/api/settings/pause", "거래를 일시 중지했습니다.", "live_actions", syncSettings)}
          onResume={() => runPost("/api/settings/resume", "거래 일시 중지를 해제했습니다.", "live_actions", syncSettings)}
          onArm={() =>
            runPost("/api/settings/live/arm", "실거래 승인 창을 열었습니다.", "live_actions", syncSettings, {
              minutes: state.live_approval_window_minutes,
            })
          }
          onDisarm={() => runPost("/api/settings/live/disarm", "실거래 승인 창을 닫았습니다.", "live_actions", syncSettings)}
          onSync={async () => {
            try {
              setLiveSyncResult(
                await requestJson<LiveSyncResult>(`/api/live/sync?symbol=${encodeURIComponent(state.default_symbol)}`, {
                  method: "POST",
                }),
              );
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
          onFieldChange={(field, value) => {
            if (field === "rollout_mode") {
              const nextMode = value as RolloutMode;
              updateField("rollout_mode", nextMode);
              updateField("live_trading_enabled", nextMode !== "paper");
              return;
            }

            updateField(field as keyof FormState, value as FormState[keyof FormState]);
          }}
        />

        <MarketRiskPanel
          form={form}
          mergedSymbols={mergedSymbols}
          symbolOptions={symbolOptions}
          onFieldChange={(field, value) => updateField(field as keyof FormState, value as FormState[keyof FormState])}
          onToggleTrackedSymbol={(symbol) => {
            const active = form.tracked_symbols.includes(symbol);
            updateField(
              "tracked_symbols",
              active
                ? form.tracked_symbols.filter((item) => item !== symbol)
                : uniqueSymbols([...form.tracked_symbols, symbol]),
            );
          }}
        />
        <section className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <EventResponseOverviewPanel
            defaultSymbol={state.default_symbol}
            eventContext={eventContext}
            aiEventView={aiEventView}
            operatorEventViewConfigured={operatorEventViewConfigured}
            blockedReason={eventOperatorControl?.blocked_reason}
            approvalRequiredReason={eventOperatorControl?.approval_required_reason}
            alignmentDecision={alignmentDecision}
            eventSourceProvenanceLabel={describeEventSourceProvenance(eventSourceProvenance)}
            effectivePolicyPreview={eventOperatorControl?.effective_policy_preview}
            policySource={eventOperatorControl?.policy_source}
            entryPolicySummary={entryPolicySummary}
            alignmentReasonSummary={alignmentReasonSummary}
            eventSourceHelp={eventSourceHelp}
          />

        <div className="space-y-5">
          <OperatorEventPanel
            operatorEventView={operatorEventView}
            operatorEventViewConfigured={operatorEventViewConfigured}
            operatorEventForm={operatorEventForm}
            isPending={isPending}
            feedback={feedback.operator_event}
            onFieldChange={(field, value) =>
              updateOperatorEventField(field as keyof OperatorEventFormState, value as OperatorEventFormState[keyof OperatorEventFormState])
            }
            onSave={saveOperatorEventView}
            onClear={clearOperatorEventView}
          />
          <ManualNoTradePanel
            manualWindows={manualWindows}
            activeManualWindows={activeManualWindows}
            manualWindowForm={manualWindowForm}
            isPending={isPending}
            feedback={feedback.manual_window}
            onFieldChange={(field, value) =>
              updateManualWindowField(field as keyof ManualWindowFormState, value as ManualWindowFormState[keyof ManualWindowFormState])
            }
            onSave={saveManualWindow}
            onReset={resetManualWindowForm}
            onEdit={editManualWindow}
            onEnd={endManualWindow}
          />
        </div>
      </section>
      <CadenceSettingsPanel
        form={form}
        mergedSymbols={mergedSymbols}
        overrideRows={overrideRows}
        effectiveCadenceBySymbol={effectiveCadenceBySymbol}
        adaptiveSignalSummary={adaptiveSignalSummary}
        positionManagementSummary={positionManagementSummary}
        aiUsage={aiUsage}
        isPending={isPending}
        feedback={feedback.control_save}
        onFieldChange={(field, value) => updateField(field as keyof FormState, value as FormState[keyof FormState])}
        onSymbolOverrideChange={updateSymbolOverride}
        onSave={() => save("control_save")}
      />
      </div>

      <div className={activeView === "integration" ? "space-y-5" : "hidden"} aria-hidden={activeView !== "integration"}>
        <IntegrationSettingsPanel
          form={form}
          state={{
            event_source_provider: state.event_source_provider,
            event_source_api_key_configured: state.event_source_api_key_configured,
          }}
          eventSourceProvenanceLabel={
            eventSourceProvenance ? describeEventSourceProvenance(eventSourceProvenance) : "외부 이벤트 소스 미연결"
          }
          eventSourceVendorLabel={eventSourceVendor ? describeEventSourceVendor(eventSourceVendor) : null}
          eventEnrichmentLabel={eventEnrichmentVendors.length > 0 ? describeEnrichmentVendors(eventEnrichmentVendors) : "없음"}
          eventSourceHelp={eventSourceHelp}
          eventSourceOverrideEnabled={eventSourceOverrideEnabled}
          eventSourceProviderLabel={describeEventSourceProviderOverride(state.event_source_provider)}
          blsEnrichmentConfigState={blsEnrichmentConfigState}
          beaEnrichmentConfigState={beaEnrichmentConfigState}
          isPending={isPending}
          feedback={feedback.integration_save}
          onFieldChange={(field, value) => updateField(field as keyof FormState, value as FormState[keyof FormState])}
          onSave={() => save("integration_save")}
        />
      </div>
    </div>
  );
}
