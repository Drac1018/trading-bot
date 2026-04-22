# Docs Index

## 목적

`docs` 디렉터리의 핵심 운영 문서와 문서 작성 기준을 한 곳에서 찾기 위한 인덱스다.

## 핵심 운영 문서

- [아키텍처](architecture.md)
- [리스크 정책](risk-policy.md)
- [실행 흐름](execution-flow.md)
- [API](api.md)
- [전략 엔진 규칙 표면](strategy-engine-rule-surface.md)
- [BLS Wrapper 운영 가이드](bls-wrapper-ops-guide.md)
- [발표 운영 최종 런북](release-operator-runbook.md)
- [발표 30분 전 운영 체크리스트](release-30m-checklist.md)
- [발표 5분 전 최종 점검표](release-5m-final-check.md)
- [발표 직후 2분 장애 대응표](release-2m-incident-response.md)

## 문서 작성 기준

- 운영자용 문서에 UI 노출 내부 키가 직접 등장하면 문서 상단에 `운영자 표현과 내부 키` 섹션을 둔다.
- 형식과 매핑 예시는 [operator-bridge-template.md](operator-bridge-template.md)를 그대로 재사용한다.
- 본문에서 중요한 내부 키가 다시 나오면 첫 등장 지점에 운영자 표현을 같이 병기한다.
- API/DB 키 이름 자체는 바꾸지 않고, 운영자 표현만 같이 연결한다.

## 새 운영 문서 추가 전 확인

- 이 문서가 운영자용 문서인지
- 화면 라벨과 내부 키가 어긋나는 구간이 있는지
- `운영자 표현과 내부 키` 섹션이 필요한지
- 기존 문서와 같은 개념을 다른 표현으로 쓰고 있지 않은지
