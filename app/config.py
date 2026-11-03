
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"

    webhook_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 5
    webhook_max_concurrency: int = 10
    webhook_secret: str = ""
    allow_local_webhooks: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
