"use client";

import { useState } from "react";

type Tone = "safe" | "warn" | "danger" | "neutral" | "info";
type TabId = "today" | "positions" | "audit";

type ActionItem = {
  id: string;
  title: string;
  detail: string;
  symbol: string;
  priority: "높음" | "보통";
};

const topNav = ["대시보드", "오늘 할 일", "포지션", "감사 로그", "설정"];
const sideNav = ["대시보드", "오늘 할 일", "포지션", "보호 설정", "감사 로그", "설정"];

const tabs: { id: TabId; label: string }[] = [
  { id: "today", label: "오늘 할 일" },
  { id: "positions", label: "포지션" },
  { id: "audit", label: "감사 로그" },
];

const actionItems: ActionItem[] = [
  {
    id: "btc-protection",
    title: "BTCUSDT 보호주문 미확정",
    detail: "손절 주문이 거래소에 남아 있는지 확인이 필요합니다.",
    symbol: "BTCUSDT",
    priority: "높음",
  },
  {
    id: "eth-threshold",
    title: "ETHUSDT 부분 청산 임계치 근접",
    detail: "정리 주문은 가능하지만 신규 진입은 계속 막습니다.",
    symbol: "ETHUSDT",
    priority: "보통",
  },
];

const safetyRows = [
  { label: "거래 일시정지", value: "해제", tone: "safe" as const },
  { label: "신규 진입", value: "차단", tone: "danger" as const },
  { label: "보호주문 상태", value: "확인 중", tone: "warn" as const },
  { label: "거래소 연결", value: "정상", tone: "safe" as const },
];

const positionRows = [
  {
    symbol: "BTCUSDT",
    side: "롱",
    size: "0.031 BTC",
    protection: "확인 중",
    exposure: "18,420 USDT",
    next: "보호주문 확인",
    tone: "warn" as const,
  },
  {
    symbol: "ETHUSDT",
    side: "롱",
    size: "0.82 ETH",
    protection: "정상",
    exposure: "6,710 USDT",
    next: "부분 청산 기준 관찰",
    tone: "safe" as const,
  },
  {
    symbol: "SOLUSDT",
    side: "없음",
    size: "-",
    protection: "해당 없음",
    exposure: "0 USDT",
    next: "신규 진입 대기",
    tone: "neutral" as const,
  },
];

const auditEvents = [
  {
    time: "09:41",
    title: "신규 진입 차단 유지",
    detail: "보호주문 확인이 끝나기 전까지 새 주문을 보내지 않습니다.",
    tone: "danger" as const,
  },
  {
    time: "09:38",
    title: "ETHUSDT 보호주문 확인 완료",
    detail: "손절 주문과 익절 주문이 모두 확인되었습니다.",
    tone: "safe" as const,
  },
  {
    time: "09:35",
    title: "BTCUSDT 보호주문 확인 요청",
    detail: "거래소 미체결 주문과 내부 포지션 상태를 다시 맞춥니다.",
    tone: "warn" as const,
  },
  {
    time: "09:29",
    title: "거래 일시정지 해제",
    detail: "해제 후에도 보호주문 확인 전까지 신규 진입은 막습니다.",
    tone: "info" as const,
  },
];

function toneClass(tone: Tone) {
  return {
    safe: "border-emerald-200 bg-emerald-50 text-emerald-800",
    warn: "border-amber-200 bg-amber-50 text-amber-900",
    danger: "border-rose-200 bg-rose-50 text-rose-800",
    neutral: "border-slate-200 bg-slate-50 text-slate-700",
    info: "border-blue-200 bg-blue-50 text-blue-800",
  }[tone];
}

function dotClass(tone: Tone) {
  return {
    safe: "bg-emerald-500",
    warn: "bg-amber-500",
    danger: "bg-rose-500",
    neutral: "bg-slate-400",
    info: "bg-blue-500",
  }[tone];
}

