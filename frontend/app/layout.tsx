import type { Metadata } from "next";

import { AppChrome } from "../components/app-chrome";

import "./globals.css";

export const metadata: Metadata = {
  title: "실거래 운영 대시보드",
  description: "실거래 상태, 리스크 차단, 수동 승인, 감사 로그를 위한 운영 대시보드",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="bg-canvas font-body text-ink">
        <AppChrome>{children}</AppChrome>
      </body>
    </html>
  );
}
