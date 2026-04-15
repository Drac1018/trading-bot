import {
  formatDisplayValue,
  formatListValue,
  getRowTitle,
  normalizeDisplayValue,
  translateLabel,
} from "../lib/ui-copy";
import { buildTableRowKeys, splitTableColumns, type TableRow } from "../lib/data-table";

function renderValue(value: unknown, key?: string) {
  if (value === null || value === undefined) {
    return <span className="text-slate-400">-</span>;
  }

  if (Array.isArray(value)) {
    const items = formatListValue(value, key);
    if (items.length === 0) {
      return <span className="text-slate-400">-</span>;
    }

    return (
      <div className="flex flex-wrap gap-2">
        {items.map((item, index) => (
          <span
            key={`${item}-${index}`}
            className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-900"
          >
            {item}
          </span>
        ))}
      </div>
    );
  }

  if (typeof value === "object") {
    return (
      <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded-2xl bg-slate-900/95 p-4 text-xs leading-6 text-slate-100">
        {JSON.stringify(normalizeDisplayValue(value, key), null, 2)}
      </pre>
    );
  }

  return <span>{formatDisplayValue(value, key)}</span>;
}

export function DataTable({
  title,
  description,
  rows,
  emptyStateTitle,
  emptyStateDescription,
  hiddenColumns = [],
}: {
  title: string;
  description: string;
  rows: TableRow[];
  emptyStateTitle?: string;
  emptyStateDescription?: string;
  hiddenColumns?: string[];
}) {
  const { primary, detail } = splitTableColumns(rows, new Set(hiddenColumns));
  const rowKeys = buildTableRowKeys(rows);

  return (
    <section className="rounded-[2rem] border border-amber-200/70 bg-white/90 p-5 shadow-frame sm:p-6">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-slate-500">{description}</p>
          <h2 className="font-display text-2xl text-ink sm:text-[2rem]">{title}</h2>
        </div>
        <div className="w-fit rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-800">
          {rows.length}건
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-amber-300 px-4 py-8 text-sm text-slate-500">
          <p className="font-semibold text-slate-700">{emptyStateTitle ?? "표시할 데이터가 없습니다."}</p>
          <p className="mt-2 leading-6">{emptyStateDescription ?? "현재 조건에 맞는 항목이 아직 없습니다."}</p>
        </div>
      ) : (
        <div className="grid gap-4 2xl:grid-cols-2">
          {rows.map((row, index) => (
            <article key={rowKeys[index]} className="rounded-[1.6rem] border border-amber-100 bg-canvas/90 p-4 shadow-sm">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <h3 className="text-base font-semibold text-ink">{getRowTitle(row, index)}</h3>
                  <p className="mt-1 text-xs text-slate-500">항목 #{index + 1}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {typeof row.status === "string" ? (
                    <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-600">
                      {formatDisplayValue(row.status, "status")}
                    </span>
                  ) : null}
                  {typeof row.provider_name === "string" ? (
                    <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-600">
                      {formatDisplayValue(row.provider_name, "provider_name")}
                    </span>
                  ) : null}
                  {typeof row.mode === "string" ? (
                    <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-600">
                      {formatDisplayValue(row.mode, "mode")}
                    </span>
                  ) : null}
                  {typeof row.protected === "boolean" ? (
                    <span
                      className={`rounded-full px-3 py-1 text-xs font-medium ${
                        row.protected ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"
                      }`}
                    >
                      {row.protected ? "보호됨" : "보호 확인 필요"}
                    </span>
                  ) : null}
                </div>
              </div>

              <dl className="mt-4 grid gap-3 md:grid-cols-2">
                {primary.map((column) => (
                  <div key={column} className="rounded-2xl border border-amber-100 bg-white px-4 py-3">
                    <dt className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                      {translateLabel(column)}
                    </dt>
                    <dd className="mt-2 min-w-0 text-sm leading-6 text-ink">{renderValue(row[column], column)}</dd>
                  </div>
                ))}
              </dl>

              {detail.length > 0 ? (
                <details className="mt-4 rounded-2xl border border-amber-200 bg-white">
                  <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-ink">
                    상세 payload 보기
                  </summary>
                  <div className="space-y-4 border-t border-amber-100 px-4 py-4">
                    {detail.map((column) => (
                      <div key={column} className="space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                          {translateLabel(column)}
                        </p>
                        {renderValue(row[column], column)}
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
