export type DecisionTimelineTone = "good" | "warn" | "danger" | "neutral";

export type DecisionTimelineSummary = {
  label: string;
  detail: string;
  kind: DecisionTimelineTone;
};

export type AiTriggerReasonPresentation = {
  label: string;
  hint: string;
  legacy: boolean;
};

export type DecisionTimelineSymbolLike = {
  ai_decision: {
    decision: string | null;
    decision_reference?: {
      display_gap?: boolean | null;
      display_gap_reason?: string | null;
    } | null;
  };
  risk_guard: {
    allowed: boolean | null;
    decision: string | null;
    auto_resized_entry: boolean;
    adjustment_reason_codes: string[];
  };
  execution: {
    order_id: number | null;
    order_status: string | null;
    execution_status: string | null;
  };
  pending_entry_plan?: {
    plan_id: number | null;
    plan_status: string | null;
    entry_mode: string | null;
    canceled_reason?: string | null;
  } | null;
  candidate_selection: {
    selected: boolean | null;
    selection_reason: string | null;
    selected_reason: string | null;
    rejected_reason: string | null;
  };
};

export function describeAiTriggerReason(
  value: string | null | undefined,
): AiTriggerReasonPresentation {
  switch (value) {
    case "entry_candidate_event":
      return {
        label: "진입 후보 이벤트",
        hint: "현재 정책 기준의 이벤트성 AI 호출 사유입니다.",
        legacy: false,
      };
    case "breakout_exception_event":
      return {
        label: "브레이크아웃 예외 이벤트",
        hint: "현재 정책 기준의 이벤트성 AI 호출 사유입니다.",
        legacy: false,
      };
    case "protection_review_event":
      return {
        label: "보호 상태 점검",
        hint: "deterministic protection recovery와 연결된 현재 정책 사유입니다.",
        legacy: false,
      };
    case "manual_review_event":
      return {
        label: "수동 검토",
        hint: "운영자가 직접 호출한 현재 정책 사유입니다.",
        legacy: false,
      };
    case "open_position_recheck_due":
      return {
        label: "포지션 시간 경과 재검토",
        hint: "과거 정책 기록입니다. 현재 runtime에서는 시간 경과만으로 AI 검토를 다시 만들지 않습니다.",
        legacy: true,
      };
    case "periodic_backstop_due":
      return {
        label: "주기 백스탑 검토",
        hint: "과거 정책 기록입니다. 현재 runtime에서는 주기 백스탑만으로 AI 검토를 다시 만들지 않습니다.",
        legacy: true,
      };
    default:
      return {
        label: value ?? "-",
        hint: value ? "저장된 trigger reason 원문입니다." : "-",
        legacy: false,
      };
  }
}

function translateDecision(decision: string | null | undefined): string {
  switch (decision) {
    case "long":
      return "롱";
    case "short":
      return "숏";
    case "reduce":
      return "축소";
    case "exit":
      return "청산";
    case "hold":
      return "보류";
    default:
      return "-";
  }
}

function isEntryDecision(decision: string | null | undefined): boolean {
  return decision === "long" || decision === "short";
}

function isSurvivalDecision(decision: string | null | undefined): boolean {
  return decision === "reduce" || decision === "exit";
}

function describeSelectionReason(reason: string | null | undefined): string {
  switch (reason) {
    case "ranked_portfolio_focus":
      return "최신 cycle 실행 후보로 선정됐습니다.";
    case "priority_position_or_protection":
      return "기존 포지션 또는 보호 주문 관리가 우선입니다.";
    case "breadth_hold_bias":
      return "시장 breadth가 약해 이번 cycle 후보에서 제외됐습니다.";
    case "capacity_reached":
      return "현재 허용 슬롯이 이미 모두 사용 중입니다.";
    case "score_below_threshold":
      return "이번 cycle 점수가 진입 기준치에 못 미쳤습니다.";
    case "low_edge_hold_candidate":
      return "기대 edge가 약해 보류 후보로 남았습니다.";
    case "underperforming_expectancy_bucket":
      return "최근 기대값 버킷이 약해 실행 후보에서 제외됐습니다.";
    case "expectancy_below_threshold":
      return "기대값 기준이 부족해 실행 후보로 올리지 않았습니다.";
    case "adverse_signed_slippage":
      return "체결 품질이 불리해 실행 후보에서 제외됐습니다.";
    case "duplicate_exposure":
      return "비슷한 노출이 이미 있어 이번 cycle에서는 제외됐습니다.";
    default:
      return reason ? `최신 cycle 사유: ${reason}` : "최신 cycle 실행 대상이 아닙니다.";
  }
}

