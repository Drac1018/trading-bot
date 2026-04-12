# AGENTS.md

`backend/`에서는 실거래 안전 경로를 우선한다.

## 범위

* FastAPI API
* DB 모델 / 마이그레이션
* Binance 연동
* AI 판단, 리스크 검증, 실행 경로
* 설정 / 제어 / 감사 로그

## 세부 원칙

* 모델 변경 시 Alembic 마이그레이션까지 함께 반영할 것
* 실주문 경로는 `risk -> execution -> audit` 순서를 우회하지 말 것
* 설정 저장과 pause/resume, live arm/disarm, auto resume는 서로 역할을 섞지 말 것
* 실계좌/시장 상태를 읽지 못하면 신규 진입보다 차단을 우선할 것
* 상세 정책은 `docs/risk-policy.md`, `docs/execution-flow.md`, `docs/architecture.md`를 기준으로 볼 것

## 권장 검증

변경 범위에 맞춰 아래를 우선 실행:

* `python -m pytest -q`
* `python -m ruff check backend tests workers`
* `python -m mypy backend\\trading_mvp`
* 필요 시 `python -m trading_mvp.migrate`
