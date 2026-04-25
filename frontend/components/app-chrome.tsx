"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

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

const topNav: ChromeNavItem[] = [
  { href: "/", label: "대시보드" },
  { href: "/#today", label: "오늘 할 일" },
  { href: "/dashboard/positions", label: "포지션" },
  { href: "/dashboard/orders", label: "주문 / 체결" },
  { href: "/dashboard/audit", label: "감사 로그" },
  { href: "/dashboard/settings", label: "설정" },
];

const sideNav: ChromeNavItem[] = [
  { href: "/", label: "대시보드", icon: "home" },
  { href: "/#today", label: "오늘 할 일", icon: "list" },
  { href: "/dashboard/account", label: "계좌 / 잔고", icon: "wallet" },
  { href: "/dashboard/market", label: "시장 상태", icon: "market" },
  { href: "/dashboard/decisions", label: "AI 판단", icon: "brain" },
  { href: "/dashboard/positions", label: "포지션", icon: "pie" },
  { href: "/dashboard/orders", label: "주문 / 체결", icon: "order" },
  { href: "/dashboard/risk", label: "안전 점검", icon: "shield" },
  { href: "/dashboard/scheduler", label: "자동 실행", icon: "clock" },
  { href: "/dashboard/audit", label: "감사 로그", icon: "clock" },
  { href: "/dashboard/settings", label: "설정", icon: "settings" },
];

const debugNav: ChromeNavItem[] = [
  { href: "/ui-example", label: "UI 개선 예시", icon: "debug" },
  { href: "/dashboard/agents", label: "고급 디버그", icon: "debug" },
];

function isActive(pathname: string, href: string) {
  if (href === "/#today") {
    return false;
  }
  return href === "/" ? pathname === href : pathname.startsWith(href);
}

function navItemClass(active: boolean) {
  return active ? "bg-blue-50 text-blue-700" : "text-slate-700 hover:bg-slate-50";
}

function topNavItemClass(active: boolean) {
  return active ? "border-b-2 border-blue-600 text-blue-700" : "text-slate-700 hover:bg-slate-50";
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

export function AppChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  if (pathname === "/ui-example") {
    return (
      <div className="min-h-screen bg-slate-50 text-slate-950">
        {children}
      </div>
    );
  }

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

          <nav className="hidden min-w-0 flex-1 items-center justify-center gap-2 lg:flex" aria-label="상단 메뉴">
            {topNav.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-md px-4 py-3 text-sm font-semibold transition xl:px-5 xl:text-base ${topNavItemClass(
                  isActive(pathname, item.href),
                )}`}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <Link
              href="/dashboard/audit"
              aria-label="감사 로그"
              className="flex h-11 w-11 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 transition hover:bg-slate-50"
            >
              <Icon name="bell" />
            </Link>
            <Link
              href="/dashboard/settings"
              className="hidden h-11 items-center gap-2 rounded-md border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-800 transition hover:bg-slate-50 sm:flex"
            >
              <Icon name="user" />
              운영자
            </Link>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1680px] gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[230px_minmax(0,1fr)] lg:px-8">
        <aside className="hidden lg:block">
          <nav className="sticky top-28 rounded-lg border border-slate-200 bg-white p-4 shadow-sm" aria-label="좌측 메뉴">
            <SideNavGroup items={sideNav} pathname={pathname} />
            <div className="mt-5 border-t border-slate-200 pt-4">
              <p className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">디버그</p>
              <SideNavGroup items={debugNav} pathname={pathname} />
            </div>
          </nav>
        </aside>
        <main className="min-w-0 space-y-6 pb-8 lg:pb-10">{children}</main>
      </div>
      <AlertNotifier />
    </div>
  );
}
