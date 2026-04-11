import { LogExplorer } from "../../../components/log-explorer";
import { PageShell } from "../../../components/page-shell";
import { fetchJson } from "../../../lib/api";

type Row = Record<string, unknown>;

export default async function OrdersPage() {
  const [orders, executions, audit] = await Promise.all([
    fetchJson<Row[]>("/api/orders?limit=100"),
    fetchJson<Row[]>("/api/executions?limit=100"),
    fetchJson<Row[]>("/api/audit?limit=100"),
  ]);

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="로그 탐색"
        title="주문 / 체결 / 감사 로그"
        description="실거래 이력을 필터링하고 검색하며, 원인 분석에 필요한 로그를 빠르게 추적합니다."
      />
      <LogExplorer initial={{ orders, executions, audit }} />
    </div>
  );
}
