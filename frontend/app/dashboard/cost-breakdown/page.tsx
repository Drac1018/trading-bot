import Link from "next/link";

import { PageShell } from "../../../components/page-shell";
import {
  type AnalyticsCostBreakdownBucket,
  type AnalyticsCostBreakdownResponse,
  type CostBreakdownSelection,
  buildCostBreakdownApiPath,
  buildCostBreakdownPageHref,
  costBreakdownBucketStatus,
  costBreakdownQualityBadges,
  costBreakdownWarningMessages,
  formatCostBreakdownBps,
  formatCostBreakdownDateTime,
  formatCostBreakdownPercent,
  formatCostBreakdownUsdt,
  resolveCostBreakdownSelection,
  statusLabel,
} from "../../../lib/cost-breakdown";
import { fetchJson } from "../../../lib/api";

type SearchParams = Record<string, string | string[] | undefined>;

function badgeClass(tone: "good" | "warn" | "danger" | "neutral") {
  return {
    good: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warn: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-rose-200 bg-rose-50 text-rose-800",
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
  }[tone];
}

function periodLabel(period: string) {
  if (period === "today") {
    return "오늘";
  }
  if (period === "year") {
    return "연간";
  }
  return "월간";
}

function periodRangeLabel(payload: AnalyticsCostBreakdownResponse) {
  return `${formatCostBreakdownDateTime(payload.start_at, payload.timezone)} - ${formatCostBreakdownDateTime(
    payload.end_at,
    payload.timezone,
  )}`;
}

function SummaryCard({
  label,
  value,
  hint,
  muted,
}: {
  label: string;
  value: string;
  hint: string;
  muted?: boolean;
}) {
  return (
    <div className={`rounded-md border p-4 ${muted ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-white"}`}>
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p className={`mt-2 text-xl leading-7 ${muted ? "font-medium text-slate-700" : "font-semibold text-slate-950"}`}>
        {value}
      </p>
      <p className="mt-2 text-xs leading-5 text-slate-500">{hint}</p>
    </div>
  );
}