function Icon({
  name,
  className = "h-5 w-5",
}: {
  name: "shield" | "home" | "list" | "pie" | "clock" | "settings" | "bell" | "user" | "swap" | "pulse";
  className?: string;
}) {
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
      {name === "pie" ? <path {...common} d="M11 3v9h9A9 9 0 1 1 11 3Z" /> : null}
      {name === "pie" ? <path {...common} d="M14 3.5A9 9 0 0 1 20.5 10H14V3.5Z" /> : null}
      {name === "clock" ? <circle {...common} cx="12" cy="12" r="9" /> : null}
      {name === "clock" ? <path {...common} d="M12 7v5l3 2" /> : null}
      {name === "settings" ? <path {...common} d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z" /> : null}
      {name === "settings" ? <path {...common} d="M4 12h2m12 0h2M12 4v2m0 12v2m5.7-13.7-1.4 1.4M7.7 16.3l-1.4 1.4m0-11.4 1.4 1.4m8.6 8.6 1.4 1.4" /> : null}
      {name === "bell" ? <path {...common} d="M18 16H6l1.5-2.5V10a4.5 4.5 0 0 1 9 0v3.5L18 16ZM10 19h4" /> : null}
      {name === "user" ? <circle {...common} cx="12" cy="8" r="3" /> : null}
      {name === "user" ? <path {...common} d="M5 20a7 7 0 0 1 14 0" /> : null}
      {name === "swap" ? <path {...common} d="M17 4l4 4-4 4M21 8H7m0 12-4-4 4-4M3 16h14" /> : null}
      {name === "pulse" ? <path {...common} d="M4 12h4l2-6 4 12 2-6h4" /> : null}
    </svg>
  );
}

function StatusPill({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-semibold ${toneClass(tone)}`}>
      <span className={`h-2 w-2 rounded-full ${dotClass(tone)}`} />
      {children}
    </span>
  );
}

function Panel({
  title,
  children,
  action,
  className = "",
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white p-6 shadow-sm ${className}`}>
      <div className="flex min-h-9 items-center justify-between gap-3">
        <h2 className="text-xl font-semibold tracking-[-0.01em] text-slate-950">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function TabButton({
  active,
  label,
  testId,
  onClick,
}: {
  active: boolean;
  label: string;
  testId: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className={`h-11 rounded-md border px-5 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
        active
          ? "border-blue-600 bg-blue-600 text-white"
          : "border-slate-200 bg-white text-slate-700 hover:border-blue-200 hover:bg-blue-50"
      }`}
    >
      {label}
    </button>
  );
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-3 overflow-hidden rounded-md bg-slate-100" aria-label={`보호 여유 ${value}%`}>
      <div className="h-full rounded-md bg-emerald-500" style={{ width: `${value}%` }} />
    </div>
  );
}

