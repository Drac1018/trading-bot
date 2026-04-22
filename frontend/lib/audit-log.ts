export type AuditTab =
  | "all"
  | "risk"
  | "execution"
  | "approval_control"
  | "protection"
  | "health_system"
  | "ai_decision";

export type SortMode = "newest" | "oldest" | "severity";

export type AuditRow = Record<string, unknown> & {
  event_category?: string;
  event_type?: string;
  entity_type?: string;
  entity_id?: string;
  severity?: string;
  message?: string;
  created_at?: string;
};

export type AuditLegacyReviewPresentation = {
  badge: string;
  label: string;
  hint: string;
  legacy: true;
  rawTriggerReason: string;
};

const legacyReviewTriggerReasons = new Set(["open_position_recheck_due", "periodic_backstop_due"]);
const maxAuditTriggerTraversalDepth = 6;

export const AUDIT_TAB_ORDER: AuditTab[] = [
  "all",
  "risk",
  "execution",
  "approval_control",
  "protection",
  "health_system",
  "ai_decision"
];

export const AUDIT_TAB_CONFIG: Record<
  AuditTab,
  {
    label: string;
    title: string;
    description: string;
    emptyTitle: string;
    emptyDescription: string;
  }
> = {
  all: {
    label: "전체",
    title: "전체 감사 로그",
    description: "최근 감사 이벤트를 시간순으로 확인합니다.",
    emptyTitle: "표시할 감사 이벤트가 없습니다.",
    emptyDescription: "운영 제어나 거래 루프가 아직 실행되지 않았거나, 조회 범위 안에 기록된 이벤트가 없습니다."
  },
  risk: {
    label: "리스크",
    title: "리스크 감사 로그",
    description: "risk_guard 허용·차단과 리스크 검증 결과를 모아 봅니다.",
    emptyTitle: "리스크 이벤트가 없습니다.",
    emptyDescription: "현재 조회 범위 안에는 risk_guard 허용·차단 또는 리스크 체크 관련 감사 이벤트가 없습니다."
  },
  execution: {
    label: "실행",
    title: "실행 감사 로그",
    description: "주문 제출, 부분 체결, 재호가, 실행 실패를 빠르게 확인합니다.",
    emptyTitle: "실행 이벤트가 없습니다.",
    emptyDescription: "현재 조회 범위 안에는 주문 제출, 체결, 재호가, 실행 실패 관련 감사 이벤트가 없습니다."
  },
  approval_control: {
    label: "승인/운영제어",
    title: "승인 및 운영제어 감사 로그",
    description: "pause, resume, live approval, auto-resume 같은 운영 제어 이벤트를 추적합니다.",
    emptyTitle: "승인/운영제어 이벤트가 없습니다.",
    emptyDescription: "현재 조회 범위 안에는 승인 창, pause/resume, auto-resume 관련 감사 이벤트가 없습니다."
  },
  protection: {
    label: "보호주문",
    title: "보호주문 감사 로그",
    description: "손절·익절, 보호 복구, 비상 청산, manage-only 전환 이벤트를 확인합니다.",
    emptyTitle: "보호주문 이벤트가 없습니다.",
    emptyDescription: "현재 조회 범위 안에는 보호 주문 생성·복구·실패나 비상 청산 관련 감사 이벤트가 없습니다."
  },
  health_system: {
    label: "헬스/시스템",
    title: "헬스 및 시스템 감사 로그",
    description: "연동 상태, 동기화, 스케줄러, 계좌·시스템 헬스 이벤트를 모아 봅니다.",
    emptyTitle: "헬스/시스템 이벤트가 없습니다.",
    emptyDescription: "현재 조회 범위 안에는 연동 상태, 스케줄러, 계좌 동기화, 시스템 헬스 관련 감사 이벤트가 없습니다."
  },
  ai_decision: {
    label: "AI/의사결정",
    title: "AI 및 의사결정 감사 로그",
    description: "AI 제안, 의사결정 생성, hold 판단 관련 이벤트를 확인합니다.",
    emptyTitle: "AI/의사결정 이벤트가 없습니다.",
    emptyDescription: "현재 조회 범위 안에는 AI 출력, 의사결정 생성, hold 판단 관련 감사 이벤트가 없습니다."
  }
};

const severityOrder: Record<string, number> = {
  critical: 0,
  error: 1,
  warning: 2,
  info: 3
};

function describeLegacyReviewTriggerReason(
  value: string,
): Pick<AuditLegacyReviewPresentation, "label" | "hint"> | null {
  switch (value) {
    case "open_position_recheck_due":
      return {
        label: "포지션 시간 경과 재검토",
        hint: "과거 정책 기록입니다. 현재 runtime에서는 시간 경과만으로 AI 검토를 다시 만들지 않습니다.",
      };
    case "periodic_backstop_due":
      return {
        label: "주기 백스탑 검토",
        hint: "과거 정책 기록입니다. 현재 runtime에서는 주기 백스탑만으로 AI 검토를 다시 만들지 않습니다.",
      };
    default:
      return null;
  }
}

