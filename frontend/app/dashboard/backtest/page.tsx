import { PageShell } from "../../../components/page-shell";

export const dynamic = "force-dynamic";

const checkpoints = [
  ["수수료/슬리피지", "ReplayValidationResponse.summary.net_pnl_after_fees, fees, slippage fields"],
  ["워크포워드", "recent_walk_forward_recommendation, recent_window_summary"],
  ["리스크 지표", "max_drawdown, win_rate, profit_factor, expectancy"],
  ["실거래 안전성", "isolated in-memory replay; live_execution_guarantee"],
];

export default function BacktestPage() {
  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="검증"
        title="백테스트 / 워크포워드"
        description="이 화면은 실주문을 만들지 않는 replay validation 경로만 제품 체크포인트로 인정합니다. 결과는 수수료, 슬리피지, 체결 지연, MDD, 승률, 손익비, 워크포워드 추천 필드를 기준으로 판단합니다."
        compact
      />

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
          <h2 className="text-base font-semibold text-slate-950">검증 기준</h2>
          <div className="mt-4 overflow-hidden rounded-md border border-slate-200">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500">
                <tr>
                  <th className="px-3 py-2">체크포인트</th>
                  <th className="px-3 py-2">source-of-truth</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {checkpoints.map(([label, source]) => (
                  <tr key={label}>
                    <td className="whitespace-nowrap px-3 py-3 font-medium text-slate-900">{label}</td>
                    <td className="px-3 py-3 font-mono text-xs leading-5 text-slate-600">{source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
          <h2 className="text-base font-semibold text-slate-950">실행 경로</h2>
          <div className="mt-4 space-y-3 text-sm text-slate-700">
            <div>
              <p className="text-xs font-semibold uppercase text-slate-500">API</p>
              <code className="mt-1 block rounded-md bg-slate-950 px-3 py-2 text-xs text-white">
                POST /api/replay/validation
              </code>
            </div>
            <div>
              <p className="text-xs font-semibold uppercase text-slate-500">제품화 검증</p>
              <code className="mt-1 block rounded-md bg-slate-950 px-3 py-2 text-xs text-white">
                scripts\run_productization_checks.ps1
              </code>
            </div>
          </div>
        </aside>
      </section>
    </div>
  );
}
