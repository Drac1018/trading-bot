import Link from "next/link";

import type { OperatorDashboardPayload } from "./overview-dashboard";
import { DataTable } from "./data-table";
import { getSelectedSymbolPolicyHint } from "../lib/selected-symbol";

type Row = Record<string, unknown>;

const operatingStateLabelMap: Record<string, string> = {
  TRADABLE: "신규 진입 가능",
  PROTECTION_REQUIRED: "보호 복구 우선",
  DEGRADED_MANAGE_ONLY: "관리 전용",
  EMERGENCY_EXIT: "비상 청산",
  PAUSED: "일시 중지",
};

const reasonCodeLabelMap: Record<string, string> = {
  TRADING_PAUSED: "운영 중지 상태",
  HOLD_DECISION: "보류 판단",
  LIVE_APPROVAL_REQUIRED: "실거래 승인 필요",
  LIVE_TRADING_DISABLED: "실거래 비활성화",
  PROTECTION_REQUIRED: "보호 주문 복구 필요",
  DEGRADED_MANAGE_ONLY: "관리 전용 상태",
  EMERGENCY_EXIT: "비상 청산 상태",
  MANUAL_USER_REQUEST: "수동 중지",
  PROTECTIVE_ORDER_FAILURE: "보호 주문 이상",
  ACCOUNT_STATE_STALE: "계좌 상태 stale",
  POSITION_STATE_STALE: "포지션 상태 stale",
  OPEN_ORDERS_STATE_STALE: "오더 상태 stale",
  PROTECTION_STATE_UNVERIFIED: "보호 주문 검증 불가",
};

const schedulerStatusLabelMap: Record<string, string> = {
  running: "실행 중",
  success: "성공",
  failed: "실패",
};

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function translateMarketInputFlag(value: string) {
  const labels: Record<string, string> = {
    account: "계좌 stale",
    positions: "포지션 stale",
    open_orders: "오더 stale",
    protective_orders: "보호 주문 stale",
    market_snapshot: "시장 스냅샷 stale",
    market_snapshot_incomplete: "시장 스냅샷 불완전",
    feature_input_missing: "피처 입력 없음",
  };
  return labels[value] ?? value;
}

function formatMarketTiming(symbol: OperatorDashboardPayload["symbols"][number]) {
  const parts: string[] = [];
  if (symbol.market_candle_time) {
    parts.push(`캔들 ${formatDateTime(symbol.market_candle_time)}`);
  }
  if (symbol.market_snapshot_time) {
    parts.push(`수집 ${formatDateTime(symbol.market_snapshot_time)}`);
  }
  return `${parts.join(" / ") || "기록 없음"} / 시장 ${symbol.timeframe ?? "-"}`;
}

