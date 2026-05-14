import { BinanceAccountPanel } from "../../../components/binance-account-panel";
import { DashboardSectionTabs, type DashboardSectionTab } from "../../../components/dashboard-section-tabs";
import { OrdersView, PositionsView } from "../../../components/dashboard-views";
import { PageShell } from "../../../components/page-shell";
import { fetchJson } from "../../../lib/api";
import { ordersDataEndpoints, resolveOrdersQuery } from "../../../lib/orders-query";

type SearchParams = Record<string, string | string[] | undefined>;
type Row = Record<string, unknown>;
type TradingSection = "account" | "positions" | "orders";

export const dynamic = "force-dynamic";

const sectionMeta: Record<TradingSection, Omit<DashboardSectionTab, "href">> = {
  account: {
    value: "account",
    label: "계좌 / 잔고",
    description: "Binance 계정 캐시와 로컬 동기화 기준 자산 상태를 확인합니다.",
  },
  positions: {
    value: "positions",
    label: "포지션",
    description: "열린 포지션, 손절/익절 가격, 보호 주문 상태를 확인합니다.",
  },
  orders: {
    value: "orders",
    label: "주문 / 체결",
    description: "주문과 execution row를 position_id 기준으로 묶어 확인합니다.",
  },
};

function queryValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

function resolveSection(value: string | string[] | undefined): TradingSection {
  const normalized = queryValue(value);
  if (normalized === "positions" || normalized === "orders") {
    return normalized;
  }
  return "account";
}

function sectionHref(section: TradingSection, searchParams: SearchParams) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(searchParams)) {
    if (key === "section" || key === "tab" || value === undefined) {
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        params.append(key, item);
      }
      continue;
    }
    params.set(key, value);
  }
  params.set("section", section);
  return `/dashboard/trading?${params.toString()}`;
}

function tabs(searchParams: SearchParams): DashboardSectionTab[] {
  return (Object.keys(sectionMeta) as TradingSection[]).map((section) => ({
    ...sectionMeta[section],
    href: sectionHref(section, searchParams),
  }));
}

async function PositionsSection() {
  const positionRows = await fetchJson<Row[]>("/api/positions?limit=20");
  return <PositionsView positionRows={positionRows} />;
}

async function OrdersSection({ query }: { query: SearchParams }) {
  const ordersQuery = resolveOrdersQuery(query);
  const [ordersEndpoint, executionsEndpoint] = ordersDataEndpoints(query);
  const [orderRows, executionRows] = await Promise.all([
    fetchJson<Row[]>(ordersEndpoint),
    fetchJson<Row[]>(executionsEndpoint),
  ]);

  return (
    <OrdersView
      orderRows={orderRows}
      executionRows={executionRows}
      activeTab={ordersQuery.tab}
      selectedSymbol={ordersQuery.symbol}
      selectedPositionId={ordersQuery.positionId}
    />
  );
}

export default async function TradingPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const query = await searchParams;
  const activeSection = resolveSection(query.section);

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="거래 상태"
        title="거래 상태 통합"
        description="계좌, 포지션, 주문/체결을 거래 상태 축으로 묶어 확인합니다. 이 화면 진입만으로 계좌 새로고침 POST나 설정 저장은 실행하지 않습니다."
        compact
      />
      <DashboardSectionTabs tabs={tabs(query)} active={activeSection} />

      {activeSection === "positions" ? (
        <PositionsSection />
      ) : activeSection === "orders" ? (
        <OrdersSection query={query} />
      ) : (
        <BinanceAccountPanel />
      )}
    </div>
  );
}
