from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_DIR = (_PROJECT_ROOT / "data").resolve()
_DEFAULT_DATABASE_PATH = (_DEFAULT_DATA_DIR / "trading_mvp.db").resolve()


def _resolve_project_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (_PROJECT_ROOT / candidate).resolve()


def _sqlite_url_from_path(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _resolve_sqlite_database_url(database_url: str) -> str:
    try:
        url = make_url(database_url)
    except Exception:
        return database_url

    if not url.drivername.startswith("sqlite"):
        return database_url
    if url.database in {None, "", ":memory:"}:
        return database_url

    return str(url.set(database=_resolve_project_path(url.database).as_posix()))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    frontend_port: int = 3000
    database_url: str = _sqlite_url_from_path(_DEFAULT_DATABASE_PATH)
    redis_url: str = "redis://localhost:6379/0"
    default_symbol: str = "BTCUSDT"
    tracked_symbols: str = "BTCUSDT"
    default_timeframe: str = "15m"
    exchange_sync_interval_seconds: int = 60
    market_refresh_interval_minutes: int = 1
    position_management_interval_seconds: int = 60
    live_trading_enabled: bool = False
    live_trading_env_enabled: bool = False
    manual_live_approval: bool = False
    trading_paused: bool = False
    max_leverage: float = 5.0
    max_risk_per_trade: float = 0.02
    max_daily_loss: float = 0.05
    max_consecutive_losses: int = 3
    max_gross_exposure_pct: float = 3.0
    max_largest_position_pct: float = 1.5
    max_directional_bias_pct: float = 2.0
    max_same_tier_concentration_pct: float = 2.5
    stale_market_seconds: int = 1800
    slippage_threshold_pct: float = 0.003
    adaptive_signal_enabled: bool = False
    position_management_enabled: bool = True
    break_even_enabled: bool = True
    atr_trailing_stop_enabled: bool = True
    partial_take_profit_enabled: bool = True
    partial_tp_rr: float = 1.5
    partial_tp_size_pct: float = 0.25
    move_stop_to_be_rr: float = 1.0
    time_stop_enabled: bool = False
    time_stop_minutes: int = 120
    time_stop_profit_floor: float = 0.15
    holding_edge_decay_enabled: bool = True
    reduce_on_regime_shift_enabled: bool = True
    schedule_windows: str = "1h,4h,12h,24h"
    mock_provider_enabled: bool = True
    ai_enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    app_secret_seed: str = "change-me-local-dev-secret"
    ai_provider: str = "openai"
    ai_call_interval_minutes: int = 10
    decision_cycle_interval_minutes: int = 5
    ai_max_input_candles: int = 32
    ai_temperature: float = 0.1
    binance_market_data_enabled: bool = False
    binance_testnet_enabled: bool = False
    binance_futures_enabled: bool = True
    exchange_recv_window_ms: int = 5000
    next_public_api_base_url: str = "http://localhost:8000"
    data_dir: Path = Field(default_factory=lambda: _DEFAULT_DATA_DIR)

    def model_post_init(self, __context: Any) -> None:
        self.data_dir = _resolve_project_path(self.data_dir)
        self.database_url = _resolve_sqlite_database_url(self.database_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
