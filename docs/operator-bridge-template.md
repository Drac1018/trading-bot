# 운영 문서 용어 브리지 템플릿

## 목적

새 운영 문서에서 내부 구현 키를 생으로 쓰더라도, 운영자가 화면 표현과 바로 연결해서 읽을 수 있게 만드는 복붙용 템플릿이다.

## 언제 넣는가

- settings, cadence, trigger, strategy, risk 관련 내부 키가 직접 문서에 등장할 때
- 운영자 화면 라벨과 API/DB 키가 서로 다를 때
- 장애 대응표, 체크리스트, 런북처럼 빠르게 읽는 문서일 때

## 기본 원칙

- 문서 상단에 `운영자 표현과 내부 키` 섹션을 둔다.
- 본문에서 중요한 키가 다시 나오면 첫 등장 지점에 화면 표현을 같이 병기한다.
- API/DB 키 이름 자체는 바꾸지 않는다.
- 운영자 표현은 이미 UI에서 쓰는 라벨을 우선 사용한다.

## 복붙 템플릿

```md
## 운영자 표현과 내부 키

- 이 문서는 [문서 목적] 설명이라 내부 설정 키와 화면 표현이 같이 섞여 있습니다.
- 운영자 화면에서는 아래처럼 읽으면 됩니다.
  - `internal_key_a`
    - 화면 표현: `사용자 친화 라벨 A`
  - `internal_key_b`
    - 화면 표현: `사용자 친화 라벨 B`
  - `internal_key_c`
    - 화면 표현: `사용자 친화 라벨 C`
- 즉, 이 문서에서 [settings/cadence/trigger/strategy] 키를 말하는 부분은 운영자 화면에서는 위 표현으로 대응해 읽으면 됩니다.
```

## 자주 쓰는 매핑 예시

- cadence
  - `exchange_sync_interval_seconds` -> `거래소 동기화 주기`
  - `market_refresh_interval_minutes` -> `시장 갱신 주기`
  - `decision_cycle_interval_minutes` -> `재검토 확인 주기`
  - `ai_call_interval_minutes` -> `AI 기본 검토 간격`
- event source
  - `event_source_provider` -> `Event source provider`
  - `event_source_bls_enrichment_url` -> `BLS enrichment URL`
  - `event_source_bea_enrichment_url` -> `BEA enrichment URL`
- strategy / review
  - `strategy_engine` -> `전략 엔진` 또는 `진입 서사 분류`
  - `trigger_type` -> `검토 이벤트 종류`
  - `holding_profile` -> `보유 성격`
  - `entry_mode` -> `진입 방식`

## 본문 병기 예시

```md
- `decision_cycle_interval_minutes = 1` (`재검토 확인 주기`)
- `ai_call_interval_minutes = 5` (`AI 기본 검토 간격`)
- `Event source provider = fred` (`event_source_provider`)
- `BLS enrichment URL = http://127.0.0.1:8091/bls/releases` (`event_source_bls_enrichment_url`)
```

## 적용 전 체크

- 이 문서가 운영자용 문서인지
- 내부 키를 생으로 적는 구간이 있는지
- 같은 개념을 문서마다 다른 표현으로 쓰고 있지 않은지
- 기존 UI 라벨과 충돌하지 않는지
