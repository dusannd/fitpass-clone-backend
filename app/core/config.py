from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    STRIPE_API_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # --- NEW: URL & CORS SETTINGS ---
    # Fallback to localhost if not provided in .env
    FRONTEND_URL: str = "http://localhost:5173"
    # Note: Pydantic parses comma-separated strings into lists automatically if defined as list
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # --- NEW: SECURITY & ANTI-SPAM SETTINGS ---
    FEATURE_RECAPTCHA: bool = False
    RECAPTCHA_SECRET: str = ""

    # --- EMAIL SETTINGS ---
    # Only used for the reply-to style address in copy; the actual From header is
    # derived from SMTP_USER, because Gmail will not relay an address it does not
    # own. See app/services/email.py.
    EMAIL_FROM: str = "noreply@localhost"
    # The display name in front of the address, so the inbox shows
    # "FitPass <gym@gmail.com>" rather than a bare address.
    EMAIL_FROM_NAME: str = "FitPass"

    # --- SMTP (the only transport; Gmail in practice) ---
    # All three of HOST/USER/PASS must be set or the app falls back to the mock
    # provider and silently sends nothing. Port 465 switches to implicit TLS,
    # anything else (587) uses STARTTLS.
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASS: Optional[str] = None

    # --- LOCAL TIME ---
    # The gym's wall clock. Timestamps are stored in UTC, so anything an admin
    # reads as a time of day - peak hours, "today", the weekly breakdown - has to
    # be converted into this zone first, or a 01:30 visit is reported as having
    # happened the previous evening.
    GYM_TIMEZONE: str = "Europe/Belgrade"

    # Personal training can only be booked inside opening hours, read on the gym's
    # wall clock (GYM_TIMEZONE). A session may start at GYM_OPEN_HOUR and must be
    # over by GYM_CLOSE_HOUR.
    GYM_OPEN_HOUR: int = 6
    GYM_CLOSE_HOUR: int = 23

    # A member may cancel their own session up to this many hours before it starts.
    # Inside the window only the trainer can, so a trainer isn't left with an empty
    # slot at the last minute.
    MEMBER_CANCEL_CUTOFF_HOURS: int = 12

    # A member a trainer rejected may ask again right away - but after this many
    # rejections in a row they wait REJECTION_COOLDOWN_DAYS before the next try.
    MAX_CONSECUTIVE_REJECTIONS: int = 3
    REJECTION_COOLDOWN_DAYS: int = 7

    TESTING: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()