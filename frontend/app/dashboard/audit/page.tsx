import { DashboardSectionTabs, type DashboardSectionTab } from "../../../components/dashboard-section-tabs";
import { AgentDebugView } from "../../../components/dashboard-views";
import { LogExplorer, type AuditRow } from "../../../components/log-explorer";
import { PageShell } from "../../../components/page-shell";
import { SafetyCheckList } from "../../../components/safety-check-list";
import { fetchJson } from "../../../lib/api";
import {
  buildAuditListEndpoint,
  parseAuditLimit,
  parseAuditSort,
} from "../../../lib/audit-log";
import type { SafetyCheckSummaryRow } from "../../../lib/safety-checks";

type SearchParams = Record<string, string | string[] | undefined>;
type Row = Record<string, unknown>;
type AuditSection = "log" | "safety-checks" | "agents";

export const dynamic = "force-dynamic";

const sectionMeta: Record<AuditSection, Omit<DashboardSectionTab, "href">> = {
  log: {
    value: "log",
    label: "감사 로그",
    description: "리스크, 주문, 동기화, 에이전트 실행 이벤트를 시간순으로 추적합니다.",
  },
  "safety-checks": {
    value: "safety-checks",
    label: "안전 점검 감사",
    description: "리스크 점검 요약과 필요 시 원본 JSON 상세를 열어서 확인합니다.",
  },
  agents: {
    value: "agents",
    label: "AI 실행 추적",
    description: "최근 AI 에이전트 실행 결과를 원인 추적용 보조 정보로 확인합니다.",
  },
};

function queryValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

function resolveSection(value: string | string[] | undefined): AuditSection {
  const normalized = queryValue(value);
  if (normalized === "safety-checks" || normalized === "agents") {
    return normalized;
  }
  return "log";
}

function sectionHref(section: AuditSection, searchParams: SearchParams) {
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
  return `/dashboard/audit?${params.toString()}`;
}

function tabs(searchParams: SearchParams): DashboardSectionTab[] {
  return (Object.keys(sectionMeta) as AuditSection[]).map((section) => ({
    ...sectionMeta[section],
    href: sectionHref(section, searchParams),
  }));
}

async function LogSection({ query }: { query: SearchParams }) {
  const rows = await fetchJson<AuditRow[]>(buildAuditListEndpoint(query, { compact: true }));
  const initialTab = typeof query.tab === "string" ? query.tab : "all";
  const initialSeverity = typeof query.severity === "string" ? query.severity : "";
  const initialSearch = typeof query.q === "string" ? query.q : typeof query.search === "string" ? query.search : "";
  const initialSort = parseAuditSort(typeof query.sort === "string" ? query.sort : null);
  const initialLimit = parseAuditLimit(typeof query.limit === "string" ? query.limit : null, 30);

  return (
    <LogExplorer
      initialRows={rows}
      initialTab={initialTab}
      initialSeverity={initialSeverity}
      initialSearch={initialSearch}
      initialSort={initialSort}
      initialLimit={initialLimit}
    />
  );
}

async function SafetyChecksSection() {
  try {
    const rows = await fetchJson<SafetyCheckSummaryRow[]>("/api/risk/checks/summary?limit=20");
    return <SafetyCheckList rows={rows} />;
  } catch (error) {
    return (
      <SafetyCheckList
        rows={[]}
        errorMessage={error instanceof Error ? error.message : "알 수 없는 API 오류"}
      />
    );
  }
}

async function AgentsSection() {
  const agentRows = await fetchJson<Row[]>("/api/agents?limit=12&compact=true");
  return <AgentDebugView agentRows={agentRows} />;
}

export default async function AuditPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const query = await searchParams;
  const activeSection = resolveSection(query.section);

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="감사 / 원인 추적"
        title="감사와 원인 추적 통합"
        description="운영 로그, 안전 점검 상세, AI 실행 기록을 원인 추적 축으로 묶어 확인합니다. 거래 정책과 실행 로직은 변경하지 않습니다."
        compact
      />
      <DashboardSectionTabs tabs={tabs(query)} active={activeSection} />

      {activeSection === "safety-checks" ? (
        <SafetyChecksSection />
      ) : activeSection === "agents" ? (
        <AgentsSection />
      ) : (
        <LogSection query={query} />
      )}
    </div>
  );
}
