# 개선 백로그 UI

개선 백로그 화면은 이제 아래 내용을 한 화면에서 함께 보여준다.

- backlog 개선 항목
- 사용자가 직접 요청한 개선 항목
- 실제로 적용된 개선 내역
- 적용 후 검증/확인 내용
- 최근 24시간 시그널 성과 분해 리포트
- 구조화된 경쟁사 메모 요약

## 저장되는 데이터

### AI 백로그
- 기존 `product_backlog`

### 사용자 요청
- 제목
- 상세 내용
- 상태
- 연결된 backlog id
- 생성/수정 시각

### 적용 내역
- 제목
- 요약
- 상세 설명
- 연결된 backlog id
- 출처 유형 (`ai`, `user`, `manual`)
- 변경 파일 목록
- 검증/확인 내용
- 적용 시각

## 화면 사용 방식

`/dashboard/backlog` 에서 아래 작업을 할 수 있다.

- 기존 backlog 항목 확인
- 사용자 요청 직접 등록
- 실제 적용 내역 직접 등록
- 각 backlog 카드 안에서 연결된 요청과 적용/검증 내역 확인
- 아직 backlog와 연결하지 않은 요청/적용 내역 별도 확인
- 최신 활동 기준으로 backlog, 사용자 요청, 적용 내역을 최신순으로 확인
- 최근 24시간 시그널 성과 리포트와 구조화된 경쟁사 메모를 backlog 화면 안에서 확인

## API

- `GET /api/backlog`
  - 백로그 보드 전체 조회
- `GET /api/backlog/{backlog_id}`
  - 특정 backlog 상세와 연결 데이터 조회
- `POST /api/backlog/requests`
  - 사용자 요청 등록
- `POST /api/backlog/applied`
  - 적용 내역 등록
