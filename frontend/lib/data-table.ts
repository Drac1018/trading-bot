export type TableRow = Record<string, unknown>;

const preferredColumnOrder = [
  "status",
  "event_category",
  "protected",
  "protective_order_count",
  "has_stop_loss",
  "has_take_profit",
  "missing_components",
  "decision",
  "allowed",
  "symbol",
  "timeframe",
  "ai_trigger_summary",
  "reason_codes",
  "ai_trigger_reason",
  "mode",
  "provider_name",
  "confidence",
  "latest_price",
  "approved_leverage",
  "approved_risk_pct",
  "realized_pnl",
  "daily_pnl",
  "cumulative_pnl",
  "schedule_window",
  "workflow",
  "next_run_at",
  "created_at",
  "updated_at",
] as const;

const detailColumnSet = new Set([
  "event_category",
  "input_payload",
  "output_payload",
  "payload",
  "metadata_json",
  "outcome",
]);

const primaryKeyFields = [
  "id",
  "order_id",
  "execution_id",
  "position_id",
  "decision_run_id",
  "agent_run_id",
  "scheduler_run_id",
  "event_id",
  "audit_id",
] as const;

const compositeKeyCandidates = [
  ["symbol", "timeframe", "created_at"],
  ["symbol", "created_at"],
  ["symbol", "updated_at"],
  ["symbol", "timestamp"],
  ["symbol", "market_snapshot_time"],
  ["event_type", "created_at"],
  ["workflow", "created_at"],
  ["provider_name", "created_at"],
] as const;

function asStablePrimitive(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : null;
  }

  if (typeof value === "number" || typeof value === "bigint") {
    return String(value);
  }

  return null;
}

function orderColumns(columns: string[]) {
  const rank = new Map<string, number>(preferredColumnOrder.map((key, index) => [key, index]));
  return [...columns].sort((left, right) => {
    const leftRank = rank.get(left) ?? 999;
    const rightRank = rank.get(right) ?? 999;
    if (leftRank !== rightRank) {
      return leftRank - rightRank;
    }
    return left.localeCompare(right);
  });
}

function buildFallbackIdentity(row: TableRow) {
  return JSON.stringify(
    Object.entries(row).sort(([left], [right]) => left.localeCompare(right)),
  );
}

function resolveRowIdentity(row: TableRow) {
  for (const field of primaryKeyFields) {
    const value = asStablePrimitive(row[field]);
    if (value) {
      return `${field}:${value}`;
    }
  }

  for (const fields of compositeKeyCandidates) {
    const parts = fields
      .map((field) => [field, asStablePrimitive(row[field])] as const)
      .filter(([, value]) => value !== null);
    if (parts.length === fields.length) {
      return parts.map(([field, value]) => `${field}:${value}`).join("|");
    }
  }

  return `fallback:${buildFallbackIdentity(row)}`;
}

export function collectTableColumns(rows: TableRow[]) {
  const columnSet = new Set<string>();
  rows.forEach((row) => {
    Object.keys(row).forEach((column) => columnSet.add(column));
  });
  return orderColumns([...columnSet]);
}

export function splitTableColumns(rows: TableRow[], hiddenColumns: Set<string>) {
  const columns = collectTableColumns(rows);
  const visibleColumns = columns.filter((column) => !hiddenColumns.has(column));
  const visiblePrimary = visibleColumns.filter((column) => !detailColumnSet.has(column));
  const primary = visiblePrimary.slice(0, 10);
  const detail = [
    ...visiblePrimary.slice(10),
    ...visibleColumns.filter((column) => detailColumnSet.has(column)),
  ];
  return { primary, detail };
}

export function buildTableRowKeys(rows: TableRow[]) {
  const seen = new Map<string, number>();
  return rows.map((row) => {
    const identity = resolveRowIdentity(row);
    const nextCount = (seen.get(identity) ?? 0) + 1;
    seen.set(identity, nextCount);
    return nextCount === 1 ? identity : `${identity}#${nextCount}`;
  });
}