function formatNumber(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined) {
    return "-";
  }
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatRatio(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${(value * 100).toLocaleString("ko-KR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}%`;
}

function badgeClass(kind: "good" | "warn" | "danger" | "neutral") {
  return {
    good: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warn: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-rose-200 bg-rose-50 text-rose-800",
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
  }[kind];
}

function metricCard(title: string, value: string, hint: string, options?: { compact?: boolean }) {
  const compact = options?.compact ?? false;
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <p className="text-xs font-medium text-slate-500">{title}</p>
      <p
        className={`mt-2 font-semibold text-slate-950 ${
          compact ? "text-base leading-6 break-all sm:text-lg" : "text-xl leading-7 break-words"
        }`}
      >
        {value}
      </p>
      <p className="mt-2 break-words text-xs leading-5 text-slate-500">{hint}</p>
    </div>
  );
}

function isEntryDecision(value: string | null | undefined) {
  return value === "long" || value === "short";
}

function isSurvivalDecision(value: string | null | undefined) {
  return value === "reduce" || value === "exit";
}

function translateDecision(value: string | null | undefined) {
  if (value === "long") {
    return "롱";
  }
  if (value === "short") {
    return "숏";
  }
  if (value === "reduce") {
    return "축소";
  }
  if (value === "exit") {
    return "청산";
  }
  if (value === "hold") {
    return "보류";
  }
  return value ?? "-";
}

function decisionSummary(decision: string | null | undefined) {
  if (isEntryDecision(decision)) {
    return {
      label: "신규 진입 제안",
      detail: translateDecision(decision),
    };
  }
  if (isSurvivalDecision(decision)) {
    return {
      label: "생존 경로 제안",
      detail: translateDecision(decision),
    };
  }
  if (decision === "hold") {
    return {
      label: "보류 제안",
      detail: "신규 진입 없음",
    };
  }
  return {
    label: "추천 없음",
    detail: "-",
  };
}

function riskSummary(symbol: OperatorDashboardPayload["symbols"][number]) {
  const decision = symbol.risk_guard.decision ?? symbol.ai_decision.decision;
  const hasAdjustmentReasons = symbol.risk_guard.adjustment_reason_codes.length > 0;
  if (symbol.risk_guard.allowed === null) {
    return {
      label: "risk 평가 없음",
      detail: "-",
    };
  }
  if (symbol.risk_guard.allowed) {
    if (isSurvivalDecision(decision)) {
      return {
        label: "생존 경로 허용",
        detail: translateDecision(decision),
      };
    }
    if (isEntryDecision(decision)) {
      if (symbol.risk_guard.auto_resized_entry && hasAdjustmentReasons) {
        return {
          label: "진입 허용(자동 축소)",
          detail: translateDecision(decision),
        };
      }
      return {
        label: "신규 진입 승인",
        detail: translateDecision(decision),
      };
    }
    return {
      label: "보류 유지",
      detail: translateDecision(decision),
    };
  }
  if (isSurvivalDecision(decision)) {
    return {
      label: "생존 경로 차단",
      detail: translateDecision(decision),
    };
  }
  return {
    label: "신규 진입 차단",
    detail: translateDecision(decision),
  };
}

function translateAiTriggerReason(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    entry_candidate_event: "신규 진입 후보 이벤트",
    breakout_exception_event: "브레이크아웃 예외 이벤트",
    open_position_recheck_due: "오픈 포지션 재검토 도래",
    protection_review_event: "보호 상태 점검",
    manual_review_event: "수동 검토",
    periodic_backstop_due: "주기적 백스탑 검토",
  };
  return labels[value] ?? value;
}

function translateAiSkipReason(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const labels: Record<string, string> = {
    NO_EVENT: "검토 이벤트 없음",
    TRIGGER_DEDUPED: "동일 지문 중복",
    AI_DISABLED: "AI 비활성화",
    AI_FAILURE_BACKOFF: "AI 실패 백오프",
    AI_COOLDOWN_ACTIVE: "AI 쿨다운 유지",
    PROTECTION_REVIEW_DETERMINISTIC_ONLY: "보호 검토는 결정론 경로만 사용",
  };
  return labels[value] ?? value;
}

function aiReviewSummary(symbol: OperatorDashboardPayload["symbols"][number]) {
  if (symbol.ai_decision.last_ai_skip_reason === "NO_EVENT") {
    return { label: "AI 미호출", detail: "검토 이벤트 없음" };
  }
  if (symbol.ai_decision.trigger_deduped || symbol.ai_decision.last_ai_skip_reason === "TRIGGER_DEDUPED") {
    return { label: "AI 재호출 생략", detail: "동일 상태 중복" };
  }
  if (symbol.ai_decision.last_ai_skip_reason) {
    return {
      label: "AI 미호출",
      detail: translateAiSkipReason(symbol.ai_decision.last_ai_skip_reason),
    };
  }
  if (symbol.ai_decision.last_ai_invoked_at || symbol.ai_decision.provider_name) {
    return {
      label: "AI 호출",
      detail: translateAiTriggerReason(symbol.ai_decision.last_ai_trigger_reason),
    };
  }
  return { label: "AI 상태 미확정", detail: "-" };
}

