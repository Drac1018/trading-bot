"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { AlertNotifier } from "./alert-notifier";

type ChromeIcon =
  | "shield"
  | "home"
  | "list"
  | "wallet"
  | "market"
  | "brain"
  | "pie"
  | "order"
  | "clock"
  | "settings"
  | "bell"
  | "user"
  | "debug";

type ChromeNavItem = {
  href: string;
  label: string;
  icon?: ChromeIcon;
};

type ChromeNavGroup = {
  label: string;
  items: ChromeNavItem[];
};

const navGroups: ChromeNavGroup[] = [
  {
    label: "운영",
    items: [
      { href: "/", label: "운영 개요", icon: "home" },
      { href: "/dashboard/operations", label: "운영 판단", icon: "brain" },
      { href: "/dashboard/trading", label: "거래 상태", icon: "order" },
      { href: "/dashboard/market", label: "시장 상태", icon: "market" },
    ],
  },
  {
    label: "분석 / 추적",
    items: [
      { href: "/dashboard/analytics", label: "비용 분석", icon: "pie" },
      { href: "/dashboard/audit", label: "감사 / 디버그", icon: "list" },
    ],
  },
  {
    label: "설정",
    items: [
      { href: "/dashboard/settings", label: "설정", icon: "settings" },
    ],
  },
];

const navItems = navGroups.flatMap((group) => group.items);

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === href : pathname.startsWith(href);
}

function navItemClass(active: boolean) {
  return active ? "bg-blue-50 text-blue-700" : "text-slate-700 hover:bg-slate-50";
}

function Icon({ name, className = "h-5 w-5" }: { name: ChromeIcon; className?: string }) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 2,
  };

  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      {name === "shield" ? <path {...common} d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6l-7-3Z" /> : null}
      {name === "shield" ? <path {...common} d="m9 12 2 2 4-5" /> : null}
      {name === "home" ? <path {...common} d="m4 11 8-7 8 7v9a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1v-9Z" /> : null}
      {name === "list" ? <path {...common} d="M9 7h11M9 12h11M9 17h11M4 7h.01M4 12h.01M4 17h.01" /> : null}
      {name === "wallet" ? <path {...common} d="M4 7h16v12H4zM16 11h4M7 7V5h10v2" /> : null}
      {name === "market" ? <path {...common} d="M4 19V5M4 19h16M8 15l3-4 3 2 4-7" /> : null}
      {name === "brain" ? <path {...common} d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0 0 6v1a3 3 0 0 0 5 2.2M15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 0 6v1a3 3 0 0 1-5 2.2M12 5v14" /> : null}
      {name === "pie" ? <path {...common} d="M11 3v9h9A9 9 0 1 1 11 3Z" /> : null}
      {name === "pie" ? <path {...common} d="M14 3.5A9 9 0 0 1 20.5 10H14V3.5Z" /> : null}
      {name === "order" ? <path {...common} d="M7 4h10l3 3v13H4V4h3ZM8 10h8M8 14h8M8 18h5" /> : null}
      {name === "clock" ? <circle {...common} cx="12" cy="12" r="9" /> : null}
      {name === "clock" ? <path {...common} d="M12 7v5l3 2" /> : null}
      {name === "settings" ? <path {...common} d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z" /> : null}
      {name === "settings" ? <path {...common} d="M4 12h2m12 0h2M12 4v2m0 12v2m5.7-13.7-1.4 1.4M7.7 16.3l-1.4 1.4m0-11.4 1.4 1.4m8.6 8.6 1.4 1.4" /> : null}
      {name === "bell" ? <path {...common} d="M18 16H6l1.5-2.5V10a4.5 4.5 0 0 1 9 0v3.5L18 16ZM10 19h4" /> : null}
      {name === "user" ? <circle {...common} cx="12" cy="8" r="3" /> : null}
      {name === "user" ? <path {...common} d="M5 20a7 7 0 0 1 14 0" /> : null}
      {name === "debug" ? <path {...common} d="M8 8h8v8H8zM4 12h4m8 0h4M12 4v4m0 8v4" /> : null}
    </svg>
  );
}

function SideNavGroup({
  items,
  pathname,
}: {
  items: ChromeNavItem[];
  pathname: string;
}) {
  return (
    <div className="space-y-2">
      {items.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          className={`flex h-12 items-center gap-3 rounded-md px-4 text-sm font-semibold transition ${navItemClass(
            isActive(pathname, item.href),
          )}`}
        >
          {item.icon ? <Icon name={item.icon} className="h-5 w-5 shrink-0" /> : null}
          <span className="truncate">{item.label}</span>
        </Link>
      ))}
    </div>
  );
}

