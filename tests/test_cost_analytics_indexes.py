from sqlalchemy import create_engine, inspect, text
from trading_mvp.database import Base

COST_ANALYTICS_INDEXES = {
    "executions": "ix_executions_created_at",
    "orders": "ix_orders_mode_created_at",
    "positions": "ix_positions_mode_closed_at",
}


def test_cost_analytics_indexes_created_for_new_sqlite_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cost-indexes.db'}", future=True)
    try:
        Base.metadata.create_all(bind=engine)
        inspector = inspect(engine)

        for table_name, index_name in COST_ANALYTICS_INDEXES.items():
            index_names = {index["name"] for index in inspector.get_indexes(table_name)}
            assert index_name in index_names
    finally:
        engine.dispose()


def test_cost_analytics_indexes_can_be_applied_idempotently_to_existing_sqlite_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'existing-cost-indexes.db'}", future=True)
    try:
        Base.metadata.create_all(bind=engine)
        statements = [
            "CREATE INDEX IF NOT EXISTS ix_executions_created_at ON executions (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_orders_mode_created_at ON orders (mode, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_positions_mode_closed_at ON positions (mode, closed_at)",
        ]

        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
            for statement in statements:
                connection.execute(text(statement))
            rows = connection.execute(
                text(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'index'
                      AND name IN (
                        'ix_executions_created_at',
                        'ix_orders_mode_created_at',
                        'ix_positions_mode_closed_at'
                      )
                    """
                )
            ).all()

        assert {row[0] for row in rows} == set(COST_ANALYTICS_INDEXES.values())
    finally:
        engine.dispose()