function translateOperatingState(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return operatingStateLabelMap[value] ?? value;
}

function translateSchedulerStatus(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return schedulerStatusLabelMap[value] ?? value;
}

function translateReasonCode(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const extraReasonCodeLabelMap: Record<string, string> = {
    ENTRY_AUTO_RESIZED: "진입 수량이 자동 축소 승인되었습니다.",
    ENTRY_CLAMPED_TO_GROSS_EXPOSURE_LIMIT: "총 노출 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_CLAMPED_TO_DIRECTIONAL_LIMIT: "방향 편향 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_CLAMPED_TO_SINGLE_POSITION_LIMIT: "최대 단일 포지션 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_CLAMPED_TO_SAME_TIER_LIMIT: "동일 티어 집중도 한도에 맞게 진입 수량이 축소되었습니다.",
    ENTRY_SIZE_BELOW_MIN_NOTIONAL: "최소 실행 가능 주문 미만",
    ENTRY_TRIGGER_NOT_MET: "진입 트리거 미충족",
    CHASE_LIMIT_EXCEEDED: "추격 진입 한도 초과",
    INVALID_INVALIDATION_PRICE: "무효화 가격 기준 이상",
  };
  return extraReasonCodeLabelMap[value] ?? reasonCodeLabelMap[value] ?? value;
}

function formatCodeList(values: string[] | null | undefined) {
  if (!values || values.length === 0) {
    return "-";
  }
  return values.join(", ");
}

function formatTranslatedCodeList(values: string[] | null | undefined) {
  if (!values || values.length === 0) {
    return "-";
  }
  return values.map(translateReasonCode).join(", ");
}

