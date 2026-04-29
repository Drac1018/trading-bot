from __future__ import annotations

from pathlib import Path

from trading_mvp import config as config_module

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_settings_default_database_url_is_postgresql(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)

    settings = config_module.Settings(_env_file=None)

    assert settings.data_dir == (PROJECT_ROOT / "data").resolve()
    assert settings.database_url == "postgresql+psycopg://trading:trading@127.0.0.1:5432/trading_mvp"


def test_settings_relative_sqlite_override_is_project_root_relative(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./runtime/state.db")
    monkeypatch.setenv("DATA_DIR", "./runtime/data")

    settings = config_module.Settings(_env_file=None)

    assert settings.data_dir == (PROJECT_ROOT / "runtime" / "data").resolve()
    assert settings.database_url == f"sqlite:///{(PROJECT_ROOT / 'runtime' / 'state.db').resolve().as_posix()}"


def test_runtime_database_url_requires_explicit_configuration(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TRADING_MVP_ALLOW_SQLITE", raising=False)
    monkeypatch.setattr(config_module, "_read_dotenv_value", lambda _name: None)

    try:
        config_module.require_runtime_database_url("test startup")
    except RuntimeError as exc:
        assert "DATABASE_URL is required" in str(exc)
    else:
        raise AssertionError("missing DATABASE_URL should fail")


def test_runtime_database_url_accepts_utf8_bom_dotenv(monkeypatch, tmp_path) -> None:
    database_url = "postgresql+psycopg://trading:trading@127.0.0.1:5432/trading_mvp"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TRADING_MVP_ALLOW_SQLITE", raising=False)
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text(f"DATABASE_URL={database_url}\n", encoding="utf-8-sig")

    assert config_module.get_explicit_database_url() == database_url
    assert config_module.require_runtime_database_url("test startup") == database_url


def test_runtime_database_url_requires_sqlite_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./runtime/state.db")
    monkeypatch.delenv("TRADING_MVP_ALLOW_SQLITE", raising=False)
    monkeypatch.setattr(config_module, "_read_dotenv_value", lambda _name: None)

    try:
        config_module.require_runtime_database_url("test startup")
    except RuntimeError as exc:
        assert "TRADING_MVP_ALLOW_SQLITE=1" in str(exc)
    else:
        raise AssertionError("SQLite without opt-in should fail")

    monkeypatch.setenv("TRADING_MVP_ALLOW_SQLITE", "1")
    assert config_module.require_runtime_database_url("test startup") == (
        f"sqlite:///{(PROJECT_ROOT / 'runtime' / 'state.db').resolve().as_posix()}"
    )


def test_runtime_database_url_accepts_dotenv_sqlite_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./runtime/state.db")
    monkeypatch.delenv("TRADING_MVP_ALLOW_SQLITE", raising=False)
    monkeypatch.setattr(
        config_module,
        "_read_dotenv_value",
        lambda name: "1" if name == "TRADING_MVP_ALLOW_SQLITE" else None,
    )

    assert config_module.require_runtime_database_url("test startup") == (
        f"sqlite:///{(PROJECT_ROOT / 'runtime' / 'state.db').resolve().as_posix()}"
    )
