import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

function analyticsRedirectPath(searchParams: SearchParams) {
  const params = new URLSearchParams();
  params.set("section", "cost");

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

  return `/dashboard/analytics?${params.toString()}`;
}

export default async function CostBreakdownLegacyPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  redirect(analyticsRedirectPath(await searchParams));
}
