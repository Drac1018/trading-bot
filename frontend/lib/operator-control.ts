export type ServiceGateReasonSource = {
  service_gate_gate_clear?: boolean | null;
  service_gate_blockers?: ReasonCodeInput | null;
  service_gate_root_cause_codes?: ReasonCodeInput | null;
  service_gate_counts?: Record<string, number | null | undefined> | null;
  service_gate_recent_scheduler_non_success?: readonly ServiceGateDetailRow[] | null;
  service_gate_recent_health_errors?: readonly ServiceGateDetailRow[] | null;
  service_gate_redis_cache?: Record<string, unknown> | null;
};

type ReasonCodeInput = string | null | undefined | readonly (string | null | undefined)[];
type ServiceGateDetailRow = Record<string, unknown>;

const serviceGateReasonCodeAliases: Record<string, string> = {
  RECONCILIATION_NOT_SYNCED: "RECONCILIATION_UNSYNCED",
};

const genericServiceGateRootCauseCodes = new Set(["WORKFLOW_EXCEPTION"]);

function normalizedReasonCodes(
  values: ReasonCodeInput | null | undefined,
  options: { uppercase?: boolean } = {},
) {
  const result: string[] = [];
  const candidates = Array.isArray(values) ? values : [values];
  for (const value of candidates) {
    const code = typeof value === "string" ? value.trim() : "";
    const rawNormalized = options.uppercase ? code.toUpperCase() : code;
    const normalized = serviceGateReasonCodeAliases[rawNormalized] ?? rawNormalized;
    if (normalized && !result.includes(normalized)) {
      result.push(normalized);
    }
  }
  return result;
}

export function serviceGateDisplayReasonCodes(control: ServiceGateReasonSource) {
  const rootCauses = normalizedReasonCodes(control.service_gate_root_cause_codes, { uppercase: true });
  const blockers = normalizedReasonCodes(control.service_gate_blockers, { uppercase: true });
  const hasGenericRootCause = rootCauses.some((code) => genericServiceGateRootCauseCodes.has(code));
  const publishedReasons =
    rootCauses.length > 0
      ? normalizedReasonCodes(hasGenericRootCause ? [...rootCauses, ...blockers] : rootCauses)
      : blockers;
  const gateStateUnknown =
    control.service_gate_gate_clear === null ||
    typeof control.service_gate_gate_clear === "undefined";
  if (control.service_gate_gate_clear === true) {
    return publishedReasons.length > 0
      ? normalizedReasonCodes(["SERVICE_GATE_STATUS_UNKNOWN", ...publishedReasons])
      : [];
  }
  if (gateStateUnknown) {
    return publishedReasons.length > 0
      ? normalizedReasonCodes(["SERVICE_GATE_STATUS_UNKNOWN", ...publishedReasons])
      : ["SERVICE_GATE_STATUS_UNKNOWN"];
  }
  if (publishedReasons.length > 0) {
    return publishedReasons;
  }
  if (control.service_gate_gate_clear === false) {
    return ["SERVICE_GATE_BLOCKED"];
  }
  return [];
}

function serviceGateCount(control: ServiceGateReasonSource, key: string) {
  const value = control.service_gate_counts?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function detailString(row: ServiceGateDetailRow, key: string) {
  const value = row[key];
  return typeof value === "string" ? value.trim() : "";
}

function detailRowSummary(row: ServiceGateDetailRow, label: string) {
  const component =
    detailString(row, "workflow") ||
    detailString(row, "component") ||
    detailString(row, "service") ||
    detailString(row, "task");
  const status =
    detailString(row, "status") ||
    detailString(row, "state") ||
    detailString(row, "outcome");
  const reason =
    detailString(row, "reason_code") ||
    detailString(row, "root_cause_code") ||
    detailString(row, "error_code") ||
    detailString(row, "reason");
  const parts = [component, status, reason].filter(Boolean);
  if (parts.length === 0) {
    return `${label} detail unavailable`;
  }
  return `${label} ${parts.join("/")}`;
}

export function serviceGateDetailSummary(control: ServiceGateReasonSource) {
  const schedulerCount = serviceGateCount(control, "recent_scheduler_non_success");
  const healthCount = serviceGateCount(control, "recent_health_errors");
  const schedulerRows = Array.isArray(control.service_gate_recent_scheduler_non_success)
    ? control.service_gate_recent_scheduler_non_success
    : [];
  const healthRows = Array.isArray(control.service_gate_recent_health_errors)
    ? control.service_gate_recent_health_errors
    : [];
  const details = [
    ...schedulerRows.slice(0, 1).map((row) => detailRowSummary(row, "scheduler")),
    ...healthRows.slice(0, 2).map((row) => detailRowSummary(row, "health")),
  ];
  if (control.service_gate_redis_cache?.blocking === true) {
    details.push("redis cache blocking");
  }

  const counts = `scheduler ${schedulerCount}, health ${healthCount}`;
  if (details.length > 0) {
    return `service-gate detail: ${counts}; ${details.join("; ")}`;
  }
  const reasons = serviceGateDisplayReasonCodes(control);
  if (reasons.length > 0) {
    return `service-gate reasons: ${reasons.join(", ")}`;
  }
  return `service-gate detail: ${counts}`;
}
