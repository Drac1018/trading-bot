# Execution Flow

## Holding Profile Overlay

- 신규 진입 기본 프로필은 `holding_profile=scalp`입니다.
- `holding_profile=swing` 또는 `position`은 강한 higher timeframe 구조 정렬, breadth, lead-lag, derivatives 역풍 부재, meta gate `pass`가 동시에 맞을 때만 사용합니다.
- `interval_decision_cycle`이 만든 `holding_profile`과 `holding_profile_reason`은 pending entry plan, risk, execution, position management까지 그대로 전달됩니다.
- `breakout_confirm` 신규 진입은 기본적으로 scalp/intraday 성격으로 다루며, 장기 보유 프로필에서 예외를 넓히지 않습니다.

## Hard Stop Handling

- 최초 손절은 항상 deterministic hard stop 기준으로 생성됩니다.
- live execution은 exchange-resident protective stop을 계속 유지해야 하며, protection 없는 상태를 정상 상태로 표시하지 않습니다.
- AI는 stop width 제안, break-even 이동, trailing tighten, partial reduce 같은 보조 관리만 할 수 있습니다.
- AI는 hard stop 제거, stop widening, 무손절 유지, protection 없는 상태 허용을 할 수 없습니다.

현재 운영 루프는 하나의 interval decision cycle에 모든 책임을 몰아넣지 않고, 아래 4개 cycle로 분리됩니다.

## 운영 cycle

1. `exchange_sync_cycle`
   - 계좌, 포지션, 오픈 오더, 보호주문 상태 동기화만 수행
   - AI 호출 금지
   - 신규 진입 판단 금지
   - 전역 `exchange_sync_interval_seconds`만 사용

2. `market_refresh_cycle`
   - 심볼별 시장 스냅샷 수집
   - 필요 시 feature 계산을 위한 기반만 갱신
   - 신규 진입 판단 금지
   - 심볼별 `market_refresh_interval_minutes` effective cadence 사용

3. `position_management_cycle`
   - 열린 포지션이 있을 때만 break-even, trailing, partial take-profit, edge decay, reduce 강화 수행
   - 신규 진입 금지
   - `tighten_only` 유지
   - 심볼별 `position_management_interval_seconds` effective cadence 사용

4. `interval_decision_cycle`
   - 신규 진입/축소/청산 판단의 중심 루프
   - AI 판단, deterministic baseline, `risk_guard`, live execution 담당
   - exchange sync / position management를 매번 강제로 포함하지 않음
   - 심볼별 `decision_cycle_interval_minutes` effective cadence 사용

## 전역 기본값 + symbol override

- 전역 설정:
  - `default_timeframe`
  - `exchange_sync_interval_seconds`
  - `market_refresh_interval_minutes`
  - `position_management_interval_seconds`
  - `decision_cycle_interval_minutes`
  - `ai_call_interval_minutes`
- 심볼별 override:
  - `timeframe_override`
  - `market_refresh_interval_minutes_override`
  - `position_management_interval_seconds_override`
  - `decision_cycle_interval_minutes_override`
  - `ai_call_interval_minutes_override`
  - `enabled`

override가 비어 있으면 전역값을 그대로 상속합니다.

## 중복 신규 진입 방지

- base timeframe이 `15m`여도 decision cycle을 `5m`로 더 촘촘히 돌릴 수 있습니다.
- 단, 같은 base candle 안에서는 동일 symbol의 신규 진입 평가를 다시 만들지 않습니다.
- 현재 1차 구현은 `latest decision market_snapshot.snapshot_time == current snapshot_time`이면 same-candle 신규 진입 평가를 skip합니다.
- 열린 포지션의 보호 관리는 `position_management_cycle`에서 더 자주 실행할 수 있습니다.

## 안전 경계

- `risk_guard`는 여전히 최종 허용/차단 관문입니다.
- pause, guard mode, live approval, protection recovery, stale sync 차단 로직은 그대로 유지됩니다.
- `historical_replay`는 live execution을 절대 수행하지 않습니다.
- AI가 꺼져 있어도 exchange sync, market refresh, position management는 계속 실행할 수 있습니다.
- 보호주문 관련 stop widening은 허용되지 않으며, 관리 로직은 항상 보호 방향 우선입니다.
