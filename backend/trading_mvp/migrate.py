from __future__ import annotations

import subprocess
import sys

from sqlalchemy import create_engine, inspect, text

from trading_mvp.config import get_settings

APP_TABLES = {
    "users",
    "settings",
    "market_snapshots",
    "feature_snapshots",
    "agent_runs",
    "risk_checks",
    "positions",
    "orders",
    "executions",
    "pnl_snapshots",
    "alerts",
    "scheduler_runs",
    "competitor_notes",
    "ui_feedback",
    "system_health_events",
    "audit_events",
}
INITIAL_REVISION = "855703716928"


def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, future=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    version_rows = 0
    if "alembic_version" in tables:
        with engine.connect() as connection:
            version_rows = len(list(connection.execute(text("select version_num from alembic_version"))))

    if (("alembic_version" not in tables) or version_rows == 0) and tables.intersection(APP_TABLES):
        subprocess.check_call([sys.executable, "-m", "alembic", "stamp", INITIAL_REVISION])
        subprocess.check_call([sys.executable, "-m", "alembic", "upgrade", "head"])
    else:
        subprocess.check_call([sys.executable, "-m", "alembic", "upgrade", "head"])


if __name__ == "__main__":
    main()
