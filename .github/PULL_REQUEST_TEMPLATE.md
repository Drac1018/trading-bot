## 변경 요약

- 무엇을 왜 바꾸는지 간단히 적습니다.

## 확인 사항

- [ ] 이 PR에 entry-plan 테스트 추가/수정이 있으면 각 테스트를 먼저 분류했습니다: orchestration shape test -> deterministic helper 허용, policy calculation test -> 실제 `evaluate_risk/meta_gate` 경로 유지. 기준은 `tests/entry-plan-test-guideline.md`를 따릅니다.
- [ ] 백엔드 상태 의미와 UI 표시 의미가 어긋나지 않습니다.
- [ ] 응답 필드/의미 변경이 있으면 `docs/api.md`를 함께 갱신했습니다.
- [ ] 운영자용 문서에 UI 노출 내부 키가 직접 등장하면 `운영자 표현과 내부 키` 섹션을 추가했고, 형식은 `docs/operator-bridge-template.md`를 재사용했습니다.
- [ ] 위험 경로를 건드렸다면 차단 이유, pause/guard, stale 처리 검증을 확인했습니다.

## 검증

- 실행한 테스트/검증 명령을 적습니다.

## 남은 리스크

- 남은 운영 리스크나 후속 작업이 있으면 적습니다.
