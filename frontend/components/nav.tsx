"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  { href: "/", label: "운영 개요" },
  { href: "/dashboard/account", label: "거래소 계정" },
  { href: "/dashboard/market", label: "시장 / 신호" },
  { href: "/dashboard/decisions", label: "의사결정" },
  { href: "/dashboard/positions", label: "포지션" },
  { href: "/dashboard/orders", label: "주문 / 체결" },
  { href: "/dashboard/risk", label: "리스크" },
  { href: "/dashboard/agents", label: "에이전트" },
  { href: "/dashboard/scheduler", label: "스케줄러" },
  { href: "/dashboard/audit", label: "감사 로그" },
  { href: "/dashboard/settings", label: "설정" },
  { href: "/dashboard/backlog", label: "개선 백로그" },
];

function itemClass(active: boolean) {
  return active
    ? "border-amber-400 bg-amber-100 text-ink shadow-sm"
    : "border-transparent bg-white/60 text-ink hover:border-amber-300 hover:bg-amber-50";
}

export function AppNav() {
  const pathname = usePathname();

  return (
    <nav className="rounded-[2rem] border border-amber-300/60 bg-panel/95 p-3 shadow-frame backdrop-blur">
      <div className="rounded-[1.6rem] bg-ink px-4 py-5 text-canvas sm:px-5 sm:py-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.34em] text-canvas/70">운영 콘솔</p>
        <h1 className="mt-3 font-display text-2xl leading-tight sm:text-[2rem]">실거래 운영 대시보드</h1>
        <p className="mt-3 text-sm leading-6 text-canvas/80">
          실거래 상태, 리스크 차단, 수동 승인, 감사 로그를 한 화면에서 운영합니다.
        </p>
      </div>

      <div className="mt-4 lg:hidden">
        <p className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.32em] text-slate-500">빠른 이동</p>
        <div className="-mx-1 overflow-x-auto pb-1">
          <div className="flex w-max gap-2 px-1">
            {items.map((item) => {
              const active = item.href === "/" ? pathname === item.href : pathname.startsWith(item.href);
              return (
                <Link key={item.href} href={item.href} className={`whitespace-nowrap rounded-full border px-4 py-2 text-sm font-medium transition ${itemClass(active)}`}>
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>
      </div>

      <div className="mt-4 hidden gap-2 lg:grid">
        {items.map((item) => {
          const active = item.href === "/" ? pathname === item.href : pathname.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href} className={`rounded-2xl border px-4 py-3 text-sm font-medium transition ${itemClass(active)}`}>
              {item.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
