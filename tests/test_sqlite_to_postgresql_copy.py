from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from trading_mvp.sqlite_to_postgresql_copy import (
    TableCopyPlan,
    _target_tables_requiring_empty_check,
    build_copy_plan,
    build_source_preflight,
    plan_tables_to_copy,
    run_one_shot_copy,
    validate_copy_urls,
)


def test_validate_copy_urls_requires_sqlite_source_and_postgresql_target() -> None:
    source_url, target_url = validate_copy_urls(
        source_url="sqlite:///./data/source.db",
        target_url="postgresql+psycopg://user:pass@localhost:5432/trading_mvp",
    )

    assert source_url.startswith("sqlite:///")
    assert target_url == "postgresql+psycopg://user:pass@localhost:5432/trading_mvp"

    with pytest.raises(ValueError, match="source_url must point to sqlite://"):
        validate_copy_urls(
            source_url="postgresql+psycopg://user:pass@localhost:5432/source_db",
            target_url="postgresql+psycopg://user:pass@localhost:5432/trading_mvp",
        )

    with pytest.raises(ValueError, match="target_url must point to PostgreSQL"):
        validate_copy_urls(
            source_url="sqlite:///./data/source.db",
            target_url="sqlite:///./data/target.db",
        )


def test_plan_tables_to_copy_prioritizes_operational_tables_and_ignores_system_tables() -> None:
    plan = plan_tables_to_copy(
        source_tables={
            "alembic_version",
            "audit_events",
            "orders",
            "positions",
            "settings",
            "unknown_table",
        },
        target_tables={
            "alembic_version",
            "audit_events",
            "orders",
            "positions",
            "settings",
            "system_health_events",
        },
    )

    assert plan.tables == ["settings", "positions", "orders", "audit_events"]
    assert plan.source_only_tables == ["unknown_table"]
    assert plan.target_only_tables == ["system_health_events"]


def test_empty_target_check_includes_target_only_tables() -> None:
    plan = TableCopyPlan(
        tables=["settings", "orders"],
        source_only_tables=[],
        target_only_tables=["future_table"],
    )

    assert _target_tables_requiring_empty_check(plan) == ["settings", "orders", "future_table"]


def test_build_source_preflight_reports_ordered_row_counts(tmp_path) -> None:
    source_path = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{source_path}", future=True)
    with engine.begin() as connection:
        connection.execute(text("create table settings (trading_paused integer, live_execution_armed integer)"))
        connection.execute(text("insert into settings values (1, 0)"))
        connection.execute(text("create table orders (id integer primary key, symbol text)"))
        connection.execute(text("insert into orders (symbol) values ('BTCUSDT'), ('ETHUSDT')"))
        connection.execute(text("create table alembic_version (version_num text)"))

    summary = build_source_preflight(
        source_url=f"sqlite:///{source_path}",
        allow_unpaused_source=False,
    )

    assert summary.tables == ["settings", "orders"]
    assert summary.source_counts == {"settings": 1, "orders": 2}
    assert summary.total_rows == 3


def test_build_copy_plan_rejects_non_postgresql_target_even_when_called_directly() -> None:
    with pytest.raises(ValueError, match="target_url must point to PostgreSQL"):
        build_copy_plan(
            source_url="sqlite:///./data/source.db",
            target_url="sqlite:///./data/target.db",
            allow_unpaused_source=True,
            allow_nonempty_target=True,
            allow_column_mismatch=True,
        )


@pytest.mark.parametrize(
    ("allow_unpaused_source", "allow_nonempty_target", "allow_column_mismatch", "flag"),
    [
        (True, False, False, "--allow-unpaused-source"),
        (False, True, False, "--allow-nonempty-target"),
        (False, False, True, "--allow-column-mismatch"),
    ],
)
def test_run_one_shot_copy_rejects_apply_overrides(
    allow_unpaused_source: bool,
    allow_nonempty_target: bool,
    allow_column_mismatch: bool,
    flag: str,
) -> None:
    with pytest.raises(RuntimeError, match=flag):
        run_one_shot_copy(
            source_url="sqlite:///./data/source.db",
            target_url="postgresql+psycopg://user:pass@localhost:5432/trading_mvp",
            batch_size=500,
            allow_unpaused_source=allow_unpaused_source,
            allow_nonempty_target=allow_nonempty_target,
            allow_column_mismatch=allow_column_mismatch,
        )
