from __future__ import annotations

import os
import shutil
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/pytest_bootstrap.db")
os.environ.setdefault("TRADING_MVP_ALLOW_SQLITE", "1")
os.environ.setdefault("LIVE_TRADING_ENV_ENABLED", "true")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_SECRET_SEED", "change-me-local-dev-secret")
os.environ.setdefault("OPERATOR_API_KEY", "")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from trading_mvp.database import Base, get_db
from trading_mvp.main import app


@pytest.hookimpl(trylast=True)
def pytest_configure(config) -> None:
    _configure_windows_pytest_basetemp(config)


def _configure_windows_pytest_basetemp(config) -> None:
    if os.name != "nt":
        return
    if getattr(config.option, "basetemp", None) is not None:
        return

    tmp_path_factory = getattr(config, "_tmp_path_factory", None)
    if (
        tmp_path_factory is not None
        and getattr(tmp_path_factory, "_basetemp", None) is not None
    ):
        return

    # Avoid pytest's global Windows temp symlink, which can become undeletable.
    tmp_root = Path(config.rootpath) / "tmp"
    tmp_root.mkdir(exist_ok=True)
    basetemp = tmp_root / f"pytest-{os.getpid()}"
    config.option.basetemp = basetemp
    config._trading_mvp_windows_basetemp = basetemp

    if tmp_path_factory is not None:
        tmp_path_factory._given_basetemp = basetemp


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus) -> None:
    if exitstatus == 0:
        _cleanup_windows_pytest_basetemp(session.config)


def _cleanup_windows_pytest_basetemp(config) -> None:
    if os.name != "nt":
        return

    basetemp = getattr(config, "_trading_mvp_windows_basetemp", None)
    if basetemp is None:
        return

    basetemp = Path(basetemp)
    tmp_root = Path(config.rootpath) / "tmp"
    try:
        resolved_basetemp = basetemp.resolve()
        resolved_tmp_root = tmp_root.resolve()
    except OSError:
        return

    if (
        resolved_basetemp == resolved_tmp_root
        or resolved_tmp_root not in resolved_basetemp.parents
        or not basetemp.name.startswith("pytest-")
    ):
        return

    shutil.rmtree(basetemp, ignore_errors=True)


@pytest.fixture()
def db_session(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", future=True)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        with TestingSessionLocal() as session:
            try:
                yield session
            finally:
                session.rollback()
    finally:
        engine.dispose()


@pytest.fixture()
def full_live_operator_headers(monkeypatch):
    from trading_mvp.config import get_settings

    monkeypatch.setenv("APP_SECRET_SEED", "pytest-non-default-secret")
    monkeypatch.setenv("OPERATOR_API_KEY", "pytest-operator-key")
    get_settings.cache_clear()
    try:
        yield {"X-Operator-API-Key": "pytest-operator-key", "X-Operator-Intent": "operator.write"}
    finally:
        get_settings.cache_clear()


@pytest.fixture()
def testclient_db_factory(monkeypatch, tmp_path):
    created_engines = []

    async def _noop_background_loop() -> None:
        return None

    monkeypatch.setattr("trading_mvp.main._background_scheduler_loop", _noop_background_loop)
    monkeypatch.setattr("trading_mvp.main._background_user_stream_loop", _noop_background_loop)
    monkeypatch.setattr("trading_mvp.main._background_market_stream_loop", _noop_background_loop)

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
