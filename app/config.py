from functools import lru_cache
from typing import List, Optional

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    project_name: str = "GaNIndustry Monitor-assistant"
    db_url: str = "sqlite:///./data/ganiq.db"
    timezone: str = "Asia/Taipei"

    ingest_interval_hours: int = 2        # auto-fetch every N hours
    stock_interval_minutes: int = 30
    weekly_cron_day_of_week: str = "*"
    weekly_cron_hour: int = 8
    weekly_cron_minute: int = 0

    max_articles_per_source: int = 20

    deepseek_api_key: Optional[str] = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout_seconds: int = 35
    deepseek_enabled: bool = True

    gmail_smtp_host: str = "smtp.gmail.com"
    gmail_smtp_port: int = 587
    gmail_user: Optional[str] = None
    gmail_app_password: Optional[str] = None
    gmail_to: Optional[str] = None
    gmail_from_display_name: str = "GaN Intelligence Bot"

    stock_tickers_csv: str = "NVTS,ON,STM,IFNNY,WOLF,TXN,RNECY,MCHP,ADI"

    @computed_field
    @property
    def stock_tickers(self) -> List[str]:
        return [ticker.strip().upper() for ticker in self.stock_tickers_csv.split(",") if ticker.strip()]

    @computed_field
    @property
    def email_enabled(self) -> bool:
        return bool(self.gmail_user and self.gmail_app_password and self.gmail_to)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
