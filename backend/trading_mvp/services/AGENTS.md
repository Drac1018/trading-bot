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

## 전략 / 운영 기본값 확인

* 진입 성격, `entry_mode`, `holding_profile`, 전략 편향은 고정 문구가 아니라 런타임 settings와 source-of-truth 모듈을 확인한 뒤 판단한다
* 관련 source-of-truth는 `settings.py`, `orchestrator.py`, `scheduler.py`, `ai_prompt_routing.py`, `agents.py`, `risk.py`와 전략/리스크 문서다
* AGENTS 문구만 근거로 전략 기본값을 바꾸거나 신규 진입 경로를 넓히지 말 것
* `breakout`, `swing`, `position` 성격의 변경은 데이터 품질, lead-lag, relative strength, derivatives, risk gate와 실행 경계까지 함께 검증할 것

## 손절과 보호주문 원칙

* 최초 손절은 항상 deterministic hard stop 이어야 한다
* 실주문 경로에서는 exchange-resident protective stop 생성과 검증을 유지할 것
* AI는 break-even 이동, trailing tighten, partial reduce 같은 관리 제안만 할 수 있다
* hard stop 제거, stop widening, protection 없는 포지션 유지, 손실 중 포지션의 장기 보유 승격은 금지한다
