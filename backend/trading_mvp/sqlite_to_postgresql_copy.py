from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.sqltypes import JSON

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_TABLES = {"alembic_version"}
PRIORITY_TABLES = (
    "users",
    "settings",
    "market_snapshots",
    "feature_snapshots",
    "agent_runs",
    "risk_checks",
    "pending_entry_plans",
    "positions",
    "orders",
    "executions",
    "pnl_snapshots",
    "account_ledger_entries",
    "skipped_trade_events",
    "alerts",
    "scheduler_runs",
    "competitor_notes",
    "ui_feedback",
    "system_health_events",
    "audit_events",
)


@dataclass(frozen=True)
class TableCopyPlan:
    tables: list[str]
    source_only_tables: list[str]
    target_only_tables: list[str]


@dataclass(frozen=True)
class TableCopySummary:
    table: str
    source_count: int
    target_count_before: int
    copied_count: int
    target_count_after: int


@dataclass(frozen=True)
class SourcePreflightSummary:
    tables: list[str]
    source_counts: dict[str, int]
    total_rows: int


def _resolve_sqlite_url(database_url: str) -> str:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return database_url
    if url.database in {None, "", ":memory:"}:
        return database_url
    database_path = Path(url.database)
    if not database_path.is_absolute():
        database_path = (PROJECT_ROOT / database_path).resolve()
    return str(url.set(database=database_path.as_posix()))


def validate_source_url(source_url: str) -> str:
    resolved_source_url = _resolve_sqlite_url(source_url)
    if not make_url(resolved_source_url).drivername.startswith("sqlite"):
        raise ValueError("source_url must point to sqlite:// for one-shot copy.")
    return resolved_source_url


def validate_copy_urls(*, source_url: str, target_url: str) -> tuple[str, str]:
    resolved_source_url = validate_source_url(source_url)
    resolved_target_url = _resolve_sqlite_url(target_url)

    if make_url(resolved_target_url).drivername.startswith("sqlite"):
        raise ValueError("target_url must point to PostgreSQL, not sqlite://.")
    if not make_url(resolved_target_url).drivername.startswith("postgresql"):
        raise ValueError("target_url must point to postgresql+psycopg://.")

    return resolved_source_url, resolved_target_url


def plan_tables_to_copy(*, source_tables: set[str], target_tables: set[str]) -> TableCopyPlan:
    source_user_tables = source_tables - SYSTEM_TABLES
    target_user_tables = target_tables - SYSTEM_TABLES
    common_tables = source_user_tables & target_user_tables
    ordered_tables = [table for table in PRIORITY_TABLES if table in common_tables]
    ordered_tables.extend(sorted(common_tables - set(ordered_tables)))
    return TableCopyPlan(
        tables=ordered_tables,
        source_only_tables=sorted(source_user_tables - target_user_tables),
        target_only_tables=sorted(target_user_tables - source_user_tables),
    )


def _target_tables_requiring_empty_check(table_plan: TableCopyPlan) -> list[str]:
    return [*table_plan.tables, *table_plan.target_only_tables]


def _order_source_tables(source_tables: set[str]) -> list[str]:
    source_user_tables = source_tables - SYSTEM_TABLES
    ordered_tables = [table for table in PRIORITY_TABLES if table in source_user_tables]
    ordered_tables.extend(sorted(source_user_tables - set(ordered_tables)))
    return ordered_tables


def _reflect_table(connection: Connection, table_name: str) -> Table:
    metadata = MetaData()
    return Table(table_name, metadata, autoload_with=connection)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _count_rows(connection: Connection, table_name: str) -> int:
    quoted_table = _quote_identifier(table_name)
    return int(connection.execute(text(f"select count(*) from {quoted_table}")).scalar_one())