function shortFingerprint(value: string | null | undefined) {
  if (!value || value === "-") {
    return "없음";
  }
  if (value.length <= 14) {
    return value;
  }
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function hardStopLabel(symbol: OperatorDashboardPayload["symbols"][number]) {
  if (symbol.open_position.hard_stop_active === true) {
    return "활성";
  }
  if (symbol.open_position.hard_stop_active === false) {
    return "비활성";
  }
  return symbol.open_position.is_open ? "미확인" : "-";
}

function stopWideningLabel(symbol: OperatorDashboardPayload["symbols"][number]) {
  if (symbol.open_position.stop_widening_allowed === false) {
    return "금지";
  }
  if (symbol.open_position.stop_widening_allowed === true) {
    return "허용";
  }
  return symbol.open_position.is_open ? "미확인" : "-";
}

function executionSummary(symbol: OperatorDashboardPayload["symbols"][number]) {
  const executionStatus = symbol.execution.execution_status ?? symbol.execution.order_status;
  if (!symbol.execution.order_id) {
    if (symbol.risk_guard.allowed === false) {
      return { label: "실행 없음", detail: "risk 차단" };
    }
    if (symbol.ai_decision.decision === "hold") {
      return { label: "실행 없음", detail: "보류" };
    }
    return { label: "실행 없음", detail: "주문 없음" };
  }
  if (executionStatus === "filled") {
    return { label: "체결 완료", detail: executionStatus };
  }
  return { label: "주문 제출", detail: executionStatus ?? "pending" };
}

function SymbolTabs({
  slug,
  symbols,
  selectedSymbol,
  includeAll = false,
}: {
  slug: string;
  symbols: string[];
  selectedSymbol: string;
  includeAll?: boolean;
}) {
  const items = includeAll ? ["ALL", ...symbols] : symbols;
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((symbol) => {
        const active = selectedSymbol === symbol;
        const href =
          symbol === "ALL" ? `/dashboard/${slug}` : `/dashboard/${slug}?symbol=${encodeURIComponent(symbol)}`;
        return (
          <Link
            key={symbol}
            href={href}
            className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
              active
                ? "border-slate-900 bg-slate-900 text-white"
                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50"
            }`}
          >
            {symbol === "ALL" ? "전체" : symbol}
          </Link>
        );
      })}
    </div>
  );
}

export function MarketSignalView({
  operator,
  snapshots,
  features,
  selectedSymbol,
}: {
  operator: OperatorDashboardPayload;
  snapshots: Row[];
  features: Row[];
  selectedSymbol: string;
}) {
  const symbols =
    selectedSymbol === "ALL"
      ? operator.symbols
      : operator.symbols.filter((item) => item.symbol === selectedSymbol);

  const filteredSnapshots =
    selectedSymbol === "ALL"
      ? snapshots
      : snapshots.filter((row) => String(row.symbol ?? "").toUpperCase() === selectedSymbol);
  const filteredFeatures =
    selectedSymbol === "ALL"
      ? features
      : features.filter((row) => String(row.symbol ?? "").toUpperCase() === selectedSymbol);
  const formatMarketSnapshotRowTitle = (row: Row, index: number) => {
    const symbol = typeof row.symbol === "string" ? row.symbol : null;
    const timeframe = typeof row.timeframe === "string" ? row.timeframe : null;
    if (symbol && timeframe) {
      return `${symbol} / 시장 ${timeframe}`;
    }
    if (symbol) {
      return symbol;
    }
    return `항목 ${index + 1}`;
  };

  return (
    <div className="space-y-6">
      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">시장 / 신호</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">시장 입력과 신호 입력만 분리해서 확인</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          이 탭은 가격과 feature 입력을 보여줍니다. AI 판단, risk 승인, 실제 실행 상태는 다른 탭에서 별도로 확인합니다.
        </p>
        <div className="mt-4">
          <SymbolTabs
            slug="market"
            symbols={operator.control.tracked_symbols}
            selectedSymbol={selectedSymbol}
            includeAll
          />
        </div>
        <div className="mt-5 grid gap-4 xl:grid-cols-2">
          {symbols.map((symbol) => {
            const featureInputMissing = symbol.stale_flags.includes("feature_input_missing");
            const featureInputDelayed = symbol.feature_input_delayed;
            return (
            <div key={symbol.symbol} className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-lg font-semibold text-slate-950">{symbol.symbol}</h3>
                  <p className="mt-1 text-sm text-slate-500">
                    {formatMarketTiming(symbol)}
                  </p>
                </div>
                <span
                  className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                    featureInputDelayed ? "danger" : symbol.stale_flags.length > 0 ? "warn" : "good",
                  )}`}
                >
                  {featureInputDelayed ? "입력 지연" : symbol.stale_flags.length > 0 ? "입력 주의" : "입력 정상"}
                </span>
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                {metricCard("현재가", formatNumber(symbol.latest_price), "선택 심볼 기준 최신 가격")}
                {metricCard(
                  "시장 레짐",
                  String(symbol.market_context_summary.primary_regime ?? "-"),
                  featureInputDelayed
                    ? "피처 입력 생성 지연"
                    : featureInputMissing
                      ? "피처 입력 대기"
                    : `정렬 ${String(symbol.market_context_summary.trend_alignment ?? "-")}`,
                )}
                {metricCard(
                  "변동성 / 거래량",
                  `${String(symbol.market_context_summary.volatility_regime ?? "-")} / ${String(
                    symbol.market_context_summary.volume_regime ?? "-",
                  )}`,
                  featureInputDelayed ? "피처 입력 생성 지연" : featureInputMissing ? "피처 입력 대기" : "시장 상태 입력",
                )}
                {metricCard(
                  "신선도",
                  featureInputDelayed ? "지연" : symbol.stale_flags.length > 0 ? "주의" : "정상",
                  featureInputDelayed
                    ? `피처 입력 없음, ${symbol.feature_input_delay_minutes ?? "-"}분 경과`
                    : symbol.stale_flags.length > 0
                    ? symbol.stale_flags.map(translateMarketInputFlag).join(", ")
                    : "이상 플래그 없음",
                )}
              </div>
              {featureInputMissing ? (
                <div
                  className={`mt-3 rounded-2xl px-4 py-3 text-sm leading-6 ${
                    featureInputDelayed
                      ? "border border-rose-200 bg-rose-50 text-rose-900"
                      : "border border-amber-200 bg-amber-50 text-amber-900"
                  }`}
                >
                  {featureInputDelayed
                    ? `시장 스냅샷은 수집됐지만 피처 입력 생성이 ${symbol.feature_input_delay_minutes ?? "-"}분째 지연되고 있습니다. 시장 ${symbol.timeframe ?? "-"} 기준 예상 대기 ${symbol.feature_input_delay_threshold_minutes ?? "-"}분을 넘겼습니다.`
                    : "시장 스냅샷은 수집됐지만 피처 입력은 아직 생성되지 않았습니다."}
                </div>
              ) : null}
            </div>
          )})}
        </div>
      </section>

      <DataTable
        title="시장 스냅샷"
        description="최근 가격 입력"
        rows={filteredSnapshots}
        emptyStateTitle="표시할 시장 스냅샷이 없습니다."
        emptyStateDescription="선택한 심볼 기준으로 아직 저장된 market snapshot이 없습니다."
        hiddenColumns={["candle_count", "candles", "payload"]}
        rowTitleFormatter={formatMarketSnapshotRowTitle}
        labelOverrides={{ timeframe: "시장 타임프레임" }}
      />

      <DataTable
        title="특성 입력"
        description="최근 feature 계산 결과"
        rows={filteredFeatures}
        emptyStateTitle="표시할 feature 입력이 없습니다."
        emptyStateDescription="선택한 심볼 기준으로 아직 계산된 feature snapshot이 없습니다."
      />
    </div>
  );
}

