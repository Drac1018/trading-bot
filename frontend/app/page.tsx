import { OverviewDashboard, type OperatorDashboardPayload } from "../components/overview-dashboard";
import { fetchJson } from "../lib/api";

export default async function HomePage() {
  const operator = await fetchJson<OperatorDashboardPayload>("/api/dashboard/operator");

  return <OverviewDashboard initial={{ operator }} />;
}
