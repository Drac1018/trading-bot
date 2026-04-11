# Architecture

## 상위 구성

1. Market data layer
   mock 시계열 데이터를 정규화된 스냅샷으로 변환합니다.

2. Feature layer
   추세, 변동성, 거래량, RSI, ATR, drawdown 특징을 계산하고 영속화합니다.

3. Agent layer
   다섯 개 AI 역할이 이벤트 종류에 따라 선택적으로 호출됩니다.

4. Deterministic risk layer
   AI 결정 위에서 최종 허용/차단을 판정합니다.

5. Execution layer
   종이매매 주문과 체결, 포지션, 손익을 기록합니다.

6. Scheduler / worker layer
   1h, 4h, 12h, 24h 주기의 리뷰 작업을 실행하거나 큐에 넣습니다.

7. Audit / UI layer
   모든 상태 전환을 DB에 남기고, Next.js 대시보드에서 운영 관점으로 노출합니다.

## 핵심 설계 포인트

- AI는 실행 권한이 없습니다.
- 실거래 경계는 존재하지만 비활성 상태입니다.
- DB 엔티티는 의사결정, 리스크, 주문, 체결, 백로그, 스케줄, 감사 기록을 분리 저장합니다.
- FastAPI 시작 시 `create_all`로 바로 실행 가능하며, Alembic 마이그레이션도 병행 제공합니다.
- 기본 로컬은 SQLite, Compose는 PostgreSQL + Redis를 사용합니다.

