"use client";

import dynamic from "next/dynamic";

import { GenericDashboardSections } from "./dashboard-views";
import type { DashboardViewModuleProps } from "./dashboard-view-types";

type DashboardViewLoaderProps = DashboardViewModuleProps & {
  slug: string;
};

const MarketView = dynamic(() => import("./views/market-view").then((module) => module.MarketView), {
  loading: () => <DashboardViewLoading />,
  ssr: false,
});
const DecisionDashboardView = dynamic(
  () => import("./views/decision-view").then((module) => module.DecisionDashboardView),
  { loading: () => <DashboardViewLoading />, ssr: false },
);
const SchedulerDashboardView = dynamic(
  () => import("./views/scheduler-view").then((module) => module.SchedulerDashboardView),
  { loading: () => <DashboardViewLoading />, ssr: false },
);
const RiskDashboardView = dynamic(() => import("./views/risk-view").then((module) => module.RiskDashboardView), {
  loading: () => <DashboardViewLoading />,
  ssr: false,
});
const SettingsDashboardView = dynamic(
  () => import("./views/settings-view").then((module) => module.SettingsDashboardView),
  { loading: () => <DashboardViewLoading />, ssr: false },
);
const AuditDashboardView = dynamic(() => import("./views/audit-view").then((module) => module.AuditDashboardView), {
  loading: () => <DashboardViewLoading />,
  ssr: false,
});
const OrdersDashboardView = dynamic(() => import("./views/orders-view").then((module) => module.OrdersDashboardView), {
  loading: () => <DashboardViewLoading />,
  ssr: false,
});
const PositionsDashboardView = dynamic(
  () => import("./views/positions-view").then((module) => module.PositionsDashboardView),
  { loading: () => <DashboardViewLoading />, ssr: false },
);
const AgentsDashboardView = dynamic(() => import("./views/agents-view").then((module) => module.AgentsDashboardView), {
  loading: () => <DashboardViewLoading />,
  ssr: false,
});

function DashboardViewLoading() {
  return (
    <section className="rounded-lg border border-slate-200 bg-white px-4 py-8 text-sm text-slate-500 shadow-sm">
      화면을 불러오는 중입니다.
    </section>
  );
}

export function DashboardViewLoader(props: DashboardViewLoaderProps) {
  switch (props.slug) {
    case "market":
      return <MarketView {...props} />;
    case "decisions":
      return <DecisionDashboardView {...props} />;
    case "scheduler":
      return <SchedulerDashboardView {...props} />;
    case "risk":
      return <RiskDashboardView {...props} />;
    case "settings":
      return <SettingsDashboardView {...props} />;
    case "audit":
      return <AuditDashboardView {...props} />;
    case "orders":
      return <OrdersDashboardView {...props} />;
    case "positions":
      return <PositionsDashboardView {...props} />;
    case "agents":
      return <AgentsDashboardView {...props} />;
    default:
      return <GenericDashboardSections sections={props.sections} />;
  }
}
