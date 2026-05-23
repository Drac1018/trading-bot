"use client";

import { useState } from "react";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

type RiskCheckDetail = {
  risk_check?: {
    payload?: unknown;
  };
  audit_event?: unknown;
};

export function RiskCheckPayloadDetails({ riskCheckId }: { riskCheckId: number | null | undefined }) {
  const [detail, setDetail] = useState<RiskCheckDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadDetail() {
    if (!riskCheckId || detail || loading) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/api/risk/checks/${riskCheckId}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      setDetail((await response.json()) as RiskCheckDetail);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "상세 정보를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  if (!riskCheckId) {
    return null;
  }

  const payload = detail?.risk_check?.payload ?? detail ?? null;

  return (
    <details
      className="mt-4 rounded-md border border-slate-200 bg-white"
      onToggle={(event) => {
        if (event.currentTarget.open) {
          void loadDetail();
        }
      }}
    >
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-ink">
        고급 정보 보기
      </summary>
      <div className="border-t border-slate-200 px-4 py-4">
        {loading ? <p className="text-sm text-slate-500">불러오는 중입니다.</p> : null}
        {error ? <p className="text-sm text-rose-700">{error}</p> : null}
        {payload ? (
          <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-slate-950 p-4 text-xs leading-6 text-slate-100">
            {JSON.stringify(payload, null, 2)}
          </pre>
        ) : null}
      </div>
    </details>
  );
}
