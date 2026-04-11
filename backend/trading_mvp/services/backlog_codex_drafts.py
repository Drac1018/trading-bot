from __future__ import annotations

from trading_mvp.models import ProductBacklog
from trading_mvp.schemas import (
    AppliedChangeRecordResponse,
    BacklogCodexDraftResponse,
    UserChangeRequestResponse,
)
from trading_mvp.time_utils import utcnow_naive


def build_codex_prompt_draft(
    backlog: ProductBacklog,
    *,
    user_requests: list[UserChangeRequestResponse],
    applied_records: list[AppliedChangeRecordResponse],
) -> BacklogCodexDraftResponse | None:
    if backlog.status == "verified" or applied_records:
        return None

    request_lines = (
        "\n".join(f"- {item.title}: {item.detail}" for item in user_requests)
        if user_requests
        else "- 연결된 사용자 요청 없음"
    )

    prompt = "\n".join(
        [
            "현재 프로젝트의 개선 backlog 항목을 실제로 반영해줘.",
            "",
            f"backlog id: {backlog.id}",
            f"제목: {backlog.title}",
            f"문제: {backlog.problem}",
            f"제안 내용: {backlog.proposal}",
            f"근거: {backlog.rationale}",
            "",
            "연결된 사용자 요청:",
            request_lines,
            "",
            "중요:",
            "- 설계만 하지 말고 실제 구현해줘",
            "- 현재 리포지토리 구조와 스택에 맞춰 반영해줘",
            "- 관련 DB/API/프런트/문서/테스트까지 필요한 범위로 함께 반영해줘",
            "- 적용 후 backlog 화면에서 적용/검증 내역이 보이도록 기록까지 남겨줘",
            "- 안전 로직과 실거래 보호 장치는 훼손하지 말아줘",
            "",
            "작업 후 최종 보고 형식:",
            "1. 무엇을 변경했는지",
            "2. backlog 항목이 어떻게 반영됐는지",
            "3. 적용/검증 내역에 무엇을 남겼는지",
            "4. 무엇이 fully working인지",
            "5. 남은 리스크",
            "6. 수정/생성 파일 목록",
        ]
    )

    return BacklogCodexDraftResponse(
        available=True,
        title=f"Codex 실행 초안 #{backlog.id}",
        prompt=prompt,
        generated_at=utcnow_naive(),
        note="이 초안은 로컬 규칙 기반으로 자동 생성되며, Codex API를 호출하지 않아 추가 모델 비용이 발생하지 않습니다.",
    )
