from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from trading_mvp import main as main_module

try:
    import workers.scheduler as worker_scheduler
except ModuleNotFoundError as exc:
    if exc.name != "redis":
        raise
    worker_scheduler = None


def test_sqlite_background_guard_skips_overlapping_tick(tmp_path, monkeypatch) -> None:
    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'background_guard.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(main_module, "engine", test_engine)
    monkeypatch.setattr(main_module, "_sqlite_background_write_guard", threading.Lock())

    tick_started = threading.Event()
    release_tick = threading.Event()
    tick_results: list[int] = []

    def slow_tick() -> int:
        tick_started.set()
        assert release_tick.wait(timeout=1.0)
        return 15

    tick_thread = threading.Thread(
        target=lambda: tick_results.append(
            main_module._run_background_tick_with_sqlite_guard(slow_tick, 1)
        )
    )
    tick_thread.start()

    assert tick_started.wait(timeout=1.0)
    skipped_sleep = main_module._run_background_tick_with_sqlite_guard(
        lambda: pytest.fail("overlapping sqlite writer tick should be skipped"),
        1,
    )

    release_tick.set()
    tick_thread.join(timeout=1.0)

    assert skipped_sleep == 1
    assert tick_results == [15]


def test_background_failure_logging_is_best_effort(monkeypatch) -> None:
    calls: list[str] = []

    class FailingRecoverySession:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001, ANN204
            return False

        def commit(self) -> None:
            raise RuntimeError("database is locked")

    monkeypatch.setattr(main_module, "record_audit_event", lambda *args, **kwargs: calls.append("audit"))
    monkeypatch.setattr(main_module, "record_health_event", lambda *args, **kwargs: calls.append("health"))

    main_module._record_background_loop_failure(
        lambda: FailingRecoverySession(),
        event_type="background_scheduler_failed",
        entity_type="scheduler",
        entity_id="background",
        severity="error",
        component="scheduler",
        message="Background scheduler loop failed.",
        payload={"error": "locked"},
    )

    assert calls == ["audit", "health"]


def test_sqlite_background_loops_are_disabled_by_default(monkeypatch) -> None:
    sqlite_engine = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    monkeypatch.setattr(main_module, "engine", sqlite_engine)
    monkeypatch.delenv("TRADING_MVP_ENABLE_BACKGROUND_SCHEDULER", raising=False)
    monkeypatch.delenv("TRADING_MVP_ENABLE_BACKGROUND_USER_STREAM", raising=False)

    assert main_module._background_scheduler_enabled() is False
    assert main_module._background_user_stream_enabled() is False


def test_background_loops_can_be_reenabled_on_sqlite(monkeypatch) -> None:
    sqlite_engine = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    monkeypatch.setattr(main_module, "engine", sqlite_engine)
    monkeypatch.setenv("TRADING_MVP_ENABLE_BACKGROUND_SCHEDULER", "true")
    monkeypatch.setenv("TRADING_MVP_ENABLE_BACKGROUND_USER_STREAM", "1")

    assert main_module._background_scheduler_enabled() is True
    assert main_module._background_user_stream_enabled() is True


def test_worker_scheduler_recovery_logging_is_best_effort(monkeypatch) -> None:
    if worker_scheduler is None:
        pytest.skip("redis is not installed")

    calls: list[str] = []

    class FailingRecoverySession:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001, ANN204
            return False

        def commit(self) -> None:
            raise RuntimeError("database is locked")

    monkeypatch.setattr(worker_scheduler, "SessionLocal", lambda: FailingRecoverySession())
    monkeypatch.setattr(worker_scheduler, "record_health_event", lambda *args, **kwargs: calls.append("health"))

    worker_scheduler._record_scheduler_loop_failure(RuntimeError("locked"))

    assert calls == ["health"]
