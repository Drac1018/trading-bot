export function PageShell({
  eyebrow,
  title,
  description,
  aside
}: {
  eyebrow: string;
  title: string;
  description: string;
  aside?: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white p-5 shadow-sm sm:p-6 lg:p-7">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">{eyebrow}</p>
          <h1 className="mt-3 text-2xl font-semibold leading-tight text-slate-950 sm:text-3xl lg:text-4xl">
            {title}
          </h1>
          <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-600 sm:text-base">{description}</p>
        </div>
        {aside ? <div className="flex flex-wrap items-center gap-3">{aside}</div> : null}
      </div>
    </section>
  );
}
