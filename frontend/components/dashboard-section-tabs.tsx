import Link from "next/link";

export type DashboardSectionTab = {
  value: string;
  label: string;
  description: string;
  href: string;
};

export function DashboardSectionTabs({
  tabs,
  active,
}: {
  tabs: DashboardSectionTab[];
  active: string;
}) {
  const activeTab = tabs.find((tab) => tab.value === active) ?? tabs[0];

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
      <div className="flex flex-wrap gap-2">
        {tabs.map((tab) => {
          const selected = tab.value === active;
          return (
            <Link
              key={tab.value}
              href={tab.href}
              className={`inline-flex min-h-10 items-center rounded-md border px-4 text-sm font-semibold transition ${
                selected
                  ? "border-blue-600 bg-blue-600 text-white"
                  : "border-slate-200 bg-white text-slate-700 hover:border-blue-200 hover:bg-blue-50"
              }`}
            >
              {tab.label}
            </Link>
          );
        })}
      </div>
      <p className="mt-3 text-sm leading-6 text-slate-600">{activeTab?.description}</p>
    </section>
  );
}
