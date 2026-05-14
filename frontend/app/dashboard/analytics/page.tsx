import { redirect } from "next/navigation";

import { CostBreakdownDashboard } from "../../../components/cost-breakdown-dashboard";

type SearchParams = Record<string, string | string[] | undefined>;

function queryValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

function analyticsPath(searchParams: SearchParams) {
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
  params.set("section", "cost");
  return `/dashboard/analytics?${params.toString()}`;
}

export default async function AnalyticsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const query = await searchParams;
  const section = queryValue(query.section);

  if (section && section !== "cost") {
    redirect(analyticsPath(query));
  }

  return (
    <CostBreakdownDashboard
      searchParams={query}
      backHref="/"
      backLabel="운영 개요로 돌아가기"
    />
  );
}
