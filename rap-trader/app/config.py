from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration with deliberately safe defaults."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = Field(default="rap-trader", validation_alias="APP_NAME")
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    trading_mode: Literal["paper", "live"] = Field(default="paper", validation_alias="TRADING_MODE")
    live_trading_enabled: bool = Field(default=False, validation_alias="LIVE_TRADING_ENABLED")
    allow_live_trading: bool = Field(default=False, validation_alias="ALLOW_LIVE_TRADING")
    max_position_percent: float = Field(default=5, gt=0, le=100, validation_alias="MAX_POSITION_PERCENT")
    max_daily_loss_percent: float = Field(default=2, gt=0, le=100, validation_alias="MAX_DAILY_LOSS_PERCENT")
    max_portfolio_drawdown_percent: float = Field(default=10, gt=0, le=100, validation_alias="MAX_PORTFOLIO_DRAWDOWN_PERCENT")
    kronos_provider: str = Field(default="mock", validation_alias="KRONOS_PROVIDER")
    kronos_model_id: str = Field(default="mock-kronos-v0", validation_alias="KRONOS_MODEL_ID")
    kronos_offline_only: bool = Field(default=True, validation_alias="KRONOS_OFFLINE_ONLY")
    allow_non_offline_kronos: bool = Field(default=False, validation_alias="ALLOW_NON_OFFLINE_KRONOS")
    kronos_device: str = Field(default="cpu", validation_alias="KRONOS_DEVICE")
    kronos_model_path: str | None = Field(default=None, validation_alias="KRONOS_MODEL_PATH")
    kronos_tokenizer_path: str | None = Field(default=None, validation_alias="KRONOS_TOKENIZER_PATH")
    kronos_max_lookback: int = Field(default=60, gt=0, validation_alias="KRONOS_MAX_LOOKBACK")
    kronos_max_forecast_horizon: int = Field(default=5, gt=0, validation_alias="KRONOS_MAX_FORECAST_HORIZON")
    kronos_max_sample_count: int = Field(default=1, gt=0, validation_alias="KRONOS_MAX_SAMPLE_COUNT")
    kronos_timeout_seconds: float = Field(default=30.0, gt=0, validation_alias="KRONOS_TIMEOUT_SECONDS")
    backtest_offline_only: bool = Field(default=True, validation_alias="BACKTEST_OFFLINE_ONLY")
    allow_non_offline_backtest: bool = Field(default=False, validation_alias="ALLOW_NON_OFFLINE_BACKTEST")
    backtest_result_dir: str = Field(default="backtest_results", validation_alias="BACKTEST_RESULT_DIR")

    @model_validator(mode="after")
    def enforce_runtime_safety(self) -> "Settings":
        if self.trading_mode == "live" and not self.live_trading_enabled:
            raise ValueError("TRADING_MODE=live requires LIVE_TRADING_ENABLED=true")
        if not self.kronos_offline_only and not self.allow_non_offline_kronos:
            raise ValueError("KRONOS_OFFLINE_ONLY=false requires ALLOW_NON_OFFLINE_KRONOS=true")
        if not self.backtest_offline_only and not self.allow_non_offline_backtest:
            raise ValueError("BACKTEST_OFFLINE_ONLY=false requires ALLOW_NON_OFFLINE_BACKTEST=true")
        if self.trading_mode == "live" or self.live_trading_enabled:
            if self.app_env.casefold() not in {"staging", "production"}:
                raise ValueError("live trading requires APP_ENV=staging or APP_ENV=production")
            if not self.allow_live_trading:
                raise ValueError("live trading requires ALLOW_LIVE_TRADING=true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
