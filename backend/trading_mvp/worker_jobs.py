from __future__ import annotations

from trading_mvp.database import SessionLocal
from trading_mvp.services.orchestrator import TradingOrchestrator
from trading_mvp.services.scheduler import run_interval_decision_cycle, run_window


def run_decision_cycle_job(symbol: str | None = None, timeframe: str | None = None) -> dict[str, object]:
    with SessionLocal() as session:
        output = TradingOrchestrator(session).run_decision_cycle(symbol=symbol, timeframe=timeframe, trigger_event="worker")
        session.commit()
        return output


def run_window_job(window: str) -> dict[str, object]:
    with SessionLocal() as session:
        output = run_window(session, window=window, triggered_by="worker")
        session.commit()
        return output


def run_interval_decision_cycle_job() -> dict[str, object]:
    with SessionLocal() as session:
        output = run_interval_decision_cycle(session, triggered_by="worker")
        session.commit()
        return output
