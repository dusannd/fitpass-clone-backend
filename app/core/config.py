from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    STRIPE_API_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # --- NEW: SECURITY & ANTI-SPAM SETTINGS ---
    FEATURE_RECAPTCHA: bool = False
    RECAPTCHA_SECRET: str = ""

    # --- EMAIL SETTINGS ---
    EMAIL_FROM: str = "onboarding@resend.dev"
    RESEND_API_KEY: Optional[str] = None

    # SMTP Fallbacks
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASS: Optional[str] = None

    TESTING: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()