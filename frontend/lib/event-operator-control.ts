export type OperatorEventBias = "bullish" | "bearish" | "neutral" | "no_trade" | "unknown";
export type OperatorEventRiskState = "risk_on" | "risk_off" | "neutral" | "unknown";
export type OperatorEventAlignmentStatus = "aligned" | "partially_aligned" | "conflict" | "insufficient_data";
export type OperatorEventEnforcementMode =
  | "observe_only"
  | "approval_required"
  | "block_on_conflict"
  | "force_no_trade";
export type OperatorEventSourceStatus = "available" | "stale" | "incomplete" | "unavailable" | "error";
export type OperatorEventImportance = "low" | "medium" | "high" | "critical" | "unknown";
export type OperatorEffectivePolicyPreview =
  | "allow_normal"
  | "allow_with_approval"
  | "block_new_entries"
  | "force_no_trade_window"
  | "insufficient_data";
export type OperatorPolicySource =
  | "manual_no_trade_window"
  | "operator_enforcement_mode"
  | "operator_bias"
  | "alignment_policy"
  | "none";
export type AIEventSourceState = OperatorEventSourceStatus | "unknown";
export type EventControlTone = "good" | "warn" | "danger" | "neutral";

export type OperatorEventItemPayload = {
  event_name: string;
  event_at: string;
  importance: OperatorEventImportance;
  affected_assets: string[];
  source_status?: OperatorEventSourceStatus;
  summary_note?: string | null;
};

export type OperatorActiveRiskWindowPayload = {
  is_active: boolean;
  event_name?: string | null;
  event_importance: OperatorEventImportance;
  start_at?: string | null;
  end_at?: string | null;
  affected_assets: string[];
  summary_note?: string | null;
};

export type OperatorEventContextPayload = {
  source_status: OperatorEventSourceStatus;
  generated_at: string;
  is_stale: boolean;
  is_complete: boolean;
  active_risk_window: boolean;
  active_risk_window_detail?: OperatorActiveRiskWindowPayload | null;
  next_event_at?: string | null;
  next_event_name?: string | null;
  next_event_importance: OperatorEventImportance;
  minutes_to_next_event?: number | null;
  upcoming_events: OperatorEventItemPayload[];
  affected_assets: string[];
  summary_note?: string | null;
};

export type AIEventViewPayload = {
  ai_bias: OperatorEventBias;
  ai_risk_state: OperatorEventRiskState;
  ai_confidence?: number | null;
  scenario_note?: string | null;
  confidence_penalty_reason?: string | null;
  source_state: AIEventSourceState;
};

export type OperatorEventViewPayload = {
  operator_bias: OperatorEventBias;
  operator_risk_state: OperatorEventRiskState;
  applies_to_symbols: string[];
  horizon?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
  enforcement_mode: OperatorEventEnforcementMode;
  note?: string | null;
  created_by: string;
  updated_at?: string | null;
};

export type ManualNoTradeWindowScopePayload = {
  scope_type: "global" | "symbols";
  symbols: string[];
};

export type ManualNoTradeWindowPayload = {
  window_id: string;
  scope: ManualNoTradeWindowScopePayload;
  start_at: string;
  end_at: string;
  reason: string;
  auto_resume: boolean;
  require_manual_rearm: boolean;
  created_by: string;
  updated_at?: string | null;
  is_active: boolean;
};

export type AlignmentDecisionPayload = {
  ai_bias: OperatorEventBias;
  operator_bias: OperatorEventBias;
  ai_risk_state: OperatorEventRiskState;
  operator_risk_state: OperatorEventRiskState;
  alignment_status: OperatorEventAlignmentStatus;
  reason_codes: string[];
  effective_policy_preview: OperatorEffectivePolicyPreview;
  evaluated_at: string;
};

export type EvaluatedOperatorPolicyPayload = {
  operator_view_active: boolean;
  matched_window_id?: string | null;
  alignment_status: OperatorEventAlignmentStatus;
  enforcement_mode: OperatorEventEnforcementMode;
  reason_codes: string[];
  effective_policy_preview: OperatorEffectivePolicyPreview;
  event_source_status: OperatorEventSourceStatus;
  event_source_stale: boolean;
  evaluated_at: string;
};

