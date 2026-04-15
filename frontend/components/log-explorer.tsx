"use client";

import {
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import {
  AUDIT_TAB_CONFIG,
  AUDIT_TAB_ORDER,
  filterAuditRows,
  getAuditEventCategory,
  getAuditTabCounts,
  parseAuditTab,
  type AuditRow,
  type AuditTab,
  type SortMode,
} from "../lib/audit-log";
import { formatDisplayValue } from "../lib/ui-copy";
import { DataTable } from "./data-table";

export type { AuditRow } from "../lib/audit-log";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const refreshMs = 20000;
const limitOptions = [50, 100, 200] as const;
const inputClass =
  "w-full rounded-2xl border border-amber-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-amber-400";

export type LogExplorerProps = {
  initialRows: AuditRow[];
  initialTab?: string;
  initialLimit?: number;
};

async function fetchAuditLogs(limit: number): Promise<AuditRow[]> {
  const response = await fetch(`${apiBaseUrl}/api/audit?limit=${limit}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load audit logs: ${response.status}`);
  }
  return (await response.json()) as AuditRow[];
}

function updateTabQuery(pathname: string, searchParams: URLSearchParams, nextTab: AuditTab) {
  const nextParams = new URLSearchParams(searchParams.toString());
  if (nextTab === "all") {
    nextParams.delete("tab");
  } else {
    nextParams.set("tab", nextTab);
  }

  const queryString = nextParams.toString();
  return queryString ? `${pathname}?${queryString}` : pathname;
}

export function LogExplorer({ initialRows, initialTab = "all", initialLimit = 100 }: LogExplorerProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const [rows, setRows] = useState<AuditRow[]>(initialRows);
  const [activeTab, setActiveTab] = useState<AuditTab>(parseAuditTab(initialTab));
  const [severityFilter, setSeverityFilter] = useState("");
  const [searchFilter, setSearchFilter] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("newest");
  const [limit, setLimit] = useState(initialLimit);
  const deferredSearch = useDeferredValue(searchFilter);

  useEffect(() => {
    setActiveTab(parseAuditTab(searchParams.get("tab")));
  }, [searchParams]);

  useEffect(() => {
    let active = true;

    const refresh = async () => {
      try {
        const nextRows = await fetchAuditLogs(limit);
        if (active) {
          setRows(nextRows);
        }
      } catch {
        return;
      }
    };

    void refresh();

    const interval = window.setInterval(() => {
      void refresh();
    }, refreshMs);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [limit]);

  const categoryRows = useMemo(
    () => (activeTab === "all" ? rows : rows.filter((row) => getAuditEventCategory(row) === activeTab)),
    [activeTab, rows],
  );

  const tabCounts = useMemo(() => getAuditTabCounts(rows), [rows]);

  const severityOptions = useMemo(() => {
    const values = new Set<string>();
    categoryRows.forEach((row) => {
      if (typeof row.severity === "string" && row.severity.trim()) {
        values.add(row.severity);
      }
    });
    return [...values];
  }, [categoryRows]);

  const filteredRows = useMemo(
    () =>
      filterAuditRows(rows, {
        activeTab,
        severityFilter,
        searchFilter: deferredSearch,
        sortMode,
      }),
    [activeTab, deferredSearch, rows, severityFilter, sortMode],
  );

  const currentTab = AUDIT_TAB_CONFIG[activeTab];
  const panelId = "audit-tabpanel";
  const emptyState =
    categoryRows.length === 0
      ? {
          title: currentTab.emptyTitle,
          description: currentTab.emptyDescription,
        }
      : {
          title: "현재 필터 조건에 맞는 감사 이벤트가 없습니다.",
          description: "검색어, 심각도, 정렬 조건을 조정하면 필요한 감사 이벤트를 더 빠르게 찾을 수 있습니다.",
        };

  const selectTab = (nextTab: AuditTab) => {
    setActiveTab(nextTab);
    router.replace(updateTabQuery(pathname, new URLSearchParams(searchParams.toString()), nextTab), {
      scroll: false,
    });
  };

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex = index;

    if (event.key === "ArrowRight") {
      nextIndex = (index + 1) % AUDIT_TAB_ORDER.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (index - 1 + AUDIT_TAB_ORDER.length) % AUDIT_TAB_ORDER.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = AUDIT_TAB_ORDER.length - 1;
    } else {
      return;
    }

    event.preventDefault();
    const nextTab = AUDIT_TAB_ORDER[nextIndex];
    selectTab(nextTab);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <div className="space-y-6">
      <section className="rounded-[2rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
        <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-slate-500">Audit Explorer</p>
        <h2 className="mt-2 text-2xl font-semibold text-ink">감사 이벤트 탐색</h2>
        <p className="mt-3 text-sm leading-7 text-slate-600">
          리스크, 실행, 승인/운영제어, 보호주문, 헬스/시스템, AI/의사결정 이벤트를 같은 규칙으로 탐색합니다.
        </p>

        <div className="mt-4 flex flex-wrap gap-2" role="tablist" aria-label="감사 이벤트 분류 탭">
          {AUDIT_TAB_ORDER.map((tab, index) => {
            const selected = activeTab === tab;
            return (
              <button
                key={tab}
                ref={(element) => {
                  tabRefs.current[index] = element;
                }}
                id={`audit-tab-${tab}`}
                aria-selected={selected}
                aria-controls={panelId}
                className={`rounded-full px-4 py-2 text-sm font-semibold ${
                  selected ? "bg-amber-400 text-slate-900" : "border border-amber-200 bg-white text-slate-700"
                }`}
                onClick={() => selectTab(tab)}
                onKeyDown={(event) => handleTabKeyDown(event, index)}
                role="tab"
                tabIndex={selected ? 0 : -1}
                type="button"
              >
                {AUDIT_TAB_CONFIG[tab].label} {tabCounts.get(tab) ?? 0}
              </button>
            );
          })}
        </div>
      </section>

      <section id={panelId} aria-labelledby={`audit-tab-${activeTab}`} className="space-y-6" role="tabpanel">
        <section className="rounded-[1.8rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame">
          <div className="grid gap-3 lg:grid-cols-4">
            <input
              aria-label="감사 로그 검색"
              className={inputClass}
              placeholder="이벤트 유형, 메시지, payload 검색"
              value={searchFilter}
              onChange={(event) => setSearchFilter(event.target.value)}
            />
            <select
              aria-label="감사 로그 심각도 필터"
              className={inputClass}
              value={severityFilter}
              onChange={(event) => setSeverityFilter(event.target.value)}
            >
              <option value="">모든 심각도</option>
              {severityOptions.map((value) => (
                <option key={value} value={value}>
                  {formatDisplayValue(value, "severity")}
                </option>
              ))}
            </select>
            <select
              aria-label="감사 로그 정렬"
              className={inputClass}
              value={sortMode}
              onChange={(event) => setSortMode(event.target.value as SortMode)}
            >
              <option value="newest">최신순</option>
              <option value="oldest">오래된순</option>
              <option value="severity">심각도 우선</option>
            </select>
            <select
              aria-label="감사 로그 조회 건수"
              className={inputClass}
              value={String(limit)}
              onChange={(event) => setLimit(Number(event.target.value))}
            >
              {limitOptions.map((value) => (
                <option key={value} value={value}>
                  최근 {value}건
                </option>
              ))}
            </select>
          </div>
        </section>

        <DataTable
          title={currentTab.title}
          description={currentTab.description}
          rows={filteredRows}
          emptyStateTitle={emptyState.title}
          emptyStateDescription={emptyState.description}
        />
      </section>
    </div>
  );
}
