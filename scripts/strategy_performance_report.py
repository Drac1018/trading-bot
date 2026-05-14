from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read closed trade records and aggregate performance by strategy/regime/confirmation/risk mode.",
    )
    parser.add_argument("--strategy-id", help="Filter by strategy_id, e.g. range_mean_reversion_engine.")
    parser.add_argument("--regime-id", help="Filter by regime_id, e.g. range:range.")
    parser.add_argument("--confirmation-type", help="Filter by confirmation type, e.g. range_edge_confirm.")
    parser.add_argument("--direction", choices=["long", "short"], help="Filter by trade direction.")
    parser.add_argument("--symbol", help="Filter by symbol, e.g. BTCUSDT or ETHUSDT.")
    parser.add_argument("--risk-mode", help="Filter by risk_mode, e.g. drawdown_recovery or normal.")
    parser.add_argument("--mode", help="Filter by stored position mode, e.g. live, paper, live_dry_run.")
    parser.add_argument("--since", help="Closed-at lower bound as ISO datetime.")
    parser.add_argument("--until", help="Closed-at upper bound as ISO datetime.")
    parser.add_argument("--limit", type=int, help="Maximum closed positions to inspect after time filters.")
    parser.add_argument(
        "--group-by",
        default="strategy_id,regime_id,confirmation_type,risk_mode,symbol,direction",
        help="Comma-separated group keys. Supported: strategy_id,regime_id,confirmation_type,risk_mode,symbol,direction,mode.",
    )
    parser.add_argument("--format", choices=["table", "csv", "json"], default="table")
    parser.add_argument("--output", help="Write output to a file instead of stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from trading_mvp.database import SessionLocal
    from trading_mvp.services.strategy_performance_report import (
        StrategyPerformanceFilters,
        build_strategy_performance_report,
        format_console_report,
        format_csv_report,
        format_json_report,
    )

    filters = StrategyPerformanceFilters(
        strategy_id=args.strategy_id,
        regime_id=args.regime_id,
        confirmation_type=args.confirmation_type,
        direction=args.direction,
        symbol=args.symbol,
        risk_mode=args.risk_mode,
        mode=args.mode,
        since=_parse_datetime(args.since),
        until=_parse_datetime(args.until),
        limit=args.limit,
    )
    group_by = [item.strip() for item in args.group_by.split(",") if item.strip()]
    with SessionLocal() as session:
        report = build_strategy_performance_report(session, filters=filters, group_by=group_by)
    if args.format == "json":
        output = format_json_report(report)
    elif args.format == "csv":
        output = format_csv_report(report)
    else:
        output = format_console_report(report)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
