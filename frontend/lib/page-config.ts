export type PageSection = {
  title: string;
  endpoint: string;
  description: string;
};

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
    title: "시장 / 신호 스냅샷",
    eyebrow: "시장 데이터",
    description: "선택한 심볼들의 최신 시장 스냅샷과 계산된 피처 값을 확인합니다.",
    sections: [
      {
        title: "시장 스냅샷",
        endpoint: "/api/market/snapshots",
        description: "거래소에서 수집한 최신 시장 데이터입니다."
      },
      {
        title: "피처 스냅샷",
        endpoint: "/api/market/features",
        description: "추세, 변동성, 거래량 비율, RSI, ATR 등 계산 결과입니다."
      }
    ]
  },
  decisions: {
    title: "거래 의사결정",
    eyebrow: "AI 추천",
    description: "선택 심볼별 AI 의사결정 결과와 설명을 확인합니다.",
    sections: [
      {
        title: "의사결정 기록",
        endpoint: "/api/decisions",
        description: "Trading Decision AI의 최신 출력입니다."
      }
    ]
  },
  positions: {
    title: "실거래 포지션",
    eyebrow: "보유 상태",
    description: "실거래 기준 현재 포지션과 손익 상태를 확인합니다.",
    sections: [
      {
        title: "포지션 목록",
        endpoint: "/api/positions",
        description: "실거래 포지션만 표시합니다."
      }
    ]
  },
  orders: {
    title: "실거래 주문 / 체결",
    eyebrow: "Live Logs",
    description: "Binance 실거래 주문과 체결 내역을 한 화면에서 확인합니다.",
    sections: [
      {
        title: "실거래 주문",
        endpoint: "/api/orders",
        description: "주문 상태, 외부 주문 ID, 보호 주문 관계를 표시합니다."
      },
      {
        title: "실거래 체결",
        endpoint: "/api/executions",
        description: "부분 체결 포함 실제 체결 기록입니다."
      }
    ]
  },
  risk: {
    title: "리스크 상태",
    eyebrow: "정책 우선",
    description: "AI 추천보다 우선하는 리스크 엔진 결과와 차단 사유를 확인합니다.",
    sections: [
      {
        title: "리스크 체크",
        endpoint: "/api/risk/checks",
        description: "허용 여부와 reason code를 확인합니다."
      },
      {
        title: "알림",
        endpoint: "/api/alerts",
        description: "운영 중 즉시 확인이 필요한 경고와 안내입니다."
      }
    ]
  },
  agents: {
    title: "에이전트 실행",
    eyebrow: "AI 작동",
    description: "5개 AI 역할의 입력·출력, 공급자, 실행 상태를 추적합니다.",
    sections: [
      {
        title: "에이전트 실행 기록",
        endpoint: "/api/agents",
        description: "최신 에이전트 실행 이력입니다."
      }
    ]
  },
  scheduler: {
    title: "스케줄러",
    eyebrow: "주기 실행",
    description: "의사결정 주기와 4h / 12h / 24h 리뷰 실행 결과를 확인합니다.",
    sections: [
      {
        title: "스케줄러 실행 기록",
        endpoint: "/api/scheduler",
        description: "주기별 실행 결과와 다음 실행 시각입니다."
      }
    ]
  },
  audit: {
    title: "감사 로그",
    eyebrow: "운영 추적",
    description: "리스크 체크, 주문, 동기화, 승인 창, 에이전트 실행을 시간순으로 추적합니다.",
    sections: [
      {
        title: "감사 타임라인",
        endpoint: "/api/audit",
        description: "운영 감사 로그입니다."
      }
    ]
  },
  settings: {
    title: "설정",
    eyebrow: "운영 제어",
    description: "실거래 가드, 추적 심볼, OpenAI, Binance 연결을 제어합니다.",
    sections: [
      {
        title: "현재 설정",
        endpoint: "/api/settings",
        description: "저장된 운영 설정 값입니다."
      }
    ]
  },
  backlog: {
    title: "개선 백로그",
    eyebrow: "제품 개선",
    description: "AI 제안, 사용자 요청, 실제 적용 내역, 검증 결과를 한 화면에서 추적합니다.",
    sections: [
      {
        title: "백로그",
        endpoint: "/api/backlog",
        description: "AI 개선 과제와 연결된 요청·적용·검증 이력입니다."
      }
    ]
  }
};
