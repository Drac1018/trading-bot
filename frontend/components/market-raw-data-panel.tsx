"use client";

import { useState } from "react";

import { fetchJson } from "../lib/api";
import { DataTable } from "./data-table";

type Row = Record<string, unknown>;
type MarketChartTimeframe = "15m" | "1h" | "4h";

type MarketRawRows = {
  snapshots: Row[];
  features: Row[];
};

function marketRawEndpoint(path: "snapshots" | "features", selectedSymbol: string) {
  const params = new URLSearchParams({
    limit: "20",
    compact: "true",
  });
  if (selectedSymbol !== "ALL") {
    params.set("symbol", selectedSymbol);
  }
  return `/api/market/${path}?${params.toString()}`;
}

function formatMarketSnapshotRowTitle(row: Row, index: number) {
  const symbol = typeof row.symbol === "string" ? row.symbol : null;
  const timeframe = typeof row.timeframe === "string" ? row.timeframe : null;
  if (symbol && timeframe) {
    return `${symbol} / 봉 기준 ${timeframe}`;
  }
  if (symbol) {
    return symbol;
  }
  return `항목 ${index + 1}`;
}

export function MarketRawDataPanel({
  selectedSymbol,
  selectedTimeframe,
}: {
  selectedSymbol: string;
  selectedTimeframe: MarketChartTimeframe;
}) {
  const [rows, setRows] = useState<MarketRawRows | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loaded = rows !== null;

  async function loadRows() {
    if (loading) {
      return;
    }
    if (loaded) {
      setRows(null);
      setError(null);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const [snapshots, features] = await Promise.all([
        fetchJson<Row[]>(marketRawEndpoint("snapshots", selectedSymbol)),
        fetchJson<Row[]>(marketRawEndpoint("features", selectedSymbol)),
      ]);
      setRows({ snapshots, features });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "시장 입력 테이블을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  const scopeLabel = selectedSymbol === "ALL" ? "전체 심볼" : selectedSymbol;

  return (
    <>
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">보조 입력</p>
            <h2 className="mt-2 text-xl font-semibold text-slate-950">원본 입력 요약 테이블</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {scopeLabel} 기준 최근 market snapshot과 feature row는 필요할 때만 조회합니다. 차트 봉 기준은 {selectedTimeframe}입니다.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadRows()}
            aria-expanded={loaded}
            className="w-fit rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 transition hover:border-slate-400 hover:bg-slate-50 disabled:cursor-wait disabled:opacity-70"
            disabled={loading}
          >
            {loading ? "조회 중" : loaded ? "요약 테이블 접기" : "요약 테이블 조회"}
          </button>
        </div>
        {error ? (
          <div className="mt-4 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
            {error}
          </div>
        ) : null}
      </section>

      {loaded ? (
        <>
          <DataTable
            title="시장 스냅샷"
            description="최근 가격 입력"
            rows={rows.snapshots}
            emptyStateTitle="표시할 시장 스냅샷이 없습니다."
            emptyStateDescription="선택한 조건 기준으로 아직 저장된 시장 스냅샷이 없습니다."
            hiddenColumns={["candle_count", "candles", "payload"]}
            rowTitleFormatter={formatMarketSnapshotRowTitle}
            labelOverrides={{ timeframe: "봉 기준" }}
          />
          <DataTable
            title="특성 입력"
            description="최근 지표 계산 결과"
            rows={rows.features}
            emptyStateTitle="표시할 지표 입력이 없습니다."
            emptyStateDescription="선택한 조건 기준으로 아직 계산된 지표 스냅샷이 없습니다."
          />
        </>
      ) : null}
    </>
  );
}
