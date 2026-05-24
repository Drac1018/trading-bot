import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from trading_mvp.services.secret_store import secret_seed_is_insecure

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_DIR = (_PROJECT_ROOT / "data").resolve()
_DEFAULT_DATABASE_PATH = (_DEFAULT_DATA_DIR / "trading_mvp.db").resolve()
_DEFAULT_POSTGRES_DATABASE_URL = "postgresql+psycopg://trading:trading@127.0.0.1:5432/trading_mvp"
DEFAULT_APP_SECRET_SEED = "change-me-local-dev-secret"
PRODUCTION_APP_ENVS = {"prod", "production"}
_PRODUCTION_APP_ENVS = {"prod", "production"}


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


def _explicit_flag(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        value = _read_dotenv_value(name)
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _read_dotenv_value(name: str) -> str | None:
    dotenv_path = _PROJECT_ROOT / ".env"
    if not dotenv_path.exists():
        return None
    for line in dotenv_path.read_text(encoding="utf-8-sig").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        key, value = trimmed.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return None


def get_explicit_database_url() -> str | None:
    value = os.getenv("DATABASE_URL")
    if value and value.strip():
        return value.strip()
    return _read_dotenv_value("DATABASE_URL")


def require_runtime_database_url(context: str) -> str:
    database_url = get_explicit_database_url()
    if not database_url:
        raise RuntimeError(
            f"DATABASE_URL is required for {context}. "
            "Set a PostgreSQL URL, or explicitly opt in to SQLite for local/dev use."
        )
    database_url = _resolve_sqlite_database_url(database_url)
    if database_url.startswith("sqlite") and not _explicit_flag("TRADING_MVP_ALLOW_SQLITE"):
        raise RuntimeError(
            f"DATABASE_URL points to SQLite for {context}. "
            "Set TRADING_MVP_ALLOW_SQLITE=1 only for explicit local/dev use."
        )
    return database_url


def runtime_security_enforced(settings: "Settings", *, rollout_mode: str | None = None) -> bool:
    return settings.app_env.strip().lower() in PRODUCTION_APP_ENVS or str(rollout_mode or "").strip().lower() == "full_live"


def runtime_security_errors(settings: "Settings", *, rollout_mode: str | None = None) -> list[str]:
    if not runtime_security_enforced(settings, rollout_mode=rollout_mode):
        return []

    errors: list[str] = []
    if secret_seed_is_insecure(settings.app_secret_seed):
        errors.append("APP_SECRET_SEED must be changed before production/full_live runtime.")
    if not any(
        key.strip()
        for key in (
            settings.operator_api_key,
            settings.operator_viewer_api_key,
            settings.operator_trader_api_key,
            settings.operator_admin_api_key,
        )
    ):
        errors.append(
            "OPERATOR_API_KEY or role-specific operator API keys are required before production/full_live runtime."
        )
    return errors


def require_runtime_security_settings(settings: "Settings", *, rollout_mode: str | None = None) -> None:
    errors = runtime_security_errors(settings, rollout_mode=rollout_mode)
    if errors:
        raise RuntimeError("; ".join(errors))


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def app_env_is_production(value: str | None) -> bool:
    return str(value or "").strip().lower() in _PRODUCTION_APP_ENVS


def runtime_requires_private_secret(settings: "Settings", *, rollout_mode: object | None = None) -> bool:
    return runtime_security_enforced(settings, rollout_mode=str(rollout_mode or ""))


def validate_runtime_secret_seed(settings: "Settings", *, rollout_mode: object | None = None) -> None:
    if runtime_requires_private_secret(settings, rollout_mode=rollout_mode) and secret_seed_is_insecure(
        settings.app_secret_seed
    ):
        raise RuntimeError(
            "APP_SECRET_SEED must be changed from the local development default before production/full_live runtime."
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_port: int = 3000
    database_url: str = _DEFAULT_POSTGRES_DATABASE_URL
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
    max_same_direction_major_exposure_pct: float = 2.0
    stale_market_seconds: int = 1800
    slippage_threshold_pct: float = 0.003
    cost_model_maker_fee_bps: float = 5.0
    cost_model_taker_fee_bps: float = 5.0
    cost_model_marketable_slippage_bps: float = 3.0
    cost_model_passive_slippage_bps: float = 1.0
    cost_model_unknown_slippage_bps: float = 2.0
    cost_model_recent_sample_limit: int = 20
    cost_model_min_required_net_bps: float = 15.0
    expected_edge_gate_enabled: bool = False
    expected_edge_gate_shadow: bool = True
    max_cost_to_edge_ratio: float = 0.50
    min_net_expected_edge_bps: float = 15.0
    min_expected_gross_bps_default: float = 40.0
    max_fee_to_gross_ratio: float = 0.30
    min_confidence_for_entry: float = 0.70
    net_edge_sizing_min_multiplier: float = 0.45
    net_edge_sizing_high_fee_cap: float = 0.65
    net_edge_sizing_tight_gross_cap: float = 0.70
    net_edge_sizing_tight_gross_bps: float = 60.0
    symbol_side_min_expected_gross_bps: dict[str, float] | str = Field(
        default_factory=lambda: {
            "BTCUSDT:long": 40.0,
            "ETHUSDT:long": 0.0,
        }
    )
    same_symbol_side_tp_cooldown_minutes: int = 30
    recent_tp_reentry_edge_uplift_bps: float = 10.0
    range_mr_cooldown_enabled: bool = True
    range_mr_max_consecutive_failures: int = 2
    range_mr_cooldown_minutes: int = 30
    range_mr_breakout_cooldown_minutes: int = 60
    adaptive_signal_enabled: bool = False
    position_management_enabled: bool = True
    break_even_enabled: bool = False
    breakeven_shadow: bool = True
    breakeven_lock_bps: float = 0.0
    breakeven_min_hold_seconds: int = 0
    atr_trailing_stop_enabled: bool = True
    partial_take_profit_enabled: bool = True
    partial_tp_rr: float = 1.5
    partial_tp_size_pct: float = 0.25
    move_stop_to_be_rr: float = 1.0
    time_stop_enabled: bool = False
    time_stop_minutes: int = 120
    time_stop_profit_floor: float = 0.15
    scalp_early_fail_minutes: int = 15
    scalp_early_fail_profit_floor_r: float = 0.10
    short_tp_expected_gross_bps: float = 50.0
    short_tp_expected_holding_minutes: int = 60
    use_limit_take_profit_for_tight_tp: bool = True
    tight_tp_bps_threshold: float = 40.0
    tp_limit_post_only: bool = True
    holding_edge_decay_enabled: bool = True
    reduce_on_regime_shift_enabled: bool = True
    schedule_windows: str = "1h,4h,12h,24h"
    mock_provider_enabled: bool = True
    ai_enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    app_secret_seed: str = DEFAULT_APP_SECRET_SEED
    operator_api_key: str = ""
    operator_viewer_api_key: str = ""
    operator_trader_api_key: str = ""
    operator_admin_api_key: str = ""
    operator_api_rate_limit_per_minute: int = Field(default=600, ge=1, le=10000)
    operator_api_write_rate_limit_per_minute: int = Field(default=120, ge=1, le=10000)
    cors_allowed_origins: str = ""
    cors_allow_local_dev_origins: bool = True
    ai_provider: str = "openai"
    ai_call_interval_minutes: int = 10
    decision_cycle_interval_minutes: int = 15
    ai_decision_ttl_seconds: int = 900
    ai_decision_price_move_invalidation_pct: float = 0.004
    ai_decision_volatility_spike_multiplier: float = 1.5
    ai_decision_volatility_spike_min_pct: float = 0.02
    ai_trading_decision_daily_token_budget: int = Field(default=1_000_000, ge=10_000, le=50_000_000)
    ai_max_input_candles: int = 32
    ai_temperature: float = 0.1
    ai_market_settings_advisor_enabled: bool = True
    ai_market_settings_advisor_shadow: bool = True
    ai_market_settings_advisor_normal_interval_seconds: int = 900
    ai_market_settings_advisor_elevated_interval_seconds: int = 900
    ai_market_settings_advisor_min_recheck_interval_seconds: int = 900
    ai_market_settings_advisor_recommendation_ttl_seconds: int = 900
    ai_market_settings_advisor_min_confidence_to_apply: float = 0.70
    ai_market_settings_auto_apply_mode: str = "shadow"
    ai_market_settings_relax_requires_consecutive_confirmations: int = 2
    ai_market_settings_min_profile_dwell_seconds: int = 900
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