export function OperatorFriendlyPreview() {
  const [activeTab, setActiveTab] = useState<TabId>("today");
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const remainingActions = actionItems.filter((item) => !checkedIds.includes(item.id));

  const toggleChecked = (id: string) => {
    setCheckedIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  };

  return (
    <div className="min-h-screen bg-[#f7f9fc]">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-20 max-w-[1680px] items-center gap-6 px-4 sm:px-6 lg:px-8">
          <div className="flex min-w-fit items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-md bg-blue-50 text-blue-600">
              <Icon name="shield" className="h-8 w-8" />
            </div>
            <div>
              <p className="text-[11px] font-semibold tracking-[0.28em] text-slate-400">PREVIEW</p>
              <p className="text-2xl font-semibold tracking-[-0.02em] text-slate-950">거래 안전 콘솔</p>
            </div>
          </div>

          <nav className="hidden min-w-0 flex-1 items-center justify-center gap-2 lg:flex" aria-label="예시 상단 메뉴">
            {topNav.map((item) => (
              <a
                key={item}
                href="#"
                className={`rounded-md px-5 py-3 text-base font-semibold ${
                  item === "대시보드"
                    ? "border-b-2 border-blue-600 text-blue-700"
                    : "text-slate-700 hover:bg-slate-50"
                }`}
              >
                {item}
              </a>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <button
              type="button"
              aria-label="알림"
              className="flex h-11 w-11 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600"
            >
              <Icon name="bell" />
            </button>
            <button
              type="button"
              className="hidden h-11 items-center gap-2 rounded-md border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-800 sm:flex"
            >
              <Icon name="user" />
              운영자
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1680px] gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[230px_minmax(0,1fr)] lg:px-8">
        <aside className="hidden lg:block">
          <nav className="sticky top-28 space-y-2 rounded-lg border border-slate-200 bg-white p-4 shadow-sm" aria-label="예시 좌측 메뉴">
            {sideNav.map((item, index) => {
              const active = item === "대시보드";
              const icons = ["home", "list", "pie", "shield", "clock", "settings"] as const;
              return (
                <a
                  key={item}
                  href="#"
                  className={`flex h-12 items-center gap-3 rounded-md px-4 text-sm font-semibold ${
                    active ? "bg-blue-50 text-blue-700" : "text-slate-700 hover:bg-slate-50"
                  }`}
                >
                  <Icon name={icons[index]} className="h-5 w-5" />
                  {item}
                </a>
              );
            })}
          </nav>
        </aside>

        <main className="min-w-0 space-y-6">
          <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)] xl:items-center">
              <div className="flex min-w-0 items-center gap-5">
                <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-full bg-rose-50 text-rose-700">
                  <Icon name="shield" className="h-11 w-11" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-500">현재 거래 상태</p>
                  <h1 data-testid="ui-example-title" className="mt-2 text-3xl font-semibold tracking-[-0.03em] text-rose-700 sm:text-4xl">
                    신규 진입 차단
                  </h1>
                  <p className="mt-2 text-sm leading-6 text-slate-600">
                    보호주문 확인이 끝날 때까지 새 포지션은 열지 않습니다.
                  </p>
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-3">
                <div className="border-t border-slate-100 pt-4 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
                  <div className="flex items-center gap-3 text-slate-500">
                    <Icon name="clock" />
                    <span className="text-sm">현재 시간</span>
                  </div>
                  <p className="mt-2 text-base font-semibold text-slate-950">09:41:23 KST</p>
                </div>
                <div className="border-t border-slate-100 pt-4 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
                  <div className="flex items-center gap-3 text-slate-500">
                    <Icon name="pulse" />
                    <span className="text-sm">봇 상태</span>
                  </div>
                  <p className="mt-2 text-base font-semibold text-emerald-700">정상</p>
                </div>
                <div className="border-t border-slate-100 pt-4 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
                  <div className="flex items-center gap-3 text-slate-500">
                    <Icon name="swap" />
                    <span className="text-sm">거래소 연결</span>
                  </div>
                  <p className="mt-2 text-base font-semibold text-emerald-700">정상</p>
                </div>
              </div>
            </div>
          </section>

          <div className="grid gap-6 xl:grid-cols-[320px_minmax(330px,1fr)_360px]">
            <Panel title="안전 상태" className="xl:min-h-[510px]">
              <div className="mt-6 flex flex-col items-center text-center">
                <div className="flex h-36 w-36 items-center justify-center rounded-full bg-emerald-50 text-emerald-700">
                  <Icon name="shield" className="h-20 w-20" />
                </div>
                <p className="mt-5 text-2xl font-semibold text-emerald-700">안전 장치 작동 중</p>
                <p className="mt-2 text-sm text-slate-500">신규 진입만 차단</p>
              </div>
              <div className="mt-6 divide-y divide-slate-100 border-y border-slate-100">
                {safetyRows.map((row) => (
                  <div key={row.label} className="flex items-center justify-between gap-4 py-3">
                    <span className="flex items-center gap-3 text-sm text-slate-700">
                      <span className={`h-2.5 w-2.5 rounded-full ${dotClass(row.tone)}`} />
                      {row.label}
                    </span>
                    <span className={`rounded-md border px-2.5 py-1 text-sm font-semibold ${toneClass(row.tone)}`}>
                      {row.value}
                    </span>
                  </div>
                ))}
              </div>
            </Panel>

            <div className="space-y-6">
              <Panel
                title="확인 필요"
                action={
                  <span data-testid="remaining-actions" className="rounded-md bg-amber-100 px-3 py-1 text-sm font-semibold text-amber-900">
                    {remainingActions.length}
                  </span>
                }
              >
                <div className="mt-5 divide-y divide-slate-100 rounded-lg border border-amber-100">
                  {actionItems.map((item) => {
                    const checked = checkedIds.includes(item.id);
                    return (
                      <div key={item.id} className="grid gap-4 p-4 sm:grid-cols-[1fr_auto] sm:items-center">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <StatusPill tone={item.priority === "높음" ? "warn" : "neutral"}>{item.priority}</StatusPill>
                            <span className="text-sm font-medium text-slate-500">{item.symbol}</span>
                          </div>
                          <p className="mt-3 text-base font-semibold leading-6 text-slate-950">{item.title}</p>
                          <p className="mt-1 text-sm leading-6 text-slate-600">{item.detail}</p>
                        </div>
                        <button
                          type="button"
                          data-testid={`action-${item.id}`}
                          onClick={() => toggleChecked(item.id)}
                          className={`h-11 rounded-md border px-5 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
                            checked
                              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                              : "border-slate-300 bg-white text-slate-900 hover:bg-slate-50"
                          }`}
                        >
                          {checked ? "확인됨" : "확인하기"}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </Panel>

              <Panel title="노출 및 보호 요약">
                <div className="mt-5 grid gap-4 sm:grid-cols-3">
                  <div className="rounded-md border border-slate-100 bg-slate-50 p-4">
                    <p className="text-sm text-slate-500">총 노출</p>
                    <p className="mt-2 text-lg font-semibold text-slate-950">35,120 USDT</p>
                  </div>
                  <div className="rounded-md border border-slate-100 bg-slate-50 p-4">
                    <p className="text-sm text-slate-500">최대 허용 손실</p>
                    <p className="mt-2 text-lg font-semibold text-slate-950">5,000 USDT</p>
                  </div>
                  <div className="rounded-md border border-slate-100 bg-slate-50 p-4">
                    <p className="text-sm text-slate-500">보호 여유</p>
                    <p className="mt-2 text-lg font-semibold text-emerald-700">5.7%</p>
                  </div>
                </div>
                <div className="mt-5 space-y-2">
                  <ProgressBar value={57} />
                  <p className="text-sm leading-6 text-slate-600">
                    허용 손실 대비 여유는 남아 있지만, 보호주문 확인 전까지 신규 진입은 계속 막습니다.
                  </p>
                </div>
              </Panel>
            </div>

            <Panel title="최근 감사 로그" className="xl:min-h-[510px]">
              <ol className="mt-6 space-y-5">
                {auditEvents.map((event) => (
                  <li key={`${event.time}-${event.title}`} className="grid grid-cols-[64px_18px_1fr] gap-3">
                    <time className="pt-0.5 text-sm text-slate-500">{event.time}</time>
                    <span className={`mt-1.5 h-3 w-3 rounded-full ${dotClass(event.tone)}`} />
                    <div className="min-w-0 border-b border-slate-100 pb-4">
                      <p className="text-sm font-semibold leading-6 text-slate-950">{event.title}</p>
                      <p className="mt-1 text-sm leading-6 text-slate-600">{event.detail}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </Panel>
          </div>

          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap gap-2">
              {tabs.map((tab) => (
                <TabButton
                  key={tab.id}
                  active={activeTab === tab.id}
                  label={tab.label}
                  testId={`tab-${tab.id}`}
                  onClick={() => setActiveTab(tab.id)}
                />
              ))}
            </div>

            <div className="mt-4 border-t border-slate-100 pt-4">
              {activeTab === "today" ? (
                <div className="grid gap-4 lg:grid-cols-2">
                  {actionItems.map((item) => (
                    <div key={item.id} className="rounded-lg border border-slate-200 p-4">
                      <p className="text-sm font-medium text-slate-500">{item.symbol}</p>
                      <p className="mt-2 text-base font-semibold leading-6 text-slate-950">{item.title}</p>
                      <p className="mt-2 text-sm leading-6 text-slate-600">{item.detail}</p>
                    </div>
                  ))}
                </div>
              ) : null}

              {activeTab === "positions" ? (
                <div className="overflow-x-auto">
                  <table data-testid="position-table" className="min-w-full border-separate border-spacing-0 text-left text-sm">
                    <thead className="text-slate-500">
                      <tr>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">심볼</th>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">방향</th>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">수량</th>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">노출</th>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">보호</th>
                        <th className="whitespace-nowrap border-b border-slate-200 px-3 py-3 font-semibold">다음 조치</th>
                      </tr>
                    </thead>
                    <tbody>
                      {positionRows.map((row) => (
                        <tr key={row.symbol}>
                          <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3 font-semibold text-slate-950">{row.symbol}</td>
                          <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3 text-slate-700">{row.side}</td>
                          <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3 text-slate-700">{row.size}</td>
                          <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3 text-slate-700">{row.exposure}</td>
                          <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3">
                            <span className={`rounded-md border px-2.5 py-1 font-semibold ${toneClass(row.tone)}`}>
                              {row.protection}
                            </span>
                          </td>
                          <td className="whitespace-nowrap border-b border-slate-100 px-3 py-3 text-slate-700">{row.next}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {activeTab === "audit" ? (
                <div data-testid="audit-tab" className="divide-y divide-slate-100">
                  {auditEvents.map((event) => (
                    <div key={`${event.time}-${event.title}`} className="grid gap-3 py-4 sm:grid-cols-[72px_1fr_auto] sm:items-center">
                      <time className="text-sm text-slate-500">{event.time}</time>
                      <div>
                        <p className="text-sm font-semibold text-slate-950">{event.title}</p>
                        <p className="mt-1 text-sm leading-6 text-slate-600">{event.detail}</p>
                      </div>
                      <span className={`w-fit rounded-md border px-2.5 py-1 text-sm font-semibold ${toneClass(event.tone)}`}>
                        {event.tone === "safe" ? "정상" : event.tone === "danger" ? "차단" : event.tone === "warn" ? "확인" : "기록"}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
