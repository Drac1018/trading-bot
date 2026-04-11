import { BacklogBoard, type BacklogBoardPayload } from "../../../components/backlog-board";
import { PageShell } from "../../../components/page-shell";
import { fetchJson } from "../../../lib/api";

export default async function BacklogPage() {
  const board = await fetchJson<BacklogBoardPayload>("/api/backlog");

  return (
    <div className="space-y-6">
      <PageShell
        eyebrow="개선 추적"
        title="개선 백로그"
        description="AI 제안, 사용자 요청, 실제 적용 내역, 검증 결과를 같은 화면에서 추적합니다."
      />
      <BacklogBoard initial={board} />
    </div>
  );
}
