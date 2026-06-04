import Link from "next/link";

import { PageShell } from "./page-shell";
import { fetchJson } from "../lib/api";
import {
  type AnalyticsCostBreakdownBucket,
  type AnalyticsCostBreakdownResponse,
  type CostBreakdownSelection,
  buildCostBreakdownApiPath,
  buildCostBreakdownPageHref,
  costBreakdownBucketSlippageStatus,
  costBreakdownBucketStatus,
  costBreakdownQualityBadges,
  costBreakdownStatusTone,
  costBreakdownWarningMessages,
  costBreakdownWarningTone,
  costMetricDescription,
  costMetricLabel,
  formatCostBreakdownBps,
  formatCostBreakdownDateTime,
  formatCostBreakdownPercent,
  formatCostBreakdownUsdt,
  resolveCostBreakdownSelection,
  slippageDataQualityLabel,
  slippageDataQualityStatus,
  slippageDataQualityTone,
  slippageWeightingLabel,
  statusLabel,
} from "../lib/cost-breakdown";

type SearchParams = Record<string, string | string[] | undefined>;
type SearchParamsInput = Promise<SearchParams> | SearchParams;

function badgeClass(tone: "good" | "warn" | "danger" | "neutral") {
  return {
    good: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warn: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-rose-200 bg-rose-50 text-rose-800",
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
  }[tone];
}

function warningItemClass(tone: "good" | "warn" | "danger" | "neutral") {
  return {
    good: "border-emerald-200 bg-emerald-50 text-emerald-800",
    warn: "border-amber-200 bg-amber-50 text-amber-900",
    danger: "border-rose-200 bg-rose-50 text-rose-900",
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
  }[tone];
}

function dataQualityTileClass(tone: "good" | "warn" | "danger" | "neutral") {
  return {
    good: "border-emerald-200 bg-emerald-50 text-emerald-900",
    warn: "border-amber-200 bg-amber-50 text-amber-950",
    danger: "border-rose-200 bg-rose-50 text-rose-950",
    neutral: "border-slate-200 bg-slate-50 text-slate-900",
  }[tone];
}

function periodLabel(period: AnalyticsCostBreakdownResponse["period"]) {
  if (period === "today") {
    return "오늘";
  }
  if (period === "year") {
    return "연간";
  }
  return "월간";
}

