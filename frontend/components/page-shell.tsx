export function PageShell({
  eyebrow,
  title,
  description,
  aside,
  compact = false,
}: {
  eyebrow: string;
  title: string;
  description: string;
  aside?: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <section
      className={`overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm ${
        compact ? "p-4" : "p-4 sm:p-5"
      }`}
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">{eyebrow}</p>
          <h1
            className={`mt-2 font-semibold leading-tight text-slate-950 ${
              compact ? "text-xl sm:text-2xl" : "text-2xl sm:text-3xl"
            }`}
          >
            {title}
          </h1>
          <p className={`${compact ? "mt-2" : "mt-3"} max-w-3xl text-sm leading-6 text-slate-600`}>{description}</p>
        </div>
        {aside ? <div className="flex flex-wrap items-center gap-3">{aside}</div> : null}
      </div>
    </section>
  );
}
