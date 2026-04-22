from __future__ import annotations

from pathlib import Path

from trading_mvp import config as config_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_settings_default_sqlite_path_uses_project_root(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)

    settings = config_module.Settings(_env_file=None)

    assert settings.data_dir == (PROJECT_ROOT / "data").resolve()
    assert settings.database_url == f"sqlite:///{(PROJECT_ROOT / 'data' / 'trading_mvp.db').resolve().as_posix()}"


def test_settings_relative_sqlite_override_is_project_root_relative(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./runtime/state.db")
    monkeypatch.setenv("DATA_DIR", "./runtime/data")

    settings = config_module.Settings(_env_file=None)

    assert settings.data_dir == (PROJECT_ROOT / "runtime" / "data").resolve()
    assert settings.database_url == f"sqlite:///{(PROJECT_ROOT / 'runtime' / 'state.db').resolve().as_posix()}"
