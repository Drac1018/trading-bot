# Sales Docs README

이 디렉터리는 판매 설명용 문서와 내부 학습 문서의 source of truth를 보관합니다.

## 구성

- `customer-sales-guide-ko.md`
  고객용 판매 설명 원본
- `internal-sales-enablement-ko.md`
  내부 학습 / 데모 / 질의응답 원본
- `assets/`
  PDF에 넣는 이미지 자산, 용어 표, 보조 자료
- `export/`
  생성된 PDF 출력 위치

## PDF 재생성 명령

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\export_sales_pdf.ps1
```

## 의존성

PDF export는 Python dev dependency를 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

필요 라이브러리:

- `reportlab`
- `markdown`
- `beautifulsoup4`
- `pillow`

## 자산 관리

- `assets/*.png`는 export 스크립트가 자동 생성하거나 갱신할 수 있습니다.
- 용어 설명용 보조 자료는 `assets/*.md`로 유지합니다.
- 이미지 교체 시 같은 파일명을 유지하면 Markdown 수정 없이 재사용할 수 있습니다.

## 유지보수 원칙

- 반드시 **현재 MVP 기준**만 설명합니다.
- 미래 기능은 “향후 확장 가능” 박스로만 분리합니다.
- 실거래 준비 완료처럼 오해를 부르는 표현은 금지합니다.
- AI와 실행 통제의 분리를 항상 명시합니다.

## 제품 변경 시 같이 수정해야 할 곳

### 판매 메시지 변경

- `customer-sales-guide-ko.md`
- `internal-sales-enablement-ko.md`

### 스키마 변경

- `backend/trading_mvp/schemas.py`
- `schemas/generated/*.json`
- 내부 학습 문서의 source of truth 표

### 실행 흐름 변경

- `docs/execution-flow.md`
- `customer-sales-guide-ko.md`
- `internal-sales-enablement-ko.md`

### 리스크 규칙 변경

- `docs/risk-policy.md`
- 판매/학습 문서의 안전 기능 섹션

## 폰트 주의

한글 PDF 생성을 위해 시스템 한글 폰트가 필요합니다.  
기본적으로 Windows의 `Malgun Gothic`을 우선 사용합니다.

직접 지정하려면 아래 환경 변수를 사용할 수 있습니다.

- `SALES_PDF_FONT_REGULAR`
- `SALES_PDF_FONT_BOLD`

## 권장 검증

- PDF가 정상 생성되는지
- 기업/개인 가치 제안이 혼합되지 않았는지
- 문서가 실제 구현 범위를 넘어서지 않는지
- 한계와 확장 가능성이 분리되어 서술됐는지