function PeriodControls({ selection }: { selection: CostBreakdownSelection }) {
  const monthHref = buildCostBreakdownPageHref({ ...selection, period: "month" });
  const yearHref = buildCostBreakdownPageHref({ ...selection, period: "year" });
  const todayHref = buildCostBreakdownPageHref({ ...selection, period: "today" });

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">조회 기간</p>
          <h2 className="mt-2 text-lg font-semibold text-slate-950">월간 / 연간 비용 진단</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            기간 변경은 백엔드 분석 API를 다시 조회합니다. 이 화면은 읽기 전용이며 주문, 리스크, AI 판단 상태를 변경하지 않습니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href={monthHref}
            className={`inline-flex min-h-10 items-center rounded-md border px-4 text-sm font-semibold ${
              selection.period === "month" ? "border-blue-600 bg-blue-600 text-white" : "border-slate-200 bg-white text-slate-700"
            }`}
          >
            월간
          </Link>
          <Link
            href={yearHref}
            className={`inline-flex min-h-10 items-center rounded-md border px-4 text-sm font-semibold ${
              selection.period === "year" ? "border-blue-600 bg-blue-600 text-white" : "border-slate-200 bg-white text-slate-700"
            }`}
          >
            연간
          </Link>
          <Link
            href={todayHref}
            className="inline-flex min-h-10 items-center rounded-md border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700"
          >
            오늘 preset
          </Link>
        </div>
      </div>

      <form method="get" className="mt-5 grid gap-3 md:grid-cols-[160px_140px_160px_auto] md:items-end">
        <label className="grid gap-2 text-sm font-medium text-slate-700">
          기간
          <select
            name="period"
            defaultValue={selection.period}
            className="h-11 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-950"
          >
            <option value="month">월간</option>
            <option value="year">연간</option>
            <option value="today">오늘</option>
          </select>
        </label>
        <label className="grid gap-2 text-sm font-medium text-slate-700">
          연도
          <input
            name="year"
            type="number"
            min="2020"
            max="2100"
            defaultValue={selection.year}
            className="h-11 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-950"
          />
        </label>
        <label className="grid gap-2 text-sm font-medium text-slate-700">
          월
          <select
            name="month"
            defaultValue={selection.month}
            className="h-11 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-950"
          >
            {Array.from({ length: 12 }, (_, index) => index + 1).map((month) => (
              <option key={month} value={month}>
                {month}월
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="inline-flex h-11 items-center justify-center rounded-md border border-slate-950 bg-slate-950 px-5 text-sm font-semibold text-white transition hover:bg-slate-800"
        >
          조회
        </button>
      </form>
      <p className="mt-3 text-xs leading-5 text-slate-500">월 선택값은 월간 조회에서만 적용됩니다.</p>
    </section>
  );
}

function SummaryGrid({ payload }: { payload: AnalyticsCostBreakdownResponse }) {
  const summary = payload.summary;
  const pnlMuted = !payload.data_quality.realized_pnl_confirmed;
  const slippageStatus = payload.data_quality.slippage_data_status;
  const slippageHint = slippageStatus === "COMPLETE" ? "양수는 불리한 평균 체결" : "데이터 부족으로 확정 불가";
  const adverseHint = slippageStatus === "COMPLETE" ? "불리한 방향만 누적한 평균" : "데이터 부족으로 확정 불가";

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Summary</p>
          <h2 className="mt-2 text-lg font-semibold text-slate-950">{periodLabel(payload.period)} 수익성 비용 분해</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            {periodRangeLabel(payload)} / {payload.timezone}
          </p>
        </div>
        {!payload.data_quality.realized_pnl_confirmed ? (
          <span className={`w-fit rounded-md border px-3 py-2 text-sm font-semibold ${badgeClass("danger")}`}>
            확정 손익 아님
          </span>
        ) : null}
      </div>

      <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard label="Net PnL" value={formatCostBreakdownUsdt(summary.net_pnl_usdt)} hint="funding 포함" muted={pnlMuted} />
        <SummaryCard label="Gross PnL" value={formatCostBreakdownUsdt(summary.gross_pnl_usdt)} hint="실현 손익" muted={pnlMuted} />
        <SummaryCard
          label="Fee"
          value={formatCostBreakdownUsdt(summary.fee_usdt)}
          hint={`gross 대비 ${formatCostBreakdownPercent(summary.fee_ratio_pct)}`}
        />
        <SummaryCard label="Funding" value={formatCostBreakdownUsdt(summary.funding_usdt)} hint="양수는 수취, 음수는 비용" />
        <SummaryCard
          label="총 비용"
          value={formatCostBreakdownUsdt(summary.total_cost_usdt)}
          hint={`gross 대비 ${formatCostBreakdownPercent(summary.total_cost_ratio_pct)}`}
        />
        <SummaryCard
          label="Signed slippage"
          value={formatCostBreakdownBps(summary.signed_slippage_bps, slippageStatus)}
          hint={slippageHint}
        />
        <SummaryCard
          label="Adverse slippage"
          value={formatCostBreakdownBps(summary.adverse_slippage_bps, slippageStatus)}
          hint={adverseHint}
        />
      </div>
    </section>
  );
}

function WarningsPanel({ payload }: { payload: AnalyticsCostBreakdownResponse }) {
  const messages = costBreakdownWarningMessages(payload);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Warnings</p>
          <h2 className="mt-2 text-lg font-semibold text-slate-950">비용 경고</h2>
        </div>
        <span
          className={`w-fit rounded-md border px-3 py-2 text-sm font-semibold ${
            messages.length > 0 ? badgeClass("warn") : badgeClass("good")
          }`}
        >
          {messages.length > 0 ? `${messages.length}건` : "API 경고 없음"}
        </span>
      </div>

      {messages.length > 0 ? (
        <ul className="mt-4 space-y-2 text-sm leading-6 text-amber-900">
          {messages.map((message) => (
            <li key={message} className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3">
              {message}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-600">
          현재 응답에는 별도 비용/동기화 경고가 없습니다.
        </p>
      )}
    </section>
  );
}

function DataQualityPanel({ payload }: { payload: AnalyticsCostBreakdownResponse }) {
  const badges = costBreakdownQualityBadges(payload.data_quality);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Data Quality</p>
      <h2 className="mt-2 text-lg font-semibold text-slate-950">데이터 품질</h2>
      <div className="mt-4 flex flex-wrap gap-2">
        {badges.map((badge) => (
          <span key={badge.label} className={`rounded-md border px-3 py-2 text-sm font-semibold ${badgeClass(badge.tone)}`}>
            {badge.label}
          </span>
        ))}
      </div>
      <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
          <dt className="font-medium text-slate-500">Execution sync</dt>
          <dd className="mt-2 font-semibold text-slate-950">{statusLabel(payload.data_quality.execution_sync_status)}</dd>
        </div>
        <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
          <dt className="font-medium text-slate-500">Funding sync</dt>
          <dd className="mt-2 font-semibold text-slate-950">{statusLabel(payload.data_quality.funding_sync_status)}</dd>
        </div>
        <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
          <dt className="font-medium text-slate-500">Slippage data</dt>
          <dd className="mt-2 font-semibold text-slate-950">{statusLabel(payload.data_quality.slippage_data_status)}</dd>
        </div>
        <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
          <dt className="font-medium text-slate-500">Slippage weighting</dt>
          <dd className="mt-2 font-semibold text-slate-950">{payload.data_quality.slippage_weighting ?? "N/A"}</dd>
        </div>
      </dl>
    </section>
  );
}

function BucketRow({
  bucket,
  payload,
}: {
  bucket: AnalyticsCostBreakdownBucket;
  payload: AnalyticsCostBreakdownResponse;
}) {
  const slippageStatus = payload.data_quality.slippage_data_status;

  return (
    <tr className="border-b border-slate-100 last:border-b-0">
      <td className="whitespace-nowrap px-4 py-3 font-semibold text-slate-950">{bucket.label}</td>
      <td className="whitespace-nowrap px-4 py-3">{formatCostBreakdownUsdt(bucket.net_pnl_usdt)}</td>
      <td className="whitespace-nowrap px-4 py-3">{formatCostBreakdownUsdt(bucket.gross_pnl_usdt)}</td>
      <td className="whitespace-nowrap px-4 py-3">{formatCostBreakdownUsdt(bucket.fee_usdt)}</td>
      <td className="whitespace-nowrap px-4 py-3">{formatCostBreakdownPercent(bucket.fee_ratio_pct)}</td>
      <td className="whitespace-nowrap px-4 py-3">{formatCostBreakdownUsdt(bucket.funding_usdt)}</td>
      <td className="whitespace-nowrap px-4 py-3">{formatCostBreakdownUsdt(bucket.total_cost_usdt)}</td>
      <td className="whitespace-nowrap px-4 py-3">{formatCostBreakdownPercent(bucket.total_cost_ratio_pct)}</td>
      <td className="whitespace-nowrap px-4 py-3">{formatCostBreakdownBps(bucket.signed_slippage_bps, slippageStatus)}</td>
      <td className="whitespace-nowrap px-4 py-3">{formatCostBreakdownBps(bucket.adverse_slippage_bps, slippageStatus)}</td>
      <td className="min-w-48 px-4 py-3 text-slate-600">{costBreakdownBucketStatus(bucket, payload.data_quality)}</td>
    </tr>
  );
}

function BreakdownTable({ payload }: { payload: AnalyticsCostBreakdownResponse }) {
  const bucketDescription =
    payload.period === "year" ? "연간 조회는 월별 bucket을 표시합니다." : "월간 조회는 일자별 bucket을 표시합니다.";

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Breakdown</p>
          <h2 className="mt-2 text-lg font-semibold text-slate-950">기간별 상세</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">{bucketDescription}</p>
        </div>
        <span className="w-fit rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700">
          {payload.buckets.length} rows
        </span>
      </div>

      {payload.buckets.length > 0 ? (
        <div className="mt-5 overflow-x-auto rounded-md border border-slate-200">
          <table className="min-w-[1180px] text-left text-sm">
            <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">
              <tr>
                <th className="px-4 py-3">기간</th>
                <th className="px-4 py-3">Net PnL</th>
                <th className="px-4 py-3">Gross PnL</th>
                <th className="px-4 py-3">Fee</th>
                <th className="px-4 py-3">Fee / Gross</th>
                <th className="px-4 py-3">Funding</th>
                <th className="px-4 py-3">총 비용</th>
                <th className="px-4 py-3">총 비용 / Gross</th>
                <th className="px-4 py-3">Signed slippage bps</th>
                <th className="px-4 py-3">Adverse slippage bps</th>
                <th className="px-4 py-3">데이터 상태</th>
              </tr>
            </thead>
            <tbody className="text-slate-700">
              {payload.buckets.map((bucket) => (
                <BucketRow key={bucket.label} bucket={bucket} payload={payload} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-5 rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-sm text-slate-500">
          이 기간 응답에는 bucket이 없습니다. 상단 summary 기준으로만 확인하세요.
        </p>
      )}
    </section>
  );
}

export default async function CostBreakdownPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const selection = resolveCostBreakdownSelection(await searchParams);
  const payload = await fetchJson<AnalyticsCostBreakdownResponse>(buildCostBreakdownApiPath(selection));

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="비용 진단"
        title="기간별 수익성 비용 분해"
        description="거래 확대 판단용 화면이 아니라 gross PnL이 수수료, funding, 불리한 체결에 먹히는지 확인하는 화면입니다."
        aside={
          <Link
            href="/"
            className="inline-flex min-h-10 items-center rounded-md border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            오늘 카드로 돌아가기
          </Link>
        }
        compact
      />
      <PeriodControls selection={selection} />
      <SummaryGrid payload={payload} />
      <WarningsPanel payload={payload} />
      <DataQualityPanel payload={payload} />
      <BreakdownTable payload={payload} />
    </div>
  );
}
