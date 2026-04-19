"use client";

export type AIUsagePayload = {
  recent_ai_calls_24h: number;
  recent_ai_calls_7d: number;
  recent_ai_successes_24h: number;
  recent_ai_successes_7d: number;
  recent_ai_failures_24h: number;
  recent_ai_failures_7d: number;
  recent_ai_tokens_24h: Record<string, number>;
  recent_ai_tokens_7d: Record<string, number>;
  recent_ai_role_calls_24h: Record<string, number>;
  recent_ai_role_calls_7d: Record<string, number>;
  recent_ai_role_failures_24h: Record<string, number>;
  recent_ai_role_failures_7d: Record<string, number>;
  recent_ai_failure_reasons: string[];
  observed_monthly_ai_calls_projection: number;
  observed_monthly_ai_calls_projection_breakdown: Record<string, number>;
  manual_ai_guard_minutes: number;
};

const roleLabels: Record<string, string> = {
  trading_decision: "거래 판단",
  chief_review: "운영 검토",
};

function formatNumber(value: number) {
  return value.toLocaleString("ko-KR");
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
    <div className="rounded-[1.4rem] border border-amber-200 bg-white px-4 py-4">
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
    <div className="rounded-[1.4rem] border border-amber-200 bg-white px-4 py-4">
      <p className="text-sm font-semibold text-slate-900">{title}</p>
      {roles.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">아직 집계된 AI 호출 기록이 없습니다.</p>
      ) : (
        <div className="mt-3 space-y-3">
          {roles.map((role) => (
            <div
              key={role}
              className="flex items-center justify-between rounded-2xl bg-canvas px-4 py-3 text-sm text-slate-700"
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

export function AIUsagePanel({ usage }: { usage: AIUsagePayload | null }) {
  if (usage === null) {
    return (
      <section className="space-y-5 rounded-[1.9rem] border border-amber-200/70 bg-canvas/70 p-4 sm:p-5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-500">AI 사용 관측</p>
          <h3 className="mt-2 text-xl font-semibold text-slate-900">실제 호출 기록 기준 모니터링</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            최근 AI 호출 집계를 불러오는 중입니다. 설정 본문은 먼저 표시하고, 사용량 통계는 분리 로드합니다.
          </p>
        </div>
        <div className="rounded-[1.4rem] border border-dashed border-amber-300 bg-white px-4 py-6 text-sm text-slate-500">
          AI 사용량을 불러오는 중입니다.
        </div>
      </section>
    );
  }

  const token24h = usage.recent_ai_tokens_24h.total_tokens ?? 0;
  const token7d = usage.recent_ai_tokens_7d.total_tokens ?? 0;
  const observedBreakdown = Object.entries(usage.observed_monthly_ai_calls_projection_breakdown);

  return (
    <section className="space-y-5 rounded-[1.9rem] border border-amber-200/70 bg-canvas/70 p-4 sm:p-5">
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

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="최근 24시간 호출"
          value={`${formatNumber(usage.recent_ai_calls_24h)}회`}
          hint={`성공 ${formatNumber(usage.recent_ai_successes_24h)} / 실패 ${formatNumber(usage.recent_ai_failures_24h)}`}
        />
        <MetricCard
          label="최근 7일 호출"
          value={`${formatNumber(usage.recent_ai_calls_7d)}회`}
          hint={`성공 ${formatNumber(usage.recent_ai_successes_7d)} / 실패 ${formatNumber(usage.recent_ai_failures_7d)}`}
        />
        <MetricCard
          label="수동 보호 간격"
          value={`${formatNumber(usage.manual_ai_guard_minutes)}분`}
          hint="반복 수동 실행으로 인한 과도한 AI 호출을 막는 최소 간격입니다."
        />
        <MetricCard
          label="최근 24시간 토큰"
          value={formatNumber(token24h)}
          hint={`Prompt ${formatNumber(usage.recent_ai_tokens_24h.prompt_tokens ?? 0)} / Completion ${formatNumber(
            usage.recent_ai_tokens_24h.completion_tokens ?? 0,
          )}`}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <MetricCard
          label="최근 7일 토큰"
          value={formatNumber(token7d)}
          hint={`Prompt ${formatNumber(usage.recent_ai_tokens_7d.prompt_tokens ?? 0)} / Completion ${formatNumber(
            usage.recent_ai_tokens_7d.completion_tokens ?? 0,
          )}`}
        />
        <MetricCard
          label="관측 월간 환산"
          value={`${formatNumber(usage.observed_monthly_ai_calls_projection)}회`}
          hint="최근 24시간 또는 7일 실제 호출 기록을 기준으로 환산한 참고값입니다."
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <BreakdownTable
          title="최근 24시간 역할별 호출"
          calls={usage.recent_ai_role_calls_24h}
          failures={usage.recent_ai_role_failures_24h}
        />
        <BreakdownTable
          title="최근 7일 역할별 호출"
          calls={usage.recent_ai_role_calls_7d}
          failures={usage.recent_ai_role_failures_7d}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-[1.4rem] border border-amber-200 bg-white px-4 py-4">
          <p className="text-sm font-semibold text-slate-900">최근 실패 사유</p>
          {usage.recent_ai_failure_reasons.length === 0 ? (
            <p className="mt-3 text-sm text-slate-500">최근 7일 기준 실패 사유가 없습니다.</p>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              {usage.recent_ai_failure_reasons.map((reason) => (
                <span
                  key={reason}
                  className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-slate-700"
                >
                  {reason}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="rounded-[1.4rem] border border-amber-200 bg-white px-4 py-4">
          <p className="text-sm font-semibold text-slate-900">관측 월간 환산 역할 분포</p>
          {observedBreakdown.length === 0 ? (
            <p className="mt-3 text-sm text-slate-500">최근 관측 기록 기준으로 환산한 역할별 호출 분포가 없습니다.</p>
          ) : (
            <div className="mt-3 space-y-3">
              {observedBreakdown.map(([role, count]) => (
                <div
                  key={role}
                  className="grid gap-2 rounded-2xl bg-canvas px-4 py-3 text-sm text-slate-700 sm:grid-cols-[1fr_auto]"
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
