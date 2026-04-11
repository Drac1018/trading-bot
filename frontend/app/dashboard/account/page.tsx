import { DataTable } from "../../../components/data-table";
import { PageShell } from "../../../components/page-shell";
import { fetchJson } from "../../../lib/api";
import { formatDisplayValue } from "../../../lib/ui-copy";

type AccountPayload = {
  summary: Record<string, unknown>;
  assets: Record<string, unknown>[];
  positions: Record<string, unknown>[];
  open_orders: Record<string, unknown>[];
};

function getSummaryValue(summary: Record<string, unknown>, key: string) {
  return formatDisplayValue(summary[key], key);
}

export default async function BinanceAccountPage() {
  const payload = await fetchJson<AccountPayload>("/api/binance/account");
  const summary = payload.summary;

  const cards = [
    { label: "연결 상태", value: getSummaryValue(summary, "connected") },
    { label: "거래 가능", value: getSummaryValue(summary, "can_trade") },
    { label: "사용 가능 잔고", value: getSummaryValue(summary, "available_balance") },
    { label: "총 지갑 잔고", value: getSummaryValue(summary, "total_wallet_balance") },
    { label: "총 미실현 손익", value: getSummaryValue(summary, "total_unrealized_profit") },
    { label: "오픈 포지션", value: getSummaryValue(summary, "open_positions") },
    { label: "미체결 주문", value: getSummaryValue(summary, "open_orders") },
    { label: "조회 시각", value: getSummaryValue(summary, "exchange_update_time") }
  ];

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="Binance Account"
        title="연동된 바이낸스 계정"
        description="저장된 API 키로 Futures 계정 요약, 자산 잔고, 포지션, 미체결 주문을 직접 조회합니다."
      />

      <section className="rounded-[2rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {cards.map((card) => (
            <div key={card.label} className="rounded-[1.6rem] bg-canvas p-5">
              <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">
                {card.label}
              </p>
              <p className="mt-3 break-words text-xl font-semibold text-ink sm:text-2xl">{card.value}</p>
            </div>
          ))}
        </div>
        <div className="mt-5 rounded-[1.6rem] bg-ink p-5 text-canvas">
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-canvas/70">안내</p>
          <p className="mt-2 text-sm leading-7 sm:text-base">
            {typeof summary.message === "string"
              ? summary.message
              : "설정 페이지에서 Binance API Key와 Secret을 저장하면 실제 계정 정보를 확인할 수 있습니다."}
          </p>
        </div>
      </section>

      <DataTable
        title="계정 요약"
        description="현재 연결된 바이낸스 계정의 핵심 상태입니다."
        rows={[summary]}
      />
      <DataTable
        title="보유 자산"
        description="잔고가 있는 자산만 표시합니다."
        rows={payload.assets}
      />
      <div className="grid gap-6 xl:grid-cols-2">
        <DataTable
          title="포지션"
          description="현재 Binance Futures에 열려 있는 포지션입니다."
          rows={payload.positions}
        />
        <DataTable
          title="미체결 주문"
          description="거래소에 남아 있는 일반 주문과 보호 주문입니다."
          rows={payload.open_orders}
        />
      </div>
    </div>
  );
}