function findLegacyTriggerReason(
  value: unknown,
  seen: Set<object>,
  depth: number,
): string | null {
  if (depth > maxAuditTriggerTraversalDepth) {
    return null;
  }

  if (!value || typeof value !== "object") {
    return null;
  }

  if (seen.has(value)) {
    return null;
  }
  seen.add(value);

  if (Array.isArray(value)) {
    for (const item of value) {
      const nested = findLegacyTriggerReason(item, seen, depth + 1);
      if (nested) {
        return nested;
      }
    }
    return null;
  }

  for (const [key, nestedValue] of Object.entries(value)) {
    if (
      (key === "trigger_reason" || key === "last_ai_trigger_reason") &&
      typeof nestedValue === "string" &&
      legacyReviewTriggerReasons.has(nestedValue)
    ) {
      return nestedValue;
    }

    const nested = findLegacyTriggerReason(nestedValue, seen, depth + 1);
    if (nested) {
      return nested;
    }
  }

  return null;
}

export function extractLegacyReviewTriggerReason(row: AuditRow): string | null {
  return findLegacyTriggerReason(row, new Set<object>(), 0);
}

export function describeAuditLegacyReview(row: AuditRow): AuditLegacyReviewPresentation | null {
  const rawTriggerReason = extractLegacyReviewTriggerReason(row);
  if (!rawTriggerReason) {
    return null;
  }

  const presentation = describeLegacyReviewTriggerReason(rawTriggerReason);
  if (!presentation) {
    return null;
  }

  return {
    badge: "과거 정책 기록",
    label: presentation.label,
    hint: presentation.hint,
    legacy: true,
    rawTriggerReason,
  };
}

export function parseAuditTab(value: string | null | undefined): AuditTab {
  if (!value) {
    return "all";
  }
  return AUDIT_TAB_ORDER.includes(value as AuditTab) ? (value as AuditTab) : "all";
}

function normalizeEventCategory(value: string | null | undefined): Exclude<AuditTab, "all"> {
  if (!value) {
    return "health_system";
  }

  if (
    value === "risk" ||
    value === "execution" ||
    value === "approval_control" ||
    value === "protection" ||
    value === "health_system" ||
    value === "ai_decision"
  ) {
    return value;
  }

  return "health_system";
}

export function getSeverityValue(row: AuditRow): string {
  return typeof row.severity === "string" ? row.severity : "";
}

export function getAuditEventCategory(row: AuditRow): AuditTab {
  const category = typeof row.event_category === "string" ? row.event_category : null;
  return normalizeEventCategory(category);
}

export function compareAuditRows(left: AuditRow, right: AuditRow, sortMode: SortMode): number {
  const leftTime = typeof left.created_at === "string" ? Date.parse(left.created_at) : 0;
  const rightTime = typeof right.created_at === "string" ? Date.parse(right.created_at) : 0;

  if (sortMode === "oldest") {
    return leftTime - rightTime;
  }

  if (sortMode === "severity") {
    const leftSeverity = severityOrder[getSeverityValue(left)] ?? 99;
    const rightSeverity = severityOrder[getSeverityValue(right)] ?? 99;
    if (leftSeverity !== rightSeverity) {
      return leftSeverity - rightSeverity;
    }
  }

  return rightTime - leftTime;
}

export function getAuditTabCounts(rows: AuditRow[]): Map<AuditTab, number> {
  const counts = new Map<AuditTab, number>(AUDIT_TAB_ORDER.map((tab) => [tab, 0]));
  counts.set("all", rows.length);
  rows.forEach((row) => {
    const category = getAuditEventCategory(row);
    counts.set(category, (counts.get(category) ?? 0) + 1);
  });
  return counts;
}

export function filterAuditRows(
  rows: AuditRow[],
  {
    activeTab,
    severityFilter,
    searchFilter,
    sortMode
  }: {
    activeTab: AuditTab;
    severityFilter: string;
    searchFilter: string;
    sortMode: SortMode;
  }
): AuditRow[] {
  const keyword = searchFilter.trim().toLowerCase();
  const categoryRows =
    activeTab === "all" ? rows : rows.filter((row) => getAuditEventCategory(row) === activeTab);

  return [...categoryRows]
    .filter((row) => {
      if (severityFilter && row.severity !== severityFilter) {
        return false;
      }
      if (!keyword) {
        return true;
      }
      return JSON.stringify(row).toLowerCase().includes(keyword);
    })
    .sort((left, right) => compareAuditRows(left, right, sortMode));
}
