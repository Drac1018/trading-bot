# Strategy Engine Rule Surface

이 문서는 현재 구현 기준의 `strategy_engine` 선택 규칙과 운영 의미를 요약한다.  
새 정책 제안이 아니라, 현재 코드가 이미 하는 일을 운영자가 빠르게 설명할 수 있게 정리한 참고 문서다.

## 운영자 표현과 내부 키

- 이 문서는 구현 기준 설명이므로 내부 키를 그대로 사용합니다.
- 운영자 화면이나 운영 보고에서는 아래처럼 읽으면 됩니다.
  - `strategy_engine`
    - 화면 표현: `전략 엔진` 또는 `진입 서사 분류`
  - `trigger_type` / trigger family
    - 화면 표현: `검토 이벤트 종류`
  - `holding_profile`
    - 화면 표현: `보유 성격`
    - 값 해석: `scalp = 짧게`, `swing = 중간`, `position = 길게`
  - `entry_mode`
    - 화면 표현: `진입 방식`
    - 대표값: `pullback_confirm`, `breakout_confirm`
  - `allowed_actions` / `forbidden_actions`
    - 화면 표현: `AI가 제안 가능한 행동 범위`
  - `intent_family` / `management_action`
    - 화면 표현: `신규 진입`이 아니라 `관리 / 보호 조치`인지 구분하는 보조 의미
- 즉, 이 문서에서 `strategy_engine`, `trigger_type`, `holding_profile`, `entry_mode`를 말하는 부분은 운영자 화면에서 각각 `전략 엔진`, `검토 이벤트`, `보유 성격`, `진입 방식`으로 읽으면 됩니다.

기준 소스:

- `backend/trading_mvp/services/strategy_engines/__init__.py`
- `backend/trading_mvp/services/holding_profile.py`
- `backend/trading_mvp/services/ai_prompt_routing.py`

## 공통 원칙

- 기본 신규 진입 bias는 `scalp / intraday`다.
- `swing / position`은 strong higher-timeframe alignment, breadth, lead-lag, derivatives가 같이 받쳐줄 때만 예외적으로 허용된다.
- `breakout_exception_engine`은 rare 예외이며 `scalp-only`다.
- `protection_reduce_engine`은 management-only다. 신규 entry를 정당화하지 않는다.
- AI prompt routing은 엔진과 trigger 조합별로 `allowed_actions`를 좁게 제한한다.
- `risk.py`와 `execution.py`의 최종 의미는 바뀌지 않는다.

## Engine Matrix

### `trend_pullback_engine`

- 목적:
  - 정렬된 추세 안에서 눌림 확인 이후 재진입을 본다.
- 일반 narrative:
  - `pullback_confirm`
  - higher-timeframe trend alignment 유지
  - weak volume / range regime가 아니어야 함
- entry bias:
  - `entry_candidate_event`에서 `hold / long / short`
  - generic breakout chase는 허용 대상이 아님
- holding profile bias:
  - 기본은 `scalp`
  - 구조 정렬이 매우 강할 때만 `swing / position` 예외 가능
- open-position review:
  - `hold / reduce / exit`
  - 반대 방향 신규 진입 금지
- 운영 메모:
  - 가장 기본적인 trend-follow entry family다.

### `trend_continuation_engine`

- 목적:
  - 이미 진행 중인 추세 continuation 구간을 보되, late extension은 보수적으로 본다.
- 일반 narrative:
  - continuation quality review
  - extension risk가 높으면 `hold / abstain` bias
- entry bias:
  - `entry_candidate_event`에서 `hold / long / short`
- holding profile bias:
  - 기본은 `scalp`
  - continuation이 매우 clean하고 구조 정렬이 강할 때만 장기 프로필 가능
- open-position review:
  - `hold / reduce / exit`
- 운영 메모:
  - pullback보다 late-entry 리스크를 더 민감하게 본다.

### `breakout_exception_engine`

- 목적:
  - 일반 엔트리 기본값이 아닌 rare breakout 예외만 다룬다.
