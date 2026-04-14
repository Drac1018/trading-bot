"use client";

import { useState, useTransition } from "react";

import { formatDisplayValue } from "../lib/ui-copy";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

type UserRequest = {
  id: number;
  title: string;
  detail: string;
  status: "requested" | "accepted" | "applied" | "verified";
  linked_backlog_id: number | null;
  linked_backlog_title: string | null;
  created_at: string;
  updated_at: string;
};

type AppliedRecord = {
  id: number;
  title: string;
  summary: string;
  detail: string;
  related_backlog_id: number | null;
  related_backlog_title: string | null;
  source_type: "ai" | "user" | "manual";
  files_changed: string[];
  verification_summary: string;
  applied_at: string;
  created_at: string;
  updated_at: string;
};

type CodexPromptDraft = {
  available: boolean;
  title: string;
  prompt: string;
  generated_at: string;
  note: string;
};

type BacklogItem = {
  id: number;
  title: string;
  problem: string;
  proposal: string;
  severity: string;
  effort: string;
  impact: string;
  priority: string;
  rationale: string;
  source: string;
  status: string;
  auto_apply_supported: boolean;
  auto_apply_label: string | null;
  created_at: string;
  updated_at: string;
  user_requests: UserRequest[];
  applied_records: AppliedRecord[];
  codex_prompt_draft: CodexPromptDraft | null;
};

type AutoApplyResult = {
  backlog_id: number;
  title: string;
  backlog_status: string;
  auto_apply_supported: boolean;
  handler_key: string | null;
  already_applied: boolean;
  message: string;
  applied_record: AppliedRecord | null;
};

type SignalPerformanceEntry = {
  rationale_code: string;
  decisions: number;
  approvals: number;
  orders: number;
  fills: number;
  holds: number;
  longs: number;
  shorts: number;
  reduces: number;
  exits: number;
  wins: number;
  losses: number;
  realized_pnl_total: number;
  fee_total: number;
  net_realized_pnl_total: number;
  average_slippage_pct: number;
  latest_seen_at: string;
};

type PerformanceAggregateEntry = {
  key: string;
  decisions: number;
  approvals: number;
  orders: number;
  fills: number;
  holds: number;
  longs: number;
  shorts: number;
  reduces: number;
  exits: number;
  wins: number;
  losses: number;
  realized_pnl_total: number;
  fee_total: number;
  net_realized_pnl_total: number;
  average_slippage_pct: number;
  latest_seen_at: string;
};

type FeatureFlagPerformanceEntry = {
  flag_name: string;
  enabled: PerformanceAggregateEntry;
  disabled: PerformanceAggregateEntry;
};

type PerformanceWindowSummary = {
  decisions: number;
  approvals: number;
  orders: number;
  fills: number;
  holds: number;
  longs: number;
  shorts: number;
  reduces: number;
  exits: number;
  wins: number;
  losses: number;
  realized_pnl_total: number;
  fee_total: number;
  net_realized_pnl_total: number;
  average_slippage_pct: number;
  average_holding_minutes: number;
  holding_over_plan_count: number;
  stop_loss_closes: number;
  take_profit_closes: number;
  manual_closes: number;
  unclassified_closes: number;
  snapshot_net_pnl_estimate: number;
};

type PerformanceWindowReport = {
  window_label: string;
  window_hours: number;
  summary: PerformanceWindowSummary;
  rationale_codes: PerformanceAggregateEntry[];
  symbols: PerformanceAggregateEntry[];
  timeframes: PerformanceAggregateEntry[];
  regimes: PerformanceAggregateEntry[];
  trend_alignments: PerformanceAggregateEntry[];
  directions: PerformanceAggregateEntry[];
  hold_conditions: PerformanceAggregateEntry[];
  close_outcomes: PerformanceAggregateEntry[];
  feature_flags: FeatureFlagPerformanceEntry[];
};

type SignalPerformanceReport = {
  generated_at: string;
  window_hours: number;
  items: SignalPerformanceEntry[];
  windows: PerformanceWindowReport[];
};

