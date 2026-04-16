# my-trading-bot

현재 저장소의 최우선 목표는 **실거래에 안전한 Binance Futures 단기/단타 트레이딩 코어 안정화**입니다.  
멀티 에이전트 구조는 유지하지만, 지금은 실주문 경계, 리스크 가드, 상태 제어, 감사 로그 정합성이 우선입니다.

## 현재 초점

- 시장 / 계좌 / 포지션 / 주문 상태 수집과 동기화
- Binance 계정/시장 상태를 기반으로 한 실거래 경로
- AI 판단과 결정론적 `risk_guard` 분리
- 실행 통제와 보호 주문 경로 유지
- 감사 로그와 운영 대시보드를 통한 추적 가능성 확보
- pause / resume / live arm / manual approval / audit trail 유지
- 실패 시 자동 중지와 제한적 auto resume 정책

## 핵심 문서

- [아키텍처](docs/architecture.md)
- [리스크 정책](docs/risk-policy.md)
- [실행 흐름](docs/execution-flow.md)
- [API](docs/api.md)
- [Codex 초안 / 자동 resume](docs/codex-drafts-and-auto-resume.md)

## 현재 리스크 기준

- BTC 최대 `5x`
- 메이저 알트 최대 `3x`
- 일반 알트 최대 `2x`
- 1회 거래 최대 손실 `2%`
- 일일 손실 한도 `5%`
- 연속 손실 `3회` 이후 신규 진입 보수 제한
- 계좌/시장 상태 불확실 시 신규 진입 차단
- AI보다 결정론적 정책이 항상 우선

`max_leverage`, `max_risk_per_trade`, `max_daily_loss`는 운영 상한값이지만, 런타임에서는 위 하드 정책을 넘을 수 없습니다.

## 최소 검증 명령

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_risk_engine.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_settings_and_connectivity.py
.\.venv\Scripts\python.exe -m ruff check backend tests workers
.\.venv\Scripts\python.exe -m mypy backend\trading_mvp
```

프런트 변경이 있을 때는 아래도 함께 사용합니다.

```powershell
C:\my-trading-bot\.tools\node-v24.14.1-win-x64\corepack.cmd pnpm -C C:\my-trading-bot\frontend lint
C:\my-trading-bot\.tools\node-v24.14.1-win-x64\corepack.cmd pnpm -C C:\my-trading-bot\frontend build
```
