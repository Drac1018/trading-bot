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
  buildAuditDetailEndpoint,
  buildAuditListEndpoint,
  describeAuditLegacyReview,
  filterAuditRows,
  formatAuditEntityType,
  formatAuditEventType,
  formatAuditMessage,
  formatAuditRowTitle,
  getAuditEventCategory,
  parseAuditLimit,
  parseAuditSort,
  parseAuditTab,
  type AuditRow,
  type AuditTab,
  type SortMode,
} from "../lib/audit-log";
import { formatDisplayValue } from "../lib/ui-copy";

export type { AuditRow } from "../lib/audit-log";

const apiBaseUrl = "";
const refreshMs = 20000;
const limitOptions = [30, 50, 100] as const;
const severityOptions = ["", "critical", "error", "warning", "info"] as const;
const inputClass =
  "w-full rounded-md border border-slate-300 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100";

export type LogExplorerProps = {
  initialRows: AuditRow[];
  initialTab?: string;
  initialLimit?: number;
  initialSeverity?: string;
  initialSearch?: string;
  initialSort?: SortMode;
};

type AuditListRequest = {
  tab: AuditTab;
  severity: string;
  q: string;
  sort: SortMode;
  limit: number;
};

async function fetchAuditLogs(query: AuditListRequest): Promise<AuditRow[]> {
  const response = await fetch(`${apiBaseUrl}${buildAuditListEndpoint(query, { compact: true })}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Failed to load audit logs: ${response.status}`);
  }
  return (await response.json()) as AuditRow[];
}

async function fetchAuditDetail(id: string | number): Promise<AuditRow> {
  const response = await fetch(`${apiBaseUrl}${buildAuditDetailEndpoint(id)}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Failed to load audit detail: ${response.status}`);
  }
  return (await response.json()) as AuditRow;
}

function updateAuditQuery(
  pathname: string,
  searchParams: URLSearchParams,
  updates: Record<string, string | number | null>,
) {
  const nextParams = new URLSearchParams(searchParams.toString());
  for (const [key, value] of Object.entries(updates)) {
    if (value === null || value === "") {
      nextParams.delete(key);
    } else {
      nextParams.set(key, String(value));
    }
  }
  const queryString = nextParams.toString();
  return queryString ? `${pathname}?${queryString}` : pathname;
}

function compactRowId(row: AuditRow): string | number | null {
  const value = row.id;
  return typeof value === "string" || typeof value === "number" ? value : null;
}

function buildAuditDisplayRow(row: AuditRow): AuditRow {
  const legacyReview = describeAuditLegacyReview(row);
  const presentation = {
    event_label: formatAuditEventType(row.event_type),
    entity_type_label: formatAuditEntityType(row.entity_type),
    message_label: formatAuditMessage(row),
  };

  if (!legacyReview) {
    return {
      ...presentation,
      ...row,
    };
  }

  return {
    ...presentation,
    ...row,
    policy_badges: [legacyReview.badge],
    policy_context: legacyReview.label,
    policy_note: legacyReview.hint,
  };
}

function severityBadgeClass(value: string | undefined) {
  if (value === "critical" || value === "error") {
    return "border-rose-200 bg-rose-50 text-rose-700";
  }
  if (value === "warning") {
    return "border-amber-200 bg-amber-50 text-amber-800";
  }
  return "border-slate-200 bg-white text-slate-700";
}

function auditValue(value: unknown) {
  if (typeof value === "string" && value.trim().length > 0) {
    return value;
  }
  if (typeof value === "number") {
    return String(value);
  }
  return "-";
}

function AuditDetailPanel({ row }: { row: AuditRow }) {
  const id = compactRowId(row);
  const [detail, setDetail] = useState<AuditRow | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDetail = async () => {
    if (id === null || detail || loading) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setDetail(await fetchAuditDetail(id));
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "감사 detail 조회 실패");
    } finally {
      setLoading(false);
    }
  };

  return (
    <details
      className="mt-4 rounded-md border border-slate-200 bg-white"
      onToggle={(event) => {
        if (event.currentTarget.open) {
          void loadDetail();
        }
      }}
    >
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-slate-950">
        원본 payload / detail 보기
      </summary>
      <div className="space-y-4 border-t border-slate-200 px-4 py-4">
        {id === null ? (
          <p className="text-sm text-slate-500">감사 이벤트 ID가 없어 detail을 조회할 수 없습니다.</p>
        ) : loading ? (
          <p className="text-sm text-slate-500">원본 payload를 불러오는 중입니다.</p>
        ) : error ? (
          <p className="text-sm text-rose-700">{error}</p>
        ) : detail ? (
          <>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">event</p>
                <p className="mt-2 text-sm text-slate-900">
                  #{auditValue(detail.id)} / {auditValue(detail.event_type)}
                </p>
              </div>
              <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">entity</p>
                <p className="mt-2 text-sm text-slate-900">
                  {auditValue(detail.entity_type)} / {auditValue(detail.entity_id)}
                </p>
              </div>
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">payload</p>
              <pre className="mt-2 max-h-[520px] max-w-full overflow-auto rounded-md bg-slate-950 p-4 text-xs leading-6 text-slate-100">
                {JSON.stringify(detail.payload ?? {}, null, 2)}
              </pre>
            </div>
          </>
        ) : (
          <p className="text-sm text-slate-500">detail을 열면 원본 payload를 조회합니다.</p>
        )}
      </div>
    </details>
  );
}

