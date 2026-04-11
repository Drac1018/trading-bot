import type { Metadata } from "next";

import { AlertNotifier } from "../components/alert-notifier";
import { AppNav } from "../components/nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "멀티 에이전트 트레이딩 콘솔",
  description: "실거래 운영, 리스크 차단, 감사 로그를 위한 멀티 에이전트 트레이딩 운영 콘솔",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="bg-canvas font-body text-ink">
        <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(182,146,49,0.18),_transparent_40%),linear-gradient(135deg,_rgba(255,255,255,0.55),_rgba(245,239,227,1))]">
          <div className="mx-auto flex min-h-screen max-w-[1600px] flex-col gap-5 px-4 py-4 sm:px-5 sm:py-5 lg:grid lg:grid-cols-[300px,minmax(0,1fr)] lg:gap-6 lg:px-6 lg:py-6">
            <aside className="lg:sticky lg:top-6 lg:h-fit">
              <AppNav />
            </aside>
            <main className="min-w-0 space-y-5 pb-8 lg:space-y-6 lg:pb-10">{children}</main>
          </div>
        </div>
        <AlertNotifier />
      </body>
    </html>
  );
}
