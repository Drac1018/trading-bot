"use client";

export type AIUsagePayload = {
  recent_ai_calls_today_kst: number;
  recent_ai_calls_24h: number;
  recent_ai_calls_7d: number;
  recent_ai_calls_30d: number;
  recent_ai_successes_today_kst: number;
  recent_ai_successes_24h: number;
  recent_ai_successes_7d: number;
  recent_ai_successes_30d: number;
  recent_ai_failures_today_kst: number;
  recent_ai_failures_24h: number;
  recent_ai_failures_7d: number;
  recent_ai_failures_30d: number;
  recent_ai_tokens_today_kst: Record<string, number>;
  recent_ai_tokens_24h: Record<string, number>;
  recent_ai_tokens_7d: Record<string, number>;
  recent_ai_tokens_30d: Record<string, number>;
  recent_ai_role_calls_today_kst: Record<string, number>;
  recent_ai_role_calls_24h: Record<string, number>;
  recent_ai_role_calls_7d: Record<string, number>;
  recent_ai_role_calls_30d: Record<string, number>;
  recent_ai_role_failures_today_kst: Record<string, number>;
  recent_ai_role_failures_24h: Record<string, number>;
  recent_ai_role_failures_7d: Record<string, number>;
  recent_ai_role_failures_30d: Record<string, number>;
  recent_ai_failure_reasons: string[];
  observed_monthly_ai_calls_projection: number;
  observed_monthly_ai_calls_projection_breakdown: Record<string, number>;
  observed_monthly_ai_cost_projection_usd?: number | null;
  observed_monthly_ai_net_projection_usd?: number | null;
  manual_ai_guard_minutes: number;
  ai_protection_status?: {
    role_budgets?: Record<
      string,
      {
        status?: string;
        reason?: string;
        calls_1h?: number;
        max_calls_1h?: number;
        tokens_24h?: number;
        max_tokens_24h?: number;
        retry_after_seconds?: number;
      }
    >;
  };
  ai_usage_today_timezone?: string;
  ai_usage_today_start_at?: string | null;
  ai_usage_today_end_at?: string | null;
  ai_usage_summary_today_kst?: AIUsageTelemetrySummary;
  ai_usage_summary_24h?: AIUsageTelemetrySummary;
  ai_usage_summary_7d?: AIUsageTelemetrySummary;
  ai_usage_summary_30d?: AIUsageTelemetrySummary;
  ai_cost_efficiency_summary?: AICostEfficiencySummary;
};

type AICostEfficiencySummary = {
  primary_window?: string;
  secondary_window?: string;
  monthly_projected_known_ai_cost_usd?: number | null;
  monthly_projected_net_after_ai_cost_usd?: number | null;
  monthly_projected_calls_from_30d?: number;
  waste_assessment?: {
    status?: string;
    note?: string;
    signals?: Array<{
      code?: string;
      severity?: string;
      message?: string;
      evidence?: Record<string, unknown>;
    }>;
  };
  focus_metrics?: Record<
    string,
    {
      provider_calls?: number | null;
      known_ai_cost_usd?: number | null;
      provider_to_order_rate?: number | null;
      net_after_ai_cost_usd?: number | null;
    }
  >;
};

type AIRoleEfficiencySummary = {
  provider_calls?: number;
  skipped_preai?: number;
  failed_calls?: number;
  input_tokens?: number;
  output_tokens?: number;
  known_ai_cost_usd?: number;
  missing_usage_rows?: number;
  unknown_cost_rows?: number;
  cost_per_provider_call_usd?: number | null;
  decision_counts?: Record<string, number>;
  hold_reason_buckets?: Record<string, number>;
  fail_closed_count?: number;
  downstream?: AIUsageTelemetrySummary["downstream"];
  actionability?: AIUsageTelemetrySummary["actionability"];
  roi?: AIUsageTelemetrySummary["roi"];
  net_contribution_estimate_usd?: number | null;
  advisor?: {
    calls?: number;
    profile_counts?: Record<string, number>;
    status_counts?: Record<string, number>;
    profile_change_count?: number;
    same_profile_recommendation_count?: number;
    ignored_count?: number;
    expired_count?: number;
    reuse_signal?: string | null;
  };
};

