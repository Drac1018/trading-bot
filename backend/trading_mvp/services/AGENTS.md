# AGENTS.md

`backend/trading_mvp/services/`는 실거래 핵심 경계가 모인 영역이다.

## 경계 규칙

* `agents.py`는 거래 의도 생성까지만 담당하고 실행 권한을 가지지 말 것
* `risk.py`는 최종 허용/차단 관문이며, 전략을 새로 만들지 말 것
* `execution.py`는 승인된 의도만 실행하고, 실패 시 감사/알림/중지 사유를 남길 것
* `settings.py`와 `pause_control.py`는 pause 원인, 수동 승인, auto resume 화이트리스트를 일관되게 관리할 것
* `scheduler.py`와 `orchestrator.py`는 예외 때문에 전체 루프가 죽지 않게 유지할 것

## 금지 사항

* 리스크 검증 없이 거래소 주문 호출 금지
* pause / resume / live arm 상태를 암묵적으로 바꾸는 변경 금지
* 감사 로그 없이 실거래 제어 상태를 바꾸는 변경 금지

## 참조 문서

* `docs/risk-policy.md`
* `docs/execution-flow.md`
* `docs/architecture.md`
* `docs/codex-drafts-and-auto-resume.md`

## 현재 운영 기본값

* 신규 진입의 기본 운영 편향은 `scalp`다
* 신규 진입 기본 `entry_mode`는 `pullback_confirm`이다
* `breakout_confirm`은 드문 예외로만 허용하고 기본 경로로 쓰지 말 것
* `swing` / `position`은 강한 구조적 레짐, breadth, lead-lag, relative strength, derivatives 상태가 동시에 지지할 때만 예외로 허용할 것

## 손절과 보호주문 원칙

* 최초 손절은 항상 deterministic hard stop 이어야 한다
* 실주문 경로에서는 exchange-resident protective stop 생성과 검증을 유지할 것
* AI는 break-even 이동, trailing tighten, partial reduce 같은 관리 제안만 할 수 있다
* hard stop 제거, stop widening, protection 없는 포지션 유지, 손실 중 포지션의 장기 보유 승격은 금지한다