- 일반 narrative:
  - `breakout_confirm`
  - spread / derivatives / lead-lag / data quality 요구가 더 엄격함
- entry bias:
  - `entry_candidate_event` 또는 `breakout_exception_event`에서만 의미 있음
  - `hold / long / short`
- holding profile bias:
  - `recommended_holding_profile = scalp`만 허용
  - `swing / position` 승격 금지
- quality rule:
  - degraded / unavailable quality에서는 `hold` 또는 fail-closed
- open-position review:
  - `hold / reduce / exit`
  - long-horizon promotion 금지
- 운영 메모:
  - “되는 날에만 짧게 쓴다”가 핵심이다.

### `range_mean_reversion_engine`

- 목적:
  - range regime 안의 small fade / mean reversion만 다룬다.
- 일반 narrative:
  - trend continuation 서사를 만들지 않는다
  - range 내부의 반대편 회귀 기대만 본다
- entry bias:
  - `entry_candidate_event`에서 `hold / long / short`
- holding profile bias:
  - 기본은 `scalp`
  - 장기 보유 서사와는 맞지 않음
- open-position review:
  - `hold / reduce / exit`
- 운영 메모:
  - range 밖으로 나간 뒤 continuation 해석으로 넘어가면 엔진 의미가 틀어진다.

### `protection_reduce_engine`

- 목적:
  - protection recovery, reduce-only, exit-only, tighten-management 성격의 관리 행위만 다룬다.
- 일반 narrative:
  - protection restore
  - reduce / exit
  - stop tightening
- entry bias:
  - 없음
  - 신규 entry 금지
- holding profile bias:
  - current profile 유지 또는 de-risk only
- trigger family:
  - `protection_review_event`
  - open-position management 문맥
- allowed actions:
  - `hold / reduce / exit`
- forbidden:
  - 신규 `long / short`
  - stop widening
  - protection removal
- 운영 메모:
  - external metadata에서는 entry가 아니라 `management / protection` semantics로 읽어야 한다.

## Trigger Family Notes

Current runtime source of truth for trigger semantics is `backend/trading_mvp/services/orchestrator.py`, `backend/trading_mvp/services/scheduler.py`, and `docs/api.md`.

Current runtime trigger families:

- `entry_candidate_event`
  - entry review 전용
  - `trend_pullback`, `trend_continuation`, `range_mean_reversion`, `breakout_exception`만 의미가 있음
- `breakout_exception_event`
  - breakout 예외 전용
  - `breakout_exception_engine`만 정상 경로
- `protection_review_event`
  - survival path
  - management-only
- `manual_review_event`
  - open position이 있으면 management review
  - 없으면 entry review 가능

Historical reference only:

- `open_position_recheck_due`
  - legacy policy의 thesis refresh
  - 현재 runtime trigger가 아니라 stored historical semantics
- `periodic_backstop_due`
  - legacy policy의 stale-thesis refresh safety net
  - 현재 runtime trigger가 아니라 stored historical semantics

## Holding Profile Overlay

- `scalp`
  - 기본값
  - 가장 짧은 review cadence hint
  - breakout exception도 여기만 허용
- `swing`
  - meta gate와 구조 정렬 요구가 강해짐
  - weak prior / degraded quality에서 쉽게 다시 `scalp` 또는 `hold`로 내려감
- `position`
  - 가장 보수적
  - strong regime + non-weak priors + acceptable quality가 아니면 유지되기 어려움

## 운영 체크포인트

- `selected_engine`는 entry narrative 설명용이고, 실제 실행 허용권은 아니다.
- operator는 아래 순서로 해석하는 것이 맞다:
  - `strategy_engine`
  - `trigger_type`
  - `prompt_family`
  - `allowed_actions`
  - `risk result`
  - `execution result`
- `protection_reduce_engine` 또는 protection trigger인데 화면상 `long / short`만 보이면, 반드시 `intent_family / management_action` metadata까지 같이 봐야 한다.
