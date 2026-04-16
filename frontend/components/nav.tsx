"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = {
  href: string;
  label: string;
};

const operatorItems: NavItem[] = [
  { href: "/", label: "운영 개요" },
  { href: "/dashboard/account", label: "거래소 계정" },
  { href: "/dashboard/market", label: "시장 / 신호 입력" },
  { href: "/dashboard/decisions", label: "의사결정" },
  { href: "/dashboard/positions", label: "포지션" },
  { href: "/dashboard/orders", label: "주문 / 체결" },
  { href: "/dashboard/risk", label: "리스크" },
  { href: "/dashboard/scheduler", label: "스케줄러 상태" },
  { href: "/dashboard/audit", label: "감사 로그" },
  { href: "/dashboard/settings", label: "설정" },
];

const debugItems: NavItem[] = [{ href: "/dashboard/agents", label: "에이전트 디버그" }];

function itemClass(active: boolean) {
  return active
    ? "border-amber-400 bg-amber-100 text-ink shadow-sm"
    : "border-transparent bg-white/60 text-ink hover:border-amber-300 hover:bg-amber-50";
}

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === href : pathname.startsWith(href);
}

function NavItemLinks({ items, pathname, compact = false }: { items: NavItem[]; pathname: string; compact?: boolean }) {
  if (compact) {
    return (
      <div className="flex flex-wrap gap-2">
        {items.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`whitespace-nowrap rounded-full border px-4 py-2 text-sm font-medium transition ${itemClass(
              isActive(pathname, item.href),
            )}`}
          >
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
          className={`rounded-2xl border px-4 py-3 text-sm font-medium transition ${itemClass(
            isActive(pathname, item.href),
          )}`}
        >
          {item.label}
        </Link>
      ))}
    </div>
  );
}

export function AppNav() {
  const pathname = usePathname();

  return (
    <nav className="rounded-[2rem] border border-amber-300/60 bg-panel/95 p-3 shadow-frame backdrop-blur">
      <div className="rounded-[1.6rem] bg-ink px-4 py-5 text-canvas sm:px-5 sm:py-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.34em] text-canvas/70">운영 콘솔</p>
        <h1 className="mt-3 font-display text-2xl leading-tight sm:text-[2rem]">실거래 운영 대시보드</h1>
        <p className="mt-3 text-sm leading-6 text-canvas/80">
          운영 핵심 메뉴는 상태 확인과 제어에 집중하고, 디버그성 화면은 별도로 분리합니다.
        </p>
      </div>

      <div className="mt-4 lg:hidden">
        <p className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.32em] text-slate-500">운영 메뉴</p>
        <div className="-mx-1 overflow-x-auto pb-1">
          <div className="w-max px-1">
            <NavItemLinks items={operatorItems} pathname={pathname} compact />
          </div>
        </div>

        <div className="mt-4 px-1">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.32em] text-slate-500">디버그</p>
          <NavItemLinks items={debugItems} pathname={pathname} compact />
        </div>
      </div>

      <div className="mt-4 hidden lg:block">
        <NavItemLinks items={operatorItems} pathname={pathname} />
      </div>

      <div className="mt-5 hidden border-t border-amber-200/70 pt-4 lg:block">
        <p className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.32em] text-slate-500">디버그</p>
        <NavItemLinks items={debugItems} pathname={pathname} />
      </div>
    </nav>
  );
}
