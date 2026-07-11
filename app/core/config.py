from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from .env file.
    Pydantic automatically validates these types.
    """
    DATABASE_URL: str
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    STRIPE_API_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


# Create a global settings object
settings = Settings()