function describeEntryMode(entryMode: string | null | undefined): string {
  switch (entryMode) {
    case "pullback_confirm":
      return "눌림 확인";
    case "breakout_confirm":
      return "돌파 확인";
    case "immediate":
      return "즉시 진입";
    case "none":
      return "직접 진입 없음";
    default:
      return "진입 조건 확인";
  }
}

function describePendingPlan(symbol: DecisionTimelineSymbolLike): DecisionTimelineSummary | null {
  const plan = symbol.pending_entry_plan;
  if (!plan || plan.plan_id === null) {
    return null;
  }
  switch (plan.plan_status) {
    case "armed":
      return {
        label: "진입 대기",
        detail: `${describeEntryMode(plan.entry_mode)} 조건을 확인하는 중입니다.`,
        kind: "warn",
      };
    case "triggered":
      return {
        label: "주문 제출 진행",
        detail: "진입 플랜이 트리거돼 주문 제출 단계로 넘어갔습니다.",
        kind: "warn",
      };
    case "canceled":
      return {
        label: "진입 플랜 취소",
        detail: plan.canceled_reason ? `취소 사유: ${plan.canceled_reason}` : "이전 진입 플랜이 취소됐습니다.",
        kind: "neutral",
      };
    case "expired":
      return {
        label: "진입 플랜 만료",
        detail: "대기 중이던 진입 플랜이 만료됐습니다.",
        kind: "neutral",
      };
    default:
      return {
        label: "진입 플랜 존재",
        detail: "진입 플랜 상태를 확인할 수 있습니다.",
        kind: "neutral",
      };
  }
}

export function hasHistoricalDecisionGap(symbol: DecisionTimelineSymbolLike): boolean {
  return Boolean(symbol.ai_decision.decision_reference?.display_gap);
}

export function describeHistoricalDecisionGap(symbol: DecisionTimelineSymbolLike): string | null {
  if (!hasHistoricalDecisionGap(symbol)) {
    return null;
  }
  const rawReason = symbol.ai_decision.decision_reference?.display_gap_reason;
  if (typeof rawReason === "string" && rawReason.trim().length > 0) {
    return "현재 화면은 마지막 AI 추천보다 더 최신 시장 새로고침과 cycle 상태를 함께 보여주고 있습니다.";
  }
  return "현재 화면은 마지막 AI 추천보다 더 최신 cycle 상태를 함께 보여주고 있습니다.";
}

export function summarizeLastAiRecommendation(symbol: DecisionTimelineSymbolLike): DecisionTimelineSummary {
  const decision = symbol.ai_decision.decision;
  const historical = hasHistoricalDecisionGap(symbol);
  if (isEntryDecision(decision) || isSurvivalDecision(decision) || decision === "hold") {
    return {
      label: historical ? "마지막 AI 추천(과거 스냅샷)" : "마지막 AI 추천",
      detail: translateDecision(decision),
      kind: historical ? "warn" : "neutral",
    };
  }
  return {
    label: "마지막 AI 추천 없음",
    detail: "-",
    kind: "neutral",
  };
}

