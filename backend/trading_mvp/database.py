from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from trading_mvp.config import get_settings, require_runtime_database_url


class Base(DeclarativeBase):
    pass


database_url = require_runtime_database_url("database engine initialization")
settings = get_settings()
_engine_kwargs: dict[str, object] = {"future": True}
if database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
else:
    _engine_kwargs["pool_pre_ping"] = True

engine = create_engine(database_url, **_engine_kwargs)

if database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.close()
else:
    @event.listens_for(engine, "connect")
    def _configure_postgresql_connection(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET idle_in_transaction_session_timeout = '30s';")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
