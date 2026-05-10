export type CloseExecutionSyncStatus = "COMPLETE" | "MISSING" | "PENDING" | "FAILED" | "UNKNOWN";
export type SettlementTone = "good" | "warn" | "danger" | "neutral";

export type OrderSettlementInput = {
  closeExecutionSyncStatus: string | null;
  missingCloseExecution: boolean | null;
  realizedPnlConfirmed: boolean | null;
  pnlSource: string | null;
  feeSource: string | null;
  feeConfirmed: boolean | null;
  warningMessage: string | null;
  feeWarningMessage: string | null;
};

export type SettlementDisplayState = {
  closeExecutionSyncStatus: CloseExecutionSyncStatus;
  missingCloseExecution: boolean;
  realizedPnlConfirmed: boolean;
  realizedPnlDisplayMode: "value" | "pending";
  realizedPnlLabel: string | null;
  realizedPnlDetail: string;
  feeDetail: string;
  badgeLabel: string | null;
  badgeTone: SettlementTone;
  warningMessage: string | null;
};

const statusPriority: Record<CloseExecutionSyncStatus, number> = {
  FAILED: 5,
  PENDING: 4,
  MISSING: 3,
  COMPLETE: 2,
  UNKNOWN: 1,
};

function normalizeCloseExecutionSyncStatus(value: string | null | undefined): CloseExecutionSyncStatus {
  const normalized = (value ?? "").trim().toUpperCase();
  if (
    normalized === "COMPLETE"
    || normalized === "MISSING"
    || normalized === "PENDING"
    || normalized === "FAILED"
    || normalized === "UNKNOWN"
  ) {
    return normalized;
  }
  return "UNKNOWN";
}

function pickCloseExecutionSyncStatus(orders: OrderSettlementInput[]): CloseExecutionSyncStatus {
  return orders.reduce<CloseExecutionSyncStatus>((selected, order) => {
    const next = normalizeCloseExecutionSyncStatus(order.closeExecutionSyncStatus);
    return statusPriority[next] > statusPriority[selected] ? next : selected;
  }, "UNKNOWN");
}

function firstText(values: Array<string | null | undefined>) {
  return values.find((value) => typeof value === "string" && value.trim().length > 0)?.trim() ?? null;
}

function settlementBadge(status: CloseExecutionSyncStatus) {
  if (status === "FAILED") {
    return { label: "동기화 실패", tone: "danger" as const };
  }
  if (status === "PENDING") {
    return { label: "동기화 대기", tone: "warn" as const };
  }
  if (status === "MISSING") {
    return { label: "청산 체결 누락", tone: "warn" as const };
  }
  if (status === "COMPLETE") {
    return { label: "정산 확인됨", tone: "good" as const };
  }
  return { label: null, tone: "neutral" as const };
}

export function summarizeSettlementDisplay(orders: OrderSettlementInput[]): SettlementDisplayState {
  const status = pickCloseExecutionSyncStatus(orders);
  const missingCloseExecution = orders.some((order) => order.missingCloseExecution) || status === "MISSING";
  const unconfirmed = missingCloseExecution || status === "PENDING" || status === "FAILED";
  const warningMessage = firstText(orders.map((order) => order.warningMessage));
  const feeWarningMessage = firstText(orders.map((order) => order.feeWarningMessage));
  const badge = settlementBadge(status);

  if (unconfirmed) {
    return {
      closeExecutionSyncStatus: status,
      missingCloseExecution,
      realizedPnlConfirmed: false,
      realizedPnlDisplayMode: "pending",
      realizedPnlLabel: status === "FAILED" ? "동기화 실패" : "동기화 대기",
      realizedPnlDetail: warningMessage ?? "거래소 손익 미반영",
      feeDetail: `로컬 executions 기준 / ${feeWarningMessage ?? "청산 수수료 미반영"}`,
      badgeLabel: badge.label,
      badgeTone: badge.tone,
      warningMessage: warningMessage ?? "거래소 손익 미반영",
    };
  }

  const pnlSource = firstText(orders.map((order) => order.pnlSource)) ?? "LOCAL_EXECUTIONS";
  const feeSource = firstText(orders.map((order) => order.feeSource)) ?? "LOCAL_EXECUTIONS";
  return {
    closeExecutionSyncStatus: status,
    missingCloseExecution: false,
    realizedPnlConfirmed: !orders.some((order) => order.realizedPnlConfirmed === false),
    realizedPnlDisplayMode: "value",
    realizedPnlLabel: null,
    realizedPnlDetail: pnlSource === "EXCHANGE" ? "Binance 원문 realizedPnL 반영" : "로컬 executions 기준",
    feeDetail: feeSource === "LOCAL_EXECUTIONS" ? "로컬 executions 기준" : feeSource,
    badgeLabel: badge.label,
    badgeTone: badge.tone,
    warningMessage: null,
  };
}