type AIUsageTelemetrySummary = {
  ai_calls_provider_invoked: number;
  ai_calls_skipped_preai: number;
  ai_calls_scheduler_skipped: number;
  ai_calls_suppressed_soft_signal: number;
  ai_calls_failed: number;
  known_estimated_cost_usd: number;
  missing_usage_rows: number;
  unknown_cost_rows: number;
  decision_counts?: Record<string, number>;
  preai_skip_reasons: Record<string, number>;
  scheduler_skip_reasons: Record<string, number>;
  downstream?: {
    risk_checks?: number;
    risk_allowed?: number;
    risk_blocked?: number;
    orders?: number;
    executions?: number;
    fills?: number;
    trade_net_realized_pnl?: number;
    net_realized_pnl?: number;
    risk_reason_counts?: Record<string, number>;
    pending_entry_plans?: number;
    active_pending_entry_plans?: number;
    canceled_pending_entry_plans?: number;
    expired_pending_entry_plans?: number;
    pending_plan_status_counts?: Record<string, number>;
  };
  roi: {
    trade_net_realized_pnl?: number;
    known_ai_cost_usd?: number;
    net_after_known_ai_cost_usd?: number;
    cost_complete?: boolean;
    missing_usage_rows?: number;
    cost_per_provider_call_usd?: number | null;
    cost_per_risk_allowed_usd?: number | null;
    cost_per_order_usd?: number | null;
    cost_per_fill_usd?: number | null;
  };
  actionability?: {
    provider_calls?: number;
    risk_checks_after_provider?: number;
    risk_allowed_after_provider?: number;
    risk_blocked_after_provider?: number;
    orders_after_provider?: number;
    fills_after_provider?: number;
    provider_to_risk_allowed_rate?: number | null;
    provider_to_risk_blocked_rate?: number | null;
    provider_to_order_rate?: number | null;
    provider_to_fill_rate?: number | null;
    usefulness_status?: string;
    warning_status?: string | null;
    warning_title?: string | null;
    warning_detail?: string | null;
  };
  role_efficiency?: Record<string, AIRoleEfficiencySummary>;
  reason_buckets?: Record<string, Record<string, number>>;
  preai_savings?: {
    provider_calls?: number;
    skipped_preai?: number;
    soft_signal_suppressed?: number;
    preai_skip_ratio?: number | null;
    estimated_cost_saved_usd?: number | null;
  };
};

const roleLabels: Record<string, string> = {
  trading_decision: "거래 판단",
  market_settings_advisor: "시장 설정 advisor",
  position_exit_review: "AI 익절 판단",
  chief_review: "운영 검토",
};

function formatNumber(value: number) {
  return value.toLocaleString("ko-KR");
}

function formatUsd(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "미집계";
  }
  return `$${value.toLocaleString("ko-KR", {
    minimumFractionDigits: value === 0 ? 2 : 4,
    maximumFractionDigits: 6,
  })}`;
}

function formatPercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "미집계";
  }
  return `${(value * 100).toLocaleString("ko-KR", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}%`;
}

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-white px-4 py-4">
      <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-900">{value}</p>
      <p className="mt-2 text-sm leading-6 text-slate-600">{hint}</p>
    </div>
  );
}