export function DecisionView({
  operator,
  decisionRows,
  selectedSymbol,
}: {
  operator: OperatorDashboardPayload;
  decisionRows: Row[];
  selectedSymbol: string;
}) {
  const symbol =
    operator.symbols.find((item) => item.symbol === selectedSymbol) ?? operator.symbols[0] ?? null;
  const filteredDecisionRows = decisionRows.filter(
    (row) => String(row.symbol ?? "").toUpperCase() === (symbol?.symbol ?? ""),
  );
  const recommendation = symbol ? decisionSummary(symbol.ai_decision.decision) : null;
  const riskOutcome = symbol ? riskSummary(symbol) : null;
  const review = symbol ? aiReviewSummary(symbol) : null;
  const execution = symbol ? executionSummary(symbol) : null;
  const blockedReasonText = symbol
    ? formatTranslatedCodeList(
        symbol.risk_guard.blocked_reason_codes.length > 0
          ? symbol.risk_guard.blocked_reason_codes
          : symbol.blocked_reasons,
      )
    : "-";
  const slotValue = symbol
    ? symbol.ai_decision.assigned_slot ??
      symbol.candidate_selection.assigned_slot ??
      symbol.risk_guard.assigned_slot ??
      "-"
    : "-";
  const candidateWeightValue = symbol
    ? symbol.ai_decision.candidate_weight ??
      symbol.candidate_selection.candidate_weight ??
      symbol.risk_guard.candidate_weight
    : null;
  const capacityReasonValue = symbol
    ? symbol.ai_decision.capacity_reason ??
      symbol.candidate_selection.capacity_reason ??
      symbol.risk_guard.capacity_reason ??
      "-"
    : "-";
  const holdingProfileValue = symbol
    ? symbol.open_position.holding_profile ??
      symbol.risk_guard.holding_profile ??
      symbol.ai_decision.holding_profile ??
      symbol.candidate_selection.holding_profile ??
      "-"
    : "-";
  const holdingProfileReasonValue = symbol
    ? symbol.open_position.holding_profile_reason ??
      symbol.risk_guard.holding_profile_reason ??
      symbol.ai_decision.holding_profile_reason ??
      symbol.candidate_selection.holding_profile_reason ??
      "-"
    : "-";

  if (symbol === null) {
    return (
      <DataTable
        title="의사결정"
        description="평가 / 판단"
        rows={[]}
        emptyStateTitle="표시할 의사결정이 없습니다."
        emptyStateDescription="추적 심볼이 없거나 아직 평가 데이터가 없습니다."
      />
    );
  }

  return (
    <div className="space-y-6">
      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">의사결정</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">AI 추천, risk 승인, 실제 실행을 분리해서 확인</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          이 탭은 왜 AI가 불렸는지 또는 건너뛰었는지, risk가 무엇을 차단했는지, slot soft cap과 hard stop 상태가 어떤지
          운영자가 바로 읽도록 구성합니다.
        </p>
        <div className="mt-4">
          <SymbolTabs
            slug="decisions"
            symbols={operator.control.tracked_symbols}
            selectedSymbol={symbol.symbol}
          />
        </div>
        <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {getSelectedSymbolPolicyHint("single")}
        </div>
        <div className="mt-5 grid gap-4 lg:grid-cols-6">
          {metricCard("마지막 평가", formatDateTime(symbol.ai_decision.created_at), "선택 심볼 기준 최신 평가 기록")}
          {metricCard("AI 검토 상태", review?.label ?? "-", review?.detail ?? "-")}
          {metricCard(
            "다음 AI 검토",
            formatDateTime(symbol.ai_decision.next_ai_review_due_at),
            `사유 ${translateAiTriggerReason(symbol.ai_decision.last_ai_trigger_reason)}`,
          )}
          {metricCard(
            "시장 요약",
            String(symbol.market_context_summary.primary_regime ?? "-"),
            `정렬 ${String(symbol.market_context_summary.trend_alignment ?? "-")}`,
          )}
          {metricCard("AI 추천", recommendation?.label ?? "-", recommendation?.detail ?? "-")}
          {metricCard("실제 실행", execution?.label ?? "-", execution?.detail ?? "-")}
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-3">
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
                AI 추천
              </span>
              <span className="text-xs text-slate-500">{formatDateTime(symbol.ai_decision.created_at)}</span>
            </div>
            <p className="mt-4 text-2xl font-semibold text-slate-950">{translateDecision(symbol.ai_decision.decision)}</p>
            <p className="mt-2 text-sm text-slate-600">신뢰도 {formatRatio(symbol.ai_decision.confidence)}</p>
            <p className="mt-3 text-sm leading-6 text-slate-700">
              {symbol.ai_decision.explanation_short ?? "최신 판단 설명이 없습니다."}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {symbol.ai_decision.rationale_codes.length > 0 ? (
                symbol.ai_decision.rationale_codes.map((code) => (
                  <span key={code} className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">
                    {code}
                  </span>
                ))
              ) : (
                <span className="text-sm text-slate-500">근거 코드 없음</span>
              )}
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {metricCard(
                "AI 호출 사유",
                translateAiTriggerReason(symbol.ai_decision.last_ai_trigger_reason),
                `건너뜀 ${translateAiSkipReason(symbol.ai_decision.last_ai_skip_reason)}`,
              )}
              {metricCard(
                "AI 호출 시각",
                formatDateTime(symbol.ai_decision.last_ai_invoked_at),
                `다음 ${formatDateTime(symbol.ai_decision.next_ai_review_due_at)}`,
              )}
              {metricCard("배정 슬롯", slotValue, `가중치 ${candidateWeightValue ?? "-"}`)}
              {metricCard(
                "수용 한도",
                capacityReasonValue,
                symbol.ai_decision.portfolio_slot_soft_cap_applied ? "soft cap 적용" : "soft cap 미적용",
              )}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                  symbol.risk_guard.allowed === null ? "neutral" : symbol.risk_guard.allowed ? "good" : "danger",
                )}`}
              >
                {riskOutcome?.label ?? "risk 평가 없음"}
              </span>
              <span className="text-xs text-slate-500">{formatDateTime(symbol.risk_guard.created_at)}</span>
            </div>
            <div className="mt-4 space-y-3">
              {metricCard("risk 차단 사유", blockedReasonText, "최신 risk 판정 기준")}
              {metricCard(
                "후보 슬롯",
                slotValue,
                `가중치 ${candidateWeightValue ?? "-"} / ${capacityReasonValue}`,
              )}
              {metricCard(
                "risk 승인 프로파일",
                symbol.risk_guard.approved_leverage !== null ? `${symbol.risk_guard.approved_leverage}x` : "-",
                `허용 risk ${symbol.risk_guard.approved_risk_pct ?? 0}`,
              )}
              {metricCard(
                "슬롯 soft cap",
                symbol.risk_guard.portfolio_slot_soft_cap_applied ? "적용" : "미적용",
                `수용 한도 ${symbol.risk_guard.capacity_reason ?? capacityReasonValue}`,
              )}
              {metricCard(
                "차단 코드",
                formatTranslatedCodeList(symbol.risk_guard.blocked_reason_codes),
                `원시 코드 ${formatCodeList(symbol.risk_guard.blocked_reason_codes)}`,
              )}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass("neutral")}`}>
                실행 / 포지션
              </span>
              <span className="text-xs text-slate-500">{formatDateTime(symbol.execution.created_at)}</span>
            </div>
            <div className="mt-4 space-y-3">
              {metricCard("실행 상태", execution?.label ?? "-", execution?.detail ?? "-")}
              {metricCard("보유 프로필", holdingProfileValue, holdingProfileReasonValue)}
              {metricCard("하드 스탑", hardStopLabel(symbol), `stop widening ${stopWideningLabel(symbol)}`)}
              {metricCard(
                "오픈 포지션",
                symbol.open_position.is_open ? `${symbol.open_position.side ?? "-"} / ${symbol.open_position.quantity ?? 0}` : "-",
                symbol.open_position.is_open
                  ? `진입가 ${symbol.open_position.entry_price ?? "-"} / 현재가 ${symbol.open_position.mark_price ?? "-"}`
                  : "현재 열린 포지션 없음",
              )}
            </div>
          </div>
        </div>
      </section>

      <DataTable
        title="최근 평가 기록"
        description="선택 심볼 의사결정"
        rows={filteredDecisionRows}
        emptyStateTitle="표시할 평가 기록이 없습니다."
        emptyStateDescription="선택한 심볼 기준으로 아직 저장된 decision row가 없습니다."
      />
    </div>
  );
}

