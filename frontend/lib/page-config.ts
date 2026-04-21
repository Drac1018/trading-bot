export type PageSection = {
  title: string;
  endpoint: string;
  description: string;
};

export type SettingsView = "control" | "integration";

export const settingsViewTabs: Array<{ value: SettingsView; label: string; description: string }> = [
  {
    value: "control",
    label: "운영 설정",
    description: "실거래 제어, 리스크, 운영 주기, 이벤트 대응",
  },
  {
    value: "integration",
    label: "연동 설정",
    description: "OpenAI, 외부 이벤트 소스, Binance 자격증명",
  },
];

export function normalizeSettingsView(value: string | null | undefined): SettingsView {
  return value === "integration" ? "integration" : "control";
}

export const dashboardPages: Record<
  string,
  {
    title: string;
    eyebrow: string;
    description: string;
    sections: PageSection[];
  }
> = {
  market: {
    title: "시장 / 신호 입력",
    eyebrow: "시장 데이터",
    description: "시장 스냅샷과 feature 입력만 확인합니다. AI 판단과 risk 차단 정보는 의사결정 탭으로 분리했습니다.",
    sections: [
      {
        title: "시장 스냅샷",
        endpoint: "/api/market/snapshots",
        description: "거래소에서 수집한 최신 시장 입력입니다.",
      },
      {
        title: "신호 입력",
        endpoint: "/api/market/features",
        description: "추세, 변동성, 거래량, RSI, ATR 등 계산된 입력 feature입니다.",
      },
    ],
  },
  decisions: {
    title: "의사결정",
    eyebrow: "평가 / 판단",
    description: "현재 입력을 바탕으로 한 AI 평가와 risk 차단 결과를 한 화면에서 확인합니다.",
    sections: [
      {
        title: "의사결정 기록",
        endpoint: "/api/decisions",
        description: "최근 저장된 trading decision 결과입니다.",
      },
    ],
  },
  positions: {
    title: "실거래 포지션",
    eyebrow: "보유 상태",
    description: "실거래 기준 현재 포지션과 보호 상태를 확인합니다.",
    sections: [
      {
        title: "포지션 목록",
        endpoint: "/api/positions",
        description: "실거래 기준 열린 포지션만 표시합니다.",
      },
    ],
  },
  orders: {
    title: "실거래 주문 / 체결",
    eyebrow: "Live Logs",
    description: "Binance 실거래 주문과 체결 내역을 확인합니다.",
    sections: [
      {
        title: "실거래 주문",
        endpoint: "/api/orders",
        description: "주문 상태, 외부 주문 ID, 보호 주문 연관 관계를 표시합니다.",
      },
      {
        title: "실거래 체결",
        endpoint: "/api/executions",
        description: "부분 체결을 포함한 실제 execution ledger입니다.",
      },
    ],
  },
  risk: {
    title: "리스크 상태",
    eyebrow: "정책 우선",
    description: "AI 추천보다 우선하는 risk_guard 결과와 운영 경고를 확인합니다.",
    sections: [
      {
        title: "리스크 체크",
        endpoint: "/api/risk/checks",
        description: "허용 여부와 reason code를 확인합니다.",
      },
      {
        title: "알림",
        endpoint: "/api/alerts",
        description: "운영 중 즉시 확인이 필요한 경고와 안내입니다.",
      },
    ],
  },
  agents: {
    title: "에이전트 디버그",
    eyebrow: "Debug Only",
    description: "운영 핵심 화면이 아닌 raw agent/provider/payload 디버그 정보만 확인합니다.",
    sections: [
      {
        title: "에이전트 실행 기록",
        endpoint: "/api/agents",
        description: "최근 agent run의 raw metadata와 payload입니다.",
      },
    ],
  },
  scheduler: {
    title: "스케쥴러 상태",
    eyebrow: "주기 실행",
    description: "마지막 실행, 다음 실행, 성공/실패 상태만 확인합니다. 판단 상세는 의사결정 탭으로 이동했습니다.",
    sections: [
      {
        title: "스케쥴러 실행 기록",
        endpoint: "/api/scheduler",
        description: "주기별 실행 결과와 다음 실행 예정 시각입니다.",
      },
    ],
  },
  audit: {
    title: "감사 로그",
    eyebrow: "운영 추적",
    description: "리스크 체크, 주문, 동기화, 승인 창, 에이전트 실행을 시간순으로 추적합니다.",
    sections: [
      {
        title: "감사 타임라인",
        endpoint: "/api/audit",
        description: "운영 감사 로그입니다.",
      },
    ],
  },
  settings: {
    title: "설정",
    eyebrow: "운영 제어",
    description: "실거래, 추적 심볼, OpenAI, Binance 연결을 제어합니다.",
    sections: [
      {
        title: "현재 설정",
        endpoint: "/api/settings",
        description: "저장된 운영 설정 값입니다.",
      },
    ],
  },
};