export function summarizeRiskGate(symbol: DecisionTimelineSymbolLike): DecisionTimelineSummary {
  const decision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  const autoResized =
    symbol.risk_guard.auto_resized_entry && symbol.risk_guard.adjustment_reason_codes.length > 0;
  if (symbol.risk_guard.allowed === null) {
    return {
      label: "리스크 평가 대기",
      detail: "아직 신규 진입 리스크 판정이 집계되지 않았습니다.",
      kind: "neutral",
    };
  }
  if (symbol.risk_guard.allowed) {
    if (isSurvivalDecision(decision)) {
      return {
        label: "리스크 통과",
        detail: translateDecision(decision),
        kind: "good",
      };
    }
    if (isEntryDecision(decision)) {
      return {
        label: autoResized ? "리스크 통과(자동 축소, 주문 전)" : "리스크 통과(주문 전)",
        detail: translateDecision(decision),
        kind: "good",
      };
    }
    return {
      label: "리스크 통과",
      detail: translateDecision(decision),
      kind: "neutral",
    };
  }
  if (isSurvivalDecision(decision)) {
    return {
      label: "생존 경로도 차단",
      detail: translateDecision(decision),
      kind: "danger",
    };
  }
  return {
    label: "리스크 차단",
    detail: translateDecision(decision),
    kind: "danger",
  };
}

export function summarizeCurrentCycleSelection(symbol: DecisionTimelineSymbolLike): DecisionTimelineSummary {
  if (symbol.candidate_selection.selected === true) {
    return {
      label: "현재 cycle 선정",
      detail: describeSelectionReason(
        symbol.candidate_selection.selected_reason ?? symbol.candidate_selection.selection_reason,
      ),
      kind: "good",
    };
  }
  if (symbol.candidate_selection.selected === false) {
    return {
      label: "현재 cycle 미선정",
      detail: describeSelectionReason(
        symbol.candidate_selection.rejected_reason ?? symbol.candidate_selection.selection_reason,
      ),
      kind: "neutral",
    };
  }
  return {
    label: "현재 cycle 정보 없음",
    detail: hasHistoricalDecisionGap(symbol)
      ? "마지막 AI 추천은 남아 있지만 최신 cycle 선택 결과는 비어 있습니다."
      : "최신 cycle 선택 결과가 아직 정리되지 않았습니다.",
    kind: "neutral",
  };
}

export function summarizeExecutionState(symbol: DecisionTimelineSymbolLike): DecisionTimelineSummary {
  const decision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  const executionStatus = symbol.execution.execution_status ?? symbol.execution.order_status;
  const flowLabel = isSurvivalDecision(decision) ? "정리/축소" : isEntryDecision(decision) ? "신규 진입" : "주문";

  if (symbol.execution.order_id !== null) {
    if (executionStatus === "filled") {
      return {
        label: `${flowLabel} 실행 완료`,
        detail: executionStatus,
        kind: "good",
      };
    }
    return {
      label: `${flowLabel} 주문 제출`,
      detail: executionStatus ?? "pending",
      kind: "warn",
    };
  }

  const pendingPlanSummary = describePendingPlan(symbol);
  if (pendingPlanSummary) {
    return pendingPlanSummary;
  }

  if (symbol.candidate_selection.selected === false) {
    const prefix = hasHistoricalDecisionGap(symbol) ? "마지막 AI 추천은 남아 있지만 " : "";
    return {
      label: "현재 cycle 미선정",
      detail: `${prefix}${describeSelectionReason(
        symbol.candidate_selection.rejected_reason ?? symbol.candidate_selection.selection_reason,
      )}`,
      kind: "neutral",
    };
  }

  if (symbol.risk_guard.allowed === false) {
    return {
      label: "실행 없음",
      detail: "안전 점검에서 차단돼 주문이 나가지 않았습니다.",
      kind: "danger",
    };
  }

  if (symbol.ai_decision.decision === "hold") {
    return {
      label: "실행 없음",
      detail: "AI가 신규 진입을 권하지 않았습니다.",
      kind: "neutral",
    };
  }

  if (symbol.risk_guard.allowed === true && isEntryDecision(decision)) {
    return {
      label: "주문 제출 전",
      detail: "리스크 통과는 주문 제출 완료 의미가 아니며, 현재 cycle 선정 또는 진입 트리거 확인이 더 필요합니다.",
      kind: "warn",
    };
  }

  return {
    label: "실행 대상 아님",
    detail: "현재 주문 또는 진입 플랜 기록이 없습니다.",
    kind: "neutral",
  };
}