def _json_normalize(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _normalize_row_for_target(row: dict[str, Any], target_table: Table) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for column in target_table.columns:
        if column.name not in row:
            continue
        value = row[column.name]
        if isinstance(column.type, JSON) and value is not None:
            normalized[column.name] = _json_normalize(value)
            continue
        normalized[column.name] = value
    return normalized


def _find_column_mismatches(source_table: Table, target_table: Table) -> dict[str, list[str]]:
    source_columns = {column.name for column in source_table.columns}
    target_columns = {column.name for column in target_table.columns}
    return {
        "source_only_columns": sorted(source_columns - target_columns),
        "target_only_columns": sorted(target_columns - source_columns),
    }


def _check_source_pause_state(source_connection: Connection) -> None:
    inspector = inspect(source_connection)
    if "settings" not in set(inspector.get_table_names()):
        raise RuntimeError("source SQLite database is missing the settings table.")

    settings_table = _reflect_table(source_connection, "settings")
    row = source_connection.execute(select(settings_table)).mappings().first()
    if row is None:
        raise RuntimeError("source SQLite database has no settings row.")
    if not bool(row.get("trading_paused")):
        raise RuntimeError("source settings.trading_paused must be true before one-shot copy.")
    if bool(row.get("live_execution_armed")):
        raise RuntimeError("source settings.live_execution_armed must be false before one-shot copy.")


def _check_target_is_empty(target_connection: Connection, tables: list[str]) -> None:
    non_empty_tables = [table for table in tables if _count_rows(target_connection, table) > 0]
    if non_empty_tables:
        raise RuntimeError(
            "target PostgreSQL is not empty for one-shot copy: " + ", ".join(non_empty_tables)
        )


def _copy_table_rows(
    *,
    source_connection: Connection,
    target_connection: Connection,
    table_name: str,
    batch_size: int,
) -> tuple[int, int, int, int]:
    source_table = _reflect_table(source_connection, table_name)
    target_table = _reflect_table(target_connection, table_name)
    source_count = _count_rows(source_connection, table_name)
    target_count_before = _count_rows(target_connection, table_name)
    copied_count = 0

    result = source_connection.execute(select(source_table)).mappings()
    while True:
        batch_rows = result.fetchmany(batch_size)
        if not batch_rows:
            break
        payload = [_normalize_row_for_target(dict(row), target_table) for row in batch_rows]
        if payload:
            target_connection.execute(target_table.insert(), payload)
            copied_count += len(payload)

    target_count_after = _count_rows(target_connection, table_name)
    return source_count, target_count_before, copied_count, target_count_after


def _reset_postgresql_sequences(target_connection: Connection, tables: list[str]) -> None:
    inspector = inspect(target_connection)
    for table_name in tables:
        primary_key = inspector.get_pk_constraint(table_name)
        constrained_columns = primary_key.get("constrained_columns") or []
        if len(constrained_columns) != 1:
            continue
        column_name = constrained_columns[0]
        quoted_table = _quote_identifier(table_name)
        quoted_column = _quote_identifier(column_name)
        sequence_name = target_connection.execute(
            text("select pg_get_serial_sequence(:relation, :column)"),
            {"relation": table_name, "column": column_name},
        ).scalar_one_or_none()
        if not sequence_name:
            continue
        target_connection.execute(
            text(
                f"""
                select setval(
                    to_regclass(:sequence_name),
                    coalesce((select max({quoted_column}) from {quoted_table}), 1),
                    (select max({quoted_column}) is not null from {quoted_table})
                )
                """
            ),
            {"sequence_name": sequence_name},
        )


def build_copy_plan(
    *,
    source_url: str,
    target_url: str,
    allow_unpaused_source: bool,
    allow_nonempty_target: bool,
    allow_column_mismatch: bool,
) -> tuple[TableCopyPlan, dict[str, dict[str, list[str]]], dict[str, int], dict[str, int]]:
    source_url, target_url = validate_copy_urls(source_url=source_url, target_url=target_url)
    source_engine = create_engine(source_url, future=True)
    target_engine = create_engine(target_url, future=True)
    try:
        with source_engine.connect() as source_connection, target_engine.connect() as target_connection:
            source_tables = set(inspect(source_connection).get_table_names())
            target_tables = set(inspect(target_connection).get_table_names())
            table_plan = plan_tables_to_copy(source_tables=source_tables, target_tables=target_tables)
            column_mismatches: dict[str, dict[str, list[str]]] = {}

            if not allow_unpaused_source:
                _check_source_pause_state(source_connection)
            if not allow_nonempty_target:
                _check_target_is_empty(target_connection, _target_tables_requiring_empty_check(table_plan))

            source_counts = {table: _count_rows(source_connection, table) for table in table_plan.tables}
            target_counts = {table: _count_rows(target_connection, table) for table in table_plan.tables}

            for table_name in table_plan.tables:
                source_table = _reflect_table(source_connection, table_name)
                target_table = _reflect_table(target_connection, table_name)
                mismatches = _find_column_mismatches(source_table, target_table)
                if mismatches["source_only_columns"] or mismatches["target_only_columns"]:
                    column_mismatches[table_name] = mismatches

            if column_mismatches and not allow_column_mismatch:
                raise RuntimeError(
                    "column mismatch detected for one-shot copy: "
                    + ", ".join(sorted(column_mismatches))
                    + ". Re-run with --allow-column-mismatch only after review."
                )

            return table_plan, column_mismatches, source_counts, target_counts
    finally:
        source_engine.dispose()
        target_engine.dispose()


def build_source_preflight(
    *,
    source_url: str,
    allow_unpaused_source: bool,
) -> SourcePreflightSummary:
    source_url = validate_source_url(source_url)
    source_engine = create_engine(source_url, future=True)
    try:
        with source_engine.connect() as source_connection:
            if not allow_unpaused_source:
                _check_source_pause_state(source_connection)
            tables = _order_source_tables(set(inspect(source_connection).get_table_names()))
            source_counts = {table: _count_rows(source_connection, table) for table in tables}
            return SourcePreflightSummary(
                tables=tables,
                source_counts=source_counts,
                total_rows=sum(source_counts.values()),
            )
    finally:
        source_engine.dispose()


def _assert_copy_summary(summary: TableCopySummary) -> None:
    if summary.copied_count != summary.source_count:
        raise RuntimeError(
            f"{summary.table}: copied_count={summary.copied_count} does not match "
            f"source_count={summary.source_count}."
        )
    expected_target_count = summary.target_count_before + summary.source_count
    if summary.target_count_after != expected_target_count:
        raise RuntimeError(
            f"{summary.table}: target_count_after={summary.target_count_after} does not match "
            f"expected_target_count={expected_target_count}."
        )


def run_one_shot_copy(
    *,
    source_url: str,
    target_url: str,
    batch_size: int,
    allow_unpaused_source: bool,
    allow_nonempty_target: bool,
    allow_column_mismatch: bool,
) -> tuple[TableCopyPlan, dict[str, dict[str, list[str]]], list[TableCopySummary]]:
    _reject_apply_overrides(
        allow_unpaused_source=allow_unpaused_source,
        allow_nonempty_target=allow_nonempty_target,
        allow_column_mismatch=allow_column_mismatch,
    )
    source_url, target_url = validate_copy_urls(source_url=source_url, target_url=target_url)
    table_plan, column_mismatches, _source_counts, _target_counts = build_copy_plan(
        source_url=source_url,
        target_url=target_url,
        allow_unpaused_source=allow_unpaused_source,
        allow_nonempty_target=allow_nonempty_target,
        allow_column_mismatch=allow_column_mismatch,
    )
    source_engine = create_engine(source_url, future=True)
    target_engine = create_engine(target_url, future=True)
    summaries: list[TableCopySummary] = []
    try:
        with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
            for table_name in table_plan.tables:
                source_count, target_count_before, copied_count, target_count_after = _copy_table_rows(
                    source_connection=source_connection,
                    target_connection=target_connection,
                    table_name=table_name,
                    batch_size=batch_size,
                )
                summaries.append(
                    TableCopySummary(
                        table=table_name,
                        source_count=source_count,
                        target_count_before=target_count_before,
                        copied_count=copied_count,
                        target_count_after=target_count_after,
                    )
                )
                _assert_copy_summary(summaries[-1])
            _reset_postgresql_sequences(target_connection, table_plan.tables)
        return table_plan, column_mismatches, summaries
    finally:
        source_engine.dispose()
        target_engine.dispose()


def _print_plan(
    *,
    table_plan: TableCopyPlan,
    column_mismatches: dict[str, dict[str, list[str]]],
    source_counts: dict[str, int],
    target_counts: dict[str, int],
) -> None:
    print("One-shot SQLite -> PostgreSQL copy plan:")
    print(f"  tables={len(table_plan.tables)}")
    if table_plan.source_only_tables:
        print(f"  source_only_tables={', '.join(table_plan.source_only_tables)}")
    if table_plan.target_only_tables:
        print(f"  target_only_tables={', '.join(table_plan.target_only_tables)}")
    if column_mismatches:
        print("  column_mismatches:")
        for table_name, mismatches in column_mismatches.items():
            print(f"    - {table_name}: {json.dumps(mismatches, ensure_ascii=False)}")
    print("  row_counts:")
    for table_name in table_plan.tables:
        print(f"    - {table_name}: source={source_counts[table_name]} target_before={target_counts[table_name]}")


def _print_apply_result(summaries: list[TableCopySummary]) -> None:
    print("One-shot SQLite -> PostgreSQL copy applied:")
    for summary in summaries:
        print(
            "  - "
            f"{summary.table}: source={summary.source_count} "
            f"target_before={summary.target_count_before} "
            f"copied={summary.copied_count} "
            f"target_after={summary.target_count_after}"
        )


def _print_source_preflight(summary: SourcePreflightSummary) -> None:
    print("Source SQLite preflight:")
    print(f"  tables={len(summary.tables)} total_rows={summary.total_rows}")
    print("  row_counts:")
    for table_name in summary.tables:
        print(f"    - {table_name}: source={summary.source_counts[table_name]}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply one-shot SQLite -> PostgreSQL data copy for Trading MVP."
    )
    parser.add_argument("--source-url", required=True, help="sqlite:// source database URL")
    parser.add_argument("--target-url", help="postgresql+psycopg:// target database URL")
    parser.add_argument("--batch-size", type=int, default=500, help="row batch size for apply mode")
    parser.add_argument("--apply", action="store_true", help="apply the copy. default is dry-run only.")
    parser.add_argument(
        "--source-only-preflight",
        action="store_true",
        help="inspect the source SQLite database without connecting to PostgreSQL",
    )
    parser.add_argument(
        "--allow-unpaused-source",
        action="store_true",
        help="skip the source settings pause/live-arm safety check",
    )
    parser.add_argument(
        "--allow-nonempty-target",
        action="store_true",
        help="allow copy into a non-empty target database after manual review",
    )
    parser.add_argument(
        "--allow-column-mismatch",
        action="store_true",
        help="allow source/target column mismatches after manual review",
    )
    return parser


def _reject_apply_overrides(
    *,
    allow_unpaused_source: bool,
    allow_nonempty_target: bool,
    allow_column_mismatch: bool,
) -> None:
    blocked_flags = []
    if allow_unpaused_source:
        blocked_flags.append("--allow-unpaused-source")
    if allow_nonempty_target:
        blocked_flags.append("--allow-nonempty-target")
    if allow_column_mismatch:
        blocked_flags.append("--allow-column-mismatch")
    if blocked_flags:
        raise RuntimeError(
            "apply mode requires paused source, empty target, and aligned columns. "
            f"Remove apply override flag(s): {', '.join(blocked_flags)}."
        )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.source_only_preflight:
            summary = build_source_preflight(
                source_url=args.source_url,
                allow_unpaused_source=args.allow_unpaused_source,
            )
            _print_source_preflight(summary)
            return 0
        if not args.target_url:
            raise ValueError("--target-url is required unless --source-only-preflight is set.")
        if args.apply:
            _reject_apply_overrides(
                allow_unpaused_source=args.allow_unpaused_source,
                allow_nonempty_target=args.allow_nonempty_target,
                allow_column_mismatch=args.allow_column_mismatch,
            )
        source_url, target_url = validate_copy_urls(source_url=args.source_url, target_url=args.target_url)
        table_plan, column_mismatches, source_counts, target_counts = build_copy_plan(
            source_url=source_url,
            target_url=target_url,
            allow_unpaused_source=args.allow_unpaused_source,
            allow_nonempty_target=args.allow_nonempty_target,
            allow_column_mismatch=args.allow_column_mismatch,
        )
        _print_plan(
            table_plan=table_plan,
            column_mismatches=column_mismatches,
            source_counts=source_counts,
            target_counts=target_counts,
        )
        if not args.apply:
            print("Dry-run only. Re-run with --apply to copy rows.")
            return 0
        _table_plan, _column_mismatches, summaries = run_one_shot_copy(
            source_url=source_url,
            target_url=target_url,
            batch_size=max(1, int(args.batch_size)),
            allow_unpaused_source=args.allow_unpaused_source,
            allow_nonempty_target=args.allow_nonempty_target,
            allow_column_mismatch=args.allow_column_mismatch,
        )
        _print_apply_result(summaries)
        return 0
    except (SQLAlchemyError, RuntimeError, ValueError) as exc:
        print(f"[sqlite-to-postgresql-copy] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
