# Execution Flow

1. 시장 스냅샷 수집
2. 특징 계산 및 저장
3. Trading Decision AI 호출
4. 스키마 검증
5. 결정론적 리스크 검사
6. 거부 시 알림 + 감사 로그 + Chief Review
7. 허용이고 종이매매 모드면 주문/체결/포지션/PnL 업데이트
8. Chief Review 생성
9. 대시보드와 감사 로그 갱신

## 리플레이

- CLI: `python -m trading_mvp.cli replay --cycles 5 --start-index 140`
- API: `POST /api/replay/run?cycles=5&start_index=140`

