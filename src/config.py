"""
Centralized application configuration.

All configuration values are loaded from environment variables (with
fallback to a `.env` file in the project root). Values are validated
and type-coerced via Pydantic Settings, so importing this module will
fail fast if config is malformed — surfacing problems at startup
rather than deep inside business logic.

Usage:
    from src.config import settings
    print(settings.stripe_llms_url)
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, HttpUrl, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (src/config.py → project root)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- Source configuration ----------
    stripe_llms_url: HttpUrl = Field(
        default=HttpUrl("https://docs.stripe.com/llms.txt"),
        description="URL of the Stripe llms.txt index file.",
    )

    # ---------- Download behavior ----------
    download_concurrency: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum concurrent HTTP downloads (semaphore size).",
    )

    request_delay_min: float = Field(
        default=0.5,
        ge=0.0,
        description="Minimum delay between requests in seconds (politeness).",
    )

    request_delay_max: float = Field(
        default=1.0,
        ge=0.0,
        description="Maximum delay between requests in seconds (politeness).",
    )

    http_timeout: float = Field(
        default=30.0,
        ge=0.0,
        description="Http timeout in seconds (politeness).",
    )

    http_connect_timeout: float = Field(
        default=10.0,
        gt=0.0,
        description="HTTP connection timeout in seconds.",
    )

    http_user_agent: str = Field(
        default="AskMyDocs/0.1 (educational)",
        description="User-Agent header for outbound HTTP requests.",
    )

    raw_data_dir: Path = Field(
        default=Path("data/raw"),
        description="Directory for raw downloaded markdown files.",
    )
    processed_data_dir: Path = Field(
        default=Path("data/processed"),
        description="Directory for processed JSON outputs.",
    )
    logs_dir: Path = Field(
        default=Path("logs"),
        description="Directory for log files.",
    )

    # ---------- Logging ----------
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL.",
    )

    # ---------- Validators ----------
    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Ensure log_level is one of Python's standard levels."""
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v!r}")
        return upper

    @field_validator("request_delay_max")
    @classmethod
    def validate_delay_range(cls, v: float, info: ValidationInfo) -> float:
        """Ensure max delay >= min delay."""
        min_delay = info.data.get("request_delay_min", 0.0)
        if v < min_delay:
            raise ValueError(f"request_delay_max ({v}) must be >= request_delay_min ({min_delay})")
        return v

    # ---------- Convenience properties ----------
    @property
    def raw_data_path(self) -> Path:
        """Absolute path to raw data directory."""
        return PROJECT_ROOT / self.raw_data_dir

    @property
    def processed_data_path(self) -> Path:
        """Absolute path to processed data directory."""
        return PROJECT_ROOT / self.processed_data_dir

    @property
    def logs_path(self) -> Path:
        """Absolute path to logs directory."""
        return PROJECT_ROOT / self.logs_dir


# Instantiated once at import time. Import this object from other modules.
settings = Settings()
