import { DashboardSectionTabs, type DashboardSectionTab } from "../../../components/dashboard-section-tabs";
import { OperationsDashboardClient } from "../../../components/operations-dashboard-client";
import { PageShell } from "../../../components/page-shell";

type SearchParams = Record<string, string | string[] | undefined>;
type OperationsSection = "decisions" | "risk" | "scheduler";

export const dynamic = "force-dynamic";

const operationSections: OperationsSection[] = ["scheduler", "risk", "decisions"];
const defaultOperationsSection: OperationsSection = "scheduler";

const sectionMeta: Record<OperationsSection, Omit<DashboardSectionTab, "href">> = {
  scheduler: {
    value: "scheduler",
    label: "자동 실행",
    description: "스케줄러 상태, 다음 실행 예정, AI 호출 분류와 생략 사유를 확인합니다.",
  },
  risk: {
    value: "risk",
    label: "운영 리스크",
    description: "최근 risk check, 진입 플랜, 차단 사유와 운영자 확인 항목을 확인합니다.",
  },
  decisions: {
    value: "decisions",
    label: "AI 판단",
    description: "심볼별 현재 판단, AI 재검토 흐름, 최근 decision row를 한 화면에서 확인합니다.",
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
  if (normalized === "decisions" || normalized === "risk" || normalized === "scheduler") {
    return normalized;
  }
  return defaultOperationsSection;
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
  return operationSections.map((section) => ({
    ...sectionMeta[section],
    href: sectionHref(section, searchParams),
  }));
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

      <OperationsDashboardClient section={activeSection} query={query} />
    </div>
  );
}
