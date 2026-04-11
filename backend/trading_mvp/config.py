from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    frontend_port: int = 3000
    database_url: str = "sqlite:///./data/trading_mvp.db"
    redis_url: str = "redis://localhost:6379/0"
    default_symbol: str = "BTCUSDT"
    tracked_symbols: str = "BTCUSDT"
    default_timeframe: str = "15m"
    live_trading_enabled: bool = False
    live_trading_env_enabled: bool = False
    manual_live_approval: bool = False
    trading_paused: bool = False
    starting_equity: float = 100000.0
    max_leverage: float = 3.0
    max_risk_per_trade: float = 0.01
    max_daily_loss: float = 0.02
    max_consecutive_losses: int = 3
    stale_market_seconds: int = 1800
    slippage_threshold_pct: float = 0.003
    schedule_windows: str = "1h,4h,12h,24h"
    mock_provider_enabled: bool = True
    ai_enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    app_secret_seed: str = "change-me-local-dev-secret"
    ai_provider: str = "openai"
    ai_call_interval_minutes: int = 30
    decision_cycle_interval_minutes: int = 15
    ai_max_input_candles: int = 32
    ai_temperature: float = 0.1
    binance_market_data_enabled: bool = False
    binance_testnet_enabled: bool = False
    binance_futures_enabled: bool = True
    exchange_recv_window_ms: int = 5000
    next_public_api_base_url: str = "http://localhost:8000"
    data_dir: Path = Field(default_factory=lambda: Path("data"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
