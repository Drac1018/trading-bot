from __future__ import annotations

import argparse
import json
from pathlib import Path

from trading_mvp.database import Base, SessionLocal, engine
from trading_mvp.schemas import (
    AgentRunRecord,
    AppliedChangeRecordResponse,
    BacklogAutoApplyBatchResponse,
    BacklogAutoApplyResult,
    BacklogBoardResponse,
    BinanceAccountResponse,
    ChiefReviewSummary,
    ExecutionIntent,
    IntegrationSuggestion,
    MarketSnapshotPayload,
    ProductBacklogDetailResponse,
    ProductBacklogItem,
    RiskCheckResult,
    SchedulerRunRecord,
    SignalPerformanceReportResponse,
    StructuredCompetitorNotesResponse,
    TradeDecision,
    UserChangeRequestResponse,
    UXSuggestion,
)
from trading_mvp.services.backlog_auto_apply import auto_apply_supported_backlogs
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.scheduler import run_window
from trading_mvp.services.seed import seed_demo_data


def export_schemas(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    schema_map = {
        "TradeDecision": TradeDecision.model_json_schema(),
        "ChiefReviewSummary": ChiefReviewSummary.model_json_schema(),
        "IntegrationSuggestion": IntegrationSuggestion.model_json_schema(),
        "BinanceAccountResponse": BinanceAccountResponse.model_json_schema(),
        "UXSuggestion": UXSuggestion.model_json_schema(),
        "ProductBacklogItem": ProductBacklogItem.model_json_schema(),
        "ProductBacklogDetailResponse": ProductBacklogDetailResponse.model_json_schema(),
        "UserChangeRequestResponse": UserChangeRequestResponse.model_json_schema(),
        "AppliedChangeRecordResponse": AppliedChangeRecordResponse.model_json_schema(),
        "BacklogBoardResponse": BacklogBoardResponse.model_json_schema(),
        "SignalPerformanceReportResponse": SignalPerformanceReportResponse.model_json_schema(),
        "StructuredCompetitorNotesResponse": StructuredCompetitorNotesResponse.model_json_schema(),
        "BacklogAutoApplyResult": BacklogAutoApplyResult.model_json_schema(),
        "BacklogAutoApplyBatchResponse": BacklogAutoApplyBatchResponse.model_json_schema(),
        "RiskCheckResult": RiskCheckResult.model_json_schema(),
        "ExecutionIntent": ExecutionIntent.model_json_schema(),
        "AgentRunRecord": AgentRunRecord.model_json_schema(),
        "SchedulerRunRecord": SchedulerRunRecord.model_json_schema(),
        "MarketSnapshotPayload": MarketSnapshotPayload.model_json_schema(),
    }
    for name, schema in schema_map.items():
        (target_dir / f"{name}.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading MVP CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("--cycles", type=int, default=10)
    replay_parser.add_argument("--start-index", type=int, default=90)

    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("--window", required=True, choices=["1h", "4h", "12h", "24h"])

    subparsers.add_parser("seed")
    subparsers.add_parser("cycle")
    subparsers.add_parser("export-schemas")
    subparsers.add_parser("backlog-auto-apply-supported")

    args = parser.parse_args()
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        if args.command == "seed":
            output = seed_demo_data(session)
        elif args.command == "cycle":
            orchestrator = TradingOrchestrator(session)
            output = orchestrator.run_selected_symbols_cycle(trigger_event="cli")
            session.commit()
        elif args.command == "review":
            output = run_window(session, args.window, triggered_by="cli")
            session.commit()
        elif args.command == "replay":
            orchestrator = TradingOrchestrator(session)
            outputs: list[dict[str, object]] = []
            for offset in range(args.cycles):
                outputs.append(
                    orchestrator.run_selected_symbols_cycle(
                        trigger_event="historical_replay",
                        upto_index=args.start_index + offset,
                    )
                )
            session.commit()
            output = {"cycles": len(outputs), "results": outputs}
        elif args.command == "export-schemas":
            export_schemas(Path("schemas/generated"))
            output = {"status": "ok", "target": "schemas/generated"}
        elif args.command == "backlog-auto-apply-supported":
            payload = auto_apply_supported_backlogs(session)
            session.commit()
            output = payload.model_dump(mode="json")
        else:
            output = {"status": "unknown"}
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
