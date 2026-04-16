import Link from "next/link";

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

export default async function BinanceAccountPage() {
  const payload = await fetchJson<AccountPayload>("/api/binance/account");
  const summary = payload.summary;
  const exchangeCanTrade = summary.exchange_can_trade ?? summary.can_trade;

  const exchangeSummaryRow: Record<string, unknown> = {
    connected: summary.connected,
    exchange_can_trade: exchangeCanTrade,
    testnet_enabled: summary.testnet_enabled,
    futures_enabled: summary.futures_enabled,
    available_balance: summary.available_balance,
    total_wallet_balance: summary.total_wallet_balance,
    total_unrealized_profit: summary.total_unrealized_profit,
    open_positions: summary.open_positions,
    open_orders: summary.open_orders,
    exchange_update_time: summary.exchange_update_time,
    message: summary.message,
  };

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="Exchange Account"
        title="거래소 계정 / 자산 현황"
        description="이 페이지는 Binance 원본 계정, 자산, 포지션, 주문 정보만 보여줍니다. 실거래 준비 상태, 운영 중지, 가드 모드, 차단 사유는 개요 화면에서 확인합니다."
      />

      <section className="rounded-[2rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <div className="flex flex-wrap gap-2">
          <StatusBadge tone={summary.connected ? "good" : "warn"} label={summary.connected ? "거래소 연결됨" : "거래소 연결 안 됨"} />
          <StatusBadge tone={exchangeCanTrade ? "good" : "warn"} label={`거래소 주문 권한 ${exchangeCanTrade ? "허용" : "제한"}`} />
          <StatusBadge tone={summary.futures_enabled ? "good" : "warn"} label={`Futures ${summary.futures_enabled ? "사용" : "꺼짐"}`} />
          <StatusBadge tone={summary.testnet_enabled ? "neutral" : "good"} label={summary.testnet_enabled ? "Testnet" : "Live Exchange"} />
        </div>

        <div className="mt-5 rounded-[1.6rem] border border-slate-200 bg-slate-50 p-5 text-sm leading-7 text-slate-700">
          운영 상태 요약은 <Link className="font-semibold text-slate-950 underline decoration-amber-300 underline-offset-4" href="/">개요 화면</Link>에서 단일 기준으로 확인합니다.
          계정 페이지는 거래소 원본 데이터와 내부 상태를 혼합하지 않습니다.
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="사용 가능 잔고" value={formatDisplayValue(summary.available_balance, "available_balance")} />
          <MetricCard label="총 지갑 잔고" value={formatDisplayValue(summary.total_wallet_balance, "total_wallet_balance")} />
          <MetricCard label="미실현 손익" value={formatDisplayValue(summary.total_unrealized_profit, "total_unrealized_profit")} />
          <MetricCard label="거래소 갱신 시각" value={formatDisplayValue(summary.exchange_update_time, "exchange_update_time")} />
          <MetricCard label="열린 포지션 수" value={formatDisplayValue(summary.open_positions, "open_positions")} />
          <MetricCard label="미체결 주문 수" value={formatDisplayValue(summary.open_orders, "open_orders")} />
          <MetricCard label="거래소 권한" value={formatDisplayValue(exchangeCanTrade, "exchange_can_trade")} hint="Binance 원본 canTrade 값입니다." />
          <MetricCard label="현재 안내" value={summary.connected ? "응답 정상" : "연결 확인 필요"} hint={summary.message} />
        </div>
      </section>

      <DataTable
        title="거래소 계정 요약"
        description="운영 상태 해석용 앱 필드는 제외하고, 거래소 원본 계정 응답 중심으로 정리한 요약입니다."
        rows={[exchangeSummaryRow]}
      />
      <DataTable
        title="보유 자산"
        description="잔고가 있는 자산만 표시합니다."
        rows={payload.assets}
      />
      <div className="grid gap-6 xl:grid-cols-2">
        <DataTable
          title="포지션"
          description="현재 Binance Futures에 열린 포지션입니다."
          rows={payload.positions}
        />
        <DataTable
          title="미체결 주문"
          description="일반 주문과 보호 주문을 포함한 거래소 원본 미체결 주문 목록입니다."
          rows={payload.open_orders}
        />
      </div>
    </div>
  );
}
