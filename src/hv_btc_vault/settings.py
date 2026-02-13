from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    hl_base_url: str = Field(alias="HL_BASE_URL")
    hl_secret_key: str = Field(alias="HL_SECRET_KEY")
    hl_account_address: str = Field(alias="HL_ACCOUNT_ADDRESS")
    hl_vault_address: str = Field(alias="HL_VAULT_ADDRESS")
    hl_asset: str = Field(default="BTC", alias="HL_ASSET")
    hl_default_slippage: float = Field(default=0.01, alias="HL_DEFAULT_SLIPPAGE")

    max_abs_position_btc: float = Field(default=1.0, alias="MAX_ABS_POSITION_BTC")
    min_trade_notional_usd: float = Field(default=25.0, alias="MIN_TRADE_NOTIONAL_USD")
    confidence_threshold: float = Field(default=0.55, alias="CONFIDENCE_THRESHOLD")
    prediction_horizon_hours: int = Field(default=48, alias="PREDICTION_HORIZON_HOURS")

    dry_run: bool = Field(default=True, alias="DRY_RUN")


settings = Settings()
