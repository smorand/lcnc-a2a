"""Application settings via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """LCNC A2A configuration loaded from environment variables.

    All variables are prefixed with ``LCNC_A2A_``.
    """

    model_config = SettingsConfigDict(
        env_prefix="LCNC_A2A_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    encryption_key: str
    session_secret: str
    trace_file: Path = Path("traces/lcnc-a2a.jsonl")
    session_expiry_hours: int = 24
    csrf_max_age_seconds: int = 3600
