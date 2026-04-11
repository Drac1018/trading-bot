from __future__ import annotations

import sys
import time

from redis import Redis
from sqlalchemy import desc, select
from trading_mvp.config import get_settings
from trading_mvp.database import SessionLocal
from trading_mvp.models import SchedulerRun
from trading_mvp.services.audit import record_health_event
from trading_mvp.services.pause_control import attempt_auto_resume
from trading_mvp.services.scheduler import (
    WINDOW_HOURS,
    is_interval_decision_due,
    run_due_interval_decision_cycle,
    run_window,
)
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive
from trading_mvp.worker_jobs import run_interval_decision_cycle_job, run_window_job


def _due_windows() -> list[str]:
    with SessionLocal() as session:
        settings_row = get_or_create_settings(session)
        if not settings_row.ai_enabled:
            return []
        due: list[str] = []
        for window in settings_row.schedule_windows:
            if window == "1h":
                continue
            latest = session.scalar(
                select(SchedulerRun)
                .where(SchedulerRun.schedule_window == window)
                .order_by(desc(SchedulerRun.created_at))
                .limit(1)
            )
            if latest is None or latest.next_run_at is None or latest.next_run_at <= utcnow_naive():
                due.append(window)
        return due


def main() -> None:
    settings = get_settings()
    interval_seconds = 60
    redis_connection: Redis | None = None
    rq_queue_class = None
    if sys.platform != "win32":
        try:
            from rq import Queue as RQQueue  # type: ignore[import-not-found]

            rq_queue_class = RQQueue
        except Exception:
            rq_queue_class = None
    try:
        redis_connection = Redis.from_url(settings.redis_url)
        redis_connection.ping()
    except Exception:
        redis_connection = None

    while True:
        try:
            with SessionLocal() as session:
                attempt_auto_resume(session, get_or_create_settings(session))
                session.commit()
            windows = _due_windows()
            with SessionLocal() as session:
                interval_due = is_interval_decision_due(session)
            if redis_connection is not None and rq_queue_class is not None:
                if interval_due:
                    rq_queue_class("trading", connection=redis_connection).enqueue(run_interval_decision_cycle_job)
                for window in windows:
                    queue_name = "trading" if WINDOW_HOURS[window] == 1 else "reviews"
                    rq_queue_class(queue_name, connection=redis_connection).enqueue(run_window_job, window)
            else:
                with SessionLocal() as session:
                    run_due_interval_decision_cycle(session)
                    for window in windows:
                        run_window(session, window, triggered_by="scheduler-inline")
                    session.commit()
        except Exception as exc:
            with SessionLocal() as session:
                record_health_event(
                    session,
                    component="scheduler",
                    status="error",
                    message="Scheduler loop recovered from an unexpected error.",
                    payload={"error": str(exc)},
                )
                session.commit()
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