type StructuredCompetitorNote = {
  id: number;
  source: string;
  category: string;
  differentiation: string;
  summary: string;
  tags: string[];
  created_at: string;
};

type StructuredCompetitorNotes = {
  generated_at: string;
  category_breakdown: Record<string, number>;
  items: StructuredCompetitorNote[];
};

export type BacklogBoardPayload = {
  ai_backlog: BacklogItem[];
  unlinked_user_requests: UserRequest[];
  unlinked_applied_records: AppliedRecord[];
  signal_performance_report: SignalPerformanceReport | null;
  structured_competitor_notes: StructuredCompetitorNotes | null;
};

const inputClass =
  "w-full rounded-2xl border border-amber-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-amber-400";

function SectionHeader({
  title,
  description,
  count,
  actions,
}: {
  title: string;
  description: string;
  count?: number;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h2 className="text-2xl font-semibold text-ink">{title}</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">{description}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {typeof count === "number" ? (
          <span className="w-fit rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-800">
            {count}건
          </span>
        ) : null}
        {actions}
      </div>
    </div>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full border border-amber-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
      {children}
    </span>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-amber-300 px-4 py-8 text-sm text-slate-500">
      {message}
    </div>
  );
}

function SignalPerformanceSection({ report }: { report: SignalPerformanceReport | null }) {
  if (!report || report.items.length === 0) {
    return (
      <section className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
        <SectionHeader
          title="시그널 성과 분해"
          description="최근 24시간 rationale code 기준 성과 리포트입니다."
        />
        <div className="mt-5">
          <EmptyState message="최근 24시간 기준으로 집계할 시그널 성과 데이터가 아직 없습니다." />
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
      <SectionHeader
        title="시그널 성과 분해"
        description={`최근 ${report.window_hours}시간 rationale code 기준 성과 리포트입니다.`}
        count={report.items.length}
      />
      <p className="mt-3 text-xs text-slate-500">생성 {formatDisplayValue(report.generated_at, "created_at")}</p>
      <div className="mt-5 space-y-3">
        {report.items.map((item) => (
          <article key={item.rationale_code} className="rounded-2xl border border-amber-100 bg-white p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <h4 className="text-base font-semibold text-ink">{item.rationale_code}</h4>
                <p className="mt-2 text-xs text-slate-500">
                  최신 감지 {formatDisplayValue(item.latest_seen_at, "created_at")}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge>의사결정 {item.decisions}건</Badge>
                <Badge>승인 {item.approvals}건</Badge>
                <Badge>주문 {item.orders}건</Badge>
                <Badge>체결 {item.fills}건</Badge>
              </div>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-2xl bg-canvas p-3 text-sm text-slate-700">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">승/패</p>
                <p className="mt-2">승 {item.wins} / 패 {item.losses}</p>
              </div>
              <div className="rounded-2xl bg-canvas p-3 text-sm text-slate-700">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">실현손익 합계</p>
                <p className="mt-2">{formatDisplayValue(item.realized_pnl_total, "realized_pnl")}</p>
              </div>
              <div className="rounded-2xl bg-canvas p-3 text-sm text-slate-700">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">평균 슬리피지</p>
                <p className="mt-2">{formatDisplayValue(item.average_slippage_pct, "slippage_threshold_pct")}</p>
              </div>
              <div className="rounded-2xl bg-canvas p-3 text-sm text-slate-700">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">적용 범위</p>
                <p className="mt-2">24시간 제품 리뷰 입력에 포함</p>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function PerformanceMiniTable({
  title,
  description,
  items,
}: {
  title: string;
  description: string;
  items: PerformanceAggregateEntry[];
}) {
  return (
    <section className="rounded-2xl border border-amber-100 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">{title}</h4>
          <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
        </div>
        <Badge>{items.length}</Badge>
      </div>
      <div className="mt-4 space-y-3">
        {items.length > 0 ? (
          items.map((item) => (
            <div key={item.key} className="rounded-2xl bg-canvas p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900">{item.key}</p>
                <Badge>hold {item.holds}</Badge>
              </div>
              <div className="mt-2 grid gap-2 text-xs text-slate-600 sm:grid-cols-3">
                <span>net {formatDisplayValue(item.net_realized_pnl_total, "realized_pnl")}</span>
                <span>decisions {item.decisions}</span>
                <span>fills {item.fills}</span>
              </div>
            </div>
          ))
        ) : (
          <EmptyState message="No data in this window." />
        )}
      </div>
    </section>
  );
}

function SignalPerformanceInsightsSection({ report }: { report: SignalPerformanceReport | null }) {
  if (!report || report.windows.length === 0) {
    return null;
  }

  const primaryWindow = report.windows.find((item) => item.window_hours === report.window_hours) ?? report.windows[0];
  const topRationales = report.items.slice(0, 4);

  return (
    <section className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
      <SectionHeader
        title="Strategy Analytics"
        description={`Recent ${report.window_hours}h performance by regime, direction, hold conditions, and feature flags.`}
        count={primaryWindow.summary.decisions}
      />
      <p className="mt-3 text-xs text-slate-500">Generated {formatDisplayValue(report.generated_at, "created_at")}</p>
      <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-2xl bg-canvas p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Net Realized</p>
          <p className="mt-2 text-lg font-semibold text-slate-900">
            {formatDisplayValue(primaryWindow.summary.net_realized_pnl_total, "realized_pnl")}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            fee {formatDisplayValue(primaryWindow.summary.fee_total, "realized_pnl")}
          </p>
        </div>
        <div className="rounded-2xl bg-canvas p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Decision Mix</p>
          <p className="mt-2 text-sm text-slate-700">
            hold {primaryWindow.summary.holds} / long {primaryWindow.summary.longs} / short {primaryWindow.summary.shorts}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            fills {primaryWindow.summary.fills} / approvals {primaryWindow.summary.approvals}
          </p>
        </div>
        <div className="rounded-2xl bg-canvas p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Close Outcome</p>
          <p className="mt-2 text-sm text-slate-700">
            TP {primaryWindow.summary.take_profit_closes} / SL {primaryWindow.summary.stop_loss_closes}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            manual {primaryWindow.summary.manual_closes} / other {primaryWindow.summary.unclassified_closes}
          </p>
        </div>
        <div className="rounded-2xl bg-canvas p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Tracking Basis</p>
          <p className="mt-2 text-sm text-slate-700">
            snapshot {formatDisplayValue(primaryWindow.summary.snapshot_net_pnl_estimate, "realized_pnl")}
          </p>
          <p className="mt-1 text-xs text-slate-500">MFE/MAE hook ready via replay path</p>
        </div>
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-3">
        <PerformanceMiniTable
          title="Regime"
          description="Which market regime produced the best net result."
          items={primaryWindow.regimes.slice(0, 4)}
        />
        <PerformanceMiniTable
          title="Direction"
          description="Long, short, and hold mix in the same window."
          items={primaryWindow.directions.slice(0, 4)}
        />
        <PerformanceMiniTable
          title="Hold Conditions"
          description="Conditions where hold decisions clustered most often."
          items={primaryWindow.hold_conditions.slice(0, 4)}
        />
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-2">
        <section className="rounded-2xl border border-amber-100 bg-white p-4">
          <h4 className="text-sm font-semibold text-slate-900">Rationale Codes</h4>
          <p className="mt-1 text-xs text-slate-500">Loss-heavy rationale codes surface here first.</p>
          <div className="mt-4 space-y-3">
            {topRationales.map((item) => (
              <div key={item.rationale_code} className="rounded-2xl bg-canvas p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.rationale_code}</p>
                  <Badge>hold {item.holds}</Badge>
                </div>
                <div className="mt-2 grid gap-2 text-xs text-slate-600 sm:grid-cols-4">
                  <span>net {formatDisplayValue(item.net_realized_pnl_total, "realized_pnl")}</span>
                  <span>w/l {item.wins}/{item.losses}</span>
                  <span>orders {item.orders}</span>
                  <span>fills {item.fills}</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-amber-100 bg-white p-4">
          <h4 className="text-sm font-semibold text-slate-900">Feature Flags</h4>
          <p className="mt-1 text-xs text-slate-500">Enabled vs disabled performance for the core regime flags.</p>
          <div className="mt-4 space-y-3">
            {primaryWindow.feature_flags.map((item) => (
              <div key={item.flag_name} className="rounded-2xl bg-canvas p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.flag_name}</p>
                  <Badge>enabled {item.enabled.decisions}</Badge>
                </div>
                <div className="mt-2 grid gap-2 text-xs text-slate-600 sm:grid-cols-2">
                  <span>on {formatDisplayValue(item.enabled.net_realized_pnl_total, "realized_pnl")}</span>
                  <span>off {formatDisplayValue(item.disabled.net_realized_pnl_total, "realized_pnl")}</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}

function StructuredCompetitorSection({ digest }: { digest: StructuredCompetitorNotes | null }) {
  if (!digest || digest.items.length === 0) {
    return (
      <section className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
        <SectionHeader
          title="구조화된 경쟁사 메모"
          description="기능 카테고리와 차별점 기준으로 정리한 경쟁사 메모입니다."
        />
        <div className="mt-5">
          <EmptyState message="구조화해 보여줄 경쟁사 메모가 아직 없습니다." />
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
      <SectionHeader
        title="구조화된 경쟁사 메모"
        description="최신 메모를 카테고리와 차별점 기준으로 바로 비교할 수 있습니다."
        count={digest.items.length}
      />
      <div className="mt-3 flex flex-wrap gap-2">
        {Object.entries(digest.category_breakdown).map(([category, count]) => (
          <Badge key={category}>
            {category} {count}건
          </Badge>
        ))}
      </div>
      <p className="mt-3 text-xs text-slate-500">생성 {formatDisplayValue(digest.generated_at, "created_at")}</p>
      <div className="mt-5 space-y-3">
        {digest.items.map((item) => (
          <article key={item.id} className="rounded-2xl border border-amber-100 bg-white p-4">
            <div className="flex flex-wrap gap-2">
              <Badge>{item.category}</Badge>
              <Badge>{formatDisplayValue(item.created_at, "created_at")}</Badge>
              <Badge>{item.source}</Badge>
            </div>
            <p className="mt-3 text-sm font-semibold text-slate-900">{item.differentiation}</p>
            <p className="mt-2 text-sm leading-6 text-slate-600">{item.summary}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {item.tags.length > 0 ? (
                item.tags.map((tag) => <Badge key={`${item.id}-${tag}`}>{tag}</Badge>)
              ) : (
                <span className="text-xs text-slate-500">기록된 태그 없음</span>
              )}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function LinkedRequestList({ items }: { items: UserRequest[] }) {
  if (items.length === 0) {
    return <EmptyState message="연결된 사용자 요청이 아직 없습니다." />;
  }

  return (
    <div className="space-y-3">
      {items.map((item) => (
        <article key={item.id} className="rounded-2xl border border-amber-100 bg-white p-4">
          <div className="flex flex-wrap gap-2">
            <Badge>{formatDisplayValue(item.status, "status")}</Badge>
            <Badge>{formatDisplayValue(item.created_at, "created_at")}</Badge>
          </div>
          <h4 className="mt-3 text-base font-semibold text-ink">{item.title}</h4>
          <p className="mt-2 text-sm leading-6 text-slate-600">{item.detail}</p>
        </article>
      ))}
    </div>
  );
}

function AppliedRecordList({ items }: { items: AppliedRecord[] }) {
  if (items.length === 0) {
    return <EmptyState message="연결된 적용 내역이 아직 없습니다." />;
  }

  return (
    <div className="space-y-3">
      {items.map((item) => (
        <article key={item.id} className="rounded-2xl border border-amber-100 bg-white p-4">
          <div className="flex flex-wrap gap-2">
            <Badge>{formatDisplayValue(item.source_type)}</Badge>
            <Badge>{formatDisplayValue(item.applied_at, "applied_at")}</Badge>
          </div>
          <h4 className="mt-3 text-base font-semibold text-ink">{item.title}</h4>
          <p className="mt-2 text-sm leading-6 text-slate-600">{item.summary}</p>
          <p className="mt-2 text-sm leading-6 text-slate-500">{item.detail}</p>
          <div className="mt-3 rounded-2xl bg-canvas p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">검증 / 확인</p>
            <p className="mt-2 text-sm leading-6 text-slate-700">{item.verification_summary}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {item.files_changed.length > 0 ? (
                item.files_changed.map((file) => <Badge key={file}>{file}</Badge>)
              ) : (
                <span className="text-xs text-slate-500">기록된 파일 없음</span>
              )}
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

function CodexPromptSection({ draft }: { draft: CodexPromptDraft | null }) {
  const [copied, setCopied] = useState(false);

  if (!draft || !draft.available) {
    return null;
  }

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(draft.prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section className="mt-6 rounded-[1.6rem] border border-dashed border-amber-300 bg-amber-50/60 p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-900">{draft.title}</p>
          <p className="mt-2 text-xs leading-6 text-slate-600">{draft.note}</p>
          <p className="mt-1 text-xs text-slate-500">
            생성 {formatDisplayValue(draft.generated_at, "created_at")}
          </p>
        </div>
        <button
          className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white"
          onClick={() => {
            void copyPrompt();
          }}
          type="button"
        >
          {copied ? "복사 완료" : "프롬프트 복사"}
        </button>
      </div>
      <textarea
        className="mt-4 min-h-72 w-full rounded-2xl border border-amber-200 bg-white px-4 py-3 text-sm leading-6 text-slate-800"
        readOnly
        value={draft.prompt}
      />
    </section>
  );
}

function AutoApplyButton({
  supported,
  label,
  disabled,
  onClick,
}: {
  supported: boolean;
  label: string | null;
  disabled: boolean;
  onClick: () => void;
}) {
  if (!supported) {
    return (
      <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-semibold text-slate-500">
        자동 적용 미지원
      </span>
    );
  }

  return (
    <button
      className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {label ?? "자동 적용"}
    </button>
  );
}

function BacklogCard({
  item,
  isPending,
  onAutoApply,
}: {
  item: BacklogItem;
  isPending: boolean;
  onAutoApply: (id: number) => void;
}) {
  return (
    <article className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h3 className="text-xl font-semibold text-ink">{item.title}</h3>
          <div className="mt-3 flex flex-wrap gap-2">
            <Badge>우선순위 {formatDisplayValue(item.priority, "priority")}</Badge>
            <Badge>영향도 {formatDisplayValue(item.impact, "impact")}</Badge>
            <Badge>작업량 {formatDisplayValue(item.effort, "effort")}</Badge>
            <Badge>상태 {formatDisplayValue(item.status, "status")}</Badge>
          </div>
        </div>
        <div className="flex flex-col items-start gap-3 lg:items-end">
          <div className="rounded-2xl bg-canvas px-4 py-3 text-sm text-slate-600">
            생성 {formatDisplayValue(item.created_at, "created_at")}
          </div>
          <AutoApplyButton
            supported={item.auto_apply_supported}
            label={item.auto_apply_label}
            disabled={isPending}
            onClick={() => onAutoApply(item.id)}
          />
        </div>
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-3">
        <div className="rounded-2xl bg-canvas p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">문제</p>
          <p className="mt-2 text-sm leading-7 text-slate-700">{item.problem}</p>
        </div>
        <div className="rounded-2xl bg-canvas p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">제안 내용</p>
          <p className="mt-2 text-sm leading-7 text-slate-700">{item.proposal}</p>
        </div>
        <div className="rounded-2xl bg-canvas p-4">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">근거</p>
          <p className="mt-2 text-sm leading-7 text-slate-700">{item.rationale}</p>
        </div>
      </div>

      <div className="mt-6 grid gap-5 xl:grid-cols-2">
        <section>
          <p className="mb-3 text-sm font-semibold text-slate-900">연결된 사용자 요청</p>
          <LinkedRequestList items={item.user_requests} />
        </section>
        <section>
          <p className="mb-3 text-sm font-semibold text-slate-900">적용 / 검증 내역</p>
          <AppliedRecordList items={item.applied_records} />
        </section>
      </div>

      <CodexPromptSection draft={item.codex_prompt_draft} />
    </article>
  );
}

function LooseList({
  title,
  description,
  requests,
  applied,
}: {
  title: string;
  description: string;
  requests?: UserRequest[];
  applied?: AppliedRecord[];
}) {
  const requestItems = requests ?? [];
  const appliedItems = applied ?? [];

  return (
    <section className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
      <SectionHeader title={title} description={description} count={requestItems.length + appliedItems.length} />
      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <div>
          <p className="mb-3 text-sm font-semibold text-slate-900">연결되지 않은 사용자 요청</p>
          <LinkedRequestList items={requestItems} />
        </div>
        <div>
          <p className="mb-3 text-sm font-semibold text-slate-900">연결되지 않은 적용 내역</p>
          <AppliedRecordList items={appliedItems} />
        </div>
      </div>
    </section>
  );
}

export function BacklogBoard({ initial }: { initial: BacklogBoardPayload }) {
  const [board, setBoard] = useState(initial);
  const [requestForm, setRequestForm] = useState({
    title: "",
    detail: "",
    status: "requested",
    linked_backlog_id: "",
  });
  const [appliedForm, setAppliedForm] = useState({
    title: "",
    summary: "",
    detail: "",
    related_backlog_id: "",
    source_type: "manual",
    files_changed: "",
    verification_summary: "",
  });
  const [message, setMessage] = useState("");
  const [isPending, startTransition] = useTransition();

  const refreshBoard = async () => {
    const response = await fetch(`${apiBaseUrl}/api/backlog`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("개선 백로그를 다시 불러오지 못했습니다.");
    }
    setBoard((await response.json()) as BacklogBoardPayload);
  };

  const submitUserRequest = () => {
    startTransition(() => {
      void fetch(`${apiBaseUrl}/api/backlog/requests`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: requestForm.title,
          detail: requestForm.detail,
          status: requestForm.status,
          linked_backlog_id: requestForm.linked_backlog_id ? Number(requestForm.linked_backlog_id) : null,
        }),
      })
        .then(async (response) => {
          if (!response.ok) {
            throw new Error((await response.text()) || "사용자 요청 등록에 실패했습니다.");
          }
          await refreshBoard();
          setRequestForm({ title: "", detail: "", status: "requested", linked_backlog_id: "" });
          setMessage("사용자 요청을 등록했습니다.");
        })
        .catch((error: unknown) => {
          setMessage(error instanceof Error ? error.message : "사용자 요청 등록에 실패했습니다.");
        });
    });
  };

  const submitAppliedRecord = () => {
    startTransition(() => {
      void fetch(`${apiBaseUrl}/api/backlog/applied`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: appliedForm.title,
          summary: appliedForm.summary,
          detail: appliedForm.detail,
          related_backlog_id: appliedForm.related_backlog_id ? Number(appliedForm.related_backlog_id) : null,
          source_type: appliedForm.source_type,
          files_changed: appliedForm.files_changed
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          verification_summary: appliedForm.verification_summary,
        }),
      })
        .then(async (response) => {
          if (!response.ok) {
            throw new Error((await response.text()) || "적용 내역 등록에 실패했습니다.");
          }
          await refreshBoard();
          setAppliedForm({
            title: "",
            summary: "",
            detail: "",
            related_backlog_id: "",
            source_type: "manual",
            files_changed: "",
            verification_summary: "",
          });
          setMessage("적용 내역을 등록했습니다.");
        })
        .catch((error: unknown) => {
          setMessage(error instanceof Error ? error.message : "적용 내역 등록에 실패했습니다.");
        });
    });
  };

  const runAutoApply = (backlogId: number) => {
    startTransition(() => {
      void fetch(`${apiBaseUrl}/api/backlog/${backlogId}/auto-apply`, { method: "POST" })
        .then(async (response) => {
          if (!response.ok) {
            throw new Error((await response.text()) || "자동 적용에 실패했습니다.");
          }
          const result = (await response.json()) as AutoApplyResult;
          await refreshBoard();
          setMessage(result.message);
        })
        .catch((error: unknown) => {
          setMessage(error instanceof Error ? error.message : "자동 적용에 실패했습니다.");
        });
    });
  };

  const runAutoApplyBatch = () => {
    startTransition(() => {
      void fetch(`${apiBaseUrl}/api/backlog/auto-apply-supported`, { method: "POST" })
        .then(async (response) => {
          if (!response.ok) {
            throw new Error((await response.text()) || "지원 backlog 자동 적용에 실패했습니다.");
          }
          const result = (await response.json()) as { items: AutoApplyResult[] };
          await refreshBoard();
          const appliedCount = result.items.filter((item) => item.auto_apply_supported).length;
          setMessage(`지원되는 backlog ${appliedCount}건에 자동 적용을 실행했습니다.`);
        })
        .catch((error: unknown) => {
          setMessage(error instanceof Error ? error.message : "지원 backlog 자동 적용에 실패했습니다.");
        });
    });
  };

  const backlogOptions = board.ai_backlog.map((item) => ({ id: item.id, title: item.title }));
  const supportedCount = board.ai_backlog.filter((item) => item.auto_apply_supported).length;

  return (
    <div className="space-y-6">
      <section className="grid gap-4 lg:grid-cols-4">
        <div className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">AI 제안</p>
          <p className="mt-3 text-3xl font-semibold text-ink">{board.ai_backlog.length}</p>
        </div>
        <div className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">사용자 요청</p>
          <p className="mt-3 text-3xl font-semibold text-ink">
            {board.ai_backlog.reduce((acc, item) => acc + item.user_requests.length, 0) + board.unlinked_user_requests.length}
          </p>
        </div>
        <div className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">적용 내역</p>
          <p className="mt-3 text-3xl font-semibold text-ink">
            {board.ai_backlog.reduce((acc, item) => acc + item.applied_records.length, 0) + board.unlinked_applied_records.length}
          </p>
        </div>
        <div className="rounded-[1.75rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">자동 적용 가능</p>
          <p className="mt-3 text-3xl font-semibold text-ink">{supportedCount}</p>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-2">
        <SignalPerformanceInsightsSection report={board.signal_performance_report} />
        <StructuredCompetitorSection digest={board.structured_competitor_notes} />
      </section>

      <section className="grid gap-5 xl:grid-cols-2">
        <div className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
          <SectionHeader
            title="사용자 요청 등록"
            description="직접 요청한 개선 사항을 AI 백로그와 연결해 기록합니다."
          />
          <div className="mt-5 space-y-4">
            <input
              className={inputClass}
              placeholder="요청 제목"
              value={requestForm.title}
              onChange={(event) => setRequestForm((current) => ({ ...current, title: event.target.value }))}
            />
            <textarea
              className={`${inputClass} min-h-32`}
              placeholder="요청 상세 내용"
              value={requestForm.detail}
              onChange={(event) => setRequestForm((current) => ({ ...current, detail: event.target.value }))}
            />
            <div className="grid gap-4 md:grid-cols-2">
              <select
                className={inputClass}
                value={requestForm.status}
                onChange={(event) => setRequestForm((current) => ({ ...current, status: event.target.value }))}
              >
                <option value="requested">requested</option>
                <option value="accepted">accepted</option>
                <option value="applied">applied</option>
                <option value="verified">verified</option>
              </select>
              <select
                className={inputClass}
                value={requestForm.linked_backlog_id}
                onChange={(event) =>
                  setRequestForm((current) => ({ ...current, linked_backlog_id: event.target.value }))
                }
              >
                <option value="">AI 백로그와 연결 안 함</option>
                {backlogOptions.map((item) => (
                  <option key={item.id} value={String(item.id)}>
                    #{item.id} {item.title}
                  </option>
                ))}
              </select>
            </div>
            <button
              className="rounded-full bg-amber-400 px-5 py-3 text-sm font-semibold text-slate-900 disabled:opacity-60"
              disabled={isPending || !requestForm.title.trim() || !requestForm.detail.trim()}
              onClick={submitUserRequest}
              type="button"
            >
              사용자 요청 저장
            </button>
          </div>
        </div>

        <div className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
          <SectionHeader
            title="적용 내역 등록"
            description="실제로 반영한 내용과 검증 결과를 백로그와 함께 남깁니다."
          />
          <div className="mt-5 space-y-4">
            <input
              className={inputClass}
              placeholder="적용 제목"
              value={appliedForm.title}
              onChange={(event) => setAppliedForm((current) => ({ ...current, title: event.target.value }))}
            />
            <input
              className={inputClass}
              placeholder="짧은 적용 요약"
              value={appliedForm.summary}
              onChange={(event) => setAppliedForm((current) => ({ ...current, summary: event.target.value }))}
            />
            <textarea
              className={`${inputClass} min-h-28`}
              placeholder="적용 상세 내용"
              value={appliedForm.detail}
              onChange={(event) => setAppliedForm((current) => ({ ...current, detail: event.target.value }))}
            />
            <textarea
              className={`${inputClass} min-h-24`}
              placeholder="검증 / 확인 내용"
              value={appliedForm.verification_summary}
              onChange={(event) =>
                setAppliedForm((current) => ({ ...current, verification_summary: event.target.value }))
              }
            />
            <div className="grid gap-4 md:grid-cols-3">
              <select
                className={inputClass}
                value={appliedForm.source_type}
                onChange={(event) => setAppliedForm((current) => ({ ...current, source_type: event.target.value }))}
              >
                <option value="manual">manual</option>
                <option value="user">user</option>
                <option value="ai">ai</option>
              </select>
              <select
                className={inputClass}
                value={appliedForm.related_backlog_id}
                onChange={(event) =>
                  setAppliedForm((current) => ({ ...current, related_backlog_id: event.target.value }))
                }
              >
                <option value="">AI 백로그와 연결 안 함</option>
                {backlogOptions.map((item) => (
                  <option key={item.id} value={String(item.id)}>
                    #{item.id} {item.title}
                  </option>
                ))}
              </select>
              <input
                className={inputClass}
                placeholder="변경 파일(쉼표 구분)"
                value={appliedForm.files_changed}
                onChange={(event) =>
                  setAppliedForm((current) => ({ ...current, files_changed: event.target.value }))
                }
              />
            </div>
            <button
              className="rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white disabled:opacity-60"
              disabled={
                isPending ||
                !appliedForm.title.trim() ||
                !appliedForm.summary.trim() ||
                !appliedForm.detail.trim() ||
                !appliedForm.verification_summary.trim()
              }
              onClick={submitAppliedRecord}
              type="button"
            >
              적용 내역 저장
            </button>
          </div>
        </div>
      </section>

      {message ? <p className="text-sm text-slate-600">{message}</p> : null}

      <section className="space-y-5">
        <SectionHeader
          title="AI 제안 백로그"
          description="문제, 제안, 근거와 함께 연결된 사용자 요청과 적용/검증 내역을 같은 화면에서 추적합니다."
          count={board.ai_backlog.length}
          actions={
            supportedCount > 0 ? (
              <button
                className="rounded-full bg-amber-400 px-4 py-2 text-sm font-semibold text-slate-900 disabled:opacity-60"
                disabled={isPending}
                onClick={runAutoApplyBatch}
                type="button"
              >
                지원되는 backlog 자동 적용
              </button>
            ) : null
          }
        />
        {board.ai_backlog.length === 0 ? (
          <EmptyState message="AI가 생성한 개선 백로그가 아직 없습니다." />
        ) : (
          <div className="space-y-5">
            {board.ai_backlog.map((item) => (
              <BacklogCard key={item.id} item={item} isPending={isPending} onAutoApply={runAutoApply} />
            ))}
          </div>
        )}
      </section>

      <LooseList
        title="연결 전 항목"
        description="아직 AI 백로그와 직접 연결하지 않은 사용자 요청과 적용 내역입니다."
        requests={board.unlinked_user_requests}
        applied={board.unlinked_applied_records}
      />
    </div>
  );
}
