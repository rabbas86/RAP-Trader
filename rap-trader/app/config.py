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
    max_position_percent: float = Field(default=5, gt=0, le=100, validation_alias="MAX_POSITION_PERCENT")
    max_daily_loss_percent: float = Field(default=2, gt=0, le=100, validation_alias="MAX_DAILY_LOSS_PERCENT")
    max_portfolio_drawdown_percent: float = Field(default=10, gt=0, le=100, validation_alias="MAX_PORTFOLIO_DRAWDOWN_PERCENT")
    kronos_provider: str = Field(default="mock", validation_alias="KRONOS_PROVIDER")
    kronos_model_id: str = Field(default="mock-kronos-v0", validation_alias="KRONOS_MODEL_ID")
    kronos_offline_only: bool = Field(default=True, validation_alias="KRONOS_OFFLINE_ONLY")
    kronos_device: str = Field(default="cpu", validation_alias="KRONOS_DEVICE")
    kronos_model_path: str | None = Field(default=None, validation_alias="KRONOS_MODEL_PATH")
    kronos_tokenizer_path: str | None = Field(default=None, validation_alias="KRONOS_TOKENIZER_PATH")
    kronos_max_lookback: int = Field(default=60, gt=0, validation_alias="KRONOS_MAX_LOOKBACK")
    kronos_max_forecast_horizon: int = Field(default=5, gt=0, validation_alias="KRONOS_MAX_FORECAST_HORIZON")
    kronos_max_sample_count: int = Field(default=1, gt=0, validation_alias="KRONOS_MAX_SAMPLE_COUNT")
    kronos_timeout_seconds: float = Field(default=30.0, gt=0, validation_alias="KRONOS_TIMEOUT_SECONDS")
    backtest_offline_only: bool = Field(default=True, validation_alias="BACKTEST_OFFLINE_ONLY")
    backtest_result_dir: str = Field(default="backtest_results", validation_alias="BACKTEST_RESULT_DIR")

    @model_validator(mode="after")
    def reject_disabled_live_mode(self) -> "Settings":
        if self.trading_mode == "live" and not self.live_trading_enabled:
            raise ValueError("TRADING_MODE=live requires LIVE_TRADING_ENABLED=true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
