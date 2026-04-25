"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavIcon =
  | "home"
  | "wallet"
  | "market"
  | "brain"
  | "position"
  | "order"
  | "shield"
  | "clock"
  | "audit"
  | "settings"
  | "debug";

type NavItem = {
  href: string;
  label: string;
  icon: NavIcon;
};

const operatorItems: NavItem[] = [
  { href: "/", label: "운영 개요", icon: "home" },
  { href: "/dashboard/account", label: "계좌 / 잔고", icon: "wallet" },
  { href: "/dashboard/market", label: "시장 상태", icon: "market" },
  { href: "/dashboard/decisions", label: "AI 판단", icon: "brain" },
  { href: "/dashboard/positions", label: "포지션", icon: "position" },
  { href: "/dashboard/orders", label: "주문 내역", icon: "order" },
  { href: "/dashboard/risk", label: "안전 점검", icon: "shield" },
  { href: "/dashboard/scheduler", label: "자동 실행", icon: "clock" },
  { href: "/dashboard/audit", label: "감사 기록", icon: "audit" },
  { href: "/dashboard/settings", label: "설정", icon: "settings" },
];

const debugItems: NavItem[] = [
  { href: "/ui-example", label: "UI 개선 예시", icon: "debug" },
  { href: "/dashboard/agents", label: "고급 디버그", icon: "debug" },
];

function itemClass(active: boolean) {
  return active
    ? "border-blue-600 bg-blue-600 text-white shadow-sm"
    : "border-transparent bg-white text-slate-700 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700";
}

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === href : pathname.startsWith(href);
}

function Icon({ name, className = "h-5 w-5" }: { name: NavIcon; className?: string }) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 2,
  };

  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      {name === "home" ? <path {...common} d="m4 11 8-7 8 7v9a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1v-9Z" /> : null}
      {name === "wallet" ? <path {...common} d="M4 7h16v12H4zM16 11h4M7 7V5h10v2" /> : null}
      {name === "market" ? <path {...common} d="M4 19V5M4 19h16M8 15l3-4 3 2 4-7" /> : null}
      {name === "brain" ? <path {...common} d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0 0 6v1a3 3 0 0 0 5 2.2M15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 0 6v1a3 3 0 0 1-5 2.2M12 5v14" /> : null}
      {name === "position" ? <path {...common} d="M11 3v9h9A9 9 0 1 1 11 3Z" /> : null}
      {name === "position" ? <path {...common} d="M14 3.5A9 9 0 0 1 20.5 10H14V3.5Z" /> : null}
      {name === "order" ? <path {...common} d="M7 4h10l3 3v13H4V4h3ZM8 10h8M8 14h8M8 18h5" /> : null}
      {name === "shield" ? <path {...common} d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6l-7-3Z" /> : null}
      {name === "shield" ? <path {...common} d="m9 12 2 2 4-5" /> : null}
      {name === "clock" ? <circle {...common} cx="12" cy="12" r="9" /> : null}
      {name === "clock" ? <path {...common} d="M12 7v5l3 2" /> : null}
      {name === "audit" ? <path {...common} d="M8 4h8l2 2v14H6V6l2-2ZM9 10h6M9 14h6M9 18h4" /> : null}
      {name === "settings" ? <path {...common} d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z" /> : null}
      {name === "settings" ? <path {...common} d="M4 12h2m12 0h2M12 4v2m0 12v2m5.7-13.7-1.4 1.4M7.7 16.3l-1.4 1.4m0-11.4 1.4 1.4m8.6 8.6 1.4 1.4" /> : null}
      {name === "debug" ? <path {...common} d="M8 8h8v8H8zM4 12h4m8 0h4M12 4v4m0 8v4" /> : null}
    </svg>
  );
}

function NavItemLinks({ items, pathname, compact = false }: { items: NavItem[]; pathname: string; compact?: boolean }) {
  if (compact) {
    return (
      <div className="flex flex-wrap gap-2">
        {items.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`inline-flex h-10 items-center gap-2 whitespace-nowrap rounded-md border px-3 text-sm font-semibold transition ${itemClass(
              isActive(pathname, item.href),
            )}`}
          >
            <Icon name={item.icon} className="h-4 w-4" />
            {item.label}
          </Link>
        ))}
      </div>
    );
  }

  return (
    <div className="grid gap-2">
      {items.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          className={`inline-flex h-11 items-center gap-3 rounded-md border px-3 text-sm font-semibold transition ${itemClass(
            isActive(pathname, item.href),
          )}`}
        >
          <Icon name={item.icon} className="h-5 w-5 shrink-0" />
          {item.label}
        </Link>
      ))}
    </div>
  );
}

export function AppNav() {
  const pathname = usePathname();

  return (
    <nav className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
      <div className="rounded-md bg-slate-950 px-4 py-5 text-white">
        <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-white/60">운영 콘솔</p>
        <h1 className="mt-3 text-xl font-semibold leading-tight">실거래 운영 대시보드</h1>
        <p className="mt-3 text-sm leading-6 text-white/70">
          상태 확인, 안전 점검, 감사 추적을 같은 구조로 정리합니다.
        </p>
      </div>

      <div className="mt-4 lg:hidden">
        <p className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">운영 메뉴</p>
        <div className="-mx-1 overflow-x-auto pb-1">
          <div className="w-max px-1">
            <NavItemLinks items={operatorItems} pathname={pathname} compact />
          </div>
        </div>

        <div className="mt-4 px-1">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">디버그</p>
          <NavItemLinks items={debugItems} pathname={pathname} compact />
        </div>
      </div>

      <div className="mt-4 hidden lg:block">
        <NavItemLinks items={operatorItems} pathname={pathname} />
      </div>

      <div className="mt-5 hidden border-t border-slate-200 pt-4 lg:block">
        <p className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.28em] text-slate-500">디버그</p>
        <NavItemLinks items={debugItems} pathname={pathname} />
      </div>
    </nav>
  );
}
