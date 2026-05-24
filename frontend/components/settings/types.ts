export type RolloutMode = "paper" | "shadow" | "live_dry_run" | "limited_live" | "full_live";

export type EventSourceProvider = "stub" | "fred";

export const SYMBOL_TIMEFRAME_OPTIONS = ["1m", "3m", "5m", "15m"] as const;
export type SymbolTimeframeOption = (typeof SYMBOL_TIMEFRAME_OPTIONS)[number];

export type AIModelRoutePolicyItem = {
  route: string;
  label: string;
  call_policy: string;
  configured_model?: string | null;
  default_model?: string | null;
  model_candidates?: string[];
  reason_codes?: string[];
  notes?: string[];
};

export type AIModelRoutingPolicy = {
  version?: string;
  read_only?: boolean;
  runtime_model_source?: string;
  primary_model?: string;
  summary?: string;
  no_model_call_routes?: string[];
  candidate_model_routes?: string[];
  routes?: AIModelRoutePolicyItem[];
};

export type ProtectionSyncState = {
  status?: string;
  protected?: boolean;
  protective_order_count?: number;
  has_stop_loss?: boolean;
  has_take_profit?: boolean;
  missing_components?: string[];
};

export type LiveSyncResult = {
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

export type AutoResumeAttemptResult = {
  status?: string;
  resumed?: boolean;
  allowed?: boolean;
  reason_code?: string | null;
  pause_origin?: string | null;
  auto_resume_after?: string | null;
  blockers?: string[];
  symbol_blockers?: Record<string, string[]>;
  blocker_details?: Array<Record<string, unknown>>;
  evaluated_symbols?: string[];
  health_error?: string | null;
};

export type SymbolCadenceOverride = {
  symbol: string;
  enabled: boolean;
  timeframe_override: string | null;
  market_refresh_interval_minutes_override: number | null;
  position_management_interval_seconds_override: number | null;
  decision_cycle_interval_minutes_override: number | null;
  ai_call_interval_minutes_override: number | null;
};

export type SymbolEffectiveCadence = {
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
  next_market_refresh_due_at?: string | null;
  next_position_management_due_at?: string | null;
  next_decision_due_at?: string | null;
  next_ai_call_due_at?: string | null;
};

export type ControlStatusSummary = {
  exchange_can_trade?: boolean | null;
  exchange_can_trade_known?: boolean;
  exchange_can_trade_source?: string;
  exchange_can_trade_checked_at?: string | null;
  exchange_connectivity_state?: string;
  rollout_mode: RolloutMode;
  exchange_submit_allowed: boolean;
  limited_live_max_notional: number | null;
  app_live_armed: boolean;
  approval_window_open: boolean;
  paused: boolean;
  degraded: boolean;
  risk_allowed: boolean | null;
  blocked_reasons_current_cycle: string[];
  blocked_reason_codes?: string[];
  degraded_reason_codes?: string[];
  protection_reason_codes?: string[];
  approval_control_blocked_reasons?: string[];
  live_arm_disabled?: boolean;
  live_arm_disable_reason_code?: string | null;
  live_arm_disable_reason?: string | null;
};