function MobileNav({ groups, pathname }: { groups: ChromeNavGroup[]; pathname: string }) {
  const [open, setOpen] = useState(false);
  const currentItem = navItems.find((item) => isActive(pathname, item.href)) ?? {
    href: "/",
    label: "운영 개요",
    icon: "home" as const,
  };

  return (
    <nav className="border-b border-slate-200 bg-white lg:hidden" aria-label="모바일 메뉴">
      <div className="mx-auto max-w-[1680px] px-4 py-3 sm:px-6">
        <button
          type="button"
          aria-expanded={open}
          aria-controls="mobile-nav-panel"
          onClick={() => setOpen((value) => !value)}
          className="flex min-h-11 w-full items-center justify-between gap-3 rounded-md border border-slate-200 bg-white px-4 text-left text-sm font-semibold text-slate-900 shadow-sm"
        >
          <span className="flex min-w-0 items-center gap-3">
            {currentItem.icon ? <Icon name={currentItem.icon} className="h-4 w-4 shrink-0 text-blue-600" /> : null}
            <span className="truncate">현재 화면: {currentItem.label}</span>
          </span>
          <span aria-hidden="true" className="text-slate-500">
            {open ? "닫기" : "메뉴"}
          </span>
        </button>

        {open ? (
          <div id="mobile-nav-panel" className="mt-3 space-y-4">
            {groups.map((group) => (
              <div key={group.label}>
                <p className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-500">
                  {group.label}
                </p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {group.items.map((item) => (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setOpen(false)}
                      className={`flex min-h-11 items-center gap-3 rounded-md border border-slate-200 px-4 text-sm font-semibold transition ${navItemClass(
                        isActive(pathname, item.href),
                      )}`}
                    >
                      {item.icon ? <Icon name={item.icon} className="h-4 w-4 shrink-0" /> : null}
                      <span>{item.label}</span>
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </nav>
  );
}

export function AppChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen bg-[#f7f9fc] text-slate-950">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-20 max-w-[1680px] items-center gap-6 px-4 sm:px-6 lg:px-8">
          <Link href="/" className="flex min-w-fit items-center gap-3">
            <span className="flex h-12 w-12 items-center justify-center rounded-md bg-blue-50 text-blue-600">
              <Icon name="shield" className="h-8 w-8" />
            </span>
            <span>
              <span className="block text-[11px] font-semibold tracking-[0.28em] text-slate-400">LIVE</span>
              <span className="block text-xl font-semibold tracking-[-0.02em] text-slate-950 sm:text-2xl">
                거래 안전 콘솔
              </span>
            </span>
          </Link>

          <div className="ml-auto" aria-hidden="true" />
        </div>
      </header>
      <MobileNav groups={navGroups} pathname={pathname} />

      <div className="mx-auto grid max-w-[1680px] gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[230px_minmax(0,1fr)] lg:px-8">
        <aside className="hidden lg:block">
          <nav className="sticky top-28 rounded-lg border border-slate-200 bg-white p-4 shadow-sm" aria-label="좌측 메뉴">
            {navGroups.map((group, index) => (
              <div key={group.label} className={index === 0 ? "" : "mt-5 border-t border-slate-200 pt-4"}>
                <p className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-500">
                  {group.label}
                </p>
                <SideNavGroup items={group.items} pathname={pathname} />
              </div>
            ))}
          </nav>
        </aside>
        <main className="min-w-0 space-y-6 pb-8 lg:pb-10">{children}</main>
      </div>
      <AlertNotifier />
    </div>
  );
}
