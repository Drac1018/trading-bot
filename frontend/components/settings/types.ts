export type RolloutMode = "paper" | "shadow" | "live_dry_run" | "limited_live" | "full_live";

export type EventSourceProvider = "stub" | "fred";

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