export function SchedulerView({
  operator,
  schedulerRows,
}: {
  operator: OperatorDashboardPayload;
  schedulerRows: Row[];
}) {
  return (
    <div className="space-y-6">
      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">스케줄러</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">주기와 검토 예정 상태 확인</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          이 탭은 AI 판단 자체보다 언제 검토가 예정되어 있는지, 왜 건너뛰었는지 또는 중복 처리되었는지, 마지막 호출과 다음 예정 시각이
          언제인지에 집중합니다.
        </p>
        <div className="mt-5 grid gap-4 lg:grid-cols-4">
          {metricCard("현재 상태", translateSchedulerStatus(operator.control.scheduler_status), "최근 스케줄러 실행 상태")}
          {metricCard("실행 윈도우", operator.control.scheduler_window ?? "-", "현재 대표 실행 주기")}
          {metricCard("다음 실행 예정", formatDateTime(operator.control.scheduler_next_run_at), "전역 스케줄 기준")}
          {metricCard(
            "운영 상태",
            operator.control.trading_paused ? "일시 중지" : translateOperatingState(operator.control.operating_state),
            "상세 차단 사유는 의사결정 탭에서 확인",
          )}
        </div>
      </section>

      <section className="rounded-[1.75rem] border border-slate-200 bg-white p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">AI 검토 일정</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">심볼별 AI 검토 / 건너뜀 상태</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          왜 AI가 불렸는지, 왜 안 불렸는지, 중복 지문으로 건너뛰었는지, 다음 검토가 언제인지, soft cap과 차단 코드가 어떤지
          심볼별로 바로 읽을 수 있습니다.
        </p>
        <div className="mt-5 grid gap-4 xl:grid-cols-3">
          {operator.symbols.map((symbol) => {
            const review = aiReviewSummary(symbol);
            return (
              <div key={symbol.symbol} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-base font-semibold text-slate-950">{symbol.symbol}</h3>
                    <p className="mt-1 text-xs text-slate-500">{symbol.timeframe ?? "-"}</p>
                  </div>
                  <span
                    className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(
                      symbol.ai_decision.trigger_deduped
                        ? "warn"
                        : symbol.ai_decision.last_ai_invoked_at
                          ? "good"
                          : symbol.ai_decision.last_ai_skip_reason
                            ? "neutral"
                            : "neutral",
                    )}`}
                  >
                    {review.label}
                  </span>
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  {metricCard(
                    "검토 사유",
                    translateAiTriggerReason(symbol.ai_decision.last_ai_trigger_reason),
                    review.detail,
                    { compact: true },
                  )}
                  {metricCard(
                    "건너뜀 상태",
                    translateAiSkipReason(symbol.ai_decision.last_ai_skip_reason),
                    symbol.ai_decision.trigger_deduped ? "중복 지문 감지됨" : "중복 지문 없음",
                    { compact: true },
                  )}
                  {metricCard(
                    "마지막 AI 호출",
                    formatDateTime(symbol.ai_decision.last_ai_invoked_at),
                    `제공자 ${symbol.ai_decision.provider_name ?? "-"}`,
                    { compact: true },
                  )}
                  {metricCard(
                    "다음 검토 예정",
                    formatDateTime(symbol.ai_decision.next_ai_review_due_at),
                    `지문 ${shortFingerprint(symbol.ai_decision.trigger_fingerprint)}`,
                    { compact: true },
                  )}
                  {metricCard(
                    "슬롯 / soft cap",
                    symbol.ai_decision.assigned_slot ?? symbol.candidate_selection.assigned_slot ?? "-",
                    symbol.risk_guard.portfolio_slot_soft_cap_applied ? "soft cap 적용" : "soft cap 미적용",
                    { compact: true },
                  )}
                  {metricCard(
                    "차단 사유",
                    formatTranslatedCodeList(
                      symbol.risk_guard.blocked_reason_codes.length > 0
                        ? symbol.risk_guard.blocked_reason_codes
                        : symbol.candidate_selection.blocked_reason_codes,
                    ),
                    `원시 코드 ${formatCodeList(
                      symbol.risk_guard.blocked_reason_codes.length > 0
                        ? symbol.risk_guard.blocked_reason_codes
                        : symbol.candidate_selection.blocked_reason_codes,
                    )}`,
                    { compact: true },
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <DataTable
        title="스케줄러 실행 기록"
        description="주기 상태와 결과"
        rows={schedulerRows}
        emptyStateTitle="표시할 스케줄러 기록이 없습니다."
        emptyStateDescription="아직 scheduler run이 저장되지 않았습니다."
      />
    </div>
  );
}

export function AgentDebugView({ agentRows }: { agentRows: Row[] }) {
  return (
    <div className="space-y-6">
      <section className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">에이전트</p>
        <h2 className="mt-2 text-xl font-semibold text-slate-950">디버그 / 고급 정보</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          이 탭은 운영 핵심 판단 화면이 아닙니다. raw provider, role, payload, schema validation 같은 디버그성 정보만 확인합니다.
        </p>
      </section>

      <DataTable
        title="에이전트 실행 기록"
        description="디버그 / raw metadata"
        rows={agentRows}
        emptyStateTitle="표시할 에이전트 실행 기록이 없습니다."
        emptyStateDescription="저장된 agent run이 아직 없습니다."
      />
    </div>
  );
}