function BreakdownTable({
  title,
  calls,
  failures,
}: {
  title: string;
  calls: Record<string, number>;
  failures: Record<string, number>;
}) {
  const roles = Array.from(new Set([...Object.keys(calls), ...Object.keys(failures)]));

  return (
    <div className="rounded-md border border-slate-200 bg-white px-4 py-4">
      <p className="text-sm font-semibold text-slate-900">{title}</p>
      {roles.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">아직 집계된 AI 호출 기록이 없습니다.</p>
      ) : (
        <div className="mt-3 space-y-3">
          {roles.map((role) => (
            <div
              key={role}
              className="flex items-center justify-between rounded-md bg-slate-50 px-4 py-3 text-sm text-slate-700"
            >
              <div>
                <p className="font-semibold text-slate-900">{roleLabels[role] ?? role}</p>
                <p className="text-xs text-slate-500">실패 {formatNumber(failures[role] ?? 0)}회</p>
              </div>
              <p className="text-lg font-semibold text-slate-900">{formatNumber(calls[role] ?? 0)}회</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function topEntries(values: Record<string, number> | undefined, limit = 5): Array<[string, number]> {
  return Object.entries(values ?? {})
    .filter(([, value]) => Number.isFinite(value) && value > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit);
}

function ReasonBucketCard({
  title,
  buckets,
  emptyText,
}: {
  title: string;
  buckets: Record<string, number> | undefined;
  emptyText: string;
}) {
  const items = topEntries(buckets);
  return (
    <div className="rounded-md border border-slate-200 bg-white px-4 py-4">
      <p className="text-sm font-semibold text-slate-900">{title}</p>
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">{emptyText}</p>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          {items.map(([reason, count]) => (
            <span
              key={reason}
              className="rounded-md border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-700"
            >
              {reason} {formatNumber(count)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function RoleEfficiencyPanel({ roles }: { roles: Record<string, AIRoleEfficiencySummary> | undefined }) {
  const focusRoles = ["trading_decision", "market_settings_advisor"];
  const roleEntries = Object.entries(roles ?? {}).sort((a, b) => {
    const focusDelta = focusRoles.indexOf(a[0]) - focusRoles.indexOf(b[0]);
    if (focusRoles.includes(a[0]) && focusRoles.includes(b[0])) {
      return focusDelta;
    }
    if (focusRoles.includes(a[0])) {
      return -1;
    }
    if (focusRoles.includes(b[0])) {
      return 1;
    }
    return a[0].localeCompare(b[0]);
  });

  return (
    <div className="rounded-md border border-slate-200 bg-white px-4 py-4">
      <p className="text-sm font-semibold text-slate-900">역할별 비용 효율</p>
      {roleEntries.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">역할별 provider 호출 기록이 없습니다.</p>
      ) : (
        <div className="mt-3 space-y-3">
          {roleEntries.map(([role, item]) => {
            const advisor = item.advisor;
            const actionability = item.actionability ?? {};
            const downstream = item.downstream ?? {};
            const roleHint =
              role === "market_settings_advisor"
                ? `profile 변경 ${formatNumber(advisor?.profile_change_count ?? 0)} / 반복 추천 ${formatNumber(
                    advisor?.same_profile_recommendation_count ?? 0,
                  )}${advisor?.reuse_signal ? ` / ${advisor.reuse_signal}` : ""}`
                : `risk 승인 ${formatNumber(downstream.risk_allowed ?? 0)} / 주문 ${formatNumber(
                    downstream.orders ?? 0,
                  )} / 체결 ${formatNumber(downstream.fills ?? 0)}`;
            return (
              <div key={role} className="rounded-md bg-slate-50 px-4 py-3 text-sm text-slate-700">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <p className="font-semibold text-slate-900">{roleLabels[role] ?? role}</p>
                    <p className="mt-1 text-xs leading-5 text-slate-500">{roleHint}</p>
                  </div>
                  <p className="text-sm font-semibold text-slate-900">{formatUsd(item.known_ai_cost_usd)}</p>
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-3">
                  <p className="rounded-md bg-white px-3 py-2 text-xs text-slate-600">
                    provider {formatNumber(item.provider_calls ?? 0)}회
                  </p>
                  <p className="rounded-md bg-white px-3 py-2 text-xs text-slate-600">
                    호출당 {formatUsd(item.cost_per_provider_call_usd)}
                  </p>
                  <p className="rounded-md bg-white px-3 py-2 text-xs text-slate-600">
                    주문 전환 {formatPercent(actionability.provider_to_order_rate)}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function WasteSignalPanel({ summary }: { summary: AICostEfficiencySummary | undefined }) {
  const signals = summary?.waste_assessment?.signals ?? [];
  return (
    <div className="rounded-md border border-slate-200 bg-white px-4 py-4">
      <p className="text-sm font-semibold text-slate-900">비용 낭비 신호</p>
      {signals.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">
          7일/30일 전환율과 AI 비용 반영 순효과 기준으로 즉시 경고할 신호는 없습니다.
        </p>
      ) : (
        <div className="mt-3 space-y-3">
          {signals.map((signal) => (
            <div
              key={signal.code ?? signal.message}
              className={`rounded-md border px-4 py-3 text-sm ${
                signal.severity === "warning"
                  ? "border-amber-200 bg-amber-50 text-amber-900"
                  : "border-slate-200 bg-slate-50 text-slate-700"
              }`}
            >
              <p className="font-semibold">{signal.message ?? signal.code}</p>
              {signal.code ? <p className="mt-1 text-xs opacity-80">{signal.code}</p> : null}
            </div>
          ))}
        </div>
      )}
      <p className="mt-3 text-xs leading-5 text-slate-500">
        {summary?.waste_assessment?.note ??
          "24시간 체결 없음은 단독 경고 조건으로 사용하지 않습니다."}
      </p>
    </div>
  );
}

export function AIUsagePanel({ usage }: { usage: AIUsagePayload | null }) {
  if (usage === null) {
    return (
      <section className="space-y-5 rounded-lg border border-slate-200 bg-slate-50 p-4 sm:p-5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">AI 사용 관측</p>
          <h3 className="mt-2 text-xl font-semibold text-slate-900">실제 호출 기록 기준 모니터링</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            최근 AI 호출 집계를 불러오는 중입니다. 설정 본문은 먼저 표시하고, 사용량 통계는 분리 로드합니다.
          </p>
        </div>
        <div className="rounded-md border border-dashed border-slate-300 bg-white px-4 py-6 text-sm text-slate-500">
          AI 사용량을 불러오는 중입니다.
        </div>
      </section>
    );
  }

  const token7d = usage.recent_ai_tokens_7d.total_tokens ?? 0;
  const token30d = usage.recent_ai_tokens_30d.total_tokens ?? 0;
  const observedBreakdown = Object.entries(usage.observed_monthly_ai_calls_projection_breakdown);
  const summaryTodayKst = usage.ai_usage_summary_today_kst;
  const summary24h = usage.ai_usage_summary_24h;
  const summary7d = usage.ai_usage_summary_7d;
  const summary30d = usage.ai_usage_summary_30d;
  const roi24h = summary24h?.roi ?? {};
  const roi7d = summary7d?.roi ?? {};
  const roi30d = summary30d?.roi ?? {};
  const actionability7d = summary7d?.actionability ?? {};
  const actionability30d = summary30d?.actionability ?? {};
  const efficiency = usage.ai_cost_efficiency_summary;
  const roleEfficiency7d = summary7d?.role_efficiency;
  const reasonBuckets7d = summary7d?.reason_buckets ?? {};

  return (
    <section className="space-y-5 rounded-lg border border-slate-200 bg-slate-50 p-4 sm:p-5">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">AI 사용 관측</p>
          <h3 className="mt-2 text-xl font-semibold text-slate-900">실제 호출 기록 기준 모니터링</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            현재 구조는 고정 주기 호출이 아니라 이벤트 기반 재검토 + 주기 백스톱입니다. 그래서 이 패널은 설정값보다
            최근 실제 호출 기록을 우선 보여줍니다. 수동 실행 보호 간격은 최소 {usage.manual_ai_guard_minutes}분입니다.
          </p>
        </div>
      </div>

      {efficiency?.waste_assessment?.status === "needs_review" || actionability7d.warning_status ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-4">
          <p className="text-sm font-semibold text-amber-900">
            {actionability7d.warning_title ?? "AI 비용 효율 확인 필요"}
          </p>
          <p className="mt-2 text-sm leading-6 text-amber-800">
            {actionability7d.warning_detail ??
              "7일/30일 기준 주문 전환율, AI 비용 반영 순효과, 반복 HOLD 구간을 함께 확인해야 합니다."}
          </p>
          <p className="mt-2 text-xs leading-5 text-amber-700">
            24시간 체결 없음은 단독 경고 조건으로 사용하지 않습니다. provider 호출{" "}
            {formatNumber(actionability7d.provider_calls ?? 0)}회 / 주문 전환{" "}
            {formatPercent(actionability7d.provider_to_order_rate)} / 체결 전환{" "}
            {formatPercent(actionability7d.provider_to_fill_rate)}
          </p>
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="7일 주문 전환"
          value={formatPercent(actionability7d.provider_to_order_rate)}
          hint={`provider ${formatNumber(actionability7d.provider_calls ?? usage.recent_ai_calls_7d)}회 / risk 승인 ${formatPercent(
            actionability7d.provider_to_risk_allowed_rate,
          )}`}
        />
        <MetricCard
          label="7일 AI 순기여"
          value={formatUsd(roi7d.net_after_known_ai_cost_usd)}
          hint={`체결 순손익 ${formatUsd(roi7d.trade_net_realized_pnl)} - 확인된 AI 비용 ${formatUsd(
            roi7d.known_ai_cost_usd,
          )}`}
        />
        <MetricCard
          label="30일 AI 순기여"
          value={formatUsd(roi30d.net_after_known_ai_cost_usd)}
          hint={`provider ${formatNumber(summary30d?.ai_calls_provider_invoked ?? usage.recent_ai_calls_30d)}회 / 주문 전환 ${formatPercent(
            actionability30d.provider_to_order_rate,
          )}`}
        />
        <MetricCard
          label="월간 비용 추정"
          value={formatUsd(usage.observed_monthly_ai_cost_projection_usd)}
          hint={`30일 관측 ${formatNumber(
            efficiency?.monthly_projected_calls_from_30d ?? usage.recent_ai_calls_30d,
          )}회 / 월간 순효과 ${formatUsd(usage.observed_monthly_ai_net_projection_usd)}`}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="KST 오늘 호출"
          value={`${formatNumber(usage.recent_ai_calls_today_kst)}회`}
          hint={`성공 ${formatNumber(usage.recent_ai_successes_today_kst)} / 실패 ${formatNumber(
            usage.recent_ai_failures_today_kst,
          )}. 보조 지표입니다.`}
        />
        <MetricCard
          label="최근 7일 토큰"
          value={formatNumber(token7d)}
          hint={`Prompt ${formatNumber(usage.recent_ai_tokens_7d.prompt_tokens ?? 0)} / Completion ${formatNumber(
            usage.recent_ai_tokens_7d.completion_tokens ?? 0,
          )}`}
        />
        <MetricCard
          label="최근 30일 토큰"
          value={formatNumber(token30d)}
          hint={`Prompt ${formatNumber(usage.recent_ai_tokens_30d.prompt_tokens ?? 0)} / Completion ${formatNumber(
            usage.recent_ai_tokens_30d.completion_tokens ?? 0,
          )}`}
        />
        <MetricCard
          label="수동 보호 간격"
          value={`${formatNumber(usage.manual_ai_guard_minutes)}분`}
          hint="반복 수동 실행으로 인한 과도한 AI 호출을 막는 최소 간격입니다."
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="KST 오늘 관망 억제"
          value={`${formatNumber(summaryTodayKst?.ai_calls_suppressed_soft_signal ?? 0)}회`}
          hint={`KST 00시 이후 OpenAI 호출 전 차단. 스케줄러 skip ${formatNumber(
            summaryTodayKst?.ai_calls_scheduler_skipped ?? 0,
          )}회`}
        />
        <MetricCard
          label="KST 오늘 AI 비용"
          value={formatUsd(summaryTodayKst?.known_estimated_cost_usd)}
          hint={`usage 누락 ${formatNumber(summaryTodayKst?.missing_usage_rows ?? 0)}건 / 추정 불가 ${formatNumber(
            summaryTodayKst?.unknown_cost_rows ?? 0,
          )}건`}
        />
        <MetricCard
          label="24시간 AI 순기여 보조"
          value={formatUsd(roi24h.net_after_known_ai_cost_usd)}
          hint={`체결 순손익 ${formatUsd(roi24h.trade_net_realized_pnl)} - 확인된 AI 비용 ${formatUsd(
            roi24h.known_ai_cost_usd,
          )}`}
        />
        <MetricCard
          label="7일 AI 순기여"
          value={formatUsd(roi7d.net_after_known_ai_cost_usd)}
          hint={`확인된 AI 비용 ${formatUsd(roi7d.known_ai_cost_usd)} / 체결당 비용 ${formatUsd(
            roi7d.cost_per_fill_usd,
          )}`}
        />
        <MetricCard
          label="7일 체결 전환"
          value={formatPercent(actionability7d.provider_to_fill_rate)}
          hint={`risk 승인 ${formatPercent(actionability7d.provider_to_risk_allowed_rate)} / 주문 ${formatPercent(
            actionability7d.provider_to_order_rate,
          )}`}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <BreakdownTable
          title="최근 7일 역할별 호출"
          calls={usage.recent_ai_role_calls_7d}
          failures={usage.recent_ai_role_failures_7d}
        />
        <BreakdownTable
          title="최근 30일 역할별 호출"
          calls={usage.recent_ai_role_calls_30d}
          failures={usage.recent_ai_role_failures_30d}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <RoleEfficiencyPanel roles={roleEfficiency7d} />
        <WasteSignalPanel summary={efficiency} />
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <ReasonBucketCard
          title="7일 HOLD 반복 reason"
          buckets={reasonBuckets7d.hold}
          emptyText="7일 기준 HOLD 반복 reason이 집계되지 않았습니다."
        />
        <ReasonBucketCard
          title="7일 pre-AI skip 절감"
          buckets={reasonBuckets7d.preai_skip}
          emptyText="pre-AI skip reason이 집계되지 않았습니다."
        />
        <ReasonBucketCard
          title="7일 risk 차단 reason"
          buckets={reasonBuckets7d.risk}
          emptyText="risk 차단 reason이 집계되지 않았습니다."
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-md border border-slate-200 bg-white px-4 py-4">
          <p className="text-sm font-semibold text-slate-900">최근 실패 사유</p>
          {usage.recent_ai_failure_reasons.length === 0 ? (
            <p className="mt-3 text-sm text-slate-500">최근 7일 기준 실패 사유가 없습니다.</p>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              {usage.recent_ai_failure_reasons.map((reason) => (
                <span
                  key={reason}
                  className="rounded-md border border-blue-100 bg-blue-50 px-3 py-1 text-xs font-semibold text-slate-700"
                >
                  {reason}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="rounded-md border border-slate-200 bg-white px-4 py-4">
          <p className="text-sm font-semibold text-slate-900">관측 월간 환산 역할 분포</p>
          {observedBreakdown.length === 0 ? (
            <p className="mt-3 text-sm text-slate-500">최근 30일 또는 7일 기준으로 환산한 역할별 호출 분포가 없습니다.</p>
          ) : (
            <div className="mt-3 space-y-3">
              {observedBreakdown.map(([role, count]) => (
                <div
                  key={role}
                  className="grid gap-2 rounded-md bg-slate-50 px-4 py-3 text-sm text-slate-700 sm:grid-cols-[1fr_auto]"
                >
                  <p className="font-semibold text-slate-900">{roleLabels[role] ?? role}</p>
                  <p>{formatNumber(count)}회</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

