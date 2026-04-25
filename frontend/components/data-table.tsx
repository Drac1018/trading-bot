import {
  formatDisplayValue,
  formatListValue,
  getRowTitle as defaultGetRowTitle,
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
            className="rounded-md border border-blue-100 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-800"
          >
            {item}
          </span>
        ))}
      </div>
    );
  }

  if (typeof value === "object") {
    return (
      <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-slate-950 p-4 text-xs leading-6 text-slate-100">
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
  rowTitleFormatter,
  labelOverrides,
}: {
  title: string;
  description: string;
  rows: TableRow[];
  emptyStateTitle?: string;
  emptyStateDescription?: string;
  hiddenColumns?: string[];
  rowTitleFormatter?: (row: TableRow, index: number) => string;
  labelOverrides?: Partial<Record<string, string>>;
}) {
  const { primary, detail } = splitTableColumns(rows, new Set(hiddenColumns));
  const rowKeys = buildTableRowKeys(rows);
  const getLabel = (column: string) => labelOverrides?.[column] ?? translateLabel(column);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">{description}</p>
          <h2 className="mt-1 text-xl font-semibold text-slate-950 sm:text-2xl">{title}</h2>
        </div>
        <div className="w-fit rounded-md bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
          {rows.length}건
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-sm text-slate-500">
          <p className="font-semibold text-slate-700">{emptyStateTitle ?? "표시할 데이터가 없습니다."}</p>
          <p className="mt-2 leading-6">{emptyStateDescription ?? "현재 조건에 맞는 항목이 아직 없습니다."}</p>
        </div>
      ) : (
        <div className="grid gap-4 2xl:grid-cols-2">
          {rows.map((row, index) => (
            <article key={rowKeys[index]} className="rounded-lg border border-slate-200 bg-slate-50 p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <h3 className="text-base font-semibold text-slate-950">
                    {rowTitleFormatter ? rowTitleFormatter(row, index) : defaultGetRowTitle(row, index)}
                  </h3>
                  <p className="mt-1 text-xs text-slate-500">항목 #{index + 1}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {typeof row.status === "string" ? (
                    <span className="rounded-md bg-white px-3 py-1 text-xs font-medium text-slate-600">
                      {formatDisplayValue(row.status, "status")}
                    </span>
                  ) : null}
                  {typeof row.provider_name === "string" ? (
                    <span className="rounded-md bg-white px-3 py-1 text-xs font-medium text-slate-600">
                      {formatDisplayValue(row.provider_name, "provider_name")}
                    </span>
                  ) : null}
                  {typeof row.mode === "string" ? (
                    <span className="rounded-md bg-white px-3 py-1 text-xs font-medium text-slate-600">
                      {formatDisplayValue(row.mode, "mode")}
                    </span>
                  ) : null}
                  {typeof row.protected === "boolean" ? (
                    <span
                      className={`rounded-md px-3 py-1 text-xs font-medium ${
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
                  <div key={column} className="rounded-md border border-slate-200 bg-white px-4 py-3">
                    <dt className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                      {getLabel(column)}
                    </dt>
                    <dd className="mt-2 min-w-0 text-sm leading-6 text-slate-900">{renderValue(row[column], column)}</dd>
                  </div>
                ))}
              </dl>

              {detail.length > 0 ? (
                <details className="mt-4 rounded-md border border-slate-200 bg-white">
                  <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-slate-950">
                    상세 payload 보기
                  </summary>
                  <div className="space-y-4 border-t border-slate-200 px-4 py-4">
                    {detail.map((column) => (
                      <div key={column} className="space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
                          {getLabel(column)}
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