export function LogExplorer({
  initialRows,
  initialTab = "all",
  initialLimit = 30,
  initialSeverity = "",
  initialSearch = "",
  initialSort = "newest",
}: LogExplorerProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const didUseInitialRows = useRef(false);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const activeTab = parseAuditTab(searchParams.get("tab") ?? initialTab);
  const severityFilter = searchParams.get("severity") ?? initialSeverity;
  const sortMode = parseAuditSort(searchParams.get("sort") ?? initialSort);
  const limit = parseAuditLimit(searchParams.get("limit") ?? initialLimit, initialLimit);
  const querySearch = searchParams.get("q") ?? searchParams.get("search") ?? initialSearch;
  const [rows, setRows] = useState<AuditRow[]>(initialRows);
  const [searchFilter, setSearchFilter] = useState(querySearch);
  const deferredSearch = useDeferredValue(searchFilter);

  useEffect(() => {
    setSearchFilter(querySearch);
  }, [querySearch]);

  const request = useMemo<AuditListRequest>(
    () => ({
      tab: activeTab,
      severity: severityFilter,
      q: querySearch,
      sort: sortMode,
      limit,
    }),
    [activeTab, limit, querySearch, severityFilter, sortMode],
  );
  const requestKey = `${request.tab}|${request.severity}|${request.q}|${request.sort}|${request.limit}`;

  useEffect(() => {
    let active = true;

    const refresh = async () => {
      try {
        const nextRows = await fetchAuditLogs(request);
        if (active) {
          setRows(nextRows);
        }
      } catch {
        return;
      }
    };

    if (didUseInitialRows.current) {
      void refresh();
    } else {
      didUseInitialRows.current = true;
    }

    const interval = window.setInterval(() => {
      void refresh();
    }, refreshMs);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [request, requestKey]);

  const displayRows = useMemo(() => rows.map(buildAuditDisplayRow), [rows]);
  const filteredRows = useMemo(
    () =>
      filterAuditRows(displayRows, {
        activeTab,
        severityFilter,
        searchFilter: deferredSearch,
        sortMode,
      }),
    [activeTab, deferredSearch, displayRows, severityFilter, sortMode],
  );

  const visibleLegacyRows = useMemo(
    () => filteredRows.filter((row) => describeAuditLegacyReview(row)).length,
    [filteredRows],
  );
  const visibleSuppressedRows = useMemo(
    () => filteredRows.filter((row) => row.suppression_active === true).length,
    [filteredRows],
  );

  const currentTab = AUDIT_TAB_CONFIG[activeTab];
  const panelId = "audit-tabpanel";
  const emptyState =
    rows.length === 0
      ? {
          title: currentTab.emptyTitle,
          description: currentTab.emptyDescription,
        }
      : {
          title: "현재 필터 조건에 맞는 감사 이벤트가 없습니다.",
          description: "검색어, 심각도, 정렬 조건을 조정하면 필요한 감사 이벤트를 다시 찾을 수 있습니다.",
        };

  const updateQuery = (updates: Record<string, string | number | null>) => {
    router.replace(updateAuditQuery(pathname, new URLSearchParams(searchParams.toString()), updates), {
      scroll: false,
    });
  };

  const selectTab = (nextTab: AuditTab) => {
    updateQuery({ tab: nextTab === "all" ? null : nextTab });
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
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">Audit Explorer</p>
        <h2 className="mt-2 text-2xl font-semibold text-slate-950">감사 이벤트 탐색</h2>
        <p className="mt-3 text-sm leading-7 text-slate-600">
          목록은 요약 필드만 먼저 표시하고, 원본 payload는 행을 펼쳤을 때 별도로 조회합니다.
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
                className={`rounded-md px-4 py-2 text-sm font-semibold ${
                  selected ? "bg-blue-600 text-white" : "border border-slate-200 bg-white text-slate-700"
                }`}
                onClick={() => selectTab(tab)}
                onKeyDown={(event) => handleTabKeyDown(event, index)}
                role="tab"
                tabIndex={selected ? 0 : -1}
                type="button"
              >
                {AUDIT_TAB_CONFIG[tab].label}
              </button>
            );
          })}
        </div>
      </section>

      <section id={panelId} aria-labelledby={`audit-tab-${activeTab}`} className="space-y-6" role="tabpanel">
        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <div className="grid gap-3 lg:grid-cols-4">
            <input
              aria-label="감사 로그 검색"
              className={inputClass}
              placeholder="이벤트, 메시지, entity 검색"
              value={searchFilter}
              onChange={(event) => {
                const value = event.target.value;
                setSearchFilter(value);
                updateQuery({ q: value.trim() || null });
              }}
            />
            <select
              aria-label="감사 로그 심각도 필터"
              className={inputClass}
              value={severityFilter}
              onChange={(event) => updateQuery({ severity: event.target.value || null })}
            >
              {severityOptions.map((value) => (
                <option key={value || "all"} value={value}>
                  {value ? formatDisplayValue(value, "severity") : "모든 심각도"}
                </option>
              ))}
            </select>
            <select
              aria-label="감사 로그 정렬"
              className={inputClass}
              value={sortMode}
              onChange={(event) => updateQuery({ sort: event.target.value === "newest" ? null : event.target.value })}
            >
              <option value="newest">최신순</option>
              <option value="oldest">오래된순</option>
              <option value="severity">심각도 우선</option>
            </select>
            <select
              aria-label="감사 로그 조회 건수"
              className={inputClass}
              value={String(limit)}
              onChange={(event) => updateQuery({ limit: event.target.value === "30" ? null : event.target.value })}
            >
              {limitOptions.map((value) => (
                <option key={value} value={value}>
                  최근 {value}건
                </option>
              ))}
            </select>
          </div>
        </section>

        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">
                {currentTab.description}
              </p>
              <h2 className="mt-1 text-xl font-semibold text-slate-950 sm:text-2xl">{currentTab.title}</h2>
            </div>
            <div className="w-fit rounded-md bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
              {filteredRows.length}건
            </div>
          </div>

          {filteredRows.length === 0 ? (
            <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-sm text-slate-500">
              <p className="font-semibold text-slate-700">{emptyState.title}</p>
              <p className="mt-2 leading-6">{emptyState.description}</p>
            </div>
          ) : (
            <div className="space-y-4">
              {filteredRows.map((row, index) => {
                const category = getAuditEventCategory(row);
                const relatedType = typeof row.related_type === "string" ? row.related_type : null;
                const relatedId = auditValue(row.related_id);
                return (
                  <article key={String(compactRowId(row) ?? `${category}-${index}`)} className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="min-w-0">
                        <h3 className="text-base font-semibold text-slate-950">
                          {formatAuditRowTitle(row, index)}
                        </h3>
                        <p className="mt-1 text-sm leading-6 text-slate-600">
                          {String(row.message_summary ?? row.message_label ?? formatAuditMessage(row))}
                        </p>
                        <p className="mt-1 text-xs leading-5 text-slate-500">
                          {formatDisplayValue(row.created_at, "created_at")} / {AUDIT_TAB_CONFIG[category].label}
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${severityBadgeClass(row.severity)}`}>
                          {formatDisplayValue(row.severity ?? "-", "severity")}
                        </span>
                        <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
                          #{auditValue(row.id)}
                        </span>
                      </div>
                    </div>

                    <dl className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                      <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
                        <dt className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">대상</dt>
                        <dd className="mt-2 text-sm leading-6 text-slate-900">
                          {String(row.entity_type_label ?? formatAuditEntityType(row.entity_type))} / {auditValue(row.entity_id)}
                        </dd>
                      </div>
                      <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
                        <dt className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">관련 ID</dt>
                        <dd className="mt-2 text-sm leading-6 text-slate-900">
                          {relatedType ? `${relatedType} ${relatedId}` : relatedId}
                        </dd>
                      </div>
                      <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
                        <dt className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">이벤트</dt>
                        <dd className="mt-2 text-sm leading-6 text-slate-900">
                          {String(row.event_label ?? formatAuditEventType(row.event_type))}
                        </dd>
                      </div>
                      <div className="rounded-md border border-slate-200 bg-white px-4 py-3">
                        <dt className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">payload</dt>
                        <dd className="mt-2 text-sm leading-6 text-slate-900">
                          {row.has_payload ? "detail에서 조회" : "payload 없음"}
                        </dd>
                      </div>
                    </dl>

                    {typeof row.policy_context === "string" || typeof row.policy_note === "string" ? (
                      <div className="mt-3 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-900">
                        {[row.policy_context, row.policy_note].filter((value) => typeof value === "string" && value.trim()).join(" / ")}
                      </div>
                    ) : null}

                    <AuditDetailPanel row={row} />
                  </article>
                );
              })}
            </div>
          )}
        </section>

        {visibleSuppressedRows > 0 ? (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-700">
            진입 제안 억제 상태가 포함된 row가 있습니다. 실제 주문 여부는 리스크/실행 row와 함께 확인하세요.
          </div>
        ) : null}
        {visibleLegacyRows > 0 ? (
          <div className="rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm leading-6 text-slate-700">
            과거 시간 기반 AI 검토 기록은 현재 runtime trigger와 분리해 표시합니다.
          </div>
        ) : null}
      </section>
    </div>
  );
}
