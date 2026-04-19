from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from trading_mvp.database import Base, get_db
from trading_mvp.main import app


@pytest.fixture()
def db_session(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with TestingSessionLocal() as session:
        yield session
        session.rollback()


@pytest.fixture()
def testclient_db_factory(monkeypatch, tmp_path):
    created_engines = []

    async def _noop_background_loop() -> None:
        return None

    monkeypatch.setattr("trading_mvp.main._background_scheduler_loop", _noop_background_loop)
    monkeypatch.setattr("trading_mvp.main._background_user_stream_loop", _noop_background_loop)

    def factory(db_name: str):
        test_engine = create_engine(
            f"sqlite:///{tmp_path / db_name}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        testing_session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=test_engine)
        monkeypatch.setattr("trading_mvp.main.engine", test_engine)

        def override_get_db():
            with testing_session() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        created_engines.append(test_engine)
        return testing_session

    yield factory

    app.dependency_overrides.clear()
    for engine in created_engines:
        engine.dispose()
