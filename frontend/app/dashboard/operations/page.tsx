import { DashboardSectionTabs, type DashboardSectionTab } from "../../../components/dashboard-section-tabs";
import {
  DecisionView,
  RiskView,
  SchedulerView,
} from "../../../components/dashboard-views";
import { PageShell } from "../../../components/page-shell";
import type { OperatorDashboardPayload } from "../../../components/overview-dashboard";
import { fetchJson } from "../../../lib/api";
import { resolveSelectedSymbol } from "../../../lib/selected-symbol";

type SearchParams = Record<string, string | string[] | undefined>;
type Row = Record<string, unknown>;
type OperationsSection = "decisions" | "risk" | "scheduler";
type DecisionEntryFlowTab = "summary" | "plan" | "execution";

export const dynamic = "force-dynamic";

const sectionMeta: Record<OperationsSection, Omit<DashboardSectionTab, "href">> = {
  decisions: {
    value: "decisions",
    label: "AI 판단",
    description: "심볼별 현재 판단, AI 재검토 흐름, 최근 decision row를 한 화면에서 확인합니다.",
  },
  risk: {
    value: "risk",
    label: "운영 리스크",
    description: "최근 risk check, 진입 플랜, 차단 사유와 운영자 확인 항목을 확인합니다.",
  },
  scheduler: {
    value: "scheduler",
    label: "자동 실행",
    description: "스케줄러 상태, 다음 실행 예정, AI 호출 분류와 생략 사유를 확인합니다.",
  },
};

function queryValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

function resolveSection(value: string | string[] | undefined): OperationsSection {
  const normalized = queryValue(value);
  if (normalized === "risk" || normalized === "scheduler") {
    return normalized;
  }
  return "decisions";
}

function resolveDecisionEntryFlowTab(value: string | string[] | undefined): DecisionEntryFlowTab {
  const normalized = queryValue(value);
  if (normalized === "plan" || normalized === "execution") {
    return normalized;
  }
  return "summary";
}

function sectionHref(section: OperationsSection, searchParams: SearchParams) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(searchParams)) {
    if (key === "section" || value === undefined) {
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
  return `/dashboard/operations?${params.toString()}`;
}

function tabs(searchParams: SearchParams): DashboardSectionTab[] {
  return (Object.keys(sectionMeta) as OperationsSection[]).map((section) => ({
    ...sectionMeta[section],
    href: sectionHref(section, searchParams),
  }));
}

async function DecisionSection({ query }: { query: SearchParams }) {
  const [operator, decisionRows] = await Promise.all([
    fetchJson<OperatorDashboardPayload>("/api/dashboard/operator?view=decision"),
    fetchJson<Row[]>("/api/decisions?limit=12&compact=true"),
  ]);
  const selectedSymbol = resolveSelectedSymbol(
    queryValue(query.symbol),
    operator.control.tracked_symbols,
    operator.control.default_symbol,
    { mode: "single" },
  );

  return (
    <DecisionView
      operator={operator}
      decisionRows={decisionRows}
      selectedSymbol={selectedSymbol}
      entryFlowTab={resolveDecisionEntryFlowTab(query.flow)}
    />
  );
}

async function RiskSection() {
  const [operator, riskRows, alertRows] = await Promise.all([
    fetchJson<OperatorDashboardPayload>("/api/dashboard/operator?view=risk"),
    fetchJson<Row[]>("/api/risk/checks?limit=12&compact=true"),
    fetchJson<Row[]>("/api/alerts?limit=20"),
  ]);

  return <RiskView operator={operator} riskRows={riskRows} alertRows={alertRows} />;
}

async function SchedulerSection() {
  const [operator, schedulerRows] = await Promise.all([
    fetchJson<OperatorDashboardPayload>("/api/dashboard/operator?view=scheduler"),
    fetchJson<Row[]>("/api/scheduler?limit=20&compact=true"),
  ]);

  return <SchedulerView operator={operator} schedulerRows={schedulerRows} />;
}

export default async function OperationsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const query = await searchParams;
  const activeSection = resolveSection(query.section);

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="운영 판단"
        title="운영 판단 통합"
        description="AI 판단, 리스크 승인 흐름, 자동 실행 상태를 한 축에서 확인합니다. 거래 실행 내역과 시장 차트는 별도 화면으로 분리합니다."
        compact
      />
      <DashboardSectionTabs tabs={tabs(query)} active={activeSection} />

      {activeSection === "risk" ? <RiskSection /> : activeSection === "scheduler" ? <SchedulerSection /> : <DecisionSection query={query} />}
    </div>
  );
}
