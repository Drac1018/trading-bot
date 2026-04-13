import { DataTable } from "../../../components/data-table";
import { PageShell } from "../../../components/page-shell";
import { fetchJson } from "../../../lib/api";
import { formatDisplayValue } from "../../../lib/ui-copy";

type AccountSummary = {
  connected: boolean;
  message: string;
  testnet_enabled: boolean;
  futures_enabled: boolean;
  can_trade: boolean;
  exchange_can_trade?: boolean;
  app_live_execution_ready?: boolean;
  app_trading_paused?: boolean;
  app_operating_state?: string;
  latest_blocked_reasons?: string[];
  available_balance: number;
  total_wallet_balance: number;
  total_unrealized_profit: number;
  open_positions: number;
  open_orders: number;
  exchange_update_time: string | null;
};

type AccountPayload = {
  summary: AccountSummary;
  assets: Record<string, unknown>[];
  positions: Record<string, unknown>[];
  open_orders: Record<string, unknown>[];
};

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-[1.6rem] bg-canvas p-5">
      <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">{label}</p>
      <p className="mt-3 break-words text-xl font-semibold text-ink sm:text-2xl">{value}</p>
      {hint ? <p className="mt-2 text-sm leading-6 text-slate-600">{hint}</p> : null}
    </div>
  );
}

function StatusBadge({
  tone,
  label,
}: {
  tone: "neutral" | "good" | "warn" | "danger";
  label: string;
}) {
  const className = {
    neutral: "border border-slate-200 bg-slate-50 text-slate-700",
    good: "border border-emerald-200 bg-emerald-50 text-emerald-700",
    warn: "border border-amber-200 bg-amber-50 text-amber-800",
    danger: "border border-rose-200 bg-rose-50 text-rose-800",
  }[tone];
  return <span className={`rounded-full px-4 py-2 text-sm font-semibold ${className}`}>{label}</span>;
}

function formatReasonList(values: string[] | undefined): string {
  if (!values || values.length === 0) {
    return "없음";
  }
  return values.map((item) => formatDisplayValue(item)).join(" / ");
}

export default async function BinanceAccountPage() {
  const payload = await fetchJson<AccountPayload>("/api/binance/account");
  const summary = payload.summary;
  const exchangeCanTrade = summary.exchange_can_trade ?? summary.can_trade;
  const appLiveReady = summary.app_live_execution_ready ?? false;
  const appTradingPaused = summary.app_trading_paused ?? false;
  const appOperatingState = summary.app_operating_state ?? "TRADABLE";
  const latestBlockedReasons = summary.latest_blocked_reasons ?? [];

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="Binance Account"
        title="연동된 Binance 계정"
        description="거래소 원본 권한과 앱 내부 실주문 준비 상태를 분리해서 확인합니다."
      />

      <section className="rounded-[2rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <div className="flex flex-wrap gap-2">
          <StatusBadge tone={summary.connected ? "good" : "warn"} label={summary.connected ? "거래소 연결됨" : "거래소 연결 안 됨"} />
          <StatusBadge tone={exchangeCanTrade ? "good" : "warn"} label={`거래소 주문 권한 ${exchangeCanTrade ? "허용" : "제한"}`} />
          <StatusBadge tone={appTradingPaused ? "danger" : appLiveReady ? "good" : "warn"} label={`앱 실주문 준비 ${appLiveReady ? "완료" : "미완료"}`} />
          <StatusBadge tone={appOperatingState === "TRADABLE" ? "good" : appOperatingState === "PAUSED" ? "danger" : "warn"} label={`운영 상태 ${formatDisplayValue(appOperatingState, "operating_state")}`} />
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            label="거래소 주문 권한"
            value={formatDisplayValue(exchangeCanTrade, "exchange_can_trade")}
            hint="Binance 원본 canTrade 값입니다. 앱 내부 실주문 가능 여부와는 다릅니다."
          />
          <MetricCard
            label="앱 실주문 준비"
            value={formatDisplayValue(appLiveReady, "app_live_execution_ready")}
            hint="live_execution_ready 기준입니다. 키, 승인창, 환경 게이트를 함께 반영합니다."
          />
          <MetricCard
            label="앱 거래 중지"
            value={formatDisplayValue(appTradingPaused, "app_trading_paused")}
            hint="켜져 있으면 신규 진입은 차단됩니다. 보호/축소/비상청산은 별도 규칙을 따릅니다."
          />
          <MetricCard
            label="현재 차단 사유"
            value={formatReasonList(latestBlockedReasons)}
            hint="최신 deterministic risk 차단 코드입니다."
          />
          <MetricCard label="사용 가능 잔고" value={formatDisplayValue(summary.available_balance, "available_balance")} />
          <MetricCard label="총 지갑 잔고" value={formatDisplayValue(summary.total_wallet_balance, "total_wallet_balance")} />
          <MetricCard label="미실현 손익" value={formatDisplayValue(summary.total_unrealized_profit, "total_unrealized_profit")} />
          <MetricCard label="거래소 갱신 시각" value={formatDisplayValue(summary.exchange_update_time, "exchange_update_time")} />
        </div>

        <div className="mt-5 grid gap-4 xl:grid-cols-2">
          <div className="rounded-[1.6rem] bg-ink p-5 text-canvas">
            <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-canvas/70">설명</p>
            <p className="mt-2 text-sm leading-7 sm:text-base">
              거래소 주문 권한은 Binance 계정이 주문을 받을 수 있는지만 보여줍니다. 실제 앱 실주문 여부는
              `live_execution_ready`, `trading_paused`, `operating_state`, 최신 차단 사유를 함께 봐야 합니다.
            </p>
          </div>
          <div className="rounded-[1.6rem] border border-amber-200 bg-amber-50 p-5 text-slate-900">
            <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-slate-500">현재 판독</p>
            <p className="mt-2 text-sm leading-7">
              {summary.message}
            </p>
            <p className="mt-3 text-sm leading-7">
              앱 실주문 준비: {formatDisplayValue(appLiveReady, "app_live_execution_ready")} / 운영 상태:{" "}
              {formatDisplayValue(appOperatingState, "operating_state")} / 차단 사유: {formatReasonList(latestBlockedReasons)}
            </p>
          </div>
        </div>
      </section>

      <DataTable
        title="계정 요약"
        description="거래소 원본 상태와 앱 내부 상태를 함께 담은 계정 응답입니다."
        rows={[summary as unknown as Record<string, unknown>]}
      />
      <DataTable
        title="보유 자산"
        description="잔고가 있는 자산만 표시합니다."
        rows={payload.assets}
      />
      <div className="grid gap-6 xl:grid-cols-2">
        <DataTable
          title="포지션"
          description="현재 Binance Futures에 열린 실포지션입니다."
          rows={payload.positions}
        />
        <DataTable
          title="미체결 주문"
          description="일반 주문과 보호 주문을 함께 표시합니다."
          rows={payload.open_orders}
        />
      </div>
    </div>
  );
}