function periodRangeLabel(payload: AnalyticsCostBreakdownResponse) {
  return `${formatCostBreakdownDateTime(payload.start_at, payload.timezone)} ~ ${formatCostBreakdownDateTime(
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
          <h2 className="mt-2 text-lg font-semibold text-slate-950">월간 / 연간 비용 분해</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            기간을 바꾸면 비용 분해 API를 다시 조회합니다. 이 화면은 읽기 전용이며 주문, 리스크, AI 판단 상태를
            변경하지 않습니다.
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
            오늘
          </Link>
        </div>
      </div>

      <form method="get" className="mt-5 grid gap-3 md:grid-cols-[160px_140px_160px_auto] md:items-end">
        <label className="grid gap-2 text-sm font-medium text-slate-700">
          조회 기간
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
  const slippageStatus = slippageDataQualityStatus(payload.data_quality);
  const slippageStatusDetail = slippageDataQualityLabel(payload.data_quality);
  const slippageComplete = slippageStatus === "COMPLETE";
  const slippageHint =
    slippageComplete ? "양수는 운영자에게 불리한 평균 체결입니다." : slippageStatusDetail;
  const adverseHint =
    slippageComplete ? "불리한 방향의 체결 차이만 누적한 평균입니다." : slippageStatusDetail;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">요약</p>
          <h2 className="mt-2 text-lg font-semibold text-slate-950">{periodLabel(payload.period)} 손익과 비용 분해</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            {periodRangeLabel(payload)} / {payload.timezone}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {!payload.data_quality.realized_pnl_confirmed ? (
            <span className={`w-fit rounded-md border px-3 py-2 text-sm font-semibold ${badgeClass("danger")}`}>
              실현 손익 미확정
            </span>
          ) : null}
          {!slippageComplete ? (
            <span
              className={`w-fit rounded-md border px-3 py-2 text-sm font-semibold ${badgeClass(
                slippageDataQualityTone(slippageStatus),
              )}`}
            >
              {slippageStatusDetail}
            </span>
          ) : null}
        </div>
      </div>

      <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          label={costMetricLabel("net_pnl_usdt")}
          value={formatCostBreakdownUsdt(summary.net_pnl_usdt)}
          hint={costMetricDescription("net_pnl_usdt")}
          muted={pnlMuted}
        />
        <SummaryCard
          label={costMetricLabel("gross_pnl_usdt")}
          value={formatCostBreakdownUsdt(summary.gross_pnl_usdt)}
          hint={costMetricDescription("gross_pnl_usdt")}
          muted={pnlMuted}
        />
        <SummaryCard
          label={costMetricLabel("fee_usdt")}
          value={formatCostBreakdownUsdt(summary.fee_usdt)}
          hint={`${costMetricLabel("fee_ratio_pct")} ${formatCostBreakdownPercent(summary.fee_ratio_pct)}`}
        />
        <SummaryCard
          label={costMetricLabel("funding_usdt")}
          value={formatCostBreakdownUsdt(summary.funding_usdt)}
          hint={costMetricDescription("funding_usdt")}
        />
        <SummaryCard
          label={costMetricLabel("total_cost_usdt")}
          value={formatCostBreakdownUsdt(summary.total_cost_usdt)}
          hint={`${costMetricLabel("total_cost_ratio_pct")} ${formatCostBreakdownPercent(summary.total_cost_ratio_pct)}`}
        />
        <SummaryCard
          label={costMetricLabel("signed_slippage_bps")}
          value={formatCostBreakdownBps(summary.signed_slippage_bps, slippageStatus)}
          hint={slippageHint}
        />
        <SummaryCard
          label={costMetricLabel("adverse_slippage_bps")}
          value={formatCostBreakdownBps(summary.adverse_slippage_bps, slippageStatus)}
          hint={adverseHint}
        />
      </div>
    </section>
  );
}

function WarningsPanel({ payload }: { payload: AnalyticsCostBreakdownResponse }) {
  const messages = costBreakdownWarningMessages(payload);
  const tone = costBreakdownWarningTone(payload);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">경고</p>
          <h2 className="mt-2 text-lg font-semibold text-slate-950">비용/동기화 경고</h2>
        </div>
        <span
          className={`w-fit rounded-md border px-3 py-2 text-sm font-semibold ${
            badgeClass(tone)
          }`}
        >
          {messages.length > 0 ? `${messages.length}건` : "경고 없음"}
        </span>
      </div>

      {messages.length > 0 ? (
        <ul className="mt-4 space-y-2 text-sm leading-6">
          {messages.map((message) => (
            <li key={message} className={`rounded-md border px-4 py-3 ${warningItemClass(tone)}`}>
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
  const executionTone = costBreakdownStatusTone(payload.data_quality.execution_sync_status);
  const fundingTone = costBreakdownStatusTone(payload.data_quality.funding_sync_status);
  const slippageTone = costBreakdownStatusTone(slippageDataQualityStatus(payload.data_quality));

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">데이터 품질</p>
      <h2 className="mt-2 text-lg font-semibold text-slate-950">비용 데이터 상태</h2>
      <div className="mt-4 flex flex-wrap gap-2">
        {badges.map((badge) => (
          <span key={badge.label} className={`rounded-md border px-3 py-2 text-sm font-semibold ${badgeClass(badge.tone)}`}>
            {badge.label}
          </span>
        ))}
      </div>
      <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
        <div className={`rounded-md border p-4 ${dataQualityTileClass(executionTone)}`}>
          <dt className="font-medium opacity-75">체결 동기화</dt>
          <dd className="mt-2 font-semibold">{statusLabel(payload.data_quality.execution_sync_status)}</dd>
        </div>
        <div className={`rounded-md border p-4 ${dataQualityTileClass(fundingTone)}`}>
          <dt className="font-medium opacity-75">펀딩비 동기화</dt>
          <dd className="mt-2 font-semibold">{statusLabel(payload.data_quality.funding_sync_status)}</dd>
        </div>
        <div className={`rounded-md border p-4 ${dataQualityTileClass(slippageTone)}`}>
          <dt className="font-medium opacity-75">슬리피지 데이터</dt>
          <dd className="mt-2 font-semibold">{slippageDataQualityLabel(payload.data_quality)}</dd>
        </div>
        <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
          <dt className="font-medium text-slate-500">슬리피지 가중 방식</dt>
          <dd className="mt-2 font-semibold text-slate-950">
            {slippageWeightingLabel(payload.data_quality.slippage_weighting)}
          </dd>
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
  const slippageStatus = costBreakdownBucketSlippageStatus(bucket, payload.data_quality);

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

function BucketCard({
  bucket,
  payload,
}: {
  bucket: AnalyticsCostBreakdownBucket;
  payload: AnalyticsCostBreakdownResponse;
}) {
  const slippageStatus = costBreakdownBucketSlippageStatus(bucket, payload.data_quality);

  return (
    <article className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-950">{bucket.label}</h3>
          <p className="mt-1 text-xs text-slate-500">{costBreakdownBucketStatus(bucket, payload.data_quality)}</p>
        </div>
        <span className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-600">
          {periodLabel(payload.period)}
        </span>
      </div>
      <dl className="mt-4 grid gap-3 text-sm">
        <div className="grid grid-cols-[1fr_auto] gap-3">
          <dt className="text-slate-500">순손익</dt>
          <dd className="font-semibold text-slate-950">{formatCostBreakdownUsdt(bucket.net_pnl_usdt)}</dd>
        </div>
        <div className="grid grid-cols-[1fr_auto] gap-3">
          <dt className="text-slate-500">총손익</dt>
          <dd className="font-semibold text-slate-950">{formatCostBreakdownUsdt(bucket.gross_pnl_usdt)}</dd>
        </div>
        <div className="grid grid-cols-[1fr_auto] gap-3">
          <dt className="text-slate-500">수수료 / 총 비용</dt>
          <dd className="font-semibold text-slate-950">
            {formatCostBreakdownUsdt(bucket.fee_usdt)} / {formatCostBreakdownUsdt(bucket.total_cost_usdt)}
          </dd>
        </div>
        <div className="grid grid-cols-[1fr_auto] gap-3">
          <dt className="text-slate-500">슬리피지</dt>
          <dd className="font-semibold text-slate-950">
            {formatCostBreakdownBps(bucket.signed_slippage_bps, slippageStatus)} /{" "}
            {formatCostBreakdownBps(bucket.adverse_slippage_bps, slippageStatus)}
          </dd>
        </div>
      </dl>
    </article>
  );
}

function BreakdownTable({ payload }: { payload: AnalyticsCostBreakdownResponse }) {
  const bucketDescription =
    payload.period === "year" ? "연간 조회는 월별 구간으로 비용을 보여줍니다." : "월간 조회는 일자별 구간으로 비용을 보여줍니다.";

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">상세</p>
          <h2 className="mt-2 text-lg font-semibold text-slate-950">기간별 상세</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">{bucketDescription}</p>
        </div>
        <span className="w-fit rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700">
          {payload.buckets.length}개 구간
        </span>
      </div>

      {payload.buckets.length > 0 ? (
        <>
        <div className="mt-5 grid gap-3 md:hidden">
          {payload.buckets.map((bucket) => (
            <BucketCard key={bucket.label} bucket={bucket} payload={payload} />
          ))}
        </div>
        <div className="mt-5 hidden overflow-x-auto rounded-md border border-slate-200 md:block">
          <table className="min-w-[1180px] text-left text-sm">
            <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">
              <tr>
                <th className="px-4 py-3">기간</th>
                <th className="px-4 py-3">{costMetricLabel("net_pnl_usdt")}</th>
                <th className="px-4 py-3">{costMetricLabel("gross_pnl_usdt")}</th>
                <th className="px-4 py-3">{costMetricLabel("fee_usdt")}</th>
                <th className="px-4 py-3">{costMetricLabel("fee_ratio_pct")}</th>
                <th className="px-4 py-3">{costMetricLabel("funding_usdt")}</th>
                <th className="px-4 py-3">{costMetricLabel("total_cost_usdt")}</th>
                <th className="px-4 py-3">{costMetricLabel("total_cost_ratio_pct")}</th>
                <th className="px-4 py-3">{costMetricLabel("signed_slippage_bps")}</th>
                <th className="px-4 py-3">{costMetricLabel("adverse_slippage_bps")}</th>
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
        </>
      ) : (
        <p className="mt-5 rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-sm text-slate-500">
          이 기간 응답에는 상세 구간 데이터가 없습니다. 상단 요약 기준으로만 확인하세요.
        </p>
      )}
    </section>
  );
}

export async function CostBreakdownDashboard({
  searchParams,
  backHref = "/",
  backLabel = "오늘 카드로 돌아가기",
}: {
  searchParams: SearchParamsInput;
  backHref?: string;
  backLabel?: string;
}) {
  const selection = resolveCostBreakdownSelection(await searchParams);
  const payload = await fetchJson<AnalyticsCostBreakdownResponse>(buildCostBreakdownApiPath(selection));

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="비용 분해"
        title="기간별 손익과 비용 분해"
        description="거래 전략 판단용 화면이 아니라 총손익이 수수료, 펀딩비, 불리한 체결에 얼마나 소모되는지 확인하는 읽기 전용 화면입니다."
        aside={
          <Link
            href={backHref}
            className="inline-flex min-h-10 items-center rounded-md border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            {backLabel}
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
