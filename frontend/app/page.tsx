import { OverviewDashboard } from "../components/overview-dashboard";
import { fetchJson } from "../lib/api";

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
  position_protection_summary: Array<{
    symbol: string;
    side: string;
    status: string;
    protected: boolean;
    protective_order_count: number;
    has_stop_loss: boolean;
    has_take_profit: boolean;
    missing_components: string[];
    position_size: number;
  }>;
};

type Row = Record<string, unknown>;

export default async function HomePage() {
  const [overview, alerts, decisions, orders, positions] = await Promise.all([
    fetchJson<Overview>("/api/dashboard/overview"),
    fetchJson<Row[]>("/api/alerts"),
    fetchJson<Row[]>("/api/decisions"),
    fetchJson<Row[]>("/api/orders?limit=10"),
    fetchJson<Row[]>("/api/positions"),
  ]);

  return (
    <OverviewDashboard
      initial={{
        overview,
        alerts,
        decisions,
        orders,
        positions,
      }}
    />
  );
}
