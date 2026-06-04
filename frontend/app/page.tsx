import { OperatorFriendlyDashboard } from "../components/operator-friendly-dashboard";
import { type OperatorDashboardPayload } from "../components/overview-dashboard";
import { fetchJson } from "../lib/api";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const operator = await fetchJson<OperatorDashboardPayload>("/api/dashboard/operator?view=home");

  return <OperatorFriendlyDashboard initial={operator} />;
}
