"use client";

import { useEffect, useMemo, useState } from "react";

import { DataTable } from "./data-table";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const refreshMs = 20000;

type Row = Record<string, unknown>;
type Tab = "orders" | "executions" | "audit";

type Payload = {
  orders: Row[];
  executions: Row[];
  audit: Row[];
};

async function fetchLogs(): Promise<Payload> {
  const [orders, executions, audit] = await Promise.all([
    fetch(`${apiBaseUrl}/api/orders?limit=100`, { cache: "no-store" }).then((response) => response.json()),
    fetch(`${apiBaseUrl}/api/executions?limit=100`, { cache: "no-store" }).then((response) => response.json()),
    fetch(`${apiBaseUrl}/api/audit?limit=100`, { cache: "no-store" }).then((response) => response.json()),
  ]);
  return { orders, executions, audit };
}

const inputClass =
  "w-full rounded-2xl border border-amber-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-amber-400";

function FilterBar({
  symbol,
  setSymbol,
  status,
  setStatus,
  search,
  setSearch,
  statusOptions,
  symbolOptions,
  statusLabel,
}: {
  symbol: string;
  setSymbol: (value: string) => void;
  status: string;
  setStatus: (value: string) => void;
  search: string;
  setSearch: (value: string) => void;
  statusOptions: string[];
  symbolOptions: string[];
  statusLabel: string;
}) {
  return (
    <div className="grid gap-3 lg:grid-cols-3">
      <select className={inputClass} value={symbol} onChange={(event) => setSymbol(event.target.value)}>
        <option value="">모든 심볼</option>
        {symbolOptions.map((item) => (
          <option key={item} value={item}>
            {item}
          </option>
        ))}
      </select>
      <select className={inputClass} value={status} onChange={(event) => setStatus(event.target.value)}>
        <option value="">모든 {statusLabel}</option>
        {statusOptions.map((item) => (
          <option key={item} value={item}>
            {item}
          </option>
        ))}
      </select>
      <input
        className={inputClass}
        placeholder="검색어로 필터링"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
    </div>
  );
}

export function LogExplorer({ initial }: { initial: Payload }) {
  const [payload, setPayload] = useState(initial);
  const [activeTab, setActiveTab] = useState<Tab>("orders");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [searchFilter, setSearchFilter] = useState("");

  useEffect(() => {
    let active = true;

    const refresh = async () => {
      try {
        const next = await fetchLogs();
        if (active) {
          setPayload(next);
        }
      } catch {
        return;
      }
    };

    const interval = window.setInterval(() => {
      void refresh();
    }, refreshMs);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, []);

  const symbolOptions = useMemo(() => {
    const values = new Set<string>();
    [payload.orders, payload.executions].forEach((rows) => {
      rows.forEach((row) => {
        if (typeof row.symbol === "string") {
          values.add(row.symbol);
        }
      });
    });
    return [...values].sort();
  }, [payload.executions, payload.orders]);

  const statusOptions = useMemo(() => {
    const values = new Set<string>();
    const rows = activeTab === "orders" ? payload.orders : activeTab === "executions" ? payload.executions : payload.audit;
    const key = activeTab === "audit" ? "severity" : "status";
    rows.forEach((row) => {
      const value = row[key];
      if (typeof value === "string") {
        values.add(value);
      }
    });
    return [...values].sort();
  }, [activeTab, payload.audit, payload.executions, payload.orders]);

  const filteredRows = useMemo(() => {
    const rows = activeTab === "orders" ? payload.orders : activeTab === "executions" ? payload.executions : payload.audit;
    const keyword = searchFilter.trim().toLowerCase();
    return rows.filter((row) => {
      if (symbolFilter && row.symbol !== symbolFilter) {
        return false;
      }
      if (statusFilter) {
        const key = activeTab === "audit" ? "severity" : "status";
        if (row[key] !== statusFilter) {
          return false;
        }
      }
      if (keyword) {
        return JSON.stringify(row).toLowerCase().includes(keyword);
      }
      return true;
    });
  }, [activeTab, payload.audit, payload.executions, payload.orders, searchFilter, statusFilter, symbolFilter]);

  const tabConfig: Record<Tab, { title: string; description: string; statusLabel: string }> = {
    orders: {
      title: "주문 로그",
      description: "실거래 주문 상태와 보호 주문 관계를 필터링해서 확인합니다.",
      statusLabel: "주문 상태",
    },
    executions: {
      title: "체결 로그",
      description: "부분 체결 포함 실제 체결 이력을 검색하고 필터링합니다.",
      statusLabel: "체결 상태",
    },
    audit: {
      title: "감사 로그",
      description: "리스크, 승인, 동기화, 자동 적용 이력을 검색하고 분석합니다.",
      statusLabel: "심각도",
    },
  };

  const current = tabConfig[activeTab];

  return (
    <div className="space-y-6">
      <section className="rounded-[2rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
        <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-slate-500">로그 탐색기</p>
        <h2 className="mt-2 text-2xl font-semibold text-ink">거래 로그 및 이력 조회 개선</h2>
        <p className="mt-3 text-sm leading-7 text-slate-600">
          주문, 체결, 감사 로그를 탭별로 나누고 심볼·상태·검색어로 빠르게 좁혀 볼 수 있게 했습니다.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {(["orders", "executions", "audit"] as Tab[]).map((tab) => (
            <button
              key={tab}
              className={`rounded-full px-4 py-2 text-sm font-semibold ${
                activeTab === tab
                  ? "bg-amber-400 text-slate-900"
                  : "border border-amber-200 bg-white text-slate-700"
              }`}
              onClick={() => setActiveTab(tab)}
              type="button"
            >
              {tabConfig[tab].title}
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
        <FilterBar
          symbol={symbolFilter}
          setSymbol={setSymbolFilter}
          status={statusFilter}
          setStatus={setStatusFilter}
          search={searchFilter}
          setSearch={setSearchFilter}
          statusOptions={statusOptions}
          symbolOptions={activeTab === "audit" ? [] : symbolOptions}
          statusLabel={current.statusLabel}
        />
      </section>

      <DataTable title={current.title} description={current.description} rows={filteredRows} />
    </div>
  );
}
