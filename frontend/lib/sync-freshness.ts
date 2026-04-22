export type SyncScopeStatus = {
  status?: string | null;
  last_sync_at?: string | null;
  freshness_seconds?: number | null;
  stale_after_seconds?: number | null;
  stale?: boolean | null;
  incomplete?: boolean | null;
  last_failure_reason?: string | null;
  last_skip_reason?: string | null;
};

type SyncStatusKey = "synced" | "stale" | "incomplete" | "failed" | "skipped" | "unknown";

export type SyncBadgePresentation = {
  label: string;
  kind: "good" | "warn" | "danger";
};

export type SyncReasonPresentation = {
  label: string;
  reasonCode: string | null;
};

const syncStatusLabelMap: Record<SyncStatusKey, string> = {
  synced: "정상",
  stale: "지연",
  incomplete: "불완전",
  failed: "동기화 실패",
  skipped: "동기화 보류",
  unknown: "확인 필요",
};

function isKnownSyncStatus(value: string): value is SyncStatusKey {
  return value in syncStatusLabelMap;
}

export function normalizeSyncScopeStatus(scope: SyncScopeStatus | undefined): SyncStatusKey {
  if (!scope) {
    return "unknown";
  }

  const rawStatus = String(scope.status ?? "").trim().toLowerCase();
  if (isKnownSyncStatus(rawStatus)) {
    return rawStatus;
  }
  if (scope.incomplete) {
    return "incomplete";
  }
  if (scope.stale) {
    return "stale";
  }
  return "synced";
}

export function translateSyncScopeStatus(scope: SyncScopeStatus | undefined): string {
  return syncStatusLabelMap[normalizeSyncScopeStatus(scope)];
}

export function getSyncScopeBadge(scope: SyncScopeStatus | undefined): SyncBadgePresentation {
  const status = normalizeSyncScopeStatus(scope);
  switch (status) {
    case "synced":
      return { label: syncStatusLabelMap[status], kind: "good" };
    case "stale":
    case "skipped":
    case "unknown":
      return { label: syncStatusLabelMap[status], kind: "warn" };
    case "incomplete":
    case "failed":
      return { label: syncStatusLabelMap[status], kind: "danger" };
  }
}

export function getSyncScopeReason(scope: SyncScopeStatus | undefined): SyncReasonPresentation {
  const status = normalizeSyncScopeStatus(scope);
  if (status === "skipped") {
    return {
      label: "보류 사유",
      reasonCode: scope?.last_skip_reason ?? null,
    };
  }
  if (status === "failed") {
    return {
      label: "실패 사유",
      reasonCode: scope?.last_failure_reason ?? null,
    };
  }
  return {
    label: "마지막 실패",
    reasonCode: scope?.last_failure_reason ?? null,
  };
}
