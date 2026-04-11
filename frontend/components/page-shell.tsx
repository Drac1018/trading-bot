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
    <section className="overflow-hidden rounded-[2rem] border border-amber-200/70 bg-white/85 p-5 shadow-frame sm:rounded-[2.5rem] sm:p-7 lg:p-8">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.34em] text-slate-500">{eyebrow}</p>
          <h1 className="mt-3 font-display text-3xl leading-tight text-ink sm:text-4xl lg:text-5xl">
            {title}
          </h1>
          <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-600 sm:text-base">{description}</p>
        </div>
        {aside ? <div className="flex flex-wrap items-center gap-3">{aside}</div> : null}
      </div>
    </section>
  );
}