export type EventOperatorControlPayload = {
  event_context: OperatorEventContextPayload;
  ai_event_view: AIEventViewPayload;
  operator_event_view: OperatorEventViewPayload;
  alignment_decision: AlignmentDecisionPayload;
  evaluated_operator_policy?: EvaluatedOperatorPolicyPayload | null;
  blocked_reason?: string | null;
  degraded_reason?: string | null;
  approval_required_reason?: string | null;
  policy_source?: OperatorPolicySource;
  manual_no_trade_windows: ManualNoTradeWindowPayload[];
  effective_policy_preview: OperatorEffectivePolicyPreview;
};

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function parseTimestamp(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function uniqueSymbols(values: string[]) {
  return Array.from(new Set(values.map((item) => item.trim().toUpperCase()).filter(Boolean)));
}

export function symbolsToCsv(values: string[]) {
  return uniqueSymbols(values).join(", ");
}

export function csvToSymbols(value: string) {
  return uniqueSymbols(value.split(","));
}

export function isoToUtcInputValue(value: string | null | undefined) {
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return "";
  }
  return `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1)}-${pad(parsed.getUTCDate())}T${pad(
    parsed.getUTCHours(),
  )}:${pad(parsed.getUTCMinutes())}`;
}

export function utcInputValueToIso(value: string) {
  const normalized = value.trim();
  if (!normalized) {
    return null;
  }
  const withSeconds = normalized.length === 16 ? `${normalized}:00` : normalized;
  return `${withSeconds}Z`;
}

export function formatUtcTimestamp(value: string | null | undefined) {
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return "unknown";
  }
  return `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1)}-${pad(parsed.getUTCDate())} ${pad(
    parsed.getUTCHours(),
  )}:${pad(parsed.getUTCMinutes())} UTC`;
}

export function describeEventBias(value: string | null | undefined) {
  switch (value) {
    case "bullish":
      return "bullish";
    case "bearish":
      return "bearish";
    case "neutral":
      return "neutral";
    case "no_trade":
      return "no_trade";
    default:
      return "unknown";
  }
}

export function describeRiskState(value: string | null | undefined) {
  switch (value) {
    case "risk_on":
      return "risk_on";
    case "risk_off":
      return "risk_off";
    case "neutral":
      return "neutral";
    default:
      return "unknown";
  }
}

export function describeAlignmentStatus(value: string | null | undefined) {
  switch (value) {
    case "aligned":
      return "aligned";
    case "partially_aligned":
      return "partially_aligned";
    case "conflict":
      return "conflict";
    default:
      return "insufficient_data";
  }
}

export function describeEnforcementMode(value: string | null | undefined) {
  switch (value) {
    case "observe_only":
      return "observe_only";
    case "approval_required":
      return "approval_required";
    case "block_on_conflict":
      return "block_on_conflict";
    case "force_no_trade":
      return "force_no_trade";
    default:
      return "observe_only";
  }
}

export function describeSourceStatus(value: string | null | undefined) {
  switch (value) {
    case "available":
      return "available";
    case "stale":
      return "stale";
    case "incomplete":
      return "incomplete";
    case "unavailable":
      return "unavailable";
    case "error":
      return "error";
    default:
      return "unknown";
  }
}

export function describeImportance(value: string | null | undefined) {
  switch (value) {
    case "low":
      return "low";
    case "medium":
      return "medium";
    case "high":
      return "high";
    case "critical":
      return "critical";
    default:
      return "unknown";
  }
}

export function describeEffectivePolicyPreview(value: string | null | undefined) {
  switch (value) {
    case "allow_normal":
      return "allow_normal";
    case "allow_with_approval":
      return "allow_with_approval";
    case "block_new_entries":
      return "block_new_entries";
    case "force_no_trade_window":
      return "force_no_trade_window";
    default:
      return "insufficient_data";
  }
}

export function describeWindowScope(scope: ManualNoTradeWindowScopePayload | null | undefined) {
  if (!scope) {
    return "global";
  }
  if (scope.scope_type === "symbols") {
    return scope.symbols.length > 0 ? `symbols: ${scope.symbols.join(", ")}` : "symbols";
  }
  return "global";
}

export function toneForSourceStatus(value: string | null | undefined): EventControlTone {
  switch (value) {
    case "available":
      return "good";
    case "stale":
    case "incomplete":
      return "warn";
    case "unavailable":
    case "error":
      return "danger";
    default:
      return "neutral";
  }
}

export function toneForAlignment(value: string | null | undefined): EventControlTone {
  switch (value) {
    case "aligned":
      return "good";
    case "partially_aligned":
      return "warn";
    case "conflict":
      return "danger";
    default:
      return "neutral";
  }
}

export function toneForPolicyPreview(value: string | null | undefined): EventControlTone {
  switch (value) {
    case "allow_normal":
      return "good";
    case "allow_with_approval":
      return "warn";
    case "block_new_entries":
    case "force_no_trade_window":
      return "danger";
    default:
      return "neutral";
  }
}
