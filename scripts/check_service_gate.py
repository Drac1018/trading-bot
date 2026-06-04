from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = REPO_ROOT / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))


def _listener_reachable(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _listener_summary(ports: list[int]) -> list[dict[str, Any]]:
    return [{"port": port, "reachable": _listener_reachable(port)} for port in ports]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the Trading MVP service switch gate.")
    parser.add_argument("--recent-minutes", type=int, default=30)
    parser.add_argument("--check-dev-ports", default="8001,3001")
    parser.add_argument("--normalize-stale-history", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from trading_mvp.database import SessionLocal
    from trading_mvp.services.service_gate import (
        build_service_switch_gate_snapshot,
        normalize_stale_pending_entry_plan_history,
    )

    ports = [
        int(item.strip())
        for item in str(args.check_dev_ports or "").split(",")
        if item.strip()
    ]
    with SessionLocal() as session:
        normalization = (
            normalize_stale_pending_entry_plan_history(session)
            if args.normalize_stale_history
            else {"normalized_count": 0, "normalized_plans": []}
        )
        if args.normalize_stale_history:
            session.commit()
        payload = build_service_switch_gate_snapshot(
            session,
            recent_minutes=args.recent_minutes,
        )
    payload["stale_history_normalization"] = normalization
    listeners = _listener_summary(ports)
    dev_listener_blockers = [item for item in listeners if item["reachable"]]
    payload["dev_port_listeners"] = listeners
    if dev_listener_blockers:
        payload["gate_clear"] = False
        blockers = list(payload.get("blockers") or [])
        blockers.append("dev_port_listener")
        payload["blockers"] = blockers
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, default=str))
    return 0 if payload.get("gate_clear") else 2


if __name__ == "__main__":
    raise SystemExit(main())